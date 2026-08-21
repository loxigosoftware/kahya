#!/usr/bin/env python3
"""Kahya — one-command cross-platform installer.

Usage:
    python3 install.py            # scan → proposal list → your approval → apply
    python3 install.py --force    # re-download amele even if present
    python3 install.py --yes      # approve every proposal without asking
    python3 install.py --dry-run  # scan + show the proposal list, change nothing

What it does:
  1. checks the Python version (3.9+); if too old, prints the exact
     install command for your platform/distro and exits
  2. detects the platform and offers an explicit environment list
     (the amele asset list — linux, macOS, Windows, Raspberry Pi, …)
  3. scans the machine: amele binary (MCP rule — an MCP-less binary is
     rejected outright, no approval dialog), .env, Node.js, ffmpeg,
     leftover installer temp files
  4. shows an automatic proposal list and asks for YOUR approval —
     nothing is installed, replaced or removed without it
  5. applies only the approved items (amele, .env, optional installs);
     the web panel port is picked automatically (no dialog), systemd
     auto-start is offered as a single question at the end
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
import struct
import socket
import subprocess
import sys
from typing import Optional
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AMELE_REPO = "loxigosoftware/amele-builds"
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
    """Return (asset_name, download_url) matching os/arch.

    amele-builds assets are plain binaries:
        amele-linux-amd64, amele-linux-arm64, amele-linux-arm,
        amele-darwin-amd64, amele-darwin-arm64,
        amele-windows-amd64.exe, amele-windows-arm64.exe
    """
    for a in release.get("assets", []):
        name = a["name"]
        if name == "SHA256SUMS" or name.startswith("multiple."):
            continue
        m = re.match(r"amele-([a-z0-9]+)-([a-z0-9]+)(\.exe)?$", name)
        if not m:
            continue
        a_os, a_arch = m.group(1), m.group(2)
        # linux_arm (32-bit) must match exactly; don't let arm64 fall back to it
        if a_os == os_name and a_arch == arch:
            return name, a["browser_download_url"]
    fail(f"no amele asset matches {os_name}_{arch} — check the "
         f"{AMELE_REPO} release list")


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


def elf_arch(path: Path) -> str | None:
    """Mimariyi ELF e_machine alanından okur: 'amd64' | 'arm64' | 'arm'.

    ELF değilse (bozuk dosya, PE, script) None döner. Bu, yanlış
    mimarideki bir binary'nin "mevcut ve MCP ✓" sayılıp Pi'de
    'Permission denied' / Exec format error üretmesini önler.
    """
    try:
        data = path.read_bytes()[:20]
        if data[:4] != b"\x7fELF":
            return None
        e_machine = struct.unpack("<H", data[18:20])[0]
        return {62: "amd64", 183: "arm64", 40: "arm"}.get(e_machine)
    except Exception:
        return None


def check_amele_mcp(binary: Path) -> bool:
    """Does the binary support MCP?

    First look for the embedded help text (cross-platform — an arm64
    binary cannot be executed on x86); if it runs, confirm via
    `amele mcp --help`. Project rule: no MCP, no amele (user decision) —
    the installer will not continue with an MCP-less binary.
    """
    try:
        data = binary.read_bytes()
        if b"amele mcp login" not in data and b"mcp login|status|logout" not in data:
            return False
    except Exception:
        return False
    try:
        proc = subprocess.run([str(binary), "mcp", "--help"],
                              capture_output=True, text=True, timeout=10)
        return proc.returncode == 0 and "login" in (proc.stdout + proc.stderr)
    except Exception:
        return False


def install_amele(os_name: str, arch: str, force: bool) -> Path:
    """Fetch the MCP-capable amele binary from loxigosoftware/amele-builds.

    Project rule (user decision): no MCP, no amele. An existing binary
    without MCP support stops the installer — this is a rule rejection,
    not an approval question.
    """
    bin_dir = ROOT / "bin"
    bin_dir.mkdir(exist_ok=True)
    binary = bin_dir / ("amele.exe" if os.name == "nt" else "amele")
    if binary.exists() and not force:
        existing = elf_arch(binary)
        if existing is not None and existing != arch:
            say(f"  · existing binary is {existing}, but this platform needs "
                f"{arch} — downloading the correct build", "yellow")
        elif not check_amele_mcp(binary):
            fail("existing amele binary has NO MCP support (`amele mcp` "
                 "missing). Project rule: no MCP, no amele. Remove "
                 "bin/amele and re-run — the installer will fetch an MCP "
                 "build from loxigosoftware/amele-builds")
        else:
            if os.name != "nt":
                binary.chmod(0o755)  # guarantee execute bit on existing binaries
            say(f"  · amele already present: {binary} (MCP ✓, use --force to "
                f"re-download)", "dim")
            return binary

    say("  · looking up the latest amele release (loxigosoftware/amele-builds) …")
    release = latest_release()
    asset_name, asset_url = find_asset(release, os_name, arch)
    say(f"  · chosen asset: {asset_name}")

    tmp = ROOT / ".install-tmp"
    tmp.mkdir(exist_ok=True)
    sums = tmp / "SHA256SUMS"
    asset = tmp / asset_name
    download(asset_url, asset)
    download(f"https://github.com/{AMELE_REPO}/releases/download/"
             f"{release['tag_name']}/SHA256SUMS", sums)
    if not verify_checksum(asset, sums, asset_name):
        shutil.rmtree(tmp, ignore_errors=True)
        fail("SHA256 verification FAILED — the download is not trustworthy, "
             "aborting")
    say("  · SHA256 verified ✓", "green")
    shutil.move(str(asset), binary)  # assets are plain binaries
    if os.name != "nt":
        binary.chmod(0o755)
    shutil.rmtree(tmp, ignore_errors=True)
    if not check_amele_mcp(binary):
        binary.unlink(missing_ok=True)
        fail(f"release {release['tag_name']} has NO MCP support (`amele mcp` "
             f"missing). Project rule: no MCP, no amele.")
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


def install_systemd(preapproved: bool = False) -> None:
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
    if not preapproved:
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
    yes = "--yes" in args
    dry_run = "--dry-run" in args

    say()
    say("╔══════════════════════════════════════════════╗", "bold")
    say("║   🧑💼 Kahya — setup wizard                     ║", "bold")
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
    if dry_run or yes:
        key = default_key
        say(f"  · using detected environment: {ENVIRONMENTS[key][0]}"
            + (" (--dry-run)" if dry_run else " (--yes)"), "dim")
    else:
        say("  Select an environment (Enter = detected):")
        for k, (label, *_rest) in ENVIRONMENTS.items():
            mark = " →" if k == default_key else "  "
            say(f"    {mark} {k}) {label}")
        choice = input("  Choice: ").strip()
        key = choice if choice in ENVIRONMENTS else default_key
    label, os_name, arch = ENVIRONMENTS[key]
    say(f"  · environment: {label}", "bold")

    # 3. system scan → automatic proposal → user approval list
    env_file = ROOT / ".env"
    preferred = DEFAULT_PORT
    if env_file.exists():
        m = re.search(r"^KAHYA_WEB_PORT=(\d+)",
                      env_file.read_text(encoding="utf-8"), re.M)
        if m:
            preferred = int(m.group(1))
    say("\n  [system scan] looking at what is already on this machine …")
    items = scan_system(os_name, arch, force)
    chosen = confirm_items(items, yes=yes, dry_run=dry_run)
    if dry_run:
        say("\n  --dry-run: nothing was changed. Re-run without --dry-run to "
            "apply the approved items.", "dim")
        return

    # 4. apply the approved items
    say()
    apply_items(items, chosen, os_name, arch, force)

    # 5. web panel port — automatic (no dialog): preferred, else next free
    free_port = port_test(preferred)
    if free_port != preferred:
        write_port(free_port)
        say(f"  · port {preferred} is busy — using {free_port} "
            f"(written to .env)", "yellow")
    else:
        say(f"  · port {free_port} is free — using it", "dim")

    # 6. auto-start: single question at the end (Linux/systemd only)
    install_systemd(preapproved=yes)

    # 7. summary
    ip = lan_ip()
    say("\n  ✅ done!")
    say()
    say("╔══════════════════════════════════════════════════════╗", "bold")
    say("║  ✅ Kahya is installed!                               ║", "bold")
    say("╠══════════════════════════════════════════════════════╣")
    say(f"║  Panel:  http://{ip}:{free_port}                    ")
    say("║  Login:  admin / kahya123  (change it from the panel)  ")
    say("╠══════════════════════════════════════════════════════╣")
    say("║  Start (each in its own terminal):                    ")
    say("║    python3 -m kahya.bot                               ")
    say("║    python3 -m kahya.scheduler                         ")
    say("║    python3 -m kahya.server                            ")
    say("╚══════════════════════════════════════════════════════╝")
    say()
    say("  Next steps: set the LLM endpoint + Telegram token from the panel;", "dim")
    say("  auto-start (systemd) is generated by the installer — examples in "
        "deploy/.", "dim")
    say()


# ---------------------------------------------------------------------------
# system scan → automatic proposal → approval list
# ---------------------------------------------------------------------------

def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def apt_install(pkg: str) -> str:
    """Distro-appropriate package install command (shown in the approval list)."""
    if which("apt-get"):
        return f"sudo apt-get install -y {pkg}"
    if which("dnf"):
        return f"sudo dnf install -y {pkg}"
    if which("pacman"):
        return f"sudo pacman -S --noconfirm {pkg}"
    return f"sudo apt-get install -y {pkg}"  # fallback; user may fix it


def scan_system(os_name: str, arch: str, force: bool) -> list[dict]:
    """System scan — what is already on the machine + automatic proposals.

    Each proposal is a dict: id, critical (default approval Y), title,
    detail, command (shell command to run if approved) or action (custom
    callable). Nothing is done without this list being approved.

    NOT in the approval list (automatic): web panel port (a free port is
    picked automatically). Rule rejection: an MCP-less amele binary stops
    the installer immediately (no approval question — user decision).
    """
    items = []

    # -- amele binary + MCP rule (critical) --
    binary = ROOT / "bin" / ("amele.exe" if os.name == "nt" else "amele")
    if binary.exists() and elf_arch(binary) not in (None, arch):
        items.append({"id": "amele", "critical": True, "status": "✗",
                      "title": f"amele binary (wrong arch: "
                               f"{elf_arch(binary)}, need {arch})",
                      "detail": f"{binary} — will re-download the "
                                f"correct build",
                      "command": None, "action": None})
    elif binary.exists() and check_amele_mcp(binary):
        items.append({"id": "amele", "critical": True, "status": "✓",
                      "title": "amele binary (MCP ✓)",
                      "detail": f"{binary} — ready",
                      "command": None, "action": None})
    elif binary.exists():
        a = elf_arch(binary)
        hint = (f" binary is {a}, this platform needs {arch} — remove "
                f"bin/amele and re-run, the installer will fetch the "
                f"correct build" if a else
                f" — remove bin/amele and re-run; the installer will "
                f"fetch an MCP build from loxigosoftware/amele-builds")
        fail("existing amele binary is not usable on this platform" + hint)
    else:
        items.append({"id": "amele", "critical": True, "status": "✗",
                      "title": "amele binary missing",
                      "detail": "MCP build will be installed "
                                "(loxigosoftware/amele-builds release)",
                      "command": None, "action": lambda: None})

    # -- .env --
    if (ROOT / ".env").exists():
        items.append({"id": "env", "critical": True, "status": "✓",
                      "title": ".env", "detail": "ready (kept untouched)",
                      "command": None, "action": None})
    else:
        items.append({"id": "env", "critical": True, "status": "○",
                      "title": ".env missing",
                      "detail": "will be created from .env.example",
                      "command": None, "action": ensure_env})

    # -- Node.js (MCP stdio servers) --
    if which("node"):
        items.append({"id": "node", "critical": False, "status": "✓",
                      "title": "Node.js",
                      "detail": which("node") or "installed",
                      "command": None, "action": None})
    else:
        cmd = apt_install("nodejs")
        items.append({"id": "node", "critical": False, "status": "○",
                      "title": "Node.js missing",
                      "detail": "recommended for MCP stdio servers "
                                "(npx @smithery/cli) — will install: " + cmd,
                      "command": cmd, "action": None})

    # -- ffmpeg (amele tools) --
    if which("ffmpeg"):
        items.append({"id": "ffmpeg", "critical": False, "status": "✓",
                      "title": "ffmpeg", "detail": which("ffmpeg") or "installed",
                      "command": None, "action": None})
    else:
        cmd = apt_install("ffmpeg")
        items.append({"id": "ffmpeg", "critical": False, "status": "○",
                      "title": "ffmpeg missing",
                      "detail": "recommended for amele tools (audio/video) — "
                                "will install: " + cmd,
                      "command": cmd, "action": None})

    # -- installer leftovers --
    tmp = ROOT / ".install-tmp"
    if tmp.exists():
        items.append({"id": "cleanup", "critical": False, "status": "!",
                      "title": ".install-tmp leftovers",
                      "detail": "stale installer temp files — will be removed",
                      "command": None,
                      "action": lambda: shutil.rmtree(tmp, ignore_errors=True)})

    return items


def confirm_items(items: list[dict], yes: bool = False,
                  dry_run: bool = False) -> list[dict]:
    """Show the proposal list and collect user approval.

    Critical items default to YES, recommendations default to NO.
    Shortcuts: a = approve all · n = approve none · q = quit.
    --yes: approve everything without asking (explicit request).
    """
    say()
    say("  ── system scan ─────────────────────────────────────", "bold")
    for i, it in enumerate(items, 1):
        mark = {"✓": "✓", "✗": "✗", "○": "○", "!": "!", "—": "—"}[it["status"]]
        default_answer = "Y" if it["critical"] else "n"
        say(f"  [{i}] {mark} {it['title']}")
        say(f"      {it['detail']}")
        if dry_run:
            say(f"      → if approved: {default_answer} ({'will be applied' if (it['command'] or it['action']) else 'ready — no action'})", "dim")
    say("  ────────────────────────────────────────────────────", "bold")
    if dry_run:
        return []

    chosen = []
    if yes:
        chosen = [it for it in items
                  if it["command"] or it["action"] or it["status"] in ("✗", "!", "○")]
        say("  --yes: all actionable items approved.", "dim")
        return chosen

    for i, it in enumerate(items, 1):
        if not (it["command"] or it["action"] or it["status"] in ("✗", "!")):
            continue  # ready items need no approval
        default_answer = "Y" if it["critical"] else "n"
        prompt = f"  [{i}] {it['title']} — apply? [{'Y' if it['critical'] else 'y'}/{'n' if it['critical'] else 'N'}] "
        ans = input(prompt).strip().lower()
        if ans in ("a", "all"):
            chosen = [x for x in items if x["command"] or x["action"]
                      or x["status"] in ("✗", "!", "○")]
            say("  · everything approved.", "green")
            return chosen
        if ans in ("n", "no"):
            say("  · nothing else approved.", "dim")
            return chosen
        if ans in ("q", "quit"):
            say("  · cancelled by user.", "yellow")
            sys.exit(1)
        if ans in ("", "y", "yes") and it["critical"]:
            chosen.append(it)
        elif ans in ("y", "yes") or (ans == "" and not it["critical"]):
            # empty Enter on recommendations = default NO
            if ans in ("y", "yes"):
                chosen.append(it)
        else:
            say("  · skipped.", "dim")
    return chosen


def apply_items(items: list[dict], chosen: list[dict], os_name: str,
                arch: str, force: bool) -> None:
    """Apply the approved proposals in order. Every step came from the
    user's approval list; nothing runs without approval."""
    step = 0

    def mark_step(it):
        nonlocal step
        step += 1
        say(f"\n  [{step}] {it['title']} …")

    for it in chosen:
        if it["id"] == "amele":
            mark_step(it)
            install_amele(os_name, arch, force)
            try:
                out = subprocess.run(
                    [str(ROOT / "bin" / ("amele.exe" if os.name == "nt"
                                         else "amele")), "version"],
                    capture_output=True, text=True, timeout=30)
                say(f"  · {out.stdout.strip() or out.stderr.strip()}", "green")
            except Exception:
                say("  · could not verify the version, but the binary is in "
                    "place", "yellow")
        elif it["id"] == "env":
            mark_step(it)
            ensure_env()
            say("  · .env ready", "green")
        elif it["command"]:
            mark_step(it)
            say(f"  · running: {it['command']}", "dim")
            proc = subprocess.run(it["command"], shell=True)
            if proc.returncode == 0:
                say("  · done ✓", "green")
            else:
                say(f"  · failed (exit {proc.returncode}) — continue anyway, "
                    f"you can install it later", "yellow")
        elif it.get("action"):
            mark_step(it)
            it["action"]()
            say("  · done ✓", "green")


if __name__ == "__main__":
    main()
