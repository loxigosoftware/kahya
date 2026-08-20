#!/usr/bin/env python3
"""Step 9 — install.py system scan + automatic selection + approval list tests.

Rule: nothing is done without approval. Tests:
- scan_system: scans installed software, produces the right proposals
  (amele MCP rule, .env, node/ffmpeg, leftovers); port is NOT an approval
  item (automatic), no ollama item, no systemd approval item (single
  question at the end)
- MCP-less binary → rule rejection (SystemExit, no approval question)
- confirm_items: critical items default YES, recommendations NO; shortcuts
  (a / q); --yes; --dry-run changes nothing
- apply_items: only approved items are applied (env, cleanup)
- port_test/write_port: automatic port pick and .env write
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


# --- temporary ROOT: no amele, no .env ---
TMP = Path(tempfile.mkdtemp(prefix="kahya_scan_"))
(TMP / "bin").mkdir()
orig_root = m.ROOT
m.ROOT = TMP

try:
    items = m.scan_system("linux", "amd64", force=False)
    by_id = {i["id"]: i for i in items}
    check("1a scan proposes amele install (missing → critical)",
          by_id["amele"]["status"] == "✗" and by_id["amele"]["critical"])
    check("1b .env missing → creation proposal (critical)",
          by_id["env"]["status"] == "○" and by_id["env"]["action"] is not None)
    check("1c no port approval item (picked automatically)",
          "port" not in by_id, list(by_id))
    check("1d node item present (✓ or ○ depending on sandbox)",
          by_id["node"]["status"] in ("✓", "○"))
    check("1e no ollama item, ffmpeg proposal present",
          "ollama" not in by_id and by_id["ffmpeg"]["status"] in ("✓", "○"))
    check("1f no systemd approval item (single question at the end)",
          "systemd" not in by_id, list(by_id))
    # ready items need no approval (no action)
    ready = [i for i in items if i["status"] == "✓"]
    check("1g ready items need no approval",
          all(i["action"] is None and i["command"] is None for i in ready))

    # --- confirm: critical Enter=Y, recommendations Enter=n ---
    with mock.patch("builtins.input", return_value=""):
        chosen = m.confirm_items(items, yes=False)
    chosen_ids = {i["id"] for i in chosen}
    check("2a critical items approved (amele/env)",
          {"amele", "env"} <= chosen_ids, chosen_ids)
    check("2b recommendations not approved (node/ffmpeg)",
          not {"node", "ffmpeg"} & chosen_ids)

    # --- shortcut 'q' → exit ---
    with mock.patch("builtins.input", return_value="q"):
        try:
            m.confirm_items(items)
            check("2c 'q' exits", False)
        except SystemExit:
            check("2c 'q' exits", True)

    # --- --yes: everything approved ---
    chosen_all = m.confirm_items(items, yes=True)
    all_ids = [c["id"] for c in chosen_all]
    ffmpeg_expected = by_id["ffmpeg"]["status"] == "○" and "ffmpeg" in all_ids
    check("2d --yes approves all actionable items (ready ones excluded)",
          {"amele", "env", "node"} <= set(all_ids)
          and "port" not in all_ids and "ollama" not in all_ids
          and (by_id["ffmpeg"]["status"] != "○" or ffmpeg_expected),
          all_ids)

    # --- --dry-run: nothing changes ---
    before = sorted(p.name for p in TMP.iterdir())
    m.confirm_items(items, dry_run=True)
    after = sorted(p.name for p in TMP.iterdir())
    check("2e dry-run does not touch files", before == after, before)

    # --- apply: only approved items ---
    chosen = [by_id["env"]]
    m.apply_items(items, chosen, "linux", "amd64", force=False)
    check("3a .env created (approved)",
          (TMP / ".env").exists())
    check("3b amele not installed (not approved)",
          not (TMP / "bin" / "amele").exists())
    check("3c .install-tmp not created (not approved)",
          not (TMP / ".install-tmp").exists())

    # --- MCP-less binary → rule rejection (SystemExit, no question) ---
    fake = TMP / "bin" / "amele"
    fake.write_text("#!/bin/sh\necho amele legacy 0.1.1\n")
    fake.chmod(0o755)
    try:
        m.scan_system("linux", "amd64", force=False)
        check("4a MCP-less binary rejected (rule rejection)", False)
    except SystemExit:
        check("4a MCP-less binary rejected (rule rejection)", True)
    fake.unlink()

    # --- ready MCP binary → ✓ ---
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
        check("4b MCP binary ready (✓, no approval needed)",
              a3["status"] == "✓" and a3["action"] is None)
        (TMP / "bin" / "amele").unlink()
    else:
        print("  [skip] 4b (no MCP test binary)")

    # --- port: automatic pick (not in the approval list but port_test runs) ---
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    busy = s.getsockname()[1]
    s.close()
    srv = socket.socket()
    srv.bind(("0.0.0.0", busy))
    srv.listen(1)
    try:
        p = m.port_test(busy)
        check("4c busy port → next free port found",
              p != busy and 8080 <= p <= 8099, p)
    finally:
        srv.close()

    # --- write_port: .env write ---
    m.write_port(8081)
    env_text = (TMP / ".env").read_text(encoding="utf-8")
    check("4d write_port writes KAHYA_WEB_PORT to .env",
          "KAHYA_WEB_PORT=8081" in env_text, env_text)

    # --- leftover proposal + cleanup on approval ---
    (TMP / ".install-tmp").mkdir()
    items5 = m.scan_system("linux", "amd64", force=False)
    t5 = next((i for i in items5 if i["id"] == "cleanup"), None)
    check("4e .install-tmp leftovers proposed", t5 is not None)
    if t5:
        m.apply_items(items5, [t5], "linux", "amd64", force=False)
        check("4f approved cleanup removes .install-tmp",
              not (TMP / ".install-tmp").exists())

finally:
    m.ROOT = orig_root
    shutil.rmtree(TMP, ignore_errors=True)

print()
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("INSTALL SCAN OK")
