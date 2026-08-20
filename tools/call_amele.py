#!/usr/bin/env python3
"""call_amele — Kahya tool (subprocess). Hand a task to another amele.

stdin:  JSON:
          {"slug": "mail-amele", "görev": "bilet rezervasyonunu işle",
           "bağlam": {...}}          (bağlam opsiyonel)
stdout: JSON — hedef amelenin çıktısı (sözleşme: {"görev", "bağlam",
        "beklenen_çıktı"} — REDESIGN §3.3) veya {"error": "..."}

Kurallar:
- Hedef amele kendi model ayarıyla ve KENDİ KAHYA_AMELE_ID'si ile çalışır
  (amele_runner bunu otomatik yapar).
- Paslama derinliği (REDESIGN §3.3): KAHYA_PASLAMA_DEPTH env'ini taşır ve
  hedef ameleye +1 verir. Limit 3 — aşılırsa zincir DURDURULUR ve hata
  döner (sınırsız paslama = sınırsız LLM maliyeti).
- Her paslama kullanıcıya raporlanır — raporu Kahya yazar.

Env:    KAHYA_DB, KAHYA_DIR, (opsiyonel) KAHYA_PASLAMA_DEPTH
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kahya.amele_runner import AmeleError, agent_yaml, run_agent  # noqa: E402
from kahya.config import Config  # noqa: E402
from kahya.db import KahyaDB  # noqa: E402

MAX_DEPTH = 3


def main():
    try:
        req = json.loads(sys.stdin.read().strip() or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad JSON: {e}"}))
        return 1

    slug = (req.get("slug") or "").strip()
    task = (req.get("görev") or req.get("task") or "").strip()
    if not slug or not task:
        print(json.dumps({"error": "slug ve görev gerekli"}))
        return 1

    depth = int(os.environ.get("KAHYA_PASLAMA_DEPTH", "0") or 0)
    if depth >= MAX_DEPTH:
        print(json.dumps({
            "error": f"paslama limiti ({MAX_DEPTH}) aşıldı — görev zinciri "
                     f"durduruldu. Sorun '{slug}' amelesine ulaşmadan önce "
                     f"oluştu."}, ensure_ascii=False))
        return 1

    db = KahyaDB(os.environ.get("KAHYA_DB", ""))
    cfg = Config(db)
    try:
        yaml_path = agent_yaml(cfg, slug)
        if yaml_path is None:
            # DB'de kayıtlı olmayan ama YAML'ı olan ameleler de çağrılabilir
            alt = cfg.ameleler_dir / f"{slug}.yaml"
            if alt.exists():
                yaml_path = alt
            else:
                print(json.dumps({"error": f"amele YAML'ı yok: {slug}"},
                                 ensure_ascii=False))
                return 1
        bağlam = req.get("bağlam")
        full = task
        if bağlam:
            full = (f"{task}\n\nBağlam (JSON):\n"
                    f"{json.dumps(bağlam, ensure_ascii=False)}")
        os.environ["KAHYA_PASLAMA_DEPTH"] = str(depth + 1)
        res = run_agent(cfg, yaml_path, full, timeout_s=180)
        out = {"slug": slug, "çıktı": res}
        print(json.dumps(out, ensure_ascii=False))
    except AmeleError as e:
        print(json.dumps({"error": f"{slug}: {e}"}, ensure_ascii=False))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{slug}: {e}"}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
