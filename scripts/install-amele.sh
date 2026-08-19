#!/usr/bin/env bash
# Install the amele binary into ./bin (repo root).
# Usage: ./scripts/install-amele.sh [version]
set -euo pipefail

VERSION="${1:-v0.1.1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/bin"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  armv7l|armv6l) ARCH=arm ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 1 ;;
esac

# Raspberry Pi OS (32-bit) is armv7l → linux_arm
FILE="amele_${VERSION#v}_${OS}_${ARCH}.tar.gz"
URL="https://github.com/lasthumanintheloop/amele/releases/download/${VERSION}/${FILE}"

mkdir -p "$DEST"
echo "→ downloading $URL"
curl -sL -o "$DEST/amele.tar.gz" "$URL"
tar xzf "$DEST/amele.tar.gz" -C "$DEST" amele
rm -f "$DEST/amele.tar.gz"
chmod +x "$DEST/amele"
echo "→ installed: $DEST/amele"
"$DEST/amele" version
