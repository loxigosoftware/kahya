#!/usr/bin/env python3
"""Step 9 — install.py sistem tarama + otomatik seçim + onay listesi testleri.

Kural: hiçbir şey onaysız yapılmaz. Testler:
- scan_system: mevcut yazılımları tarar, doğru önerileri üretir
  (amele MCP kuralı, .env, node/ffmpeg, kalıntı); port onay maddesi YOK
  (otomatik), ollama maddesi YOK, systemd onay maddesi YOK (tek soru)
- MCP'siz binary → kural-reddi (SystemExit, onay sorusu yok)
- confirm_items: kritikler varsayılan EVET, öneriler HAYIR; kısayollar
  (a / q); --yes; --dry-run hiçbir şeyi değiştirmez
- apply_items: yalnız onaylananlar uygulanır (env, temizlik)
- port_test/write_port: port otomatik seçimi ve .env'e yazımı
"""
import importlib.util
import os
import shutil
import socket
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
    items = m.scan_system("linux", "amd64", force=False)
    by_id = {i["id"]: i for i in items}
    check("1a tarama amele önerisi üretir (yok → kritik)",
          by_id["amele"]["durum"] == "✗" and by_id["amele"]["kritik"])
    check("1b .env yok → oluşturma önerisi (kritik)",
          by_id["env"]["durum"] == "○" and by_id["env"]["uygula"] is not None)
    check("1c port onay maddesi YOK (otomatik seçilir)",
          "port" not in by_id, list(by_id))
    check("1d node maddesi var (sandbox'a göre ✓ veya ○)",
          by_id["node"]["durum"] in ("✓", "○"))
    check("1e ollama maddesi YOK, ffmpeg önerisi var",
          "ollama" not in by_id and by_id["ffmpeg"]["durum"] in ("✓", "○"))
    check("1f systemd onay maddesi YOK (kurulum sonunda tek soru)",
          "systemd" not in by_id, list(by_id))
    # hazır maddeler onay istemez (uygula yok)
    hazir = [i for i in items if i["durum"] == "✓"]
    check("1g hazır maddeler onay gerektirmez",
          all(i["uygula"] is None and i["komut"] is None for i in hazir))

    # --- confirm: kritikler Enter=Y, öneriler Enter=n ---
    with mock.patch("builtins.input", return_value=""):
        chosen = m.confirm_items(items, yes=False)
    chosen_ids = {i["id"] for i in chosen}
    check("2a kritikler onaylandı (amele/env)",
          {"amele", "env"} <= chosen_ids, chosen_ids)
    check("2b öneriler onaylanmadı (node/ffmpeg)",
          not {"node", "ffmpeg"} & chosen_ids)

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
    ffmpeg_beklenen = by_id["ffmpeg"]["durum"] == "○" and "ffmpeg" in all_ids
    check("2d --yes tüm actionable'ları onaylar (hazır maddeler hariç)",
          {"amele", "env", "node"} <= set(all_ids)
          and "port" not in all_ids and "ollama" not in all_ids
          and (by_id["ffmpeg"]["durum"] != "○" or ffmpeg_beklenen),
          all_ids)

    # --- --dry-run: hiçbir şey değişmez ---
    before = sorted(p.name for p in TMP.iterdir())
    m.confirm_items(items, dry_run=True)
    after = sorted(p.name for p in TMP.iterdir())
    check("2e dry-run dosya değiştirmez", before == after, before)

    # --- apply: yalnız onaylananlar ---
    chosen = [by_id["env"]]
    m.apply_items(items, chosen, "linux", "amd64", force=False)
    check("3a .env oluşturuldu (onaylandı)",
          (TMP / ".env").exists())
    check("3b amele kurulmadı (onaylanmadı)",
          not (TMP / "bin" / "amele").exists())
    check("3c .install-tmp oluşmadı (onaylanmadı)",
          not (TMP / ".install-tmp").exists())

    # --- MCP'siz binary → kural-reddi (SystemExit, soru yok) ---
    sahte = TMP / "bin" / "amele"
    sahte.write_text("#!/bin/sh\necho amele legacy 0.1.1\n")
    sahte.chmod(0o755)
    try:
        m.scan_system("linux", "amd64", force=False)
        check("4a MCP'siz binary reddedilir (kural-reddi)", False)
    except SystemExit:
        check("4a MCP'siz binary reddedilir (kural-reddi)", True)
    sahte.unlink()

    # --- hazır MCP'li binary → ✓ ---
    mcp_src = None
    scratch = os.environ.get("LOXI_SCRATCH")
    if scratch:
        cand = Path(scratch) / "builds" / "amele-linux-amd64"
        if cand.exists():
            mcp_src = cand
    if mcp_src:
        shutil.copy(mcp_src, TMP / "bin" / "amele")
        items3 = m.scan_system("linux", "amd64", force=False)
        a3 = next(i for i in items3 if i["id"] == "amele")
        check("4b MCP'li binary hazır (✓, onay istemez)",
              a3["durum"] == "✓" and a3["uygula"] is None)
        (TMP / "bin" / "amele").unlink()
    else:
        print("  [skip] 4b (MCP'li test binary yok)")

    # --- port: otomatik seçim (onay listesi yok ama port_test çalışır) ---
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    dolu = s.getsockname()[1]
    s.close()
    srv = socket.socket()
    srv.bind(("0.0.0.0", dolu))
    srv.listen(1)
    try:
        p = m.port_test(dolu)
        check("4c dolu port için otomatik boş port bulunur",
              p != dolu and 8080 <= p <= 8099, p)
    finally:
        srv.close()

    # --- write_port: .env'e yazım ---
    m.write_port(8081)
    env_text = (TMP / ".env").read_text(encoding="utf-8")
    check("4d write_port .env'e KAHYA_WEB_PORT yazar",
          "KAHYA_WEB_PORT=8081" in env_text, env_text)

    # --- kalıntı önerisi + onaylanınca temizlik ---
    (TMP / ".install-tmp").mkdir()
    items5 = m.scan_system("linux", "amd64", force=False)
    t5 = next((i for i in items5 if i["id"] == "temizlik"), None)
    check("4e .install-tmp kalıntısı önerilir", t5 is not None)
    if t5:
        m.apply_items(items5, [t5], "linux", "amd64", force=False)
        check("4f onaylanan temizlik .install-tmp'i siler",
              not (TMP / ".install-tmp").exists())

finally:
    m.ROOT = orig_root
    shutil.rmtree(TMP, ignore_errors=True)

print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("INSTALL SCAN OK")
