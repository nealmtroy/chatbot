"""
Unit tests for 100% ported SociaBuzz Pay modules in telegram-chatbot.
"""
import os
import sys
import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from vip_bot import config as vip_config, db_store as supabase_store, helpers as vip_helpers, messages as vip_messages, loops as vip_loops
from vip_bot.handlers import admin, user


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test_hash")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test_token")
    monkeypatch.setenv("SOCIABUZZ_USERNAME", "testuser")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test_key")


def test_vip_config_loading():
    config = vip_config.load_config()
    assert isinstance(config.payment_amount, int)
    assert isinstance(config.poll_batch_size, int)
    assert config.supabase_table == "vip_payments"


def test_vip_helpers_referral_math():
    code1 = vip_helpers.format_referral_code(123456)
    code2 = vip_helpers.format_referral_code(654321)
    assert len(code1) >= 5
    assert len(code2) >= 5
    assert code1 != code2

    payload_parsed = vip_helpers.parse_referral_payload("ref_ABC12")
    assert payload_parsed == "ABC12"


def test_vip_messages_qris_caption():
    pkg = {"name": "VIP Unlimited", "amount": 50000}
    caption = vip_messages.qris_caption(pkg, "INV-123", 50000, "50001", "2026-07-21T23:00:00Z")
    assert "VIP Unlimited" in caption
    assert "INV-123" in caption
    assert "Aturan pembayaran" in caption


def test_supabase_store_instantiation():
    cfg = vip_config.load_config()
    store = supabase_store.PaymentStore(cfg)
    assert store.table == "vip_payments"
    assert store.package_table == "vip_packages"
