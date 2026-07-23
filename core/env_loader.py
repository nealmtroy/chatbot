"""
env_loader.py - Load .env terpusat buat telegram-chatbot.

Chatbot ada di sociabuzz-pay/telegram-chatbot/, tapi env disatukan di
sociabuzz-pay/.env (parent). Jadi load_dotenv() harus baca parent dulu,
baru local (kalau ada) sebagai override.

Pemakaian: ganti `from dotenv import load_dotenv; load_dotenv()`
menjadi `from env_loader import load_env; load_env()`.
"""
import os
from dotenv import load_dotenv

def load_env():
    """Load root .env (chatbot/.env) dengan override=True agar perubahan di .env langsung berlaku."""
    core_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(core_dir)
    
    root_env = os.path.join(root_dir, ".env")
    if os.path.exists(root_env):
        load_dotenv(root_env, override=True)
        
    parent_env = os.path.join(os.path.dirname(root_dir), ".env")
    if os.path.exists(parent_env):
        load_dotenv(parent_env, override=False)
        
    return root_env
