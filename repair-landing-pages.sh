#!/usr/bin/env bash
# Updates only the relay binary that serves public-site files.
set -Eeuo pipefail
umask 077

die() { echo "ERROR: $*" >&2; exit 1; }
[[ ${EUID} -eq 0 ]] || die "Run as root."
TPROXY_REF="52a5feb7fac38f68da5afef9cedd9b3bfc8473ca"

relay_context() {
    DOMAIN="$(python3 -c 'import json; print(json.load(open("/etc/tproxy-server/config.json"))["public_hostname"])' 2>/dev/null || true)"
    CSS_PY="$(mktemp /tmp/onyx-css-path.XXXXXX.py)"
    write_css_probe
    CSS_PATH="$(python3 "$CSS_PY" 2>/dev/null || true)"
    rm -f "$CSS_PY"
}

write_css_probe() {
    cat > "$CSS_PY" <<'PY'
import re
from pathlib import Path
try:
    source=Path('/srv/tproxy-site/index.html').read_text(encoding='utf-8')
    match=re.search(r'href=["\'](/panel-site(?:-[a-f0-9]{12})?\.css)["\']',source,re.I)
    if match:
        print(match.group(1))
    else:
        candidates=sorted(Path('/srv/tproxy-site').glob('*.css'))
        print('/'+candidates[0].name if candidates else '')
except Exception:
    pass
PY
}

# A previous interrupted update may already have installed and verified this
# pinned relay. Do not download, rebuild or restart it again when all public
# checks pass; the panel/protocol update can then continue offline from GitHub.
relay_context
if systemctl is-active --quiet tproxy-server.service &&
   [[ -n "$DOMAIN" && -n "$CSS_PATH" ]] &&
   [[ "$(curl -fsS --max-time 3 http://127.0.0.1:8081/healthz 2>/dev/null || true)" == "ok" ]] &&
   [[ "$(curl -fsS --max-time 3 http://127.0.0.1:8081/readyz 2>/dev/null || true)" == "ready" ]] &&
   curl -kfsSI --max-time 12 "https://${DOMAIN}${CSS_PATH}" 2>/dev/null | grep -qi '^content-type: text/css'; then
    echo "Relay and public CSS are already healthy; relay rebuild skipped."
    exit 0
fi

# The relay is never built on the server: the release ships a prebuilt binary
# (assets/tproxy-server-linux-amd64). Install it and verify it serves; there is
# no source fetch or compilation fallback any more.
RELAY_BIN_SOURCE="$(cd "$(dirname "$0")" && pwd)/assets/tproxy-server-linux-amd64"
if [[ ! -s "$RELAY_BIN_SOURCE" ]]; then
    die "Prebuilt tproxy-server binary is missing from assets/ (release bundle was not fetched). The updater no longer builds the relay."
fi

echo "Installing the prebuilt relay binary..."
install -o root -g root -m 0755 "$RELAY_BIN_SOURCE" /usr/local/bin/tproxy-server
systemctl try-restart tproxy-server.service 2>/dev/null || systemctl restart tproxy-server.service

relay_context
echo "Testing the relay after installing the prebuilt binary..."
if ! curl -fsS --max-time 3 http://127.0.0.1:8081/healthz 2>/dev/null | grep -q '^ok$' ||
   ! curl -fsS --max-time 3 http://127.0.0.1:8081/readyz 2>/dev/null | grep -q '^ready$'; then
    die "Relay did not become healthy after installing the prebuilt binary."
fi
if [[ -n "$CSS_PATH" ]]; then
    curl -kfsSI --max-time 12 "https://${DOMAIN}${CSS_PATH}" | grep -qi '^content-type: text/css' ||
        die "Relay still does not serve the current CSS file."
fi
echo "Relay update completed successfully."
