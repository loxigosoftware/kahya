"""Telegram bot — the owner's front door, and Kâhya's voice (v2, REDESIGN §4).

Routing:
  1. /<slug> ...     → direct message to that amele (Kahya is skipped)
  2. /<slug>         → chat mode with that amele (until /iptal)
  3. /amele /help /iptal /start
  4. approval words ("evet", "hayır", "iptal" in the selected language)
     → matched to the latest pending action and forwarded to its amele
  5. anything else   → Kâhya (orchestrator) with the compact amele index

Removed (panel's job now): /add-agent, /edit-agent, /delete-agent,
/add-job, /jobs, /done, /settings.

Pure stdlib HTTP long-polling; no bot framework needed.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .amele_runner import AmeleError, run_agent
from .config import Config
from .db import KahyaDB
from .i18n import I18n

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")

# approval words (selected language) — matched ONLY as a full message
APPROVE_WORDS = ("evet", "e", "yes", "onay", "tamam", "gönder", "kaydet")
REJECT_WORDS = ("hayır", "h", "no", "yok", "istemiyorum")
CANCEL_WORDS = ("iptal", "cancel", "vazgeç")

# ------------------------------------------------ telegram plumbing


class TG:
    def __init__(self, token: str):
        self.token = token
        base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
        self.base = f"{base}/bot{token}"

    def _call(self, method: str, data: dict) -> Any:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(f"{self.base}/{method}", data=body)
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send(self, chat_id, text: str, html: bool = True) -> bool:
        """Send a message.

        html=True (default): text is trusted HTML (i18n templates use
        <b>/<i> tags) and is sent with parse_mode=HTML. Callers passing
        user/LLM-generated text through i18n placeholders are safe —
        I18n.t() HTML-escapes every placeholder value. Plain free text
        (LLM answers) should be sent with html=False.
        """
        data = {"chat_id": chat_id, "text": text,
                "disable_web_page_preview": "true"}
        if html:
            data["parse_mode"] = "HTML"
        res = self._call("sendMessage", data)
        return bool(res.get("ok"))

    def set_commands(self, commands: list[tuple[str, str]]) -> bool:
        """Register the bot's /-menu with Telegram (shown on the client)."""
        payload = json.dumps([{"command": c, "description": d}
                              for c, d in commands])
        res = self._call("setMyCommands", {"commands": payload})
        return bool(res.get("ok"))

    def get_updates(self, offset: int) -> list[dict]:
        res = self._call("getUpdates", {
            "offset": offset, "timeout": 25, "allowed_updates": '["message"]'})
        return res.get("result", []) if res.get("ok") else []


# ------------------------------------------------ bot logic


class Bot:
    def __init__(self, cfg: Config, db: KahyaDB):
        self.cfg = cfg
        self.db = db
        self.tg = TG(cfg.telegram_token)
        self.offset = 0
        self.i18n = I18n(cfg.dir / "lang", cfg.language)
        self.kahya_yaml = cfg.ameleler_dir / "kahya.yaml"
        self._amele_index_cache: Optional[list[dict]] = None
        self._registered_amele_count = -1

    # -- helpers -------------------------------------------------

    def _t(self, key: str, **kw) -> str:
        self.i18n.set_language(self.cfg.language)
        return self.i18n.t(key, **kw)

    def _refresh_tg(self) -> None:
        if self.tg.token != self.cfg.telegram_token:
            self.tg = TG(self.cfg.telegram_token)
            self._registered_amele_count = -1
            print(f"  [bot] token changed, reconnected")

    def _amele_index(self) -> list[dict]:
        """Kompakt amele index (REDESIGN §3.2) — amele CRUD'unda tazelenir."""
        if self._amele_index_cache is None:
            self._amele_index_cache = self.db.amele_index()
        return self._amele_index_cache

    def _ameleler(self) -> list[dict]:
        """Etkin ameleler (komut eşleştirme için)."""
        return [a for a in self.db.list_ameleler() if a.get("enabled", 1)]

    def _slug_by_command(self, low: str) -> Optional[str]:
        """'/<komut>' → amele slug. Tire/alt çizgi farkını yok sayar
        (REDESIGN §4.1: kayıtlı komut 'mail_amele', kullanıcı
        '/mail-amele' yazabilir)."""
        cmd = low.split(" ", 1)[0].lstrip("/").lower()
        if not cmd:
            return None
        norm = cmd.replace("_", "-")
        for a in self._ameleler():
            if a["slug"] == norm or a["slug"].replace("-", "_") == cmd:
                return a["slug"]
        return None

    def _register_commands(self) -> None:
        """Publish the /-menu: /amele, /help, /iptal + every enabled
        amele's own /<slug> (underscored — Telegram allows only a-z0-9_)."""
        ameleler = self._ameleler()
        n = len(ameleler)
        if n == self._registered_amele_count:
            return
        cmds: list[tuple[str, str]] = [
            ("amele", "Amele listesi"),
            ("help", "Komut listesi"),
            ("iptal", "Akışı/oturumu iptal"),
        ]
        for a in ameleler:
            cmds.append((a["slug"].replace("-", "_"), a["name"]))
        try:
            ok = self.tg.set_commands(cmds)
            self._registered_amele_count = n
            print(f"  [bot] commands registered ({n} ameles): {ok}")
        except Exception as e:
            print(f"  [bot] setMyCommands failed: {e}")

    # -- amele execution -------------------------------------------

    def _run(self, yaml_path, task: str, timeout_s: float = 180) -> str:
        """Run an amele and stringify its answer for the owner."""
        res = run_agent(self.cfg, yaml_path, task, timeout_s=timeout_s)
        if isinstance(res, dict):
            return json.dumps(res, ensure_ascii=False)
        return str(res) if res is not None else ""

    def _ask_kahya(self, message: str) -> str:
        """Orchestrator: Kahya with the compact amele index (REDESIGN §3.2)."""
        idx = self._amele_index()
        lines = [f"{a['id']} | {a['slug']} | {a['description']}" for a in idx]
        task = ("AMELE INDEX:\n" + "\n".join(lines)
                + f"\n\nOwner message:\n{message}")
        return self._run(self.kahya_yaml, task)

    def _ask_amele(self, slug: str, message: str) -> str:
        yaml_path = self.cfg.ameleler_dir / f"{slug}.yaml"
        if not yaml_path.exists():
            raise AmeleError(1, f"amele YAML'ı yok: {slug}")
        return self._run(yaml_path, message)

    # -- commands --------------------------------------------------

    def _cmd_help(self, chat_id: int) -> None:
        self.tg.send(chat_id, self._t("bot.help"))

    def _cmd_ameleler(self, chat_id: int) -> None:
        ameleler = self._ameleler()
        if not ameleler:
            self.tg.send(chat_id, self._t("bot.ameleler_none"))
            return
        lines = [self._t("bot.ameleler_header"), ""]
        for a in ameleler:
            n = self.db.count_records(a["id"])
            lines.append(f"• <b>{a['name']}</b> (<code>/{a['slug']}</code>) — "
                         f"{self._t('bot.ameleler_records', n=n)}")
        self.tg.send(chat_id, "\n".join(lines))

    def _cmd_session(self, chat_id: int, slug: str) -> None:
        """/<slug> (argümansız) → chat mode with that amele."""
        amele = self.db.get_amele_by_slug(slug)
        if not amele:
            self.tg.send(chat_id, self._t("bot.amele_not_found", slug=slug))
            return
        self.db.set_chat_state(chat_id, {"session_slug": slug})
        self.tg.send(chat_id, self._t("bot.session_started", name=amele["name"]))

    def _cmd_cancel(self, chat_id: int, notify_pending: bool = True) -> None:
        """Cancel the current flow/session (and a pending approval if any)."""
        self.db.set_chat_state(chat_id, {})
        if notify_pending:
            pa = self.db.latest_pending_action()
            if pa:
                self._forward_approval(chat_id, pa, "cancelled")
                return
        self.tg.send(chat_id, self._t("bot.cancel_ok"))

    # -- approval matching (REDESIGN §4.2) --------------------------

    def _forward_approval(self, chat_id: int, pa: dict, verdict: str) -> None:
        """Son bekleyen onayı sahibi ameleye iletir (Step 4; tam onay
        akışı Step 6'da pending_actions yönetimiyle derinleşir)."""
        amele = self.db.get_amele(pa["amele_id"])
        name = amele["name"] if amele else pa["amele_id"]
        yaml_path = self.cfg.ameleler_dir / f"{amele['slug']}.yaml" if amele else None
        if yaml_path and yaml_path.exists():
            try:
                task = (f"Owner's approval reply: {verdict.upper()}.\n"
                        f"Your pending action was:\n"
                        f"{json.dumps(pa['action'], ensure_ascii=False)}\n\n"
                        f"Continue accordingly (e.g. save the record, send "
                        f"the message, or drop it). Reply with a short "
                        f"confirmation.")
                self._run(yaml_path, task, timeout_s=120)
            except AmeleError as e:
                self.db.log("bot", {"event": "approval_forward_failed",
                                    "action_id": pa["id"], "error": str(e)})
        status = "approved" if verdict == "approved" else "cancelled"
        self.db.resolve_pending_action(pa["id"], status)
        self.db.log("bot", {"event": "approval_resolved",
                            "action_id": pa["id"], "status": status})
        msg = (self._t("bot.approval_forwarded", name=name) if verdict == "approved"
               else self._t("bot.approval_cancelled", name=name))
        self.tg.send(chat_id, msg)

    def _try_approval(self, chat_id: int, low: str, in_session: bool = False) -> bool:
        """Onay kelimesi + bekleyen onay varsa iletir. True = handled."""
        if low in APPROVE_WORDS:
            pa = self.db.latest_pending_action()
            if pa:
                self._forward_approval(chat_id, pa, "approved")
                return True
            return False  # bağlam Kahya'da/oturumda olabilir → o akışa düşsün
        if low in REJECT_WORDS:
            pa = self.db.latest_pending_action()
            if pa:
                self._forward_approval(chat_id, pa, "cancelled")
                return True
            if in_session:
                return False  # oturum amelesine gitsin — onay cevabı olabilir
            self.db.set_chat_state(chat_id, {})
            self.tg.send(chat_id, self._t("bot.cancel_ok"))
            return True
        if low in CANCEL_WORDS:
            self.db.set_chat_state(chat_id, {})
            pa = self.db.latest_pending_action()
            if pa:
                self._forward_approval(chat_id, pa, "cancelled")
            else:
                self.tg.send(chat_id, self._t("bot.cancel_ok"))
            return True
        return False

    # -- main dispatcher ----------------------------------------------

    def _handle_text(self, chat_id: int, text: str, state: dict) -> None:
        low = text.strip().lower()
        session_slug = state.get("session_slug")

        # chat mode: non-command messages go to the session amele
        if session_slug and not low.startswith("/"):
            if low in CANCEL_WORDS:
                self._cmd_cancel(chat_id)
                return
            if self._try_approval(chat_id, low, in_session=True):
                return
            try:
                answer = self._ask_amele(session_slug, text)
            except Exception as e:
                self.tg.send(chat_id, self._t("bot.understand_error",
                                              code=getattr(e, "exit_code", "?")))
                return
            self.tg.send(chat_id, answer, html=False)
            return
        if session_slug and low.startswith("/"):
            # komut oturumu keser mi? /iptal keser; diğerleri işlenir,
            # oturum ayrıca korunur.
            pass

        # ---- commands ----
        if low in ("/start", "start", "merhaba", "selam", "hi", "hello"):
            self.tg.send(chat_id, self._t("bot.start_welcome"))
            return
        if low in ("/help", "help", "yardım", "yardim"):
            self._cmd_help(chat_id)
            return
        if low in ("/amele", "/ameleler", "amele", "ameleler", "amelist"):
            self._cmd_ameleler(chat_id)
            return
        if low in ("/iptal", "/cancel", "iptal", "cancel"):
            self._cmd_cancel(chat_id)
            return

        # direct amele: /<slug> [argüman]
        if low.startswith("/"):
            slug = self._slug_by_command(low)
            if slug:
                rest = low.split(" ", 1)[1].strip() if " " in low else ""
                if rest:
                    try:
                        answer = self._ask_amele(slug, text.split(" ", 1)[1].strip())
                    except AmeleError as e:
                        self.tg.send(chat_id, self._t("bot.understand_error",
                                                      code=e.exit_code))
                        return
                    self.tg.send(chat_id, answer, html=False)
                else:
                    self._cmd_session(chat_id, slug)
                return
            self.tg.send(chat_id, self._t("bot.unknown_command"))
            return

        # ---- approval words (full-message only) ----
        if self._try_approval(chat_id, low):
            return

        # ---- natural language → Kahya (orchestrator) ----
        try:
            answer = self._ask_kahya(text)
        except AmeleError as e:
            self.tg.send(chat_id, self._t("bot.understand_error", code=e.exit_code))
            return
        if answer:
            self.tg.send(chat_id, answer, html=False)

    # -- main loop --------------------------------------------------

    def run_forever(self) -> None:
        print(f"[kahya bot] started — {self.tg.base[:50]}…")
        self._register_commands()
        while True:
            try:
                self._refresh_tg()
                self._register_commands()  # amele eklendi/silindi → menü tazelenir
                updates = self.tg.get_updates(self.offset)
                for u in updates:
                    self.offset = u["update_id"] + 1
                    msg = u.get("message")
                    if not msg or "text" not in msg:
                        continue
                    chat_id = msg["chat"]["id"]
                    if str(chat_id) != str(self.cfg.telegram_chat_id):
                        self.tg.send(chat_id, self._t("bot.unknown_user"))
                        continue
                    state = self.db.get_chat_state(chat_id)
                    print(f"  [msg] {msg['text'][:80]!r}")
                    self._handle_text(chat_id, msg["text"], state)
            except Exception as e:
                print(f"  [ERROR] polling: {e}")
                time.sleep(5)


if __name__ == "__main__":
    db = KahyaDB(Config().db_path)
    cfg = Config(db)
    try:
        Bot(cfg, db).run_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        db.close()
