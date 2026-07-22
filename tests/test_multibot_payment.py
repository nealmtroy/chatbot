"""
Unit tests for Multi-Bot Payment System & Per-Bot Package Isolation.
"""
import os
import sys
import pytest
from unittest.mock import MagicMock

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from vip_bot import config as vip_config, db_store as supabase_store, messages as vip_messages


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test_hash")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test_token")
    monkeypatch.setenv("SOCIABUZZ_USERNAME", "testuser")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test_key")


def test_payment_bot_db_operations(monkeypatch):
    cfg = vip_config.load_config()
    store = supabase_store.PaymentStore(cfg)

    # Mock client and table calls
    fake_client = MagicMock()
    store.client = fake_client

    # Test upsert_payment_bot
    fake_execute = MagicMock()
    fake_execute.execute.return_value.data = [
        {"id": 1, "bot_token": "123:abc", "bot_username": "bot1_payment", "bot_name": "Bot 1", "active": True}
    ]
    fake_client.table.return_value.upsert.return_value = fake_execute

    bot = store.upsert_payment_bot("123:abc", "Bot 1", "bot1_payment")
    assert bot is not None
    assert bot.get("bot_token") == "123:abc"


def test_package_filtering_by_bot_username():
    cfg = vip_config.load_config()
    store = supabase_store.PaymentStore(cfg)

    fake_client = MagicMock()
    store.client = fake_client

    fake_execute = MagicMock()
    fake_execute.execute.return_value.data = [
        {"code": "pkg_b1", "name": "Package Bot 1", "amount": 2000, "bot_username": "bot1_payment", "active": True}
    ]
    fake_client.table.return_value.select.return_value.eq.return_value.or_.return_value.order.return_value.order.return_value = fake_execute

    pkgs = store.list_packages(bot_username="bot1_payment")
    assert len(pkgs) == 1
    assert pkgs[0]["code"] == "pkg_b1"
