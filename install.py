#!/usr/bin/env python3
"""Kahya — one-command cross-platform installer.

Usage:
    python3 install.py            # detect platform, install, port-test
    python3 install.py --force    # re-download amele even if present
    python3 install.py --help

What it does:
  1. detects the platform and offers an explicit environment list
     (the amele asset list — linux, macOS, Windows, Raspberry Pi, …)
  2. downloads the amele binary for that platform from GitHub and
     verifies it against SHA256SUMS
  3. creates .env from .env.example if missing
  4. port-tests the web panel port (8080 by default); if taken, picks
     the next free one and writes it to .env
  5. prints the LAN address and first-login credentials

Stdlib only — runs on Linux, macOS, Windows, Raspberry Pi.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AMELE_REPO = "lasthumanintheloop/amele"
DEFAULT_PORT = 8080
PORT_TRIES = range(8081, 8100)

# human label → amele asset triplet (os, arch)
ENVIRONMENTS = {
    "1": ("Linux (amd64)", "linux", "amd64"),
    "2": ("Linux (arm64 — 64-bit Pi / ARM server)", "linux", "arm64"),
    "3": ("Raspberry Pi OS (armv7l — 32-bit Pi)", "linux", "arm"),
    "4": ("macOS (Intel)", "darwin", "amd64"),
    "5": ("macOS (Apple Silicon)", "darwin", "arm64"),
    "6": ("Windows (amd64)", "windows", "amd64"),
    "7": ("Windows (arm64)", "windows", "arm64"),
}

C = {"reset": "\033[0m", "green": "\033[32m", "yellow": "\033[33m",
     "red": "\033[31m", "bold": "\033[1m", "dim": "\033[2m"}
if os.name == "nt":
    C = {k: "" for k in C}


def say(msg="", color="", end="\n"):
    print(f"{C.get(color,'')}{msg}{C['reset']}", end=end, flush=True)


def fail(msg):
    say(f"\n✗ {msg}", "red")
    sys.exit(1)


def detect_platform() -> tuple[str, str]:
    """(os, arch) guessed from the running machine."""
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        os_name = "darwin"
    elif sys_name == "windows":
        os_name = "windows"
    else:
        os_name = "linux"
    mach = platform.machine().lower()
    if mach in ("x86_64", "amd64", "x64"):
        arch = "amd64"
    elif mach in ("aarch64", "arm64"):
        arch = "arm64"
    elif mach in ("armv7l", "armv6l", "arm"):
        arch = "arm"
    else:
        arch = "amd64"
    return os_name, arch


def latest_release() -> dict:
    url = f"https://api.github.com/repos/{AMELE_REPO}/releases/latest"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def find_asset(release: dict, os_name: str, arch: str) -> tuple[str, str]:
    """Return (asset_name, download_url) matching os/arch."""
    tag = release["tag_name"]
    for a in release.get("assets", []):
        name = a["name"]
        if name == "SHA256SUMS" or name.startswith("multiple."):
            continue
        m = re.match(rf"amele_{re.escape(tag.lstrip('v'))}_([a-z0-9]+)_([a-z0-9]+)\.", name)
        if not m:
            continue
        a_os, a_arch = m.group(1), m.group(2)
        # linux_arm (32-bit) must match exactly; don't let arm64 fall back to it
        if a_os == os_name and a_arch == arch:
            return name, a["browser_download_url"]
    fail(f"platform eşleşmesi yok: {os_name}_{arch} — amele release'lerine bakın")


def download(url: str, dest: Path, label: str = "") -> None:
    say(f"  ↓ indiriliyor {label or url.split('/')[-1]} …")
    req = urllib.request.Request(url, headers={"User-Agent": "kahya-installer"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(file: Path, sums_path: Path, asset_name: str) -> bool:
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        h, _, name = line.partition("  ")
        if name.strip() == asset_name:
            return h.strip().lower() == sha256(file)
    return False


def extract_amele(archive: Path, dest_dir: Path) -> Path:
    """Extract the amele binary; returns the binary path."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            member = next((n for n in z.namelist() if "amele" in n.lower()
                           and not n.endswith("/")), None)
            if not member:
                fail("zip içinde amele bulunamadı")
            z.extract(member, dest_dir)
            src = dest_dir / member
    else:
        with tarfile.open(archive, "r:gz") as t:
            member = next((m for m in t.getmembers()
                           if "amele" in m.name and m.isfile()), None)
            if not member:
                fail("arşiv içinde amele bulunamadı")
            t.extract(member, dest_dir)
            src = dest_dir / member.name
    binary = dest_dir / ("amele.exe" if os.name == "nt" else "amele")
    shutil.move(str(src), binary)
    if os.name != "nt":
        binary.chmod(0o755)
    return binary


def install_amele(os_name: str, arch: str, force: bool) -> Path:
    bin_dir = ROOT / "bin"
    bin_dir.mkdir(exist_ok=True)
    binary = bin_dir / ("amele.exe" if os.name == "nt" else "amele")
    if binary.exists() and not force:
        say(f"  · amele zaten var: {binary} (--force ile yeniden indirin)", "dim")
        return binary

    say("  · en son amele sürümü aranıyor …")
    release = latest_release()
    asset_name, asset_url = find_asset(release, os_name, arch)
    say(f"  · seçim: {asset_name}")

    tmp = ROOT / ".install-tmp"
    tmp.mkdir(exist_ok=True)
    sums = tmp / "SHA256SUMS"
    archive = tmp / asset_name
    download(asset_url, archive)
    download(f"https://github.com/{AMELE_REPO}/releases/download/"
             f"{release['tag_name']}/SHA256SUMS", sums)
    if not verify_checksum(archive, sums, asset_name):
        shutil.rmtree(tmp, ignore_errors=True)
        fail("SHA256 doğrulaması BAŞARISIZ — indirme güvenilir değil, durduruldu")
    say("  · SHA256 doğrulandı ✓", "green")
    extract_amele(archive, tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    say(f"  · amele kuruldu: {binary}", "green")
    return binary


def ensure_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        return
    example = ROOT / ".env.example"
    if example.exists():
        shutil.copy(example, env)
        say("  · .env oluşturuldu (.env.example'dan) — panelden doldurun", "dim")
    else:
        env.write_text("", encoding="utf-8")


def port_test(preferred: int) -> int:
    """Return a free port: preferred, else the next free one."""
    for port in [preferred, *PORT_TRIES]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            return port
        except OSError:
            pass
        finally:
            s.close()
    fail("boş port bulunamadı (8080-8099 arası dolu) — başka port belirtin")


def write_port(port: int) -> None:
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    key = "KAHYA_WEB_PORT"
    for i, ln in enumerate(lines):
        if ln.startswith(key + "="):
            lines[i] = f"{key}={port}"
            break
    else:
        lines.append(f"\n# installer tarafından seçilen port\n{key}={port}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # route discovery only, no traffic
        return s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def main() -> None:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)
    force = "--force" in args

    say()
    say("╔══════════════════════════════════════════════╗", "bold")
    say("║   🧑💼 Kâhya — kurulum sihirbazı              ║", "bold")
    say("║   Loxigo Software · https://www.loxigo.com    ║", "dim")
    say("╚══════════════════════════════════════════════╝")
    say()

    # 1. Python check
    v = sys.version_info
    if v < (3, 10):
        fail(f"Python 3.10+ gerekli, mevcut: {v.major}.{v.minor}.{v.micro}")
    say(f"  · Python {v.major}.{v.minor}.{v.micro} ✓")

    # 2. platform selection
    det_os, det_arch = detect_platform()
    default_key = next(
        (k for k, (label, o, a) in ENVIRONMENTS.items() if o == det_os and a == det_arch),
        "1")
    say(f"\n  Algılanan ortam: {ENVIRONMENTS[default_key][0]}")
    say("  Ortam seçin (Enter = algılanan):")
    for k, (label, *_rest) in ENVIRONMENTS.items():
        mark = " →" if k == default_key else "  "
        say(f"    {mark} {k}) {label}")
    choice = input("  Seçim: ").strip()
    key = choice if choice in ENVIRONMENTS else default_key
    label, os_name, arch = ENVIRONMENTS[key]
    say(f"  · ortam: {label}", "bold")

    # 3. amele
    say("\n  [1/4] amele kuruluyor …")
    binary = install_amele(os_name, arch, force)
    try:
        out = __import__("subprocess").run(
            [str(binary), "version"], capture_output=True, text=True, timeout=30)
        say(f"  · {out.stdout.strip() or out.stderr.strip()}", "green")
    except Exception:
        say("  · sürüm doğrulanamadı, ama binary yerinde", "yellow")

    # 4. .env
    say("\n  [2/4] .env hazırlanıyor …")
    ensure_env()

    # 5. port test
    say("\n  [3/4] port testi …")
    preferred = DEFAULT_PORT
    env = ROOT / ".env"
    if env.exists():
        m = re.search(r"^KAHYA_WEB_PORT=(\d+)", env.read_text(encoding="utf-8"), re.M)
        if m:
            preferred = int(m.group(1))
    chosen = port_test(preferred)
    if chosen != preferred:
        write_port(chosen)
        say(f"  · {preferred} dolu → {chosen} seçildi ve .env'e yazıldı", "yellow")
    else:
        say(f"  · {chosen} boş ✓", "green")

    # 6. summary
    ip = lan_ip()
    say("\n  [4/4] tamam!")
    say()
    say("╔══════════════════════════════════════════════════════╗", "bold")
    say("║  ✅ Kâhya kuruldu!                                    ║", "bold")
    say("╠══════════════════════════════════════════════════════╣")
    say(f"║  Panel:  http://{ip}:{chosen}                        ")
    say("║  Giriş:  admin / kahya123  (panelden değiştirin)      ")
    say("╠══════════════════════════════════════════════════════╣")
    say("║  Başlat (her biri ayrı terminal):                     ")
    say("║    python3 -m kahya.bot                               ")
    say("║    python3 -m kahya.scheduler                         ")
    say("║    python3 -m kahya.server                            ")
    say("╚══════════════════════════════════════════════════════╝")
    say()
    say("  Sıradaki adımlar: panelden LLM endpoint + Telegram token'ı", "dim")
    say("  ayarlayın; yönlendirilmiş komutlar deploy/ klasöründe.", "dim")
    say()


if __name__ == "__main__":
    main()
