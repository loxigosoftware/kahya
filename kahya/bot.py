"""Telegram bot — the owner's front door.

Natural language in, confirmation flow out:

  "3000 TL su faturası geldi"  → amele extract agent → follow-up question
  "19 Ağustos"                 → re-extract with context → confirmation card
  "evet"                       → saved, reminders armed
  "ödedim"                     → item completed (rolls repeats forward)

Pure stdlib HTTP long-polling; no bot framework needed.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Optional

from .amele_runner import AmeleError, run_agent
from .config import Config
from .db import KahyaDB

# ------------------------------------------------ telegram plumbing


class TG:
    def __init__(self, token: str):
        base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
        self.base = f"{base}/bot{token}"

    def _call(self, method: str, data: dict) -> Any:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(f"{self.base}/{method}", data=body)
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send(self, chat_id, text: str) -> bool:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        res = self._call("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true"})
        return bool(res.get("ok"))

    def get_updates(self, offset: int) -> list[dict]:
        res = self._call("getUpdates", {
            "offset": offset, "timeout": 25, "allowed_updates": '["message"]'})
        return res.get("result", []) if res.get("ok") else []


# ------------------------------------------------ message builders


def _fmt_money(item: dict) -> str:
    if item.get("amount") is None:
        return ""
    cur = item.get("currency") or ""
    amount = item["amount"]
    if isinstance(amount, float) and amount == int(amount):
        amount = int(amount)
    return f"{amount} {cur}".strip()


def _item_line(item: dict) -> str:
    money = _fmt_money(item)
    due = item.get("due_date") or "tarih yok"
    parts = [item["title"]]
    if money:
        parts.append(money)
    parts.append(f"vade: {due}")
    if item.get("repeat_rule") != "none":
        parts.append(f"tekrar: {item['repeat_rule']}")
    return " · ".join(parts)


def _confirm_card(extracted: dict) -> str:
    money = ""
    if extracted.get("amount") is not None:
        cur = extracted.get("currency") or ""
        money = f" — {extracted['amount']} {cur}".rstrip()
    lines = [
        "📋 <b>Bunu kaydedeyim mi?</b>",
        "",
        f"<b>{extracted.get('title', '?')}</b>{money}",
    ]
    if extracted.get("due_date"):
        lines.append(f"Tarih: {extracted['due_date']}")
    if extracted.get("repeat_rule", "none") != "none":
        d = extracted.get("repeat_detail")
        lines.append(f"Tekrar: {extracted['repeat_rule']}" + (f" ({d})" if d else ""))
    if extracted.get("remind_before_days") not in (None, 0):
        lines.append(f"Hatırlatma: {extracted['remind_before_days']} gün önceden")
    if extracted.get("note"):
        lines.append(f"Not: {extracted['note']}")
    lines += ["", "Cevap: <b>evet</b> / <b>hayır</b>"]
    return "\n".join(lines)


# ------------------------------------------------ bot logic


class Bot:
    def __init__(self, cfg: Config, db: KahyaDB):
        self.cfg = cfg
        self.db = db
        self.tg = TG(cfg.telegram_token)
        self.offset = 0
        self.extract_yaml = cfg.agents_dir / "extract.yaml"

    # -- helpers -------------------------------------------------

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
        return run_agent(self.cfg, self.extract_yaml, task, timeout_s=120)

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
                     f"✅ <b>Kaydedildi</b> — {agent_name} takip ediyor.\n"
                     f"Vadeden {data['remind_before_days']} gün önce hatırlatmaya başlarım.")
        return True

    # -- message handlers -----------------------------------------

    def _handle_start(self, chat_id: int) -> None:
        self.tg.send(chat_id,
                     "🧑‍💼 <b>Kâhya</b> — evinizin ve küçük işletmenizin kâhyası.\n\n"
                     "Bana doğal dille yazın:\n"
                     "• <i>\"3000 TL su faturası geldi, son ödeme 19 ağustos\"</i>\n"
                     "• <i>\"Kedi Pamuk'un kuduz aşısı 3 eylülde\"</i>\n"
                     "• <i>\"Her ayın 20'sinde kira hatırlat\"</i>\n\n"
                     "Komutlar: <b>liste</b> · <b>ajanlar</b> · <b>ödedim</b> · <b>iptal</b>")

    def _handle_list(self, chat_id: int) -> None:
        items = self.db.list_items(status="open")
        if not items:
            self.tg.send(chat_id, "Açık görev yok. 🌿")
            return
        today = date.today().isoformat()
        lines = ["📌 <b>Açık görevler:</b>", ""]
        for it in items:
            mark = "🔴" if (it["due_date"] or "") < today else "🟢"
            lines.append(f"{mark} {_item_line(it)}")
        self.tg.send(chat_id, "\n".join(lines))

    def _handle_agents(self, chat_id: int) -> None:
        agents = self.db.list_agents()
        if not agents:
            self.tg.send(chat_id, "Henüz ajan yok. Web panelden ilk ajanınızı yaratın.")
            return
        lines = ["🤖 <b>Ajanlar:</b>", ""]
        for a in agents:
            n = self.db.con.execute(
                "SELECT COUNT(*) FROM items WHERE agent_id = ? AND status = 'open'",
                (a["id"],)).fetchone()[0]
            lines.append(f"• <b>{a['name']}</b> ({a['slug']}) — {n} açık görev")
        self.tg.send(chat_id, "\n".join(lines))

    def _handle_paid(self, chat_id: int, state: dict) -> None:
        """'ödedim' — offer the most relevant open item(s) to complete."""
        if state.get("step") == "pay_select":
            return  # waiting for a selection number
        today = date.today()
        due_now = [i for i in self.db.due_for_reminder(today)]
        if not due_now:
            self.tg.send(chat_id, "Hatırlatma penceresinde açık görev yok. 👍")
            return
        if len(due_now) == 1:
            self._complete(chat_id, due_now[0]["id"])
            return
        lines = ["Hangisini tamamlayayım?", ""]
        for idx, it in enumerate(due_now, 1):
            lines.append(f"{idx}. {_item_line(it)}")
        lines += ["", "Numarayı yazın (veya <b>iptal</b>)."]
        self.tg.send(chat_id, "\n".join(lines))
        self.db.set_chat_state(chat_id, {
            "step": "pay_select",
            "ids": [i["id"] for i in due_now]})

    def _complete(self, chat_id: int, item_id: int) -> None:
        item = self.db.get_item(item_id)
        if not item:
            self.tg.send(chat_id, "Bu görev bulunamadı.")
            return
        result = self.db.complete_item(item_id)
        self.db.log("bot", {"event": "item_completed", "item_id": item_id})
        if result and result.get("rolled"):
            self.tg.send(chat_id,
                         f"✅ <b>{item['title']}</b> tamamlandı.\n"
                         f"Tekrar eden kayıt: sonraki vade <b>{result['due_date']}</b>.")
        else:
            self.tg.send(chat_id, f"✅ <b>{item['title']}</b> tamamlandı. Kapattım.")

    def _handle_text(self, chat_id: int, text: str, state: dict) -> None:
        low = text.lower().strip()

        # state machine: confirmations & selections first
        if state.get("step") == "confirm":
            if low in ("evet", "e", "yes", "onay"):
                self._save_extracted(chat_id, state["extracted"])
            else:
                self.tg.send(chat_id, "Tamam, kaydetmedim. 🗑️")
            self.db.set_chat_state(chat_id, {})
            return

        if state.get("step") == "extract_followup":
            try:
                extracted = self._extract(text, context=state["extracted"])
            except AmeleError as e:
                self.tg.send(chat_id, f"Anlayamadım ({e.exit_code}). <b>iptal</b> yazıp tekrar deneyin.")
                self.db.set_chat_state(chat_id, {})
                return
            self.db.set_chat_state(chat_id, {"step": "confirm", "extracted": extracted})
            self.tg.send(chat_id, _confirm_card(extracted))
            return

        if state.get("step") == "pay_select" and low.isdigit():
            idx = int(low) - 1
            ids = state.get("ids", [])
            if 0 <= idx < len(ids):
                self._complete(chat_id, ids[idx])
            else:
                self.tg.send(chat_id, "Geçersiz numara.")
            self.db.set_chat_state(chat_id, {})
            return

        # commands
        if low in ("/start", "start", "merhaba", "selam"):
            self._handle_start(chat_id)
            return
        if low in ("liste", "görevler", "listele", "list"):
            self._handle_list(chat_id)
            return
        if low in ("ajanlar", "agents"):
            self._handle_agents(chat_id)
            return
        if low in ("ödedim", "tamam", "done", "bitti", "kapattım"):
            self._handle_paid(chat_id, state)
            return
        if low in ("iptal", "cancel"):
            self.db.set_chat_state(chat_id, {})
            self.tg.send(chat_id, "İptal edildi.")
            return

        # default: extract via amele
        try:
            extracted = self._extract(text)
        except AmeleError as e:
            self.tg.send(chat_id,
                         f"Anlayamadım ({e.exit_code}). Daha net yazın veya <b>iptal</b>.")
            return

        if not extracted.get("due_date") or extracted.get("ask_user"):
            question = extracted.get("ask_user") or "Son ödeme/etkinlik tarihi ne zaman? 📅"
            self.tg.send(chat_id, question)
            self.db.set_chat_state(chat_id, {
                "step": "extract_followup", "extracted": extracted})
            return

        self.db.set_chat_state(chat_id, {"step": "confirm", "extracted": extracted})
        self.tg.send(chat_id, _confirm_card(extracted))

    # -- main loop --------------------------------------------------

    def run_forever(self) -> None:
        print(f"[kahya bot] started — {self.tg.base[:50]}…")
        while True:
            try:
                updates = self.tg.get_updates(self.offset)
                for u in updates:
                    self.offset = u["update_id"] + 1
                    msg = u.get("message")
                    if not msg or "text" not in msg:
                        continue
                    chat_id = msg["chat"]["id"]
                    if str(chat_id) != str(self.cfg.telegram_chat_id):
                        self.tg.send(chat_id, "Bu kâhya başka bir evin kâhyası. 🏠")
                        continue
                    state = self.db.get_chat_state(chat_id)
                    print(f"  [msg] {msg['text'][:80]!r}")
                    self._handle_text(chat_id, msg["text"], state)
            except Exception as e:
                print(f"  [ERROR] polling: {e}")
                time.sleep(5)


if __name__ == "__main__":
    cfg = Config()
    db = KahyaDB(cfg.db_path)
    try:
        Bot(cfg, db).run_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        db.close()
