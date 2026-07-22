"""
Unit & functional tests for payment_handler integration in telegram-chatbot.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from core import db
from vip_bot import helpers as vip_helpers


@pytest.fixture(autouse=True)
def init_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_payment.db")
    monkeypatch.setattr(db, "DB_FILE", db_file)
    if hasattr(db._local, "conn"):
        db._local.conn = None
    db.init_db()
    db.add_account(name="TestAlya", session_file="test_session", api_id=123, api_hash="hash")
    yield
    if getattr(db._local, "conn", None):
        db._local.conn.close()
        db._local.conn = None


def test_db_payment_operations():
    acc = db.list_accounts()[0]
    u = db.get_or_create_user(acc["id"], 11223344, "Budi", "budi123")

    pay_id = db.add_payment(
        account_id=acc["id"],
        user_id=u["id"],
        tg_user_id=11223344,
        amount=50000,
        package_code="vip",
        socia_inv_id="INV12345",
        qris_chat_id="11223344",
        qris_message_id="999",
    )
    assert pay_id > 0

    p = db.get_payment(pay_id)
    assert p is not None
    assert p["amount"] == 50000
    assert p["status"] == "pending"
    assert p["socia_inv_id"] == "INV12345"

    active = db.active_payment_for_user(11223344)
    assert active is not None
    assert active["id"] == pay_id

    pending = db.pending_payments()
    assert len(pending) == 1
    assert pending[0]["id"] == pay_id

    db.update_payment(pay_id, status="paid", invite_link="https://t.me/+xyz")
    p_updated = db.get_payment(pay_id)
    assert p_updated["status"] == "paid"
    assert p_updated["invite_link"] == "https://t.me/+xyz"

    active_after = db.active_payment_for_user(11223344)
    assert active_after is None
