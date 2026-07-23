"""
Core package for VIP Automation System.
Contains database persistence, AI engine, payment clients, and utilities.
"""

from .env_loader import load_env
from . import db
from . import clients

__all__ = [
    "load_env",
    "db",
    "clients",
]
