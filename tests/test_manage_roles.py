"""
Unit tests for RBAC (Owner vs Admin), Packages CRUD, and Settings in telegram-chatbot.
"""
import os
import sys
import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from core import db
from handlers import manage_bot


@pytest.fixture(autouse=True)
def init_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_roles.db")
    monkeypatch.setattr(db, "DB_FILE", db_file)
    if hasattr(db._local, "conn"):
        db._local.conn = None
    db.init_db()

    monkeypatch.setattr(manage_bot, "OWNER_IDS", {111111})
    monkeypatch.setattr(manage_bot, "ADMIN_IDS", {111111, 222222})
    yield


def test_is_owner_and_is_admin():
    assert manage_bot.is_owner(111111) is True
    assert manage_bot.is_owner(222222) is False
    assert manage_bot.is_owner(999999) is False

    assert manage_bot.is_admin(111111) is True
    assert manage_bot.is_admin(222222) is True
    assert manage_bot.is_admin(999999) is False


def test_packages_crud():
    # 1. Add package
    code = db.add_package(code="vip_a", name="VIP Group A", vip_chat_id="-100123", amount=75000)
    assert code == "vip_a"

    # 2. Get package
    pkg = db.get_package("vip_a")
    assert pkg is not None
    assert pkg["name"] == "VIP Group A"
    assert pkg["amount"] == 75000
    assert pkg["vip_chat_id"] == "-100123"

    # 3. List packages
    pkgs = db.list_packages(active_only=True)
    assert len(pkgs) == 1
    assert pkgs[0]["code"] == "vip_a"

    # 4. Delete package
    db.delete_package("vip_a")
    pkgs_active = db.list_packages(active_only=True)
    assert len(pkgs_active) == 0


def test_settings_kv():
    db.set_setting("log_chat_id", "-100999888777")
    val = db.get_setting("log_chat_id")
    assert val == "-100999888777"


def test_set_account_package():
    acc_id = db.add_account(name="TestBot", session_file="test_session", api_id=1, api_hash="hash")
    db.set_account_package(acc_id, "vip_a")

    acc = db.get_account(acc_id)
    assert acc["package_code"] == "vip_a"
