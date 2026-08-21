"""amele runner — spawn an amele, get its schema-validated output.

Kahya never talks to the LLM directly; every AI step goes through an amele
(ameles/*.yaml). This module is the one place that shells out.

Model atama : her amele kendi modelini kullanır —
ameles tablosundaki model_kind/model_name/model_cfg'den çözülür.
Sistem ayarındaki LLM (cfg.model) YALNIZ Kahya içindir; diğer ameles
kendi model ayarlarıyla çağrılır.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from .config import Config

# panel language code → full name for amele prompts ("in Turkish")
_LANG_NAMES = {"tr": "Turkish", "en": "English", "de": "German",
               "fr": "French", "es": "Spanish", "it": "Italian"}

_ENV_REF = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def resolve_env_ref(value) -> Optional[str]:
    """'${VAR}' → os.environ'dan çöz; düz metin ise aynen döner."""
    if value is None:
        return None
    s = str(value).strip()
    m = _ENV_REF.match(s)
    if m:
        return os.environ.get(m.group(1))
    return s


class AmeleError(Exception):
    def __init__(self, exit_code: int, stderr: str):
        self.exit_code = exit_code
        super().__init__(f"amele exited {exit_code}: {stderr[:300]}")


def _amele_model_env(cfg: Config, slug: str) -> dict:
    """Amele başına model env'leri — DB'deki model atamasından .

    - local: model_name + (model_cfg.base_url varsa o, yoksa cfg.base_url —
      Ollama gibi yerel endpoint ortak kullanılır).
    - api: model_name + model_cfg.base_url zorunlu; api_key_ref ${VAR} ile
      çözülür, asla düz metin saklanmaz.
    DB kaydı yoksa (ör. test YAML'leri) sistem ayarına düşer — v1 davranışı.
    """
    env = {
        "AMELE_MODEL": cfg.model,
        "PROVIDER_TYPE": cfg.provider_type,
        "BASE_URL": cfg.base_url,
        "API_KEY": cfg.api_key,
    }
    amele = cfg._db.get_amele_by_slug(slug)
    if not amele:
        return env
    model_name = amele.get("model_name") or cfg.model
    cfg_map: dict = {}
    if amele.get("model_cfg"):
        try:
            cfg_map = json.loads(amele["model_cfg"]) or {}
        except (TypeError, json.JSONDecodeError):
            cfg_map = {}
    kind = amele.get("model_kind", "local")
    env["AMELE_MODEL"] = model_name
    if kind == "api":
        base = cfg_map.get("base_url")
        if not base:
            raise AmeleError(9, f"amele '{slug}' api model seçili ama "
                                 f"model_cfg.base_url yok (panel → Ameles)")
        env["BASE_URL"] = str(base)
        key = resolve_env_ref(cfg_map.get("api_key_ref"))
        if key is not None:
            env["API_KEY"] = key
    else:  # local
        base = cfg_map.get("base_url")
        if base:
            env["BASE_URL"] = str(base)
        key = resolve_env_ref(cfg_map.get("api_key_ref"))
        if key is not None:
            env["API_KEY"] = key
    return env


def run_amele(cfg: Config, yaml_path: Path, task: str,
              timeout_s: float = 180) -> Any:
    """Run `amele run <yaml> <task>`.

    Returns the parsed stdout payload (JSON when the amele declares an
    output.schema, otherwise the raw text). Raises AmeleError on any
    non-zero exit.
    """
    slug = yaml_path.stem
    env = {
        **os.environ,
        **_amele_model_env(cfg, slug),
        "TELEGRAM_BOT_TOKEN": cfg.telegram_token,
        "TELEGRAM_CHAT_ID": cfg.telegram_chat_id,
        "KAHYA_DB": str(cfg.db_path),
        "KAHYA_LANGUAGE": cfg.language,
        "KAHYA_LANGUAGE_NAME": _LANG_NAMES.get(cfg.language, "English"),
    }
    if cfg.smithery_api_key:
        env["SMITHERY_API_KEY"] = cfg.smithery_api_key  # ${SMITHERY_API_KEY} headers
    amele = cfg._db.get_amele_by_slug(slug)
    if amele:
        env["KAHYA_AMELE_ID"] = str(amele["id"])
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


def amele_yaml(cfg: Config, slug: str) -> Optional[Path]:
    """Path to an amele's YAML by slug (ameles/<slug>.yaml)."""
    p = cfg.ameles_dir / f"{slug}.yaml"
    return p if p.exists() else None
