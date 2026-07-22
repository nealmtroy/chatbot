"""
Unit tests for Referral Engine and Supabase Client integration.
"""
import os
import sys
import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from core import db
from vip_bot import helpers as vip_helpers


@pytest.fixture(autouse=True)
def init_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_ref.db")
    monkeypatch.setattr(db, "DB_FILE", db_file)
    if hasattr(db._local, "conn"):
        db._local.conn = None
    db.init_db()
    db.add_account(name="TestAccount", session_file="test_session", api_id=123, api_hash="hash")
    yield


def test_referral_code_formatting():
    code1 = vip_helpers.format_referral_code(123456)
    code2 = vip_helpers.format_referral_code(987654)
    assert len(code1) >= 5
    assert len(code2) >= 5
    assert code1 != code2


def test_referral_payload_parsing():
    payload = vip_helpers.parse_referral_payload("ref_ABC12")
    assert payload == "ABC12"
