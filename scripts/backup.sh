#!/usr/bin/env bash
# Kahya günlük yedek (REDESIGN §5: yedekler proje klasöründe — kullanıcı
# dışarı taşımakta özgür).
#
# Alınanlar: data/kahya.db (SQLite online backup) + ameles/ + tools/ +
# .env (varsa) → kahya-yedek-<YYYYMMDD>/
#
# Kullanım:
#   scripts/backup.sh              # yedek al
#   scripts/backup.sh --prune 14   # 14 günden eski yedekleri sil
#
# Cron örneği (her gece 03:00):
#   0 3 * * * cd /path/to/kahya && scripts/backup.sh >> logs/backup.log 2>&1
set -euo pipefail

KAHYA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KAHYA_ROOT"

DATE="$(date +%Y%m%d)"
DEST="kahya-yedek-$DATE"
mkdir -p "$DEST"

# SQLite online backup — WAL dosyalarıyla bozulmadan kopyalar
python3 - "$DEST/kahya.db" <<'EOF'
import sqlite3, sys
src, dst = "data/kahya.db", sys.argv[1]
con = sqlite3.connect(src)
try:
    out = sqlite3.connect(dst)
    try:
        con.backup(out)
    finally:
        out.close()
finally:
    con.close()
print(f"  db → {dst}")
EOF

# ameles (YAML config'leri) — proje içindeki her yedekte yer alır
if [ -d ameles ]; then
  cp -r ameles "$DEST/ameles"
  echo "  ameles/ → $DEST/ameles"
fi

# geçmiş dump: konuşma arşivi okunabilir formatta (JSONL)
python3 - "$DEST/gecmis.jsonl" <<'EOF'
import json, os, sqlite3, sys
try:
    con = sqlite3.connect("data/kahya.db")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT thread_id, role, content, ts FROM conversation_messages "
        "ORDER BY id").fetchall()
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
    print(f"  geçmiş: {len(rows)} mesaj → {sys.argv[1]}")
except sqlite3.Error as e:
    print(f"  NOT: geçmiş dump alınamadı ({e}) — DB yeni kurulmuş olabilir")
EOF

# .env (varsa — anahtarlar yalnız bu kopyada; git'e asla)
if [ -f .env ]; then
  cp .env "$DEST/.env"
  echo "  .env → $DEST/.env"
fi

echo "  ✓ yedek tamam: $DEST ($(du -sh "$DEST" | cut -f1))"

# --prune N: N günden eski kahya-yedek-* klasörlerini sil
if [ "${1:-}" = "--prune" ]; then
  KEEP="${2:-14}"
  find . -maxdepth 1 -type d -name "kahya-yedek-*" -mtime "+$KEEP" \
    -exec rm -rf {} +
  echo "  eski yedekler temizlendi (>$KEEP gün)"
fi
