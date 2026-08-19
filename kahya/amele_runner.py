"""amele runner — spawn an amele agent, get its schema-validated output.

Kahya never talks to the LLM directly; every AI step goes through an amele
agent (agents/*.yaml). This module is the one place that shells out.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from .config import Config

# panel language code → full name for agent prompts ("in Turkish")
_LANG_NAMES = {"tr": "Turkish", "en": "English", "de": "German",
               "fr": "French", "es": "Spanish", "it": "Italian"}


class AmeleError(Exception):
    def __init__(self, exit_code: int, stderr: str):
        self.exit_code = exit_code
        super().__init__(f"amele exited {exit_code}: {stderr[:300]}")


def run_agent(cfg: Config, yaml_path: Path, task: str,
              timeout_s: float = 180) -> Any:
    """Run `amele run <yaml> <task>`.

    Returns the parsed stdout payload (JSON when the agent declares an
    output.schema, otherwise the raw text). Raises AmeleError on any
    non-zero exit.
    """
    env = {
        **os.environ,
        "AMELE_MODEL": cfg.model,
        "PROVIDER_TYPE": cfg.provider_type,
        "BASE_URL": cfg.base_url,
        "API_KEY": cfg.api_key,
        "TELEGRAM_BOT_TOKEN": cfg.telegram_token,
        "TELEGRAM_CHAT_ID": cfg.telegram_chat_id,
        "KAHYA_DB": str(cfg.db_path),
        "KAHYA_LANGUAGE": cfg.language,
        "KAHYA_LANGUAGE_NAME": _LANG_NAMES.get(cfg.language, "English"),
    }
    cmd = [str(cfg.amele_bin), "run", str(yaml_path), task]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s, env=env,
    )
    if proc.returncode != 0:
        raise AmeleError(proc.returncode, proc.stderr)

    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def agent_yaml(cfg: Config, slug: str) -> Optional[Path]:
    """Path to an agent's YAML by slug (agents/<slug>.yaml)."""
    p = cfg.agents_dir / f"{slug}.yaml"
    return p if p.exists() else None
