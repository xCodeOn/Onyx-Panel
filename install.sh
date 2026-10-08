#!/usr/bin/env bash
# One-command entry point for Onyx Panel.
#
# Two modes:
#   1. Run from a complete package directory (offline install from local files):
#          cd onyx-panel && ./install.sh
#   2. One-command remote install — downloads the latest stable release
#      straight from this repository and installs everything from it:
#          bash <(curl -fsSL https://raw.githubusercontent.com/xCodeOn/Onyx-Panel/main/install.sh)
#
# The panel modules (AmneziaWG binaries, OpenFlux) ship with the release;
# Xray, Caddy, Go, AmneziaWG tools and the relay source are downloaded from
# their official repositories during installation.
set -Eeuo pipefail
umask 077

die() { echo "ERROR: $*" >&2; exit 1; }
[[ ${EUID:-1} -eq 0 ]] || die "Run this command with sudo or as root."

REPO_OWNER="xCodeOn"
REPO_NAME="Onyx-Panel"
REPO_SLUG="${REPO_OWNER}/${REPO_NAME}"

# When launched from a complete package directory, run the real installer.
# With `bash <(curl ...)` there is no script file; never trust unrelated
# files from the caller's current directory in that mode.
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_SOURCE" && -f "$SCRIPT_SOURCE" ]]; then
    LOCAL_BASE="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
    if [[ -s "$LOCAL_BASE/install-final.sh" && -s "$LOCAL_BASE/onyx_subscriptions.py" &&
          -s "$LOCAL_BASE/assets/OpenFlux-linux-amd64" &&
          -s "$LOCAL_BASE/assets/amneziawg-go-linux-amd64" &&
          -s "$LOCAL_BASE/assets/awg-linux-amd64" &&
          -s "$LOCAL_BASE/assets/awg-quick-linux-amd64" ]]; then
        echo "Onyx Panel: installing from the local package in $LOCAL_BASE..."
        exec bash "$LOCAL_BASE/install-final.sh"
    fi
fi

# Remote mode: fetch the latest stable release tarball and install from it.
echo "Onyx Panel: fetching the latest stable release..."
command -v curl >/dev/null 2>&1 || die "curl is required."
command -v tar >/dev/null 2>&1 || die "tar is required."

LATEST="$(curl -fsS --proto '=https' --proto-redir '=https' --tlsv1.2 --max-time 20 \
    "https://api.github.com/repos/${REPO_SLUG}/releases/latest" 2>/dev/null |
    sed -n 's/.*"tag_name":[[:space:]]*"\(v[0-9][^"]*\)".*/\1/p' | head -n1 || true)"
[[ "$LATEST" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || LATEST="main"
echo "Onyx Panel: release ${LATEST}..."

WORK="$(mktemp -d /tmp/onyx-panel-install.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# codeload serves tags under refs/tags/; branches only under HEAD.
DL_REF="refs/tags/${LATEST}"
[[ "$LATEST" == "main" ]] && DL_REF="HEAD"

# Direct GitHub first, then mirrors (codeload form for gh-proxy.com, archive
# form for the rest) so installs work when GitHub is throttled or blocked.
ONYX_DL_OK=0
for dl in \
    "https://codeload.github.com/${REPO_SLUG}/tar.gz/${DL_REF}" \
    "https://gh-proxy.com/https://codeload.github.com/${REPO_SLUG}/tar.gz/${DL_REF}" \
    "https://ghproxy.net/https://github.com/${REPO_SLUG}/archive/${LATEST}.tar.gz" \
    "https://gh-proxy.com/https://github.com/${REPO_SLUG}/archive/${LATEST}.tar.gz" \
    "https://ghfast.top/https://github.com/${REPO_SLUG}/archive/${LATEST}.tar.gz"; do
    curl -fsS --proto '=https' --proto-redir '=https' --tlsv1.2 \
        --retry 2 --retry-all-errors --connect-timeout 20 --max-time 300 \
        -o "$WORK/onyx-panel.tar.gz" "$dl" && { ONYX_DL_OK=1; break; }
done
[[ "$ONYX_DL_OK" == 1 ]] ||
    die "Could not download the Onyx Panel release: GitHub и зеркала недоступны."
mkdir -p "$WORK/package"
tar -xzf "$WORK/onyx-panel.tar.gz" -C "$WORK/package" --strip-components=1 --no-same-owner

[[ -s "$WORK/package/install-final.sh" && -s "$WORK/package/onyx_ui.py" ]] ||
    die "The downloaded release package is incomplete. Try again later."
[[ -s "$WORK/package/assets/OpenFlux-linux-amd64" ]] ||
    die "The downloaded release package is incomplete: assets are missing."

echo "Onyx Panel ${LATEST}: starting installation..."
export ONYX_PANEL_VERSION="${LATEST#v}"
exec bash "$WORK/package/install-final.sh"
