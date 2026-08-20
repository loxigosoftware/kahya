"""Telegram bot — the owner's front door, and Kâhya's voice.

Three kinds of input:
  1. admin commands  — /agents, /add-agent (wizard), /edit-agent,
                       /delete-agent, /jobs, /add-job, /done, /settings,
                       /help, /cancel — everything the panel does, from
                       Telegram
  2. natural language records — "3000 TL su faturası geldi" → amele
     extract agent → confirmation card → "evet" → saved
  3. natural language questions — "Kuduz aşısı ne zamandı?" → extract
     detects intent=question → the Kâhya orchestrator agent (agents/
     kahya.yaml) reads the store and answers

Pure stdlib HTTP long-polling; no bot framework needed.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Optional

from .amele_runner import AmeleError, run_agent
from .config import Config
from .db import KahyaDB
from .i18n import I18n

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")

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
        self.extract_yaml = cfg.ameleler_dir / "extract-amele.yaml"
        self.kahya_yaml = cfg.ameleler_dir / "kahya.yaml"
        self._amele_index_cache: Optional[list[dict]] = None

    def _amele_index(self) -> list[dict]:
        """Kompakt amele index (REDESIGN §3.2) — amele CRUD'unda tazelenir."""
        if self._amele_index_cache is None:
            self._amele_index_cache = self.db.amele_index()
        return self._amele_index_cache

    # -- helpers -------------------------------------------------

    def _t(self, key: str, **kw) -> str:
        self.i18n.set_language(self.cfg.language)
        return self.i18n.t(key, **kw)

    def _refresh_tg(self) -> None:
        if self.tg.token != self.cfg.telegram_token:
            self.tg = TG(self.cfg.telegram_token)
            self._register_commands()
            print(f"  [bot] token changed, reconnected")

    def _register_commands(self) -> None:
        """Publish the /-menu so Telegram clients show the commands.

        Telegram command names allow only a-z0-9_ (no dashes — a dash
        makes the whole setMyCommands call fail with 400).
        """
        try:
            ok = self.tg.set_commands([
                ("start", "Welcome & how to use"),
                ("help", "Command list"),
                ("settings", "Setup summary"),
                ("agents", "List agents"),
                ("add_agent", "New agent (wizard)"),
                ("edit_agent", "Edit an agent"),
                ("delete_agent", "Delete an agent"),
                ("jobs", "Open tasks"),
                ("add_job", "Add a task"),
                ("done", "Complete a task"),
                ("cancel", "Cancel the current flow"),
            ])
            print(f"  [bot] commands registered: {ok}")
        except Exception as e:
            print(f"  [bot] setMyCommands failed: {e}")

    def _known_agents(self) -> str:
        agents = self.db.list_agents()
        if not agents:
            return "KNOWN AGENTS: genel (general)"
        lines = [f"{a['slug']} — {a['name']}" for a in agents]
        return "KNOWN AGENTS:\n" + "\n".join(lines)

    def _extract(self, message: str, context: Optional[dict] = None) -> dict:
        task = message
        if context:
            prev = json.dumps(context, ensure_ascii=False)
            task = (f"Earlier extraction for this conversation:\n{prev}\n\n"
                    f"The owner just answered the follow-up question. "
                    f"Re-extract and finalize the record (fill the missing "
                    f"field, keep everything else):\n{message}")
        else:
            task = f"{self._known_agents()}\n\nOwner message:\n{message}"
        # The extract agent must resolve dates against the real current
        # date — without it the model guesses the year (seen live: "20
        # ağustos" became 2025-08-20 and the panel flagged it overdue).
        task = (f"TODAY is {date.today().isoformat()} (the real current "
                f"date — resolve every date against it, never guess a "
                f"year).\n\n{task}")
        # JSON doğrulama + yeniden deneme (REDESIGN §2.3): çıktı dict
        # değilse bir kez daha üretilir; yine bozuksa None döner —
        # bozuk yapı hiçbir koşulda DB'ye yazılmaz.
        res = None
        for attempt in (1, 2):
            res = run_agent(self.cfg, self.extract_yaml, task, timeout_s=120)
            if isinstance(res, dict):
                return res
            task = (f"{task}\n\nYour previous output was not a valid JSON "
                    f"object (got: {str(res)[:200]!r}). Reply with ONLY the "
                    f"JSON object this time.")
        self.db.log("bot", {"event": "extract_invalid", "attempts": 2,
                            "output": str(res)[:300]})
        return None

    def _ask_agent(self, slug: str | None, message: str) -> str:
        """Run the agent named by slug to answer a question.

        Generic by design — the extract agent decides WHICH agent answers
        ("finance" for market data, "health" for medical reminders, ...);
        the bot core knows nothing about domains. Falls back to the Kâhya
        orchestrator for general questions about the store.
        """
        yaml_path = self.kahya_yaml
        if slug and re.match(SLUG_RE, slug):
            p = self.cfg.ameleler_dir / f"{slug}.yaml"
            if p.exists():
                yaml_path = p
        res = run_agent(self.cfg, yaml_path, message, timeout_s=120)
        if isinstance(res, dict):
            return json.dumps(res, ensure_ascii=False)
        return str(res)

    def _save_extracted(self, chat_id: int, extracted: dict) -> bool:
        slug = extracted.get("agent_slug") or "genel"
        agent = self.db.get_agent_by_slug(slug)
        agent_id = agent["id"] if agent else None
        data = {
            "title": extracted.get("title") or "Görev",
            "kind": extracted.get("kind") or "task",
            "amount": extracted.get("amount"),
            "currency": extracted.get("currency"),
            "due_date": extracted.get("due_date"),
            "repeat_rule": extracted.get("repeat_rule") or "none",
            "repeat_detail": extracted.get("repeat_detail"),
            "remind_before_days": extracted.get("remind_before_days") or 2,
            "note": extracted.get("note"),
        }
        item_id = self.db.insert_item(data, agent_id=agent_id)
        self.db.log("bot", {"event": "item_created", "item_id": item_id,
                            "agent": slug, "title": data["title"]})
        agent_name = agent["name"] if agent else "genel"
        self.tg.send(chat_id,
                     self._t("bot.saved_ok", agent=agent_name) + "\n" +
                     self._t("bot.saved_remind",
                             days=data["remind_before_days"] or 2))
        return True

    def _fmt_money(self, item: dict) -> str:
        if item.get("amount") is None:
            return ""
        cur = item.get("currency") or ""
        amount = item["amount"]
        if isinstance(amount, float) and amount == int(amount):
            amount = int(amount)
        return f"{amount} {cur}".strip()

    def _item_line(self, item: dict) -> str:
        money = self._fmt_money(item)
        due = item.get("due_date") or "—"
        parts = [item["title"]]
        if money:
            parts.append(money)
        parts.append(due)
        if item.get("repeat_rule") != "none":
            parts.append(item["repeat_rule"])
        return " · ".join(parts)

    def _confirm_card(self, extracted: dict) -> str:
        money = ""
        if extracted.get("amount") is not None:
            cur = extracted.get("currency") or ""
            money = f" — {extracted['amount']} {cur}".rstrip()
        lines = [
            self._t("bot.confirm_title"),
            "",
            f"<b>{extracted.get('title', '?')}</b>{money}",
        ]
        if extracted.get("due_date"):
            lines.append(self._t("bot.confirm_date", date=extracted["due_date"]))
        if extracted.get("repeat_rule", "none") != "none":
            d = extracted.get("repeat_detail")
            detail = f" ({d})" if d else ""
            lines.append(self._t("bot.confirm_repeat",
                                 rule=extracted["repeat_rule"], detail=detail))
        if extracted.get("remind_before_days") not in (None, 0):
            lines.append(self._t("bot.confirm_remind",
                                 days=extracted["remind_before_days"]))
        if extracted.get("note"):
            lines.append(self._t("bot.confirm_note", note=extracted["note"]))
        lines += ["", self._t("bot.confirm_ask")]
        return "\n".join(lines)

    # -- admin commands --------------------------------------------

    def _cmd_help(self, chat_id: int) -> None:
        self.tg.send(chat_id, self._t("bot.help"))

    def _cmd_agents(self, chat_id: int) -> None:
        agents = self.db.list_agents()
        if not agents:
            self.tg.send(chat_id, self._t("bot.agents_none"))
            return
        lines = [self._t("bot.agents_header"), ""]
        for a in agents:
            n = self.db.count_records(a["id"], status="open")
            lines.append(f"• <b>{a['name']}</b> ({a['slug']}) — "
                         f"{self._t('bot.agents_open', n=n)}")
        self.tg.send(chat_id, "\n".join(lines))

    def _cmd_jobs(self, chat_id: int) -> None:
        items = self.db.list_items(status="open")
        if not items:
            self.tg.send(chat_id, self._t("bot.list_empty"))
            return
        today = date.today().isoformat()
        lines = [self._t("bot.list_header"), ""]
        for it in items:
            overdue = (it["due_date"] or "") < today
            mark = "🔴" if overdue else "🟢"
            lines.append(f"{mark} {self._item_line(it)}"
                         + (f" — {self._t('bot.list_overdue')}" if overdue else ""))
        self.tg.send(chat_id, "\n".join(lines))

    def _cmd_settings(self, chat_id: int) -> None:
        cfg = self.cfg
        tg_state = self._t("bot.settings_on") if cfg.telegram_token else \
            self._t("bot.settings_off")
        lines = [
            self._t("bot.settings_title"),
            "",
            self._t("bot.settings_line_model", model=cfg.model or "—"),
            self._t("bot.settings_line_llm", base_url=cfg.base_url or "—"),
            self._t("bot.settings_line_telegram", state=tg_state),
            self._t("bot.settings_line_web", host=self._public_host(), port=cfg.web_port),
            self._t("bot.settings_line_lang", lang=cfg.language),
        ]
        self.tg.send(chat_id, "\n".join(lines))

    def _public_host(self) -> str:
        h = os.environ.get("KAHYA_PUBLIC_HOST")
        if h:
            return h
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # route discovery only, no traffic
            return s.getsockname()[0]
        except OSError:
            return "localhost"

    # -- wizards (multi-step admin flows) ---------------------------

    def _wizard_add_name(self, chat_id: int) -> None:
        self.db.set_chat_state(chat_id, {"step": "add_name"})
        self.tg.send(chat_id, self._t("bot.wizard_agent_name"))

    def _wizard_add_slug(self, chat_id: int, name: str) -> None:
        self.db.set_chat_state(chat_id, {"step": "add_slug", "name": name})
        self.tg.send(chat_id, self._t("bot.wizard_agent_slug"))

    def _wizard_add_role(self, chat_id: int, slug: str, name: str) -> None:
        self.db.set_chat_state(chat_id, {"step": "add_role", "slug": slug, "name": name})
        self.tg.send(chat_id, self._t("bot.wizard_agent_role"))

    def _wizard_add_confirm(self, chat_id: int, slug: str, name: str, role: str) -> None:
        self.db.set_chat_state(chat_id, {
            "step": "add_confirm", "slug": slug, "name": name, "role": role})
        self.tg.send(chat_id, self._t("bot.wizard_agent_confirm",
                                      name=name, slug=slug, role=role))

    def _create_agent(self, chat_id: int, slug: str, name: str, role: str) -> None:
        yaml_path = self.cfg.ameleler_dir / f"{slug}.yaml"
        from .server import AGENT_TEMPLATE  # local import, shared template
        role_indented = "\n".join("  " + ln for ln in role.splitlines())
        yaml_path.write_text(
            AGENT_TEMPLATE.format(slug=slug, name=name, role_prompt=role_indented),
            encoding="utf-8")
        agent_id = self.db.create_agent(slug, name, role, str(yaml_path))
        self._amele_index_cache = None  # index tazelenir (REDESIGN §3.2)
        self.db.log("bot", {"event": "agent_created", "agent": slug})
        self.tg.send(chat_id, self._t("bot.wizard_agent_created",
                                      name=name, slug=slug))
        return agent_id

    def _delete_agent(self, chat_id: int, slug: str) -> None:
        agent = self.db.get_agent_by_slug(slug)
        if not agent:
            self.tg.send(chat_id, self._t("bot.wizard_delete_notfound", slug=slug))
            return
        n = self.db.count_records(agent["id"])
        self.db.set_chat_state(chat_id, {"step": "del_confirm", "slug": slug,
                                         "name": agent["name"]})
        self.tg.send(chat_id, self._t("bot.wizard_delete_confirm",
                                      name=agent["name"], slug=slug, n=n))

    def _confirm_delete(self, chat_id: int, slug: str) -> None:
        agent = self.db.get_agent_by_slug(slug)
        if agent:
            self.db.delete_amele(agent["id"])  # ON DELETE CASCADE → kayıtlar da silinir
            self._amele_index_cache = None  # index tazelenir (REDESIGN §3.2)
            yaml_path = self.cfg.ameleler_dir / f"{slug}.yaml"
            if yaml_path.exists():
                yaml_path.unlink()
            self.db.log("bot", {"event": "agent_deleted", "agent": slug})
            self.tg.send(chat_id, self._t("bot.wizard_delete_done", name=agent["name"]))
        else:
            self.tg.send(chat_id, self._t("bot.wizard_delete_notfound", slug=slug))

    def _start_edit(self, chat_id: int) -> None:
        self.db.set_chat_state(chat_id, {"step": "edit_slug"})
        self.tg.send(chat_id, self._t("bot.wizard_edit_which"))

    def _edit_field(self, chat_id: int, slug: str) -> None:
        agent = self.db.get_agent_by_slug(slug)
        if not agent:
            self.tg.send(chat_id, self._t("bot.wizard_delete_notfound", slug=slug))
            self.db.set_chat_state(chat_id, {})
            return
        self.db.set_chat_state(chat_id, {"step": "edit_field", "slug": slug,
                                         "name": agent["name"]})
        self.tg.send(chat_id, self._t("bot.wizard_edit_field", name=agent["name"]))

    def _apply_edit(self, chat_id: int, slug: str, field: str, value: str) -> None:
        agent = self.db.get_agent_by_slug(slug)
        if not agent:
            self.tg.send(chat_id, self._t("bot.wizard_delete_notfound", slug=slug))
            return
        name = value if field == "name" else agent["name"]
        role = value if field == "role" else agent["role_prompt"]
        self.db.update_amele(agent["id"], {"name": name, "description": role})
        yaml_path = self.cfg.ameleler_dir / f"{slug}.yaml"
        from .server import AGENT_TEMPLATE
        role_indented = "\n".join("  " + ln for ln in role.splitlines())
        yaml_path.write_text(
            AGENT_TEMPLATE.format(slug=slug, name=name, role_prompt=role_indented),
            encoding="utf-8")
        self.db.log("bot", {"event": "agent_edited", "agent": slug})
        self.tg.send(chat_id, self._t("bot.wizard_edit_done", name=name))

    # -- task completion ---------------------------------------------

    def _handle_paid(self, chat_id: int, state: dict) -> None:
        if state.get("step") == "pay_select":
            return
        today = date.today()
        due_now = [i for i in self.db.due_for_reminder(today)]
        if not due_now:
            self.tg.send(chat_id, self._t("bot.paid_none"))
            return
        if len(due_now) == 1:
            self._complete(chat_id, due_now[0]["id"])
            return
        lines = [self._t("bot.paid_which"), ""]
        for idx, it in enumerate(due_now, 1):
            lines.append(f"{idx}. {self._item_line(it)}")
        lines += ["", self._t("bot.paid_ask_number")]
        self.tg.send(chat_id, "\n".join(lines))
        self.db.set_chat_state(chat_id, {
            "step": "pay_select", "ids": [i["id"] for i in due_now]})

    def _complete(self, chat_id: int, item_id: int) -> None:
        item = self.db.get_item(item_id)
        if not item:
            self.tg.send(chat_id, self._t("bot.item_notfound"))
            return
        result = self.db.complete_item(item_id)
        self.db.log("bot", {"event": "item_completed", "item_id": item_id})
        if result and result.get("rolled"):
            self.tg.send(chat_id, self._t("bot.paid_rolled",
                                          title=item["title"],
                                          date=result["due_date"]))
        else:
            self.tg.send(chat_id, self._t("bot.paid_done", title=item["title"]))

    # -- main dispatcher ----------------------------------------------

    def _handle_text(self, chat_id: int, text: str, state: dict) -> None:
        low = text.lower().strip()
        step = state.get("step")

        # ---- state machine (wizards & confirmations) ----
        if step == "confirm":
            if low in ("evet", "e", "yes", "onay", "1"):
                self._save_extracted(chat_id, state["extracted"])
            else:
                self.tg.send(chat_id, self._t("bot.cancel_ok"))
            self.db.set_chat_state(chat_id, {})
            return

        if step == "extract_followup":
            try:
                extracted = self._extract(text, context=state["extracted"])
            except AmeleError as e:
                self.tg.send(chat_id, self._t("bot.understand_error", code=e.exit_code))
                self.db.set_chat_state(chat_id, {})
                return
            if extracted is None:
                self.tg.send(chat_id, self._t("bot.extract_invalid"))
                self.db.set_chat_state(chat_id, {})
                return
            if extracted.get("intent") != "record":
                self.tg.send(chat_id, self._t("bot.cancel_ok"))
                self.db.set_chat_state(chat_id, {})
                return
            self.db.set_chat_state(chat_id, {"step": "confirm", "extracted": extracted})
            self.tg.send(chat_id, self._confirm_card(extracted))
            return

        if step == "add_name":
            name = text.strip()
            if not name:
                return
            self._wizard_add_slug(chat_id, name)
            return
        if step == "add_slug":
            slug = low
            if not SLUG_RE.match(slug):
                self.tg.send(chat_id, self._t("bot.wizard_bad_slug"))
                return
            if self.db.get_agent_by_slug(slug):
                self.tg.send(chat_id, self._t("bot.wizard_agent_exists", slug=slug))
                return
            self._wizard_add_role(chat_id, slug, state["name"])
            return
        if step == "add_role":
            role = text.strip()
            if not role:
                return
            self._wizard_add_confirm(chat_id, state["slug"], state["name"], role)
            return
        if step == "add_confirm":
            if low in ("evet", "e", "yes", "onay"):
                self._create_agent(chat_id, state["slug"], state["name"], state["role"])
            else:
                self.tg.send(chat_id, self._t("bot.cancel_ok"))
            self.db.set_chat_state(chat_id, {})
            return

        if step == "del_slug":
            self._delete_agent(chat_id, low)
            self.db.set_chat_state(chat_id, {})
            return
        if step == "del_confirm":
            if low in ("evet", "e", "yes", "onay"):
                self._confirm_delete(chat_id, state["slug"])
            else:
                self.tg.send(chat_id, self._t("bot.cancel_ok"))
            self.db.set_chat_state(chat_id, {})
            return

        if step == "edit_slug":
            self._edit_field(chat_id, low)
            return
        if step == "edit_field":
            if low == "1":
                self.db.set_chat_state(chat_id, {"step": "edit_value",
                                                 "slug": state["slug"], "field": "name"})
                self.tg.send(chat_id, self._t("bot.wizard_edit_new_name"))
            elif low == "2":
                self.db.set_chat_state(chat_id, {"step": "edit_value",
                                                 "slug": state["slug"], "field": "role"})
                self.tg.send(chat_id, self._t("bot.wizard_edit_new_role"))
            else:
                self.tg.send(chat_id, self._t("bot.invalid_number"))
            return
        if step == "edit_value":
            value = text.strip()
            if value:
                self._apply_edit(chat_id, state["slug"], state["field"], value)
            self.db.set_chat_state(chat_id, {})
            return

        if step == "pay_select" and low.isdigit():
            idx = int(low) - 1
            ids = state.get("ids", [])
            if 0 <= idx < len(ids):
                self._complete(chat_id, ids[idx])
            else:
                self.tg.send(chat_id, self._t("bot.invalid_number"))
            self.db.set_chat_state(chat_id, {})
            return

        # ---- commands ----
        if low in ("/start", "start", "merhaba", "selam", "hi", "hello"):
            self.tg.send(chat_id, self._t("bot.start_welcome"))
            return
        if low in ("/help", "help", "yardım"):
            self._cmd_help(chat_id)
            return
        if low in ("/agents", "agents", "ajanlar"):
            self._cmd_agents(chat_id)
            return
        if low in ("/jobs", "jobs", "liste", "görevler", "listele", "list"):
            self._cmd_jobs(chat_id)
            return
        if low in ("/settings", "settings", "ayarlar"):
            self._cmd_settings(chat_id)
            return
        if low in ("/cancel", "iptal", "cancel"):
            self.db.set_chat_state(chat_id, {})
            self.tg.send(chat_id, self._t("bot.cancel_ok"))
            return
        if low in ("/add-agent", "/add_agent", "add-agent", "add_agent", "yeni ajan"):
            self._wizard_add_name(chat_id)
            return
        if low in ("/delete-agent", "/delete_agent") or low.startswith(("/delete-agent ", "/delete_agent ")):
            slug = low.split(" ", 1)[1].strip() if " " in low else ""
            if slug:
                self._delete_agent(chat_id, slug)
            else:
                self.db.set_chat_state(chat_id, {"step": "del_slug"})
                self.tg.send(chat_id, self._t("bot.wizard_delete_which"))
            return
        if low in ("/edit-agent", "/edit_agent") or low.startswith(("/edit-agent ", "/edit_agent ")):
            slug = low.split(" ", 1)[1].strip() if " " in low else ""
            if slug:
                self._edit_field(chat_id, slug)
            else:
                self._start_edit(chat_id)
            return
        if low in ("/add-job", "/add_job") or low.startswith(("/add-job ", "/add_job ")):
            job = low.split(" ", 1)[1].strip() if " " in low else ""
            if job:
                self._handle_natural(chat_id, text.split(" ", 1)[1].strip(), {})
            else:
                self.tg.send(chat_id, self._t("bot.job_flow_ask"))
            return
        if low in ("/done", "done", "ödedim", "tamam", "bitti"):
            self._handle_paid(chat_id, state)
            return

        # ---- natural language ----
        self._handle_natural(chat_id, text, state)

    def _handle_natural(self, chat_id: int, text: str, state: dict) -> None:
        try:
            extracted = self._extract(text)
        except AmeleError as e:
            self.tg.send(chat_id, self._t("bot.understand_error", code=e.exit_code))
            return
        if extracted is None:
            self.tg.send(chat_id, self._t("bot.extract_invalid"))
            return

        if extracted.get("intent") == "question":
            try:
                answer = self._ask_agent(extracted.get("agent_slug"), text)
            except AmeleError as e:
                self.tg.send(chat_id, self._t("bot.understand_error", code=e.exit_code))
                return
            self.tg.send(chat_id, answer, html=False)
            return

        if not extracted.get("due_date") or extracted.get("ask_user"):
            question = extracted.get("ask_user") or self._t("bot.ask_due_date")
            self.tg.send(chat_id, question, html=not extracted.get("ask_user"))
            self.db.set_chat_state(chat_id, {
                "step": "extract_followup", "extracted": extracted})
            return

        self.db.set_chat_state(chat_id, {"step": "confirm", "extracted": extracted})
        self.tg.send(chat_id, self._confirm_card(extracted))

    # -- main loop --------------------------------------------------

    def run_forever(self) -> None:
        print(f"[kahya bot] started — {self.tg.base[:50]}…")
        self._register_commands()
        while True:
            try:
                self._refresh_tg()
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
