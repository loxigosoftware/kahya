#!/usr/bin/env python3
"""Konuşma belleği testleri (REDESIGN §3.5) + yedekleme.

- 60 mesajlık sohbet: bağlam hep ≤ 20, arşivde hepsi duruyor
- FTS araması buluyor (LIKE fallback dahil)
- scripts/backup.sh çalışıyor (DB + ameleler + geçmiş dump)
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(__file__).parent / "data" / "test_mem.db"
if DB.exists():
    DB.unlink()

sys.path.insert(0, str(ROOT))
from kahya.db import KahyaDB  # noqa: E402

db = KahyaDB(DB)
fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


# --- 1. 60 mesaj simülasyonu ---
thread = "chat:42"
for i in range(60):
    db.add_message(thread, "user" if i % 2 == 0 else "assistant",
                   f"mesaj {i}")
n_archived = db.archive_old_messages(thread)
check("60 mesajda 40 arşivlenir (aktif 20'ye iner)", n_archived == 40)
check("aktif bağlam = 20", db.count_active_messages(thread) == 20)
check("toplam 60 kayıt duruyor",
      db.con.execute("SELECT COUNT(*) FROM conversation_messages "
                     "WHERE thread_id = ?", (thread,)).fetchone()[0] == 60)

# arşivleme yalnız thread başına: başka thread etkilenmez
db.add_message("amele:42:mail-amele", "user", "mailleri oku")
db.add_message("amele:42:mail-amele", "assistant", "ok")
check("ayrı thread etkilenmez", db.count_active_messages("amele:42:mail-amele") == 2)

# --- 2. arama ---
db.add_message(thread, "user", "köpek aşısı ne zaman yapıldı")
db.add_message(thread, "assistant", "19 ağustosta yapıldı")
hits = db.search_messages("aşısı")
check("FTS tam token bulur", any("aşısı" in h["content"] for h in hits))
hits2 = db.search_messages("aşı")  # çekim eki → LIKE fallback
check("çekimli arama LIKE fallback ile bulur",
      any("aşısı" in h["content"] for h in hits2))
hits3 = db.search_messages("aşısı", thread_id="amele:42:mail-amele")
check("thread filtreli arama boş", len(hits3) == 0)
db.close()

# --- 3. yedekleme script'i ---
r = subprocess.run(["bash", str(ROOT / "scripts" / "backup.sh")],
                   capture_output=True, text=True, cwd=str(ROOT))
print("  backup çıktısı:", r.stdout.strip().replace("\n", " | ")[:300])
date = subprocess.run(["date", "+%Y%m%d"], capture_output=True,
                      text=True).stdout.strip()
dest = ROOT / f"kahya-yedek-{date}"
check("backup çalıştı", r.returncode == 0 and dest.exists())
if dest.exists():
    check("db kopyası var", (dest / "kahya.db").exists())
    check("ameleler kopyası var", (dest / "ameleler").exists())
    check("geçmiş dump var", (dest / "gecmis.jsonl").exists())
    # test kalıntısını temizle
    subprocess.run(["rm", "-rf", str(dest)], check=True)

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("MEMORY OK")
