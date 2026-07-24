import os
import time
import random
import logging
import httpx
try:
    from supabase import create_client
except ImportError:
    create_client = None

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

from vip_bot.config import env_int, ACTIVE_PAYMENT_STATUSES, RETRYABLE_PAYMENT_STATUSES

LOGGER = logging.getLogger("telegram_vip_bot.db_store")


def os_getenv_wrapper(key, default=""):
    return os.getenv(key, default).strip()


class PaymentStore:
    def __init__(self, config):
        self.table = config.supabase_table
        self.package_table = config.supabase_package_table
        self.user_table = config.user_table
        self.referral_table = config.referral_table
        self.withdrawal_table = config.withdrawal_table
        self.broadcast_table = os_getenv_wrapper("SUPABASE_BROADCAST_TABLE", "vip_broadcast_messages")
        self.settings_table = os_getenv_wrapper("SUPABASE_SETTINGS_TABLE", "vip_bot_settings")
        self.payment_bots_table = os_getenv_wrapper("SUPABASE_PAYMENT_BOTS_TABLE", "vip_payment_bots")

        self.db_url = getattr(config, "database_url", "") or os.getenv("DATABASE_URL", "").strip()
        if create_client and config.supabase_url and config.supabase_service_role_key:
            self.client = create_client(config.supabase_url, config.supabase_service_role_key)
            self.use_postgres = False
            LOGGER.info("PaymentStore using Supabase API Client (%s)", config.supabase_url)
        elif psycopg2 and self.db_url:
            self.client = None
            self.use_postgres = True
            LOGGER.info("PaymentStore using Native Direct PostgreSQL (%s)", self.db_url.split("@")[-1])
        else:
            self.client = None
            self.use_postgres = False
            LOGGER.error("⚠️ Neither Supabase nor DATABASE_URL is configured in .env!")

        self.query_retries = max(1, env_int("SUPABASE_QUERY_RETRIES", 3))
        self.retry_base_delay = max(0.1, float(os_getenv_wrapper("SUPABASE_RETRY_BASE_DELAY", "0.35")))

    def _execute(self, query, action):
        if not self.client:
            return None
        for attempt in range(1, self.query_retries + 1):
            try:
                return query.execute()
            except httpx.TransportError as exc:
                if attempt >= self.query_retries:
                    raise
                delay = min(4.0, self.retry_base_delay * (2 ** (attempt - 1))) + random.uniform(0, 0.15)
                LOGGER.warning(
                    "Transient Supabase transport error during %s, retrying in %.2fs (%s/%s): %s",
                    action,
                    delay,
                    attempt,
                    self.query_retries,
                    exc,
                )
                time.sleep(delay)
            except Exception as exc:
                LOGGER.error("Supabase query error during %s: %s", action, exc)
                return None

    def _pg_query(self, sql, params=(), fetchone=False, fetchall=False):
        if not self.use_postgres or not psycopg2 or not self.db_url:
            return None if fetchone else ([] if fetchall else False)
        for attempt in range(1, self.query_retries + 1):
            try:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(sql, params)
                        conn.commit()
                        if fetchone:
                            res = cur.fetchone()
                            return dict(res) if res else None
                        if fetchall:
                            res = cur.fetchall()
                            return [dict(r) for r in res]
                        return True
            except Exception as exc:
                if attempt >= self.query_retries:
                    LOGGER.error("Native PostgreSQL query error during [%s]: %s", sql[:60], exc)
                    return None if fetchone else ([] if fetchall else False)
                time.sleep(0.2 * attempt)
        return None if fetchone else ([] if fetchall else False)

    def create_payment(
        self,
        user,
        public_invoice_id,
        order_id,
        payment_url,
        inv_id,
        amount,
        buyer_name,
        buyer_email,
        qris_data,
        qris_chat_id,
        qris_message_id,
        package=None,
        referral=None,
    ):
        self.upsert_user(user)
        from vip_bot.helpers import utc_now_iso, parse_iso_datetime, next_poll_at, display_name
        import datetime as dt
        now = utc_now_iso()
        payload = qris_data.get("data", {}) if isinstance(qris_data, dict) else {}
        package = package or {}
        expires_at = parse_iso_datetime(payload.get("countdown") or "")
        next_check_at = next_poll_at(dt.datetime.now(dt.UTC), expires_at, attempts=0, error="")
        user_id = getattr(user, "id", user) if hasattr(user, "id") else int(user)
        username = getattr(user, "username", "") or ""
        data = {
            "user_id": user_id,
            "username": username,
            "full_name": display_name(user) if hasattr(user, "first_name") else f"User_{user_id}",
            "package_code": package.get("code") or "",
            "package_name": package.get("name") or "",
            "package_amount": int(package.get("amount") or amount),
            "vip_chat_id": package.get("vip_chat_id"),
            "invite_expire_hours": int(package.get("invite_expire_hours") or 0),
            "public_invoice_id": public_invoice_id,
            "order_id": order_id,
            "payment_url": payment_url,
            "inv_id": inv_id,
            "amount": amount,
            "status": "pending",
            "buyer_name": buyer_name,
            "buyer_email": buyer_email,
            "qris_amount": str(payload.get("amount") or ""),
            "qris_expires": str(payload.get("countdown") or ""),
            "qris_chat_id": qris_chat_id,
            "qris_message_id": qris_message_id,
            "next_check_at": next_check_at,
            "poll_attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
        if referral:
            data["referral_id"] = referral.get("id")
            data["referrer_user_id"] = referral.get("referrer_user_id")

        if self.use_postgres:
            sql = f"""
                INSERT INTO {self.table} (
                    user_id, username, full_name, package_code, package_name, package_amount, vip_chat_id,
                    invite_expire_hours, public_invoice_id, order_id, payment_url, inv_id, amount, status,
                    buyer_name, buyer_email, qris_amount, qris_expires, qris_chat_id, qris_message_id,
                    next_check_at, poll_attempts, created_at, updated_at, referral_id, referrer_user_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """
            params = (
                data["user_id"], data["username"], data["full_name"], data["package_code"], data["package_name"],
                data["package_amount"], data["vip_chat_id"], data["invite_expire_hours"], data["public_invoice_id"],
                data["order_id"], data["payment_url"], data["inv_id"], data["amount"], data["status"],
                data["buyer_name"], data["buyer_email"], data["qris_amount"], data["qris_expires"],
                data["qris_chat_id"], data["qris_message_id"], data["next_check_at"], data["poll_attempts"],
                data["created_at"], data["updated_at"], data.get("referral_id"), data.get("referrer_user_id")
            )
            self._pg_query(sql, params)
            return

        if not self.client:
            LOGGER.error("Cannot save payment %s: Neither Supabase nor DATABASE_URL is configured in .env!", public_invoice_id)
            return

        self._execute(self.client.table(self.table).insert(data), "create payment")

    def ensure_payment_schema_ready(self):
        if self.use_postgres:
            columns = "id,package_code,package_name,package_amount,vip_chat_id,invite_expire_hours,next_check_at,poll_attempts,last_polled_at"
            sql = f"SELECT {columns} FROM {self.table} LIMIT 1"
            self._pg_query(sql)
            return True
        if not self.client:
            return True
        columns = "id,package_code,package_name,package_amount,vip_chat_id,invite_expire_hours,next_check_at,poll_attempts,last_polled_at"
        query = self.client.table(self.table).select(columns).limit(1)
        self._execute(query, "check payment schema")
        return True

    def list_payment_bots(self, include_inactive=False):
        if self.use_postgres:
            if include_inactive:
                sql = f"SELECT * FROM {self.payment_bots_table} ORDER BY id ASC"
                params = ()
            else:
                sql = f"SELECT * FROM {self.payment_bots_table} WHERE active = true ORDER BY id ASC"
                params = ()
            return self._pg_query(sql, params, fetchall=True)
        if not self.client:
            return []
        query = self.client.table(self.payment_bots_table).select("*")
        if not include_inactive:
            query = query.eq("active", True)
        query = query.order("id", desc=False)
        response = self._execute(query, "list payment bots")
        return response.data if response and response.data else []

    def get_payment_bot(self, token_or_username):
        if not token_or_username:
            return None
        target = str(token_or_username).strip().lstrip("@")
        if self.use_postgres:
            sql = f"SELECT * FROM {self.payment_bots_table} WHERE bot_token = %s OR bot_username = %s LIMIT 1"
            return self._pg_query(sql, (target, target), fetchone=True)
        if not self.client:
            return None
        query = (
            self.client.table(self.payment_bots_table)
            .select("*")
            .or_(f"bot_token.eq.{target},bot_username.eq.{target}")
            .limit(1)
        )
        response = self._execute(query, "get payment bot")
        return response.data[0] if response and response.data else None

    def upsert_payment_bot(self, bot_token, bot_name, bot_username=""):
        from vip_bot.helpers import utc_now_iso
        clean_token = str(bot_token).strip()
        clean_username = str(bot_username).strip().lstrip("@")
        clean_name = str(bot_name).strip() or clean_username or "Payment Bot"
        now = utc_now_iso()
        data = {
            "bot_token": clean_token,
            "bot_username": clean_username,
            "bot_name": clean_name,
            "active": True,
            "updated_at": now,
        }
        if self.use_postgres:
            sql = f"""
                INSERT INTO {self.payment_bots_table} (bot_token, bot_username, bot_name, active, updated_at)
                VALUES (%s, %s, %s, true, %s)
                ON CONFLICT (bot_token) DO UPDATE SET
                    bot_username = EXCLUDED.bot_username, bot_name = EXCLUDED.bot_name, active = true, updated_at = EXCLUDED.updated_at
                RETURNING *
            """
            return self._pg_query(sql, (clean_token, clean_username, clean_name, now), fetchone=True) or data
        if not self.client:
            return None
        query = self.client.table(self.payment_bots_table).upsert(data, on_conflict="bot_token")
        response = self._execute(query, "upsert payment bot")
        return response.data[0] if response and response.data else data

    def delete_payment_bot(self, token_or_username):
        from vip_bot.helpers import utc_now_iso
        if not token_or_username:
            return False
        target = str(token_or_username).strip().lstrip("@")
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"UPDATE {self.payment_bots_table} SET active = false, updated_at = %s WHERE bot_token = %s OR bot_username = %s RETURNING id"
            res = self._pg_query(sql, (now, target, target), fetchone=True)
            return bool(res)
        if not self.client:
            return False
        query = (
            self.client.table(self.payment_bots_table)
            .update({"active": False, "updated_at": now})
            .or_(f"bot_token.eq.{target},bot_username.eq.{target}")
        )
        response = self._execute(query, "delete payment bot")
        return bool(response and response.data)

    def list_packages(self, include_inactive=False, bot_username=None):
        if self.use_postgres:
            clean_bot = str(bot_username).strip().lstrip("@") if bot_username else ""
            if include_inactive:
                if clean_bot:
                    sql = f"SELECT * FROM {self.package_table} WHERE bot_username = %s OR bot_username = '' ORDER BY sort_order ASC, code ASC"
                    params = (clean_bot,)
                else:
                    sql = f"SELECT * FROM {self.package_table} ORDER BY sort_order ASC, code ASC"
                    params = ()
            else:
                if clean_bot:
                    sql = f"SELECT * FROM {self.package_table} WHERE active = true AND (bot_username = %s OR bot_username = '') ORDER BY sort_order ASC, code ASC"
                    params = (clean_bot,)
                else:
                    sql = f"SELECT * FROM {self.package_table} WHERE active = true ORDER BY sort_order ASC, code ASC"
                    params = ()
            return self._pg_query(sql, params, fetchall=True)
        if not self.client:
            return []
        query = self.client.table(self.package_table).select("*")
        if not include_inactive:
            query = query.eq("active", True)
        if bot_username:
            clean = str(bot_username).strip().lstrip("@")
            query = query.or_(f"bot_username.eq.{clean},bot_username.eq.''")
        query = query.order("sort_order", desc=False).order("code", desc=False)
        response = self._execute(query, "list packages")
        return response.data if response and response.data else []

    def get_package(self, code):
        from vip_bot.helpers import normalize_package_code
        clean_code = normalize_package_code(code)
        if self.use_postgres:
            sql = f"SELECT * FROM {self.package_table} WHERE code = %s AND active = true LIMIT 1"
            return self._pg_query(sql, (clean_code,), fetchone=True)
        if not self.client:
            return None
        query = self.client.table(self.package_table).select("*").eq("code", clean_code).eq("active", True).limit(1)
        response = self._execute(query, "get package")
        return response.data[0] if response and response.data else None

    def upsert_package(self, code, name, vip_chat_id, amount, invite_expire_hours=0, bot_username=""):
        from vip_bot.helpers import utc_now_iso, normalize_package_code
        now = utc_now_iso()
        clean_code = normalize_package_code(code)
        clean_bot = str(bot_username).strip().lstrip("@")
        if self.use_postgres:
            sql = f"""
                INSERT INTO {self.package_table} (code, name, vip_chat_id, amount, bot_username, invite_expire_hours, active, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, true, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name, vip_chat_id = EXCLUDED.vip_chat_id, amount = EXCLUDED.amount,
                    bot_username = EXCLUDED.bot_username, invite_expire_hours = EXCLUDED.invite_expire_hours,
                    active = true, updated_at = EXCLUDED.updated_at
            """
            self._pg_query(sql, (clean_code, name.strip(), int(vip_chat_id), int(amount), clean_bot, int(invite_expire_hours or 0), now))
            return
        if not self.client:
            return
        data = {
            "code": clean_code,
            "name": name.strip(),
            "vip_chat_id": int(vip_chat_id),
            "amount": int(amount),
            "bot_username": clean_bot,
            "invite_expire_hours": int(invite_expire_hours or 0),
            "active": True,
            "updated_at": now,
        }
        query = self.client.table(self.package_table).upsert(data, on_conflict="code")
        self._execute(query, "upsert package")

    def delete_package(self, code):
        from vip_bot.helpers import utc_now_iso, normalize_package_code
        now = utc_now_iso()
        clean_code = normalize_package_code(code)
        if self.use_postgres:
            sql = f"UPDATE {self.package_table} SET active = false, updated_at = %s WHERE code = %s RETURNING code"
            res = self._pg_query(sql, (now, clean_code), fetchone=True)
            return bool(res)
        if not self.client:
            return False
        query = self.client.table(self.package_table).update(
            {"active": False, "updated_at": now}
        ).eq("code", clean_code)
        response = self._execute(query, "delete package")
        return bool(response and response.data)

    def latest_pending_for_user(self, user_id):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.table} WHERE user_id = %s AND status IN ({','.join(['%s']*len(ACTIVE_PAYMENT_STATUSES))}) ORDER BY id DESC LIMIT 1"
            params = [int(user_id)] + list(ACTIVE_PAYMENT_STATUSES)
            return self._pg_query(sql, params, fetchone=True)
        if not self.client:
            return None
        rows = []
        for status in ACTIVE_PAYMENT_STATUSES:
            query = (
                self.client.table(self.table)
                .select("*")
                .eq("user_id", user_id)
                .eq("status", status)
                .order("id", desc=True)
                .limit(1)
            )
            response = self._execute(query, f"latest active payment {status}")
            if response and response.data:
                rows.extend(response.data)
        rows.sort(key=lambda item: item["id"], reverse=True)
        return rows[0] if rows else None

    def retryable_payments(self, due_before, limit):
        if self.use_postgres:
            rows = []
            for status in RETRYABLE_PAYMENT_STATUSES:
                sql = f"SELECT * FROM {self.table} WHERE status = %s AND (next_check_at <= %s OR next_check_at IS NULL) ORDER BY next_check_at ASC, id ASC LIMIT %s"
                res = self._pg_query(sql, (status, due_before, limit), fetchall=True)
                if res:
                    rows.extend(res)
            rows.sort(key=lambda item: ((item.get("next_check_at") or ""), item["id"]))
            return rows[:limit]
        if not self.client:
            return []
        rows = []
        for status in RETRYABLE_PAYMENT_STATUSES:
            query = (
                self.client.table(self.table)
                .select("*")
                .eq("status", status)
                .or_(f"next_check_at.lte.{due_before},next_check_at.is.null")
                .order("next_check_at", desc=False)
                .order("id", desc=False)
                .limit(limit)
            )
            response = self._execute(query, f"retryable payments {status}")
            if response and response.data:
                rows.extend(response.data)
        rows.sort(key=lambda item: ((item.get("next_check_at") or ""), item["id"]))
        return rows[:limit]

    def recover_stale_processing(self, older_than_seconds=300):
        from vip_bot.helpers import utc_now_iso
        import datetime as dt
        cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=older_than_seconds)).replace(microsecond=0).isoformat()
        now = utc_now_iso()
        if self.use_postgres:
            sql1 = f"UPDATE {self.table} SET status = 'invite_error', error = 'Recovered stale paid processing', updated_at = %s WHERE status = 'processing_paid' AND updated_at < %s"
            self._pg_query(sql1, (now, cutoff))
            sql2 = f"UPDATE {self.table} SET status = 'delivery_error', error = 'Recovered stale delivery processing', updated_at = %s WHERE status = 'processing_delivery' AND updated_at < %s"
            self._pg_query(sql2, (now, cutoff))
            return
        if not self.client:
            return
        query = self.client.table(self.table).update(
            {
                "status": "invite_error",
                "error": "Recovered stale paid processing",
                "updated_at": now,
            }
        ).eq("status", "processing_paid").lt("updated_at", cutoff)
        self._execute(query, "recover stale paid processing")
        query = self.client.table(self.table).update(
            {
                "status": "delivery_error",
                "error": "Recovered stale delivery processing",
                "updated_at": now,
            }
        ).eq("status", "processing_delivery").lt("updated_at", cutoff)
        self._execute(query, "recover stale delivery processing")

    def get_by_inv_id(self, inv_id):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.table} WHERE inv_id = %s LIMIT 1"
            return self._pg_query(sql, (inv_id,), fetchone=True)
        if not self.client:
            return None
        query = self.client.table(self.table).select("*").eq("inv_id", inv_id).limit(1)
        response = self._execute(query, "get payment by invoice")
        return response.data[0] if response and response.data else None

    def set_error(self, inv_id, error):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"UPDATE {self.table} SET error = %s, updated_at = %s WHERE inv_id = %s"
            self._pg_query(sql, (error[:1000], now, inv_id))
            return
        if not self.client:
            return
        query = self.client.table(self.table).update(
            {"error": error[:1000], "updated_at": now}
        ).eq("inv_id", inv_id)
        self._execute(query, "set payment error")

    def mark_status_if_current(self, inv_id, from_status, to_status, error=""):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        next_check = now if to_status in RETRYABLE_PAYMENT_STATUSES else None
        if self.use_postgres:
            sql = f"UPDATE {self.table} SET status = %s, error = %s, updated_at = %s, next_check_at = %s WHERE inv_id = %s AND status = %s RETURNING id"
            res = self._pg_query(sql, (to_status, error, now, next_check, inv_id, from_status), fetchone=True)
            return bool(res)
        if not self.client:
            return False
        data = {"status": to_status, "error": error, "updated_at": now, "next_check_at": next_check}
        query = self.client.table(self.table).update(
            data
        ).eq("inv_id", inv_id).eq("status", from_status)
        response = self._execute(query, f"mark status {from_status} to {to_status}")
        return bool(response and response.data)

    def record_poll_result(self, payment, next_check_at, error=""):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        attempts = int(payment.get("poll_attempts") or 0) + 1
        if self.use_postgres:
            err_val = error[:1000] if error else ("" if payment.get("error") else None)
            if err_val is not None:
                sql = f"UPDATE {self.table} SET poll_attempts = %s, last_polled_at = %s, next_check_at = %s, updated_at = %s, error = %s WHERE inv_id = %s AND status = %s"
                params = (attempts, now, next_check_at, now, err_val, payment["inv_id"], payment["status"])
            else:
                sql = f"UPDATE {self.table} SET poll_attempts = %s, last_polled_at = %s, next_check_at = %s, updated_at = %s WHERE inv_id = %s AND status = %s"
                params = (attempts, now, next_check_at, now, payment["inv_id"], payment["status"])
            self._pg_query(sql, params)
            return
        if not self.client:
            return
        data = {
            "poll_attempts": attempts,
            "last_polled_at": now,
            "next_check_at": next_check_at,
            "updated_at": now,
        }
        if error:
            data["error"] = error[:1000]
        elif payment.get("error"):
            data["error"] = ""
        query = self.client.table(self.table).update(data).eq("inv_id", payment["inv_id"]).eq("status", payment["status"])
        self._execute(query, "record poll result")

    def claim_paid_processing(self, inv_id):
        return self.mark_status_if_current(inv_id, "pending", "processing_paid") or self.mark_status_if_current(
            inv_id, "invite_error", "processing_paid"
        )

    def mark_invite_error(self, inv_id, error):
        return self.mark_status_if_current(inv_id, "processing_paid", "invite_error", error[:1000])

    def mark_delivery_processing(self, inv_id, invite_link, invite_expires_at):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"UPDATE {self.table} SET status = 'processing_delivery', invite_link = %s, invite_expires_at = %s, next_check_at = NULL, error = '', updated_at = %s WHERE inv_id = %s AND status = 'processing_paid' RETURNING id"
            res = self._pg_query(sql, (invite_link, invite_expires_at, now, inv_id), fetchone=True)
            return bool(res)
        if not self.client:
            return False
        query = (
            self.client.table(self.table)
            .update(
                {
                    "status": "processing_delivery",
                    "invite_link": invite_link,
                    "invite_expires_at": invite_expires_at,
                    "next_check_at": None,
                    "error": "",
                    "updated_at": now,
                }
            )
            .eq("inv_id", inv_id)
            .eq("status", "processing_paid")
        )
        response = self._execute(query, "mark delivery processing")
        return bool(response and response.data)

    def claim_delivery_processing(self, inv_id):
        return self.mark_status_if_current(inv_id, "delivery_error", "processing_delivery")

    def mark_delivery_error(self, inv_id, error):
        return self.mark_status_if_current(inv_id, "processing_delivery", "delivery_error", error[:1000])

    def mark_delivery_blocked(self, inv_id, error):
        return self.mark_status_if_current(inv_id, "processing_delivery", "delivery_blocked", error[:1000])

    def mark_paid(self, inv_id, invite_link, invite_expires_at):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"UPDATE {self.table} SET status = 'paid', invite_link = %s, invite_expires_at = %s, next_check_at = NULL, updated_at = %s WHERE inv_id = %s AND status = 'processing_delivery' RETURNING id"
            res = self._pg_query(sql, (invite_link, invite_expires_at, now, inv_id), fetchone=True)
            return bool(res)
        if not self.client:
            return False
        query = (
            self.client.table(self.table)
            .update(
                {
                    "status": "paid",
                    "invite_link": invite_link,
                    "invite_expires_at": invite_expires_at,
                    "next_check_at": None,
                    "updated_at": now,
                }
            )
            .eq("inv_id", inv_id)
            .eq("status", "processing_delivery")
        )
        response = self._execute(query, "mark paid")
        return bool(response and response.data)

    def get_setting(self, key, default=""):
        if self.use_postgres:
            sql = f"SELECT value FROM {self.settings_table} WHERE key = %s LIMIT 1"
            res = self._pg_query(sql, (key,), fetchone=True)
            return res.get("value") if res else default
        if not self.client:
            return default
        query = self.client.table(self.settings_table).select("value").eq("key", key).limit(1)
        response = self._execute(query, "get bot setting")
        if not response or not response.data:
            return default
        return response.data[0].get("value") or default

    def set_setting(self, key, value):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"INSERT INTO {self.settings_table} (key, value, updated_at) VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"
            self._pg_query(sql, (key, str(value), now))
            return
        if not self.client:
            return
        query = self.client.table(self.settings_table).upsert(
            {"key": key, "value": str(value), "updated_at": now},
            on_conflict="key",
        )
        self._execute(query, "set bot setting")

    def get_int_setting(self, key, default=0):
        value = self.get_setting(key, "")
        if not value:
            return default
        return int(value)

    def set_broadcast_message(self, message_text, media_file_id="", media_type="", entities_json="[]"):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            self._pg_query(f"UPDATE {self.broadcast_table} SET is_active = false, updated_at = %s WHERE is_active = true", (now,))
            sql = f"""
                INSERT INTO {self.broadcast_table} (message_text, media_telegram_file_id, media_type, entities_json, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, true, %s, %s) RETURNING *
            """
            data = (message_text or "", media_file_id or "", media_type or "", entities_json or "[]", now, now)
            res = self._pg_query(sql, data, fetchone=True)
            return res or {"message_text": message_text or "", "is_active": True}
        if not self.client:
            return {}
        self._execute(
            self.client.table(self.broadcast_table).update(
                {"is_active": False, "updated_at": now}
            ).eq("is_active", True),
            "deactivate broadcast messages",
        )
        data = {
            "message_text": message_text or "",
            "media_telegram_file_id": media_file_id or "",
            "media_type": media_type or "",
            "entities_json": entities_json or "[]",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        response = self._execute(self.client.table(self.broadcast_table).insert(data), "set broadcast message")
        return response.data[0] if response and response.data else data

    def get_active_broadcast_message(self):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.broadcast_table} WHERE is_active = true ORDER BY id DESC LIMIT 1"
            return self._pg_query(sql, (), fetchone=True)
        if not self.client:
            return None
        query = self.client.table(self.broadcast_table).select("*").eq("is_active", True).order("id", desc=True).limit(1)
        response = self._execute(query, "get active broadcast message")
        return response.data[0] if response and response.data else None

    def get_broadcast_targets(self, limit, day_start_iso=""):
        if self.use_postgres:
            if day_start_iso:
                sql = f"SELECT user_id FROM {self.user_table} WHERE is_bot = false AND (last_broadcast_at IS NULL OR last_broadcast_at < %s) ORDER BY user_id ASC LIMIT %s"
                return self._pg_query(sql, (day_start_iso, limit), fetchall=True)
            sql = f"SELECT user_id FROM {self.user_table} WHERE is_bot = false ORDER BY user_id ASC LIMIT %s"
            return self._pg_query(sql, (limit,), fetchall=True)
        if not self.client:
            return []
        query = self.client.table(self.user_table).select("user_id").eq("is_bot", False)
        if day_start_iso:
            query = query.or_(f"last_broadcast_at.is.null,last_broadcast_at.lt.{day_start_iso}")
        query = query.order("user_id", desc=False).limit(limit)
        response = self._execute(query, "get broadcast targets")
        return response.data if response and response.data else []

    def mark_user_broadcasted(self, user_id):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"UPDATE {self.user_table} SET last_broadcast_at = %s, updated_at = %s WHERE user_id = %s"
            self._pg_query(sql, (now, now, int(user_id)))
            return
        if not self.client:
            return
        query = self.client.table(self.user_table).update(
            {"last_broadcast_at": now, "updated_at": now}
        ).eq("user_id", int(user_id))
        self._execute(query, "mark user broadcasted")

    def count_broadcast_targets(self):
        if self.use_postgres:
            sql = f"SELECT COUNT(*) as count FROM {self.user_table} WHERE is_bot = false"
            res = self._pg_query(sql, (), fetchone=True)
            return int(res.get("count") or 0) if res else 0
        if not self.client:
            return 0
        response = self._execute(
            self.client.table(self.user_table).select("user_id", count="exact").eq("is_bot", False).limit(1),
            "count broadcast targets",
        )
        return int(response.count or 0) if response else 0

    def set_broadcast_time(self, time_str):
        self.set_setting("broadcast_time", time_str or "")

    def get_broadcast_time(self):
        return self.get_setting("broadcast_time", "")

    def set_last_broadcast_date(self, date_str):
        self.set_setting("last_broadcast_date", date_str or "")

    def get_last_broadcast_date(self):
        return self.get_setting("last_broadcast_date", "")

    def upsert_user(self, user):
        from vip_bot.helpers import utc_now_iso, format_referral_code, display_name
        user_id = getattr(user, "id", user) if hasattr(user, "id") else int(user)
        existing = self.get_user(user_id)
        code = existing.get("referral_code") if existing else format_referral_code(user_id)
        now = utc_now_iso()
        full_n = display_name(user) if hasattr(user, "first_name") else f"User_{user_id}"
        user_n = getattr(user, "username", "") or ""
        is_b = bool(getattr(user, "bot", False))
        if self.use_postgres:
            sql = f"""
                INSERT INTO {self.user_table} (user_id, username, full_name, referral_code, is_bot, balance, pending_referrals, successful_referrals, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 0, 0, 0, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username, full_name = EXCLUDED.full_name, updated_at = EXCLUDED.updated_at
                RETURNING *
            """
            res = self._pg_query(sql, (user_id, user_n, full_n, code, is_b, now, now), fetchone=True)
            return res or {**(existing or {}), "user_id": user_id, "username": user_n, "full_name": full_n, "referral_code": code}
        data = {
            "user_id": user_id,
            "username": user_n,
            "full_name": full_n,
            "referral_code": code,
            "is_bot": is_b,
            "updated_at": now,
        }
        if not existing:
            data.update(
                {
                    "balance": 0,
                    "pending_referrals": 0,
                    "successful_referrals": 0,
                    "created_at": now,
                }
            )
        if self.client:
            response = self._execute(self.client.table(self.user_table).upsert(data, on_conflict="user_id"), "upsert user")
            return response.data[0] if response and response.data else {**(existing or {}), **data}
        return {**(existing or {}), **data}

    def get_user(self, user_id):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.user_table} WHERE user_id = %s LIMIT 1"
            return self._pg_query(sql, (int(user_id),), fetchone=True)
        if not self.client:
            return None
        query = self.client.table(self.user_table).select("*").eq("user_id", int(user_id)).limit(1)
        response = self._execute(query, "get user")
        return response.data[0] if response and response.data else None

    def get_user_by_referral_code(self, code):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.user_table} WHERE referral_code = %s LIMIT 1"
            return self._pg_query(sql, (code,), fetchone=True)
        if not self.client:
            return None
        query = self.client.table(self.user_table).select("*").eq("referral_code", code).limit(1)
        response = self._execute(query, "get user by referral code")
        return response.data[0] if response and response.data else None

    def create_referral_if_absent(self, referrer, invited_user):
        from vip_bot.helpers import utc_now_iso, display_name, should_create_referral
        invited_id = getattr(invited_user, "id", invited_user)
        invited = self.upsert_user(invited_user)
        if not should_create_referral(invited_id, referrer.get("user_id") if referrer else 0, invited.get("invited_by_user_id")):
            return None, False
        now = utc_now_iso()
        code = referrer["referral_code"]
        inv_user_n = getattr(invited_user, "username", "") or ""
        inv_full_n = display_name(invited_user) if hasattr(invited_user, "first_name") else f"User_{invited_id}"
        if self.use_postgres:
            sql = f"""
                INSERT INTO {self.referral_table} (referrer_user_id, referrer_code, invited_user_id, invited_username, invited_full_name, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
                ON CONFLICT (invited_user_id) DO NOTHING RETURNING *
            """
            referral = self._pg_query(sql, (int(referrer["user_id"]), code, invited_id, inv_user_n, inv_full_n, now, now), fetchone=True)
            if referral:
                self._pg_query(f"UPDATE {self.user_table} SET invited_by_user_id = %s, updated_at = %s WHERE user_id = %s AND invited_by_user_id IS NULL", (int(referrer["user_id"]), now, invited_id))
                self._pg_query(f"UPDATE {self.user_table} SET pending_referrals = pending_referrals + 1 WHERE user_id = %s", (int(referrer["user_id"]),))
                return referral, True
            return None, False
        if not self.client:
            return None, False
        data = {
            "referrer_user_id": int(referrer["user_id"]),
            "referrer_code": code,
            "invited_user_id": invited_id,
            "invited_username": inv_user_n,
            "invited_full_name": inv_full_n,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }
        response = self._execute(self.client.table(self.referral_table).insert(data), "create referral")
        referral = response.data[0] if response and response.data else data
        self._execute(
            self.client.table(self.user_table).update(
                {"invited_by_user_id": int(referrer["user_id"]), "updated_at": now}
            ).eq("user_id", invited_id).is_("invited_by_user_id", "null"),
            "set invited by user",
        )
        self._execute(
            self.client.rpc("vip_increment_pending_referral", {"p_user_id": int(referrer["user_id"])}),
            "increment pending referral",
        )
        return referral, True

    def pending_referral_for_user(self, user_id):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.referral_table} WHERE invited_user_id = %s AND status = 'pending' LIMIT 1"
            return self._pg_query(sql, (int(user_id),), fetchone=True)
        if not self.client:
            return None
        query = self.client.table(self.referral_table).select("*").eq("invited_user_id", user_id).eq("status", "pending").limit(1)
        response = self._execute(query, "get pending referral")
        return response.data[0] if response and response.data else None

    def referral_stats(self, user_id):
        from vip_bot.helpers import format_referral_code
        user = self.get_user(user_id) or {}
        return {
            "pending_count": int(user.get("pending_referrals") or 0),
            "successful_count": int(user.get("successful_referrals") or 0),
            "balance": int(user.get("balance") or 0),
            "referral_code": user.get("referral_code") or format_referral_code(user_id),
            "invited_by_user_id": user.get("invited_by_user_id"),
            "phone": user.get("phone") or "",
        }

    def mark_referral_paid(self, referral_id, payment, commission):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"""
                UPDATE {self.referral_table}
                SET status = 'paid', payment_inv_id = %s, package_code = %s, package_amount = %s, commission_amount = %s, updated_at = %s
                WHERE id = %s AND status = 'pending'
                RETURNING *
            """
            params = (payment["inv_id"], payment.get("package_code") or "", int(payment.get("package_amount") or 0), int(commission), now, referral_id)
            referral = self._pg_query(sql, params, fetchone=True)
            if referral:
                self._pg_query(f"UPDATE {self.user_table} SET balance = balance + %s, pending_referrals = GREATEST(0, pending_referrals - 1), successful_referrals = successful_referrals + 1 WHERE user_id = %s", (int(commission), int(referral["referrer_user_id"])))
            return referral
        if not self.client:
            return None
        query = self.client.table(self.referral_table).update(
            {
                "status": "paid",
                "payment_inv_id": payment["inv_id"],
                "package_code": payment.get("package_code") or "",
                "package_amount": int(payment.get("package_amount") or 0),
                "commission_amount": int(commission),
                "updated_at": now,
            }
        ).eq("id", referral_id).eq("status", "pending")
        response = self._execute(query, "mark referral paid")
        referral = response.data[0] if response and response.data else None
        if referral:
            self._execute(
                self.client.rpc(
                    "vip_credit_referral_commission",
                    {"p_user_id": int(referral["referrer_user_id"]), "p_amount": int(commission)},
                ),
                "credit referral balance",
            )
        return referral

    def create_withdrawal(self, user, amount, details):
        from vip_bot.helpers import display_name, utc_now_iso
        user_id = getattr(user, "id", user)
        now = utc_now_iso()
        u_name = getattr(user, "username", "") or ""
        f_name = display_name(user) if hasattr(user, "first_name") else f"User_{user_id}"
        if self.use_postgres:
            user_row = self.get_user(user_id)
            if not user_row or int(user_row.get("balance") or 0) < int(amount):
                raise ValueError("Insufficient balance")
            self._pg_query(f"UPDATE {self.user_table} SET balance = balance - %s WHERE user_id = %s", (int(amount), user_id))
            sql = f"""
                INSERT INTO {self.withdrawal_table} (user_id, username, full_name, amount, phone, wallet_name, account_name, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s) RETURNING *
            """
            return self._pg_query(sql, (user_id, u_name, f_name, int(amount), details["phone"], details["wallet_name"], details["account_name"], now, now), fetchone=True)
        if not self.client:
            raise ValueError("Supabase client is not initialized")
        response = self._execute(
            self.client.rpc(
                "vip_create_withdrawal",
                {
                    "p_user_id": user_id,
                    "p_username": u_name,
                    "p_full_name": f_name,
                    "p_amount": int(amount),
                    "p_phone": details["phone"],
                    "p_wallet_name": details["wallet_name"],
                    "p_account_name": details["account_name"],
                },
            ),
            "create withdrawal",
        )
        if not response or not response.data:
            raise ValueError("Insufficient balance")
        return response.data[0]

    def get_withdrawal(self, withdrawal_id):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.withdrawal_table} WHERE id = %s LIMIT 1"
            return self._pg_query(sql, (int(withdrawal_id),), fetchone=True)
        if not self.client:
            return None
        query = self.client.table(self.withdrawal_table).select("*").eq("id", int(withdrawal_id)).limit(1)
        response = self._execute(query, "get withdrawal")
        return response.data[0] if response and response.data else None

    def update_withdrawal_status(self, withdrawal_id, from_status, to_status, admin_user_id):
        from vip_bot.helpers import utc_now_iso
        now = utc_now_iso()
        if self.use_postgres:
            sql = f"UPDATE {self.withdrawal_table} SET status = %s, admin_user_id = %s, updated_at = %s WHERE id = %s AND status = %s RETURNING *"
            withdrawal = self._pg_query(sql, (to_status, int(admin_user_id), now, int(withdrawal_id), from_status), fetchone=True)
            if withdrawal and to_status == "rejected":
                self._pg_query(f"UPDATE {self.user_table} SET balance = balance + %s WHERE user_id = %s", (int(withdrawal.get("amount") or 0), int(withdrawal["user_id"])))
            return withdrawal
        if not self.client:
            return None
        query = self.client.table(self.withdrawal_table).update(
            {"status": to_status, "admin_user_id": int(admin_user_id), "updated_at": now}
        ).eq("id", int(withdrawal_id)).eq("status", from_status)
        response = self._execute(query, f"mark withdrawal {to_status}")
        withdrawal = response.data[0] if response and response.data else None
        if withdrawal and to_status == "rejected":
            self._execute(
                self.client.rpc(
                    "vip_credit_balance",
                    {"p_user_id": int(withdrawal["user_id"]), "p_amount": int(withdrawal.get("amount") or 0)},
                ),
                "refund rejected withdrawal",
            )
        return withdrawal

    def latest_payment_for_user(self, user_id):
        if self.use_postgres:
            sql = f"SELECT * FROM {self.table} WHERE user_id = %s ORDER BY id DESC LIMIT 1"
            return self._pg_query(sql, (int(user_id),), fetchone=True)
        if not self.client:
            return None
        query = (
            self.client.table(self.table)
            .select("*")
            .eq("user_id", user_id)
            .order("id", desc=True)
            .limit(1)
        )
        response = self._execute(query, "latest payment for user")
        return response.data[0] if response and response.data else None

