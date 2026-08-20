"""Configuration — three layers, in order:

1. settings table in SQLite (changed from the web panel, takes effect
   immediately — the bot re-reads on every message, the scheduler on
   every tick)
2. environment variables / .env (first install, deployment, secrets
   fallback — KAHYA_ADMIN_PASSWORD lives here so a forgotten panel
   password can always be bypassed)
3. built-in defaults

Every value the panel can edit is a property here; callers never read
env or the DB directly.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

DEFAULTS = {
    "web_port": "8080",
    "provider_type": "openai",
    "language": "en",
    "admin_user": "admin",
    "remind_before_days": "2",
}

# First-run credentials. The panel warns until the password is changed;
# a forgotten password is always recoverable via KAHYA_ADMIN_PASSWORD.
DEFAULT_ADMIN_PASSWORD = "kahya123"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 with a random salt — stdlib only."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, hex_digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000)
        return hmac.compare_digest(dk.hex(), hex_digest)
    except (ValueError, TypeError):
        return False


class Config:
    def __init__(self, db=None, overrides: Optional[dict] = None) -> None:
        from .db import KahyaDB  # local import: avoid cycle

        self.dir = Path(os.environ.get("KAHYA_DIR", str(Path(__file__).resolve().parent.parent)))
        _load_dotenv(self.dir / ".env")
        _load_dotenv(self.dir / "secrets.env")
        self.db_path = Path(os.environ.get("KAHYA_DB", str(self.dir / "data" / "kahya.db")))
        self._db = db if db is not None else KahyaDB(self.db_path)
        self._overrides = overrides or {}

        self.amele_bin = Path(os.environ.get("AMELE_BIN", str(self.dir / "bin" / "amele")))
        self.ameleler_dir = self.dir / "ameleler"
        self.tools_dir = self.dir / "tools"

    # -- the three-layer lookup -------------------------------------

    def _get(self, key: str, env: str, default: str = "") -> str:
        if key in self._overrides and self._overrides[key] not in (None, ""):
            return str(self._overrides[key])
        v = self._db.get_setting(key)
        if v is not None:
            return v
        if env in os.environ and os.environ.get(env, "") != "":
            return os.environ[env]
        return default

    # -- panel-editable settings -------------------------------------

    @property
    def telegram_token(self) -> str:
        return self._get("telegram_token", "TELEGRAM_BOT_TOKEN")

    @property
    def telegram_chat_id(self) -> str:
        return self._get("telegram_chat_id", "TELEGRAM_CHAT_ID")

    @property
    def smithery_api_key(self) -> str:
        return self._get("smithery_api_key", "SMITHERY_API_KEY")

    @property
    def mcp_liability_accepted(self) -> bool:
        v = self._get("mcp_liability_accepted", "", "")
        return str(v or "").lower() in ("1", "true", "yes")

    @property
    def web_port(self) -> int:
        return int(self._get("web_port", "KAHYA_WEB_PORT", DEFAULTS["web_port"]))

    @property
    def timezone(self) -> Optional[str]:
        v = self._get("timezone", "KAHYA_TIMEZONE")
        return v or None

    @property
    def model(self) -> str:
        return self._get("model", "AMELE_MODEL")

    @property
    def provider_type(self) -> str:
        return self._get("provider_type", "PROVIDER_TYPE", DEFAULTS["provider_type"])

    @property
    def base_url(self) -> str:
        return self._get("base_url", "BASE_URL")

    @property
    def api_key(self) -> str:
        return self._get("api_key", "API_KEY")

    @property
    def language(self) -> str:
        return self._get("language", "KAHYA_LANGUAGE", DEFAULTS["language"])

    @property
    def quiet_start(self) -> str:
        """Quiet hours: no reminders between quiet_start and quiet_end."""
        return self._get("quiet_start", "QUIET_START", "22:00")

    @property
    def quiet_end(self) -> str:
        return self._get("quiet_end", "QUIET_END", "08:00")

    @property
    def admin_user(self) -> str:
        return self._get("admin_user", "KAHYA_ADMIN_USER", DEFAULTS["admin_user"])

    @property
    def admin_password_hash(self) -> str:
        return self._get("admin_password_hash", "")

    def env_admin_password(self) -> Optional[str]:
        """Env fallback for a forgotten panel password (always wins)."""
        return os.environ.get("KAHYA_ADMIN_PASSWORD") or None

    # -- helpers -----------------------------------------------------

    def check(self) -> list[str]:
        """Return a list of configuration problems (empty = ready)."""
        problems = []
        if not self.model:
            problems.append("LLM model not set (Settings → LLM)")
        if not self.base_url:
            problems.append("LLM endpoint not set (Settings → LLM)")
        if not self.amele_bin.exists():
            problems.append(f"amele binary not found: {self.amele_bin} — run install.py")
        if not self.telegram_token:
            problems.append("Telegram bot token not set (Settings → Telegram)")
        if not self.telegram_chat_id:
            problems.append("Telegram chat id not set (Settings → Telegram)")
        return problems

    def save_settings(self, updates: dict) -> list[str]:
        """Persist panel settings; returns validation problems (if any)."""
        problems = []
        for key, value in updates.items():
            if value is None:
                continue
            if key == "web_port":
                try:
                    p = int(value)
                    if not (1 <= p <= 65535):
                        raise ValueError
                except ValueError:
                    problems.append("web_port: must be 1-65535")
                    continue
            if key in ("api_key", "telegram_token"):
                value = value.strip()
            if key == "admin_password" and value:
                self._db.set_setting("admin_password_hash", hash_password(value))
                continue
            self._db.set_setting(key, str(value).strip())
        return problems

    def all_editable(self) -> dict:
        """Snapshot of every panel-editable setting (no secrets in clear)."""
        return {
            "telegram_token": self.telegram_token,
            "telegram_token_set": bool(self.telegram_token),
            "telegram_chat_id": self.telegram_chat_id,
            "smithery_api_key_set": bool(self.smithery_api_key),
            "mcp_liability_accepted": self.mcp_liability_accepted,
            "web_port": self.web_port,
            "timezone": self.timezone,
            "model": self.model,
            "provider_type": self.provider_type,
            "base_url": self.base_url,
            "api_key_set": bool(self.api_key),
            "language": self.language,
            "quiet_start": self.quiet_start,
            "quiet_end": self.quiet_end,
            "admin_user": self.admin_user,
            "using_default_password": self.admin_password_hash == ""
                                       and self.env_admin_password() is None,
        }
