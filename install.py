#!/usr/bin/env python3
"""Kahya — one-command cross-platform installer.

Usage:
    python3 install.py            # detect platform, install, port-test
    python3 install.py --force    # re-download amele even if present
    python3 install.py --help

What it does:
  1. checks the Python version (3.9+); if too old, prints the exact
     install command for your platform/distro and exits
  2. detects the platform and offers an explicit environment list
     (the amele asset list — linux, macOS, Windows, Raspberry Pi, …)
  3. downloads the amele binary for that platform from GitHub and
     verifies it against SHA256SUMS
  4. creates .env from .env.example if missing
  5. port-tests the web panel port (8080 by default); if taken, picks
     the next free one and writes it to .env
  6. prints the LAN address and first-login credentials

Stdlib only — runs on Linux, macOS, Windows, Raspberry Pi.
"""
from __future__ import annotations

import hashlib
import getpass
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AMELE_REPO = "lasthumanintheloop/amele"
MIN_PYTHON = (3, 9)
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


def detect_distro() -> tuple[str, str]:
    """(id, version_id) from /etc/os-release — e.g. ('raspbian', '11')."""
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
        d = {}
        for ln in lines:
            if "=" in ln:
                k, _, val = ln.partition("=")
                d[k] = val.strip('"')
        return d.get("ID", ""), d.get("VERSION_ID", "")
    except Exception:
        return "", ""


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


def python_fix_hint(v) -> None:
    """Print the exact command to get a supported Python on this machine."""
    say()
    say(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required — this system has "
        f"{v.major}.{v.minor}.{v.micro}.", "red")
    say("Install a newer Python, then run this installer again with that "
        "interpreter.", "red")
    say()

    # already on PATH? shortest fix wins
    for cand in ("python3.13", "python3.12", "python3.11", "python3.10", "python3.9"):
        if shutil.which(cand):
            say(f"  A newer interpreter is already installed: {cand}")
            say(f"    → just run:  {cand} install.py", "green")
            say()
            sys.exit(1)

    sys_name = platform.system().lower()
    if sys_name == "darwin":
        say("  macOS — install via Homebrew:")
        say("    brew install python@3.12", "green")
        say("    python3.12 install.py")
    elif os.name == "nt":
        say("  Windows — install via winget:")
        say("    winget install Python.Python.3.12", "green")
        say("    py install.py")
    else:
        distro, ver = detect_distro()
        say(f"  Detected OS: {distro or 'linux'} {ver or ''}")
        if distro in ("debian", "raspbian") and ver.startswith(("11", "10", "9", "8")):
            say("  This Debian release ships Python ≤ 3.9 and has no newer "
                "python3.x in its repos.")
            say("  Use pyenv (builds Python in your home dir, no sudo):")
            say("    sudo apt update", "green")
            say("    sudo apt install -y build-essential libssl-dev zlib1g-dev "
                "libbz2-dev libreadline-dev libsqlite3-dev libffi-dev "
                "liblzma-dev tk-dev curl git", "green")
            say("    curl https://pyenv.run | bash", "green")
            say("    exec $SHELL && pyenv install 3.12 && pyenv local 3.12", "green")
            say("    python3 install.py")
        elif distro in ("debian", "raspbian"):
            say("  Install a newer Python via apt:")
            say("    sudo apt update && sudo apt install -y python3.11", "green")
            say("    python3.11 install.py")
        elif distro == "ubuntu" and ver.startswith(("20.04", "18.04")):
            say("  Ubuntu's default Python is too old here. Options:")
            say("    sudo add-apt-repository ppa:deadsnakes/ppa && "
                "sudo apt update && sudo apt install -y python3.11", "green")
            say("    python3.11 install.py")
            say("  …or use pyenv (no sudo):  curl https://pyenv.run | bash")
        elif distro == "fedora":
            say("    sudo dnf install -y python3.11", "green")
            say("    python3.11 install.py")
        elif distro in ("centos", "rhel", "rocky", "almalinux"):
            say("    sudo dnf install -y python3.11", "green")
            say("    python3.11 install.py")
        elif distro == "arch":
            say("    sudo pacman -S python", "green")
            say("    python3 install.py")
        elif distro == "alpine":
            say("    sudo apk add python3", "green")
            say("    python3 install.py")
        else:
            say("  Generic fallback — pyenv (builds Python in your home dir):")
            say("    curl https://pyenv.run | bash", "green")
            say("    exec $SHELL && pyenv install 3.12 && pyenv local 3.12", "green")
            say("    python3 install.py")
    say()
    sys.exit(1)


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
    fail(f"no amele asset matches {os_name}_{arch} — check the release list")


def download(url: str, dest: Path, label: str = "") -> None:
    say(f"  ↓ downloading {label or url.split('/')[-1]} …")
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
                fail("amele not found inside the zip archive")
            z.extract(member, dest_dir)
            src = dest_dir / member
    else:
        with tarfile.open(archive, "r:gz") as t:
            member = next((m for m in t.getmembers()
                           if "amele" in m.name and m.isfile()), None)
            if not member:
                fail("amele not found inside the archive")
            if sys.version_info >= (3, 12):
                t.extract(member, dest_dir, filter="data")
            else:
                t.extract(member, dest_dir)
            src = dest_dir / member.name
    binary = dest_dir / ("amele.exe" if os.name == "nt" else "amele")
    shutil.move(str(src), binary)
    if os.name != "nt":
        binary.chmod(0o755)
    return binary


def check_amele_mcp(binary: Path) -> bool:
    """Indirilen binary MCP destekliyor mu? (Step 8 — `amele mcp` komutu)."""
    try:
        proc = subprocess.run([str(binary), "mcp", "--help"],
                              capture_output=True, text=True, timeout=10)
        return proc.returncode == 0 and "login" in (proc.stdout + proc.stderr)
    except Exception:
        return False


def install_amele(os_name: str, arch: str, force: bool) -> Path:
    bin_dir = ROOT / "bin"
    bin_dir.mkdir(exist_ok=True)
    binary = bin_dir / ("amele.exe" if os.name == "nt" else "amele")
    if binary.exists() and not force:
        if not check_amele_mcp(binary):
            say(f"  · WARNING: {binary} is an old build WITHOUT MCP support "
                f"(`amele mcp` missing). Re-run with --force after a release "
                f"with MCP ships, or copy a freshly built binary from the "
                f"amele repo (commit 415f781+).", "red")
        else:
            say(f"  · amele already present: {binary} (use --force to re-download)", "dim")
        return binary

    say("  · looking up the latest amele release …")
    release = latest_release()
    asset_name, asset_url = find_asset(release, os_name, arch)
    say(f"  · chosen asset: {asset_name}")

    tmp = ROOT / ".install-tmp"
    tmp.mkdir(exist_ok=True)
    sums = tmp / "SHA256SUMS"
    archive = tmp / asset_name
    download(asset_url, archive)
    download(f"https://github.com/{AMELE_REPO}/releases/download/"
             f"{release['tag_name']}/SHA256SUMS", sums)
    if not verify_checksum(archive, sums, asset_name):
        shutil.rmtree(tmp, ignore_errors=True)
        fail("SHA256 verification FAILED — the download is not trustworthy, "
             "aborting")
    say("  · SHA256 verified ✓", "green")
    extract_amele(archive, bin_dir)
    shutil.rmtree(tmp, ignore_errors=True)
    if not check_amele_mcp(binary):
        say(f"  · WARNING: this amele release has no MCP support (`amele mcp` "
            f"missing) — MCP features (Step 8) need a build from the amele "
            f"repo at commit 415f781 or later.", "red")
    else:
        say(f"  · amele installed: {binary} (MCP ✓)", "green")
    return binary


def ensure_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        return
    example = ROOT / ".env.example"
    if example.exists():
        shutil.copy(example, env)
        say("  · .env created from .env.example — fill it in from the panel", "dim")
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
    fail("no free port in 8080–8099 — free one or set KAHYA_WEB_PORT yourself")


def write_port(port: int) -> None:
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    key = "KAHYA_WEB_PORT"
    for i, ln in enumerate(lines):
        if ln.startswith(key + "="):
            lines[i] = f"{key}={port}"
            break
    else:
        lines.append(f"\n# port chosen by the installer\n{key}={port}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sync_port_to_db(port)


def sync_port_to_db(port: int) -> None:
    """Keep an existing DB in sync with the chosen port.

    The panel reads web_port from the settings DB first — a stale row
    would silently override the .env value and break the panel port
    after a reinstall (seen on a Pi: DB said 8080, .env said 8081,
    service failed with 'Address already in use').
    """
    db_path = ROOT / "data" / "kahya.db"
    if not db_path.exists():
        return
    try:
        import sqlite3
        con = sqlite3.connect(db_path)
        try:
            if con.execute("SELECT 1 FROM settings WHERE key='web_port'").fetchone():
                con.execute("UPDATE settings SET value=? WHERE key='web_port'",
                            (str(port),))
                con.commit()
                say(f"  · web_port synced in existing DB ({port})", "dim")
        finally:
            con.close()
    except Exception as e:
        say(f"  · could not sync DB port ({e})", "dim")


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


SERVICES = {  # unit name → module
    "kahya-web": "kahya.server",
    "kahya-bot": "kahya.bot",
    "kahya-scheduler": "kahya.scheduler",
}


def install_systemd() -> None:
    """Offer auto-start via systemd (Linux only, needs sudo).

    Generates three units from the *current* user/python path — no
    hardcoded /home/pi — and enables them with Restart=on-failure, so a
    crash is brought back automatically.
    """
    if os.name == "nt" or platform.system().lower() == "darwin":
        say("  · auto-start skipped: systemd is Linux-only "
            "(macOS/Windows must start services manually)", "dim")
        return
    if not Path("/run/systemd/system").exists() or not shutil.which("systemctl"):
        say("  · auto-start skipped: systemd not present "
            "(start services manually, see summary)", "dim")
        return
    ans = input("  Auto-start services via systemd (needs sudo)? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        say("  · skipped — start services manually (see summary below)", "dim")
        return

    user = getpass.getuser()
    py = sys.executable
    tmp = ROOT / ".install-tmp"
    tmp.mkdir(exist_ok=True)
    try:
        for name, module in SERVICES.items():
            unit = (
                "[Unit]\n"
                f"Description=Kahya {name.split('-')[1].capitalize()}\n"
                "After=network-online.target\n"
                "Wants=network-online.target\n"
                "\n"
                "[Service]\n"
                "Type=simple\n"
                f"User={user}\n"
                f"WorkingDirectory={ROOT}\n"
                f"ExecStart={py} -m {module}\n"
                "Restart=on-failure\n"
                "RestartSec=10\n"
                "\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n"
            )
            (tmp / f"{name}.service").write_text(unit, encoding="utf-8")
        for name in SERVICES:
            __import__("subprocess").run(
                ["sudo", "cp", str(tmp / f"{name}.service"),
                 f"/etc/systemd/system/{name}.service"], check=True)
        __import__("subprocess").run(["sudo", "systemctl", "daemon-reload"],
                                     check=True)
        __import__("subprocess").run(
            ["sudo", "systemctl", "enable", "--now", *SERVICES], check=True)
        say("  · services installed & started: "
            + ", ".join(SERVICES) + " (Restart=on-failure)", "green")
        say("    check: systemctl status kahya-web kahya-bot kahya-scheduler", "dim")
    except Exception as e:
        say(f"  · systemd install failed ({e}) — start services manually", "yellow")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)
    force = "--force" in args

    say()
    say("╔══════════════════════════════════════════════╗", "bold")
    say("║   🧑💼 Kâhya — setup wizard                    ║", "bold")
    say("║   Loxigo Software · https://www.loxigo.com    ║", "dim")
    say("╚══════════════════════════════════════════════╝")
    say()

    # 1. Python check
    v = sys.version_info
    if v < MIN_PYTHON:
        python_fix_hint(v)
    say(f"  · Python {v.major}.{v.minor}.{v.micro} ✓")

    # 2. platform selection
    det_os, det_arch = detect_platform()
    default_key = next(
        (k for k, (label, o, a) in ENVIRONMENTS.items() if o == det_os and a == det_arch),
        "1")
    say(f"\n  Detected environment: {ENVIRONMENTS[default_key][0]}")
    say("  Select an environment (Enter = detected):")
    for k, (label, *_rest) in ENVIRONMENTS.items():
        mark = " →" if k == default_key else "  "
        say(f"    {mark} {k}) {label}")
    choice = input("  Choice: ").strip()
    key = choice if choice in ENVIRONMENTS else default_key
    label, os_name, arch = ENVIRONMENTS[key]
    say(f"  · environment: {label}", "bold")

    # 3. amele
    say("\n  [1/4] installing amele …")
    binary = install_amele(os_name, arch, force)
    try:
        out = __import__("subprocess").run(
            [str(binary), "version"], capture_output=True, text=True, timeout=30)
        say(f"  · {out.stdout.strip() or out.stderr.strip()}", "green")
    except Exception:
        say("  · could not verify the version, but the binary is in place", "yellow")

    # 4. .env
    say("\n  [2/4] preparing .env …")
    ensure_env()

    # 5. port test
    say("\n  [3/4] port test …")
    preferred = DEFAULT_PORT
    env = ROOT / ".env"
    if env.exists():
        m = re.search(r"^KAHYA_WEB_PORT=(\d+)", env.read_text(encoding="utf-8"), re.M)
        if m:
            preferred = int(m.group(1))
    chosen = port_test(preferred)
    if chosen != preferred:
        write_port(chosen)
        say(f"  · {preferred} is taken → {chosen} chosen and written to .env", "yellow")
    else:
        say(f"  · {chosen} is free ✓", "green")

    # 6. auto-start (systemd)
    say("\n  [4/5] auto-start …")
    install_systemd()

    # 7. summary
    ip = lan_ip()
    say("\n  [5/5] done!")
    say()
    say("╔══════════════════════════════════════════════════════╗", "bold")
    say("║  ✅ Kâhya is installed!                              ║", "bold")
    say("╠══════════════════════════════════════════════════════╣")
    say(f"║  Panel:  http://{ip}:{chosen}                        ")
    say("║  Login:  admin / kahya123  (change it from the panel)  ")
    say("╠══════════════════════════════════════════════════════╣")
    say("║  Start (each in its own terminal):                    ")
    say("║    python3 -m kahya.bot                               ")
    say("║    python3 -m kahya.scheduler                         ")
    say("║    python3 -m kahya.server                            ")
    say("╚══════════════════════════════════════════════════════╝")
    say()
    say("  Next steps: set the LLM endpoint + Telegram token from the panel;", "dim")
    say("  supervised/auto-start units live in deploy/.", "dim")
    say()


if __name__ == "__main__":
    main()
