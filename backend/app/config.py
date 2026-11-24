import os
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/inboxpilot.db")
APP = os.getenv("APP_BASE_URL", "http://localhost:3000").rstrip("/")
API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
PRODUCTION = os.getenv("APP_ENV") == "production"
DEMO = os.getenv("DEMO_MODE", "true").lower() == "true"
MODEL = os.getenv("OPENAI_MODEL", "")
LLM = os.getenv("LLM_PROVIDER", "mock")
GOOGLE_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT = os.getenv("GOOGLE_REDIRECT_URI") or f"{API}/api/v1/auth/google/callback"
TOKEN_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
GOOGLE_ENABLED = bool(GOOGLE_ID and GOOGLE_SECRET and TOKEN_KEY)
MAX_BODY = min(24000, max(1000, int(os.getenv("MAX_EMAIL_CHARS", "12000"))))
THREAD_LIMIT = min(12, max(1, int(os.getenv("MAX_THREAD_MESSAGES", "8"))))
MAX_BATCH = min(10, max(1, int(os.getenv("MAX_BATCH_SIZE", "10"))))
TIMEOUT = min(30, max(1, float(os.getenv("LLM_TIMEOUT_SECONDS", "12"))))
ORIGINS = os.getenv("CORS_ORIGINS", APP).split(",")


def validate():
    if LLM not in {"mock", "openai"} or (
        LLM == "openai" and not (MODEL and os.getenv("OPENAI_API_KEY"))
    ):
        raise RuntimeError("Configure LLM_PROVIDER and its required credentials")
    if any((GOOGLE_ID, GOOGLE_SECRET, TOKEN_KEY)) and not GOOGLE_ENABLED:
        raise RuntimeError("Gmail requires client ID, client secret and token encryption key")
    if TOKEN_KEY:
        Fernet(TOKEN_KEY.encode())
    if (PRODUCTION or GOOGLE_ENABLED) and (
        len(SESSION_SECRET) < 32 or SESSION_SECRET.startswith("replace")
    ):
        raise RuntimeError("Set a strong SESSION_SECRET")
    if PRODUCTION and not all(url.startswith("https://") for url in (APP, API, *ORIGINS)):
        raise RuntimeError("Production requires HTTPS origins")
