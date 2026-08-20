#!/usr/bin/env python3
"""ask_confirm — amele tool (subprocess).

Onaylı/tehlikeli aksiyon: pending_actions'a kayıt düşer + Telegram'a
onay sorusu gider. Kullanıcı düz metin cevaplar (evet/hayır/iptal);
bot en güncel bekleyen onayı, "mail-amele evet" formatıyla da eski
bir onayı işler (REDESIGN §7).

stdin:  {"soru": "...", "aksiyon": {...}}
stdout: {"onay_id": N}  (JSON) veya "ERROR: ..."

Env:    KAHYA_AMELE_ID (amele db id — orkestratör set eder),
        KAHYA_DIR (repo kökü: db + lang yolu), KAHYA_LANGUAGE,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_API_BASE (test)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.db import KahyaDB  # noqa: E402
from kahya.i18n import I18n  # noqa: E402
from tools.telegram_send import send  # noqa: E402


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        soru = str(payload.get("soru", "")).strip()
        aksiyon = payload.get("aksiyon") or {}
        if not soru:
            print("ERROR: 'soru' alanı boş")
            return 1

        root = Path(os.environ.get("KAHYA_DIR", Path(__file__).resolve().parent.parent))
        amele_id = int(os.environ.get("KAHYA_AMELE_ID", "0") or "0")
        db = KahyaDB(root / "data" / "kahya.db")
        try:
            amele = db.get_amele(amele_id) if amele_id else None
            slug = amele["slug"] if amele else "amele"
            lang = os.environ.get("KAHYA_LANGUAGE", "tr") or "tr"
            i18n = I18n(root / "lang", lang)
            action_id = db.add_pending_action(amele_id or 0, aksiyon, lang=lang)
            msg = i18n.t("bot.approval_ask", amele=slug, soru=soru)
            os.environ["KAHYA_RAW_HTML"] = "1"  # i18n şablonu + escape'li yer tutucular
            res = send(msg)
            if res != "ok":
                # Telegram ulaşamadıysa onay kaydını geri al — kullanıcı
                # soruyu görmedi; askıda onay kalmasın.
                db.resolve_pending_action(action_id, "cancelled")
                print(f"ERROR: onay mesajı gönderilemedi: {res}")
                return 1
            print(json.dumps({"onay_id": action_id}, ensure_ascii=False))
            return 0
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — tool çıktısı her zaman JSON/ERROR
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
