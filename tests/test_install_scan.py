#!/usr/bin/env python3
"""Step 9 — install.py sistem tarama + otomatik seçim + onay listesi testleri.

Kural: hiçbir şey onaysız yapılmaz. Testler:
- scan_system: mevcut yazılımları tarar, doğru önerileri üretir
  (amele MCP kuralı, .env, port, node/ollama/ffmpeg, systemd)
- confirm_items: kritikler varsayılan EVET, öneriler HAYIR; kısayollar
  (a / q); --yes; --dry-run hiçbir şeyi değiştirmez
- apply_items: yalnız onaylananlar uygulanır (env, port, temizlik)
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = str(Path(__file__).resolve().parent.parent)
spec = importlib.util.spec_from_file_location("install_mod",
                                              str(Path(ROOT) / "install.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


# --- geçici ROOT: amele yok, .env yok ---
TMP = Path(tempfile.mkdtemp(prefix="kahya_scan_"))
(TMP / "bin").mkdir()
orig_root = m.ROOT
m.ROOT = TMP

try:
    items = m.scan_system("linux", "amd64", 8080, force=False)
    by_id = {i["id"]: i for i in items}
    check("1a tarama amele önerisi üretir (yok → kritik)",
          by_id["amele"]["durum"] == "✗" and by_id["amele"]["kritik"])
    check("1b .env yok → oluşturma önerisi (kritik)",
          by_id["env"]["durum"] == "○" and by_id["env"]["uygula"] is not None)
    check("1c port maddesi var", "port" in by_id)
    check("1d node maddesi var (sandbox'a göre ✓ veya ○)",
          by_id["node"]["durum"] in ("✓", "○"))
    check("1e ollama/ffmpeg önerileri var",
          by_id["ollama"]["durum"] in ("✓", "○")
          and by_id["ffmpeg"]["durum"] in ("✓", "○"))
    # hazır maddeler onay istemez (uygula yok)
    hazir = [i for i in items if i["durum"] == "✓"]
    check("1f hazır maddeler onay gerektirmez",
          all(i["uygula"] is None and i["komut"] is None for i in hazir))

    # --- confirm: kritikler Enter=Y, öneriler Enter=n ---
    with mock.patch("builtins.input", return_value=""):
        chosen = m.confirm_items(items, yes=False)
    chosen_ids = {i["id"] for i in chosen}
    check("2a kritikler onaylandı (amele/env)",
          {"amele", "env"} <= chosen_ids, chosen_ids)
    check("2a2 boş port onay gerektirmez (hazır — işlem yok)",
          "port" not in chosen_ids, chosen_ids)
    check("2b öneriler onaylanmadı (node/ollama/ffmpeg/systemd)",
          not {"node", "ollama", "ffmpeg", "systemd"} & chosen_ids)

    # --- kısayol 'q' → çıkış ---
    with mock.patch("builtins.input", return_value="q"):
        try:
            m.confirm_items(items)
            check("2c 'q' çıkış verir", False)
        except SystemExit:
            check("2c 'q' çıkış verir", True)

    # --- --yes: hepsi onaylanır ---
    chosen_all = m.confirm_items(items, yes=True)
    all_ids = [c["id"] for c in chosen_all]
    check("2d --yes tüm actionable'ları onaylar (hazır maddeler hariç)",
          {"amele", "env", "node", "ollama"} <= set(all_ids)
          and "port" not in all_ids, all_ids)

    # --- --dry-run: hiçbir şey değişmez ---
    before = sorted(p.name for p in TMP.iterdir())
    m.confirm_items(items, dry_run=True)
    after = sorted(p.name for p in TMP.iterdir())
    check("2e dry-run dosya değiştirmez", before == after, before)

    # --- apply: yalnız onaylananlar ---
    chosen = [by_id["env"], by_id["port"]]
    m.apply_items(items, chosen, "linux", "amd64", force=False)
    check("3a .env oluşturuldu (onaylandı)",
          (TMP / ".env").exists())
    check("3b amele kurulmadı (onaylanmadı)",
          not (TMP / "bin" / "amele").exists())
    check("3c .install-tmp oluşmadı (onaylanmadı)",
          not (TMP / ".install-tmp").exists())

    # --- MCP'siz binary → kritik 'MCP'siz!' önerisi ---
    eski = Path(__import__("os").environ["LOXI_SCRATCH"]) / "amele_eski"
    (TMP / "bin" / "amele").write_bytes(eski.read_bytes())
    items2 = m.scan_system("linux", "amd64", 8080, force=False)
    a2 = next(i for i in items2 if i["id"] == "amele")
    check("4a MCP'siz binary tespit edilir (kritik)",
          a2["durum"] == "✗" and a2["kritik"]
          and "MCP'siz" in a2["baslik"])
    (TMP / "bin" / "amele").unlink()

    # --- hazır MCP'li binary → ✓ ---
    shutil.copy(Path(ROOT) / "bin" / "amele", TMP / "bin" / "amele")
    items3 = m.scan_system("linux", "amd64", 8080, force=False)
    a3 = next(i for i in items3 if i["id"] == "amele")
    check("4b MCP'li binary hazır (✓, onay istemez)",
          a3["durum"] == "✓" and a3["uygula"] is None)
    (TMP / "bin" / "amele").unlink()

    # --- port doluysa öneri ---
    import socket
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    dolu = s.getsockname()[1]
    s.close()
    srv = socket.socket()
    srv.bind(("0.0.0.0", dolu))
    srv.listen(1)
    try:
        items4 = m.scan_system("linux", "amd64", dolu, force=False)
        p4 = next(i for i in items4 if i["id"] == "port")
        check("4c dolu port önerisi", p4["durum"] == "!"
              and p4["port"] != dolu, p4)
    finally:
        srv.close()

    # --- kalıntı önerisi ---
    (TMP / ".install-tmp").mkdir()
    items5 = m.scan_system("linux", "amd64", 8080, force=False)
    check("4d .install-tmp kalıntısı önerilir",
          any(i["id"] == "temizlik" for i in items5))
    (TMP / ".install-tmp").rmdir()

finally:
    m.ROOT = orig_root
    shutil.rmtree(TMP, ignore_errors=True)

print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("INSTALL SCAN OK")
