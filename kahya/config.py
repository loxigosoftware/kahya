"""Configuration — environment variables + optional .env file.

Everything Kahya needs comes from the environment (or .env next to the
repo root). No other config surface exists on purpose: deploy = set env,
run three processes.

Vars (see .env.example):
  KAHYA_DIR            repo root (default: parent of this package)
  KAHYA_DB             SQLite path (default: <KAHYA_DIR>/data/kahya.db)
  KAHYA_WEB_PORT       web panel port (default: 8080)
  KAHYA_TIMEZONE       IANA zone, e.g. Europe/Istanbul (default: local)

  AMELE_BIN            path to the amele binary (default: <KAHYA_DIR>/bin/amele)
  AMELE_MODEL          model id, e.g. "qwen3-vl:8b" or "gpt-4.1-mini"
  PROVIDER_TYPE        "openai" (default) or "anthropic"
  BASE_URL             OpenAI-compatible endpoint, e.g.
                       http://192.168.1.50:11434/v1  (local Ollama)
                       https://api.openai.com/v1
  API_KEY              key for BASE_URL (empty for local Ollama)

  TELEGRAM_BOT_TOKEN   bot token from @BotFather
  TELEGRAM_CHAT_ID     chat to deliver to (owner)
"""
from __future__ import annotations

import os
from pathlib import Path


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
        # do not override real env vars
        if key and key not in os.environ:
            os.environ[key] = value


class Config:
    def __init__(self) -> None:
        self.dir = Path(os.environ.get("KAHYA_DIR", str(Path(__file__).resolve().parent.parent)))
        _load_dotenv(self.dir / ".env")
        _load_dotenv(self.dir / "secrets.env")

        self.db_path = Path(os.environ.get("KAHYA_DB", str(self.dir / "data" / "kahya.db")))
        self.web_port = int(os.environ.get("KAHYA_WEB_PORT", "8080"))
        self.timezone = os.environ.get("KAHYA_TIMEZONE") or None

        self.amele_bin = Path(os.environ.get("AMELE_BIN", str(self.dir / "bin" / "amele")))
        self.model = os.environ.get("AMELE_MODEL", "")
        self.provider_type = os.environ.get("PROVIDER_TYPE", "openai")
        self.base_url = os.environ.get("BASE_URL", "")
        self.api_key = os.environ.get("API_KEY", "")

        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        self.agents_dir = self.dir / "agents"
        self.tools_dir = self.dir / "tools"

    def check(self) -> list[str]:
        """Return a list of configuration problems (empty = ready)."""
        problems = []
        if not self.model:
            problems.append("AMELE_MODEL is not set")
        if not self.base_url:
            problems.append("BASE_URL is not set (e.g. http://host:11434/v1 for Ollama)")
        if not self.amele_bin.exists():
            problems.append(f"amele binary not found at {self.amele_bin} — run scripts/install-amele.sh")
        if not self.telegram_token:
            problems.append("TELEGRAM_BOT_TOKEN is not set")
        if not self.telegram_chat_id:
            problems.append("TELEGRAM_CHAT_ID is not set")
        return problems


def get_config() -> Config:
    return Config()
