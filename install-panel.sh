#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE="$(cd "$(dirname "$0")" && pwd)"

# Visual kit: banner, colored stages, explained red errors (see install-final.sh).
UI_LIB="${BASE}/onyx-install-ui.sh"
if [[ -s "$UI_LIB" ]]; then
    # shellcheck source=onyx-install-ui.sh
    . "$UI_LIB"
else
    R='' B='' DIM='' C_RED='' C_GREEN='' C_AMBER='' C_ACCENT='' C_BLUE='' C_VIOLET='' C_GREY='' C_WHITE=''
    UI_UTF8=0
    UI_HR="------------------------------------------------------------"
    ui_banner() { :; }
    ui_stage() { echo; echo "== $* =="; }
    ui_run_with_progress() { local message="$1"; shift; echo "  $message..."; "$@"; }
    ui_ok() { echo "  [ok] $*"; }
    ui_info() { echo "  $*"; }
    ui_warn() { echo "  WARNING: $*" >&2; }
    ui_err() { echo "  ERROR: $*" >&2; }
    ui_kv() { echo "  $1: $2"; }
    ui_explain() { :; }
    ui_die() { echo "ERROR: $*" >&2; exit 1; }
    ui_trap_error() { local c="$1"; trap - ERR; echo "ERROR: command failed (code $c, line ${BASH_LINENO[0]:-?})." >&2; exit "$c"; }
    ui_success_begin() { echo "== $* =="; }
    ui_success_end() { echo; }
    ONYX_GH_MIRRORS="${ONYX_GH_MIRRORS:-https://ghproxy.net https://gh-proxy.com https://ghfast.top}"
    onyx_fetch() {
        local out="$1" url="$2" candidate
        shift 2
        local -a candidates=("$url")
        if [[ "$url" == *github.com* || "$url" == *codeload.github.com* ]]; then
            local m
            for m in $ONYX_GH_MIRRORS; do candidates+=("${m%/}/${url}"); done
        fi
        candidates+=("$@")
        for candidate in "${candidates[@]}"; do
            curl --fail --silent --show-error --location                 --proto '=https' --proto-redir '=https' --tlsv1.2                 --retry 2 --retry-all-errors --connect-timeout 15 --max-time 180                 --output "$out" "$candidate" && return 0
            rm -f "$out"
        done
        return 1
    }
    onyx_apt_update_once() {
        local stamp="${ONYX_APT_STAMP:-/run/lock/onyx-apt-updated}"
        if [[ -f "$stamp" ]] && [[ -z "$(find "$stamp" -mmin +1440 2>/dev/null)" ]]; then
            return 0
        fi
        ui_run_with_progress "Обновление списка пакетов" apt-get -o DPkg::Lock::Timeout=600 update
        touch "$stamp"
    }
    onyx_git_fetch_pinned() {
        local dir="$1" repo="$2" ref="$3" mode="$4" commit="$5"
        if [[ ! -d "$dir/.git" ]]; then
            rm -rf "$dir"; mkdir -p "$dir"; git -C "$dir" init -q
        fi
        git -C "$dir" remote remove origin 2>/dev/null || true
        git -C "$dir" remote add origin "$repo"
        if [[ "$mode" == "tag" ]]; then
            git -C "$dir" fetch -q --depth 1 origin tag "$ref" &&
                git -C "$dir" checkout -q --detach FETCH_HEAD || true
        else
            git -C "$dir" fetch -q --depth 1 origin "$ref" &&
                git -C "$dir" checkout -q --detach --force FETCH_HEAD || true
        fi
        [[ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" == "$commit" ]] && return 0
        local tarball repo_path
        repo_path="${repo#https://github.com/}"; repo_path="${repo_path%.git}"
        tarball="$(mktemp /tmp/onyx-pinned-src.XXXXXX.tar.gz)"
        onyx_fetch "$tarball" "https://github.com/${repo_path}/archive/${commit}.tar.gz" || { rm -f "$tarball"; return 1; }
        rm -rf "$dir"; mkdir -p "$dir"
        tar -xzf "$tarball" -C "$dir" --strip-components=1 --no-same-owner
        rm -f "$tarball"
    }
    onyx_curl_shim_dir() { mkdir -p "$1"; }
fi
die() { ui_die "$@"; }
trap 'ui_trap_error $?' ERR
APP_DIR="/opt/onyx-panel"
DATA_DIR="/var/lib/onyx-panel"
DATA_FILE="${DATA_DIR}/data.json"
SERVICE_FILE="/etc/systemd/system/onyx-panel.service"
FIREWALL_SERVICE_FILE="/etc/systemd/system/onyx-panel-firewall.service"
APP_FILE="${APP_DIR}/panel.py"
# The version literal in the fallback below is synced automatically from the
# repo-root VERSION file by the release workflow on every push to main.
# Bump the version in the VERSION file only — never edit the literal here.
LOGO_SOURCE="${BASE}/onyx-logo.png"
LOGO_FILE="${APP_DIR}/onyx-logo.png"
FAVICON_SOURCE="${BASE}/onyx-favicon.png"
FAVICON_FILE="${APP_DIR}/onyx-favicon.png"
PORT=8090
DOMAIN="${ONYX_PANEL_DOMAIN:-$(sed -n 's/^Environment=TPROXY_HOSTNAME=//p' /etc/systemd/system/caddy.service.d/tproxy.conf 2>/dev/null | head -n1 || true)}"
ACME_EMAIL="${ONYX_PANEL_ACME_EMAIL:-$(sed -n 's/^Environment=ACME_EMAIL=//p' /etc/systemd/system/caddy.service.d/tproxy.conf 2>/dev/null | head -n1 || true)}"
MTPROTO_HOST="$(cat /etc/onyx-panel/mtproto-host 2>/dev/null || true)"
# The file is reused in an unquoted systemd-unit heredoc: accept only a plain
# IPv4 address, otherwise fall back to the panel domain.
if [[ ! "$MTPROTO_HOST" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    MTPROTO_HOST="$DOMAIN"
fi
PANEL_PATH="/panel-$(openssl rand -hex 16)"
UPDATING="${ONYX_PANEL_UPDATE:-0}"
# Set by update.sh when component pins are unchanged: keep the proxies
# running, skip the geo refresh and reload Caddy instead of restarting it.
FAST="${ONYX_PANEL_FAST:-0}"
MANIFEST="/etc/onyx-panel/manifest"
PRIMARY_SECRET="/etc/onyx-panel/primary-secret"
USERS_FILE="/etc/onyx-panel/users.json"
SECRETS_FILE="/etc/onyx-panel/mtproxy-secrets"
MANAGER="/usr/local/sbin/onyx-panelctl"
QR_BIN="/usr/bin/qrencode"
XRAY_ROOT="/opt/onyx-panel/xray"
XRAY_BIN="${XRAY_ROOT}/xray"
XRAY_CONFIG_DIR="/etc/onyx-panel-xray"
XRAY_CONFIG="${XRAY_CONFIG_DIR}/config.json"
XRAY_PATH_FILE="/etc/onyx-panel/xray-path"
XRAY_VERSION="26.7.28"
XRAY_SHA256="8195d909f1109b8f3d99eefe401a3c451d7bf4af71f24d3815420f77e5dd2a40"
HYSTERIA_PORT=8443
OPENFLUX_ROOT="/opt/onyx-panel/openflux"
OPENFLUX_BIN="${OPENFLUX_ROOT}/openflux"
OPENFLUX_VERSION="1.0.0"
OPENFLUX_SHA256="c90cb197e4ba7c288a55e864f707f53dc82695c6f66ebc42418aba5e05ad7d58"
OPENFLUX_BUNDLED="${BASE}/assets/OpenFlux-linux-amd64"
# v2.3.6 was published with the bundled binary at the repository root while
# the installer expected assets/. Accept both layouts so an in-place update
# never fails solely because of packaging.
if [[ ! -s "$OPENFLUX_BUNDLED" && -s "${BASE}/OpenFlux-linux-amd64" ]]; then
    OPENFLUX_BUNDLED="${BASE}/OpenFlux-linux-amd64"
fi
AWG_GO_VERSION="v3.1.20260828"
AWG_GO_COMMIT="b5928efb6ca19f0153958460c3d141f04abc5c2e"
AWG_GO_BUNDLED="${BASE}/assets/amneziawg-go-linux-amd64"
AWG_GO_SHA256="9b8912d203ba7142c1957913047bb9efd6e02c173c1f3bf852c652b16aada60f"
AWG_TOOLS_VERSION="v3.1.20260812"
AWG_TOOLS_SHA256="919e9d0a367c7c72f9c16b7d0a9e4840b943628353b2210a33cb4b582785ba56"
AWG_BIN_BUNDLED="${BASE}/assets/awg-linux-amd64"
AWG_BIN_SHA256="23d29323258166183eeeb288c1f9f08b879f3707e54591b1bfb2402413f6d8d8"
AWG_QUICK_BUNDLED="${BASE}/assets/awg-quick-linux-amd64"
AWG_QUICK_SHA256="f4bb0f5d63665ade87f0cb9f2185c43515cff09868637eb311f98f65a318722c"
AWG_TOOLS_BUNDLED="${BASE}/assets/amneziawg-tools-ubuntu-22.04.zip"
AWG_GO_SOURCE_BUNDLED="${BASE}/assets/amneziawg-go-b5928ef.tar.gz"
AWG_GO_SOURCE_SHA256="10bf7458e090bf52f87df27adcd3904a60e4d17fab844d9415e46379147f1ab4"

[[ $EUID -eq 0 ]] || die "Run as root."
. /etc/os-release
case "${ID:-}" in
    ubuntu)
        dpkg --compare-versions "${VERSION_ID:-0}" ge "22.04" ||
            die "Ubuntu 22.04 or newer is required."
        ;;
    debian)
        dpkg --compare-versions "${VERSION_ID:-0}" ge "12" ||
            die "Debian 12 or newer is required."
        ;;
    *)
        die "Supported systems: Ubuntu 22.04+ or Debian 12+."
        ;;
esac
echo "Platform: ${PRETTY_NAME:-${ID} ${VERSION_ID}}"
command -v python3 >/dev/null || die "python3 required."
command -v openssl >/dev/null || die "openssl required."
[[ "$(uname -m)" == "x86_64" ]] || die "x86_64 is required."

# Older releases did not always retain the Caddy systemd drop-in. Recover the
# hostname from other authoritative project files before asking the operator.
DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"; DOMAIN="${DOMAIN,,}"
if ! [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]]; then
    DOMAIN="$(sed -n 's/^Environment=ONYX_DOMAIN=//p' "$SERVICE_FILE" 2>/dev/null | head -n1 || true)"
fi
if ! [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]] && [[ -s /etc/tproxy-server/config.json ]]; then
    DOMAIN="$(python3 - /etc/tproxy-server/config.json <<'PY' 2>/dev/null || true
import json,sys
try:
    value=json.load(open(sys.argv[1],encoding="utf-8")).get("public_hostname","")
    print(value if isinstance(value,str) else "")
except Exception:
    pass
PY
)"
fi
if ! [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]] && [[ -s /etc/caddy/Caddyfile ]]; then
    DOMAIN="$(sed -n 's/^[[:space:]]*\([a-z0-9][a-z0-9.-]*\)[[:space:]]*{[[:space:]]*$/\1/p' /etc/caddy/Caddyfile |
        grep -vE '^(http|https|localhost)$' | head -n1 || true)"
fi
if ! [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]]; then
    [[ -t 0 ]] || die "The domain could not be recovered. Re-run with ONYX_PANEL_DOMAIN=proxy.example.com."
    ui_warn "Домен старой установки не найден автоматически."
    while true; do
        read -r -p "  ${C_ACCENT}${B}▸ Домен Onyx Panel${R}: " DOMAIN </dev/tty
        DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"; DOMAIN="${DOMAIN,,}"
        [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]] && break
        ui_err "Некорректный домен. Пример: proxy.example.com"
    done
fi
MTPROTO_HOST="${MTPROTO_HOST:-$DOMAIN}"
[[ -s "$PRIMARY_SECRET" ]] || die "Primary install-time secret not found."
[[ -s "$LOGO_SOURCE" ]] || die "Panel logo file is missing: onyx-logo.png"
[[ -s "$FAVICON_SOURCE" ]] || die "Panel favicon file is missing: onyx-favicon.png"
for PWA_ICON in onyx-logo-192 onyx-logo-512 onyx-logo-maskable; do
    [[ -s "$BASE/assets/${PWA_ICON}.png" ]] || die "PWA icon is missing: assets/${PWA_ICON}.png"
done
for module in panel.py onyx_adblock.py onyx_subscriptions.py onyx_panel_extras.py onyx_i18n.py onyx_ui.py onyx_metrics.py onyx_update.py onyx_webpush.py onyx_nodes.py onyx_openflux.py onyx_awg.py onyx_firewall.py onyx_components.py onyx_cascade.py onyx_routing.py onyx_warp.py onyx_reality.py onyx_telegram.py onyx_totp.py onyx_access.py onyx_webapi.py onyx_failover.py onyx_audit.py onyx_limits.py onyx_cloud.py onyx_bypass.py onyx_censor.py onyx_defense.py onyx_promo.py; do
    [[ -s "$BASE/$module" ]] || die "Missing panel module: $module; extract the complete archive."
done
FLAG_ARCHIVE="$BASE/onyx-panel/flags.tar.gz"
[[ -s "$FLAG_ARCHIVE" ]] ||
    die "Panel flag bundle is missing; extract the complete archive."

# An update keeps the existing private panel address.  A new address would
# make an otherwise successful update look like a broken panel to its owner.
if [[ "$UPDATING" == "1" ]]; then
    [[ -s "$DATA_FILE" ]] || die "Existing panel data was not found. Run the full installer instead."
    EXISTING_PATH="$(sed -n 's/^Environment=ONYX_PANEL_PATH=//p' "$SERVICE_FILE" 2>/dev/null | head -n1 || true)"
    [[ "$EXISTING_PATH" =~ ^/[a-z0-9][a-z0-9-]{2,58}[a-z0-9]$ ]] || die "Existing panel address was not found. Run the full installer instead."
    PANEL_PATH="$EXISTING_PATH"
fi

echo "      Preparing AmneziaWG 2.0 / 3.1..."
AWG_INSTALLED_NOW=0
if ! command -v ip >/dev/null 2>&1; then
    onyx_apt_update_once
    ui_run_with_progress "Установка системных пакетов" apt-get -o DPkg::Lock::Timeout=600 install -y --no-install-recommends iproute2
fi
if [[ ! -x /usr/local/bin/amneziawg-go ]] || ! /usr/local/bin/amneziawg-go --version 2>&1 | grep -Fq "$AWG_GO_VERSION"; then
    [[ -s "$AWG_GO_BUNDLED" ]] && echo "${AWG_GO_SHA256}  ${AWG_GO_BUNDLED}" | sha256sum -c - >/dev/null ||
        die "Prebuilt AmneziaWG core binary is missing or damaged in assets/ (release bundle was not fetched). The installer no longer builds amneziawg-go on the server."
    install -o root -g root -m 0755 "$AWG_GO_BUNDLED" /usr/local/bin/amneziawg-go
    AWG_INSTALLED_NOW=1
fi
if [[ ! -x /usr/local/bin/awg ]] || ! /usr/local/bin/awg --version 2>&1 | grep -Fq "${AWG_TOOLS_VERSION#v}"; then
    [[ -s "$AWG_BIN_BUNDLED" && -s "$AWG_QUICK_BUNDLED" ]] &&
        echo "${AWG_BIN_SHA256}  ${AWG_BIN_BUNDLED}" | sha256sum -c - >/dev/null &&
        echo "${AWG_QUICK_SHA256}  ${AWG_QUICK_BUNDLED}" | sha256sum -c - >/dev/null ||
        die "Prebuilt AmneziaWG tools binaries are missing or damaged in assets/ (release bundle was not fetched). The installer no longer downloads/builds tools on the server."
    install -o root -g root -m 0755 "$AWG_BIN_BUNDLED" /usr/local/bin/awg
    install -o root -g root -m 0755 "$AWG_QUICK_BUNDLED" /usr/local/bin/awg-quick
    AWG_INSTALLED_NOW=1
fi
/usr/local/bin/awg --version >/dev/null || die "AmneziaWG tools verification failed."
if [[ "$AWG_INSTALLED_NOW" == 1 ]]; then
    : > /etc/onyx-panel/awg-owned
    chmod 0600 /etc/onyx-panel/awg-owned
fi
install -d -o root -g root -m 0700 /etc/onyx-panel/awg
cat > /usr/local/sbin/onyx-panel-awg-run <<'AWGRUN'
#!/usr/bin/env python3
import json,os,re,sys
uid=sys.argv[1] if len(sys.argv)>1 else ""
if not re.fullmatch(r"[a-f0-9]{16}",uid): raise SystemExit("invalid AWG profile id")
with open("/etc/onyx-panel/awg/"+uid+".json",encoding="ascii") as handle: meta=json.load(handle)
iface=str(meta.get("interface",""))
if not re.fullmatch(r"wa[a-f0-9]{11}",iface): raise SystemExit("invalid AWG interface")
os.execv("/usr/local/bin/amneziawg-go",["amneziawg-go","-f",iface])
AWGRUN
cat > /usr/local/sbin/onyx-panel-awg-up <<'AWGUP'
#!/usr/bin/env python3
import json,os,re,subprocess,sys,time
uid=sys.argv[1] if len(sys.argv)>1 else ""
if not re.fullmatch(r"[a-f0-9]{16}",uid): raise SystemExit("invalid AWG profile id")
base="/etc/onyx-panel/awg/"+uid
with open(base+".json",encoding="ascii") as handle: meta=json.load(handle)
iface=str(meta.get("interface","")); address=str(meta.get("address","")); mtu=int(meta.get("mtu",1280))
if not re.fullmatch(r"wa[a-f0-9]{11}",iface): raise SystemExit("invalid AWG interface")
if not 1024 <= mtu <= 1420: raise SystemExit("invalid AWG MTU")
for _ in range(80):
    if os.path.exists("/var/run/amneziawg/"+iface+".sock") or os.path.exists("/sys/class/net/"+iface): break
    time.sleep(.1)
else: raise SystemExit("AWG interface did not appear")
subprocess.run(["/usr/local/bin/awg","setconf",iface,base+".conf"],check=True)
subprocess.run(["/usr/sbin/ip","address","replace",address,"dev",iface],check=True)
subprocess.run(["/usr/sbin/ip","link","set","mtu",str(mtu),"up","dev",iface],check=True)
AWGUP
chmod 0755 /usr/local/sbin/onyx-panel-awg-run /usr/local/sbin/onyx-panel-awg-up
cat > /etc/systemd/system/onyx-panel-awg@.service <<'EOF'
[Unit]
Description=Onyx Panel independent AmneziaWG profile %i
After=network-online.target onyx-panel-firewall.service
Wants=network-online.target
Requires=onyx-panel-firewall.service

[Service]
Type=simple
User=root
Group=root
UMask=0077
Environment=WG_PROCESS_FOREGROUND=1
Environment=LOG_LEVEL=error
ExecStartPre=-/usr/local/sbin/onyx-panel-awg-down %i
ExecStart=/usr/local/sbin/onyx-panel-awg-run %i
ExecStartPost=/usr/local/sbin/onyx-panel-awg-up %i
ExecStopPost=-/usr/local/sbin/onyx-panel-awg-down %i
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
cat > /usr/local/sbin/onyx-panel-awg-down <<'AWGDOWN'
#!/usr/bin/env python3
import json,os,re,subprocess,sys
uid=sys.argv[1] if len(sys.argv)>1 else ""
if not re.fullmatch(r"[a-f0-9]{16}",uid): raise SystemExit(0)
try:
    with open("/etc/onyx-panel/awg/"+uid+".json",encoding="ascii") as handle: iface=str(json.load(handle).get("interface",""))
except Exception: iface="wa"+uid[:11]
if re.fullmatch(r"wa[a-f0-9]{11}",iface): subprocess.run(["/usr/sbin/ip","link","delete",iface],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
AWGDOWN
chmod 0755 /usr/local/sbin/onyx-panel-awg-down
# Retire the shared-interface AWG preview. Existing records are migrated by
# panelctl init to independent profiles with new ports and fingerprints.
for legacy in onyx-panel-awg20.service onyx-panel-awg31.service; do
    systemctl disable --now "$legacy" 2>/dev/null || true
    rm -f -- "/etc/systemd/system/$legacy"
done
cat > /etc/sysctl.d/90-onyx-panel-awg.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
chmod 0644 /etc/sysctl.d/90-onyx-panel-awg.conf
/usr/sbin/sysctl -p /etc/sysctl.d/90-onyx-panel-awg.conf >/dev/null

if ! [[ "$ACME_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] && [[ -s /etc/caddy/Caddyfile ]]; then
    ACME_EMAIL="$(sed -n 's/^[[:space:]]*email[[:space:]][[:space:]]*\([^[:space:]]*\)[[:space:]]*$/\1/p' /etc/caddy/Caddyfile | head -n1 || true)"
fi
if ! [[ "$ACME_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
    ui_warn "Email Caddy ACME не найден или неверен."
    read -r -p "  ${C_ACCENT}${B}▸ Email для Let's Encrypt${R}: " ACME_EMAIL </dev/tty
    [[ "$ACME_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] ||
        die "Invalid ACME email."
fi

# Keep one canonical source for the menu, future updates and Caddy itself.
install -d -m 0755 /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/tproxy.conf <<EOF
[Service]
Environment=TPROXY_HOSTNAME=$DOMAIN
Environment=TPROXY_SITE_ROOT=/srv/tproxy-site
Environment=ACME_EMAIL=$ACME_EMAIL
ReadWritePaths=/etc/caddy
EOF
chmod 0644 /etc/systemd/system/caddy.service.d/tproxy.conf

export DEBIAN_FRONTEND=noninteractive
if ! command -v qrencode >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1 || ! command -v xz >/dev/null 2>&1 || ! command -v unbound >/dev/null 2>&1; then
    onyx_apt_update_once
    ui_run_with_progress "Установка системных пакетов" apt-get -o DPkg::Lock::Timeout=600 install -y --no-install-recommends qrencode unzip xz-utils unbound unbound-anchor
fi

# Unbound — локальный DoT/DoH-резолвер для обхода DNS-отравления провайдером.
# Служба ставится, но включается только по маркеру /etc/onyx-panel/unbound.enabled
# (панельная вкладка «Обход и защита»); до тех пор Xray резолвит системным
# резолвером, как раньше. Преднастроенный root-hints и кэш в tmpfs.
if ! grep -q '^# Onyx Panel unbound' /etc/unbound/unbound.conf 2>/dev/null; then
    cat >> /etc/unbound/unbound.conf <<'UNBOUND_EOF'

# Onyx Panel unbound
server:
    interface: 127.0.0.1
    interface: ::1
    port: 53
    do-ip4: yes
    do-ip6: yes
    do-udp: yes
    do-tcp: yes
    prefer-ip6: no
    access-control: 127.0.0.0/8 allow
    access-control: ::1 allow
    hide-identity: yes
    hide-version: yes
    qname-minimisation: yes
    minimal-responses: yes
    prefetch: yes
    cache-max-ttl: 3600
    num-threads: 2
    so-reuseport: yes
    rrset-cache-size: 64m
    msg-cache-size: 64m
    local-zone: "." transparent
forward-zone:
    name: "."
    forward-tls-upstream: yes
    forward-addr: 1.1.1.1@853#cloudflare-dns.com
    forward-addr: 1.0.0.1@853#cloudflare-dns.com
    forward-addr: 9.9.9.9@853#dns.quad9.net
    forward-addr: 149.112.112.112@853#dns.quad9.net
UNBOUND_EOF
fi
systemctl enable unbound >/dev/null 2>&1 || true
systemctl restart unbound >/dev/null 2>&1 || true

# The adblock drop-in lives in unbound.conf.d; make sure the main config
# actually includes that directory. The guard must ignore the commented-out
# stock line (# include-toplevel: …) some images ship.
if ! grep -qE '^[[:space:]]*include-toplevel:.*unbound\.conf\.d' /etc/unbound/unbound.conf 2>/dev/null; then
    printf '\n# Onyx Panel drop-ins\ninclude-toplevel: "/etc/unbound/unbound.conf.d/*.conf"\n' \
        >> /etc/unbound/unbound.conf
fi

install -d -m 0755 "$APP_DIR" /etc/onyx-panel
install -d -m 0700 "$DATA_DIR"
install -d -m 0755 "$APP_DIR/icons"
install -o root -g root -m 0644 "$LOGO_SOURCE" "$LOGO_FILE"
install -o root -g root -m 0644 "$LOGO_SOURCE" "$APP_DIR/panel-logo.png"
install -o root -g root -m 0644 "$FAVICON_SOURCE" "$FAVICON_FILE"
for PWA_ICON in onyx-logo-192 onyx-logo-512 onyx-logo-maskable; do
    install -o root -g root -m 0644 "${BASE}/assets/${PWA_ICON}.png" "$APP_DIR/icons/${PWA_ICON}.png"
done
chmod 0600 "$PRIMARY_SECRET"

echo "      Preparing Xray ${XRAY_VERSION}..."
if ! getent group xray >/dev/null 2>&1; then
    groupadd --system xray
    : > /etc/onyx-panel/xray-group-owned
    chmod 0600 /etc/onyx-panel/xray-group-owned
fi
if ! id xray >/dev/null 2>&1; then
    useradd --system --gid xray --home /var/lib/onyx-panel-xray --create-home --shell /usr/sbin/nologin xray
    : > /etc/onyx-panel/xray-user-owned
    chmod 0600 /etc/onyx-panel/xray-user-owned
else
    usermod -a -G xray xray
fi
install -d -o root -g root -m 0755 "$XRAY_ROOT"
install -d -o root -g xray -m 0750 "$XRAY_CONFIG_DIR"
install -d -o root -g xray -m 0750 "${XRAY_CONFIG_DIR}/tls"
install -d -o xray -g xray -m 0750 /var/lib/onyx-panel-xray
if [[ -x "$XRAY_BIN" && -s "${XRAY_ROOT}/version" ]]; then
    echo "      Xray $(cat "${XRAY_ROOT}/version") установлен менеджером компонентов — сохраняем версию."
elif [[ ! -x "$XRAY_BIN" ]] || ! "$XRAY_BIN" version 2>/dev/null | grep -q "${XRAY_VERSION}"; then
    XRAY_ARCHIVE="$(mktemp /tmp/onyx-panel-xray.XXXXXX.zip)"
    XRAY_UNPACK="$(mktemp -d /tmp/onyx-panel-xray.XXXXXX)"
    XRAY_BUNDLED="${BASE}/assets/Xray-linux-64.zip"
    [[ -s "$XRAY_BUNDLED" ]] ||
        die "Prebuilt Xray archive is missing from assets/ (release bundle was not fetched). The installer no longer downloads Xray on the server."
    echo "      Using Xray included with this release."
    cp "$XRAY_BUNDLED" "$XRAY_ARCHIVE"
    echo "${XRAY_SHA256}  ${XRAY_ARCHIVE}" | sha256sum -c - >/dev/null || die "Xray checksum verification failed."
    unzip -q "$XRAY_ARCHIVE" xray -d "$XRAY_UNPACK"
    install -o root -g root -m 0755 "$XRAY_UNPACK/xray" "$XRAY_BIN"
    XRAY_ARCHIVE_KEPT="$XRAY_ARCHIVE"
    rm -rf "$XRAY_UNPACK"
    printf '%s\n' "v${XRAY_VERSION}" > "${XRAY_ROOT}/version"
    chmod 0644 "${XRAY_ROOT}/version"
fi

# The routing tab builds geoip:/geosite: rules, so the geo databases must sit
# next to the binary. The binary block above is skipped when the version is
# already current, hence this unconditional refresh — except on the fast
# panel-only path, where the databases are already verified to be in place.
if [[ "$FAST" == 1 && -s "${XRAY_ROOT}/geoip.dat" && -s "${XRAY_ROOT}/geosite.dat" ]]; then
    echo "      Geo databases already in place — refresh skipped (fast update)."
else
    XRAY_GEO_TMP="$(mktemp -d /tmp/onyx-panel-geo.XXXXXX)"
    GEO_ZIPPED=0
    if [[ -s "${BASE}/assets/Xray-linux-64.zip" ]]; then
        echo "      Using geo databases included with this release."
        unzip -q -o "${BASE}/assets/Xray-linux-64.zip" geoip.dat geosite.dat -d "$XRAY_GEO_TMP" && GEO_ZIPPED=1
    elif [[ -n "${XRAY_ARCHIVE_KEPT:-}" && -s "$XRAY_ARCHIVE_KEPT" ]]; then
        echo "      Using geo databases from the bundled Xray archive."
        unzip -q -o "$XRAY_ARCHIVE_KEPT" geoip.dat geosite.dat -d "$XRAY_GEO_TMP" && GEO_ZIPPED=1
        rm -f "$XRAY_ARCHIVE_KEPT"
    else
        die "Xray geo databases are missing — the bundled Xray archive was not fetched."
    fi
    if [[ "$GEO_ZIPPED" == 1 ]]; then
        install -o root -g root -m 0644 "$XRAY_GEO_TMP/geoip.dat" "$XRAY_GEO_TMP/geosite.dat" "$XRAY_ROOT/"
    else
        echo "      WARNING: geo databases unavailable; routing presets using geoip:/geosite: will not load." >&2
    fi
    rm -rf "$XRAY_GEO_TMP"
fi

echo "      Preparing OpenFlux ${OPENFLUX_VERSION}..."
if ! id onyx-openflux >/dev/null 2>&1; then
    if [[ -d /var/lib/onyx-openflux ]]; then
        useradd --system --home-dir /var/lib/onyx-openflux --no-create-home --shell /usr/sbin/nologin onyx-openflux
    else
        useradd --system --home-dir /var/lib/onyx-openflux --create-home --shell /usr/sbin/nologin onyx-openflux
    fi
fi
install -d -o root -g root -m 0755 "$OPENFLUX_ROOT"
if [[ -x "$OPENFLUX_BIN" && -s "$OPENFLUX_ROOT/version" ]]; then
    echo "      OpenFlux $(cat "$OPENFLUX_ROOT/version") установлен менеджером компонентов — сохраняем версию."
elif [[ ! -x "$OPENFLUX_BIN" ]] || ! sha256sum "$OPENFLUX_BIN" | grep -q "^${OPENFLUX_SHA256}  "; then
    [[ -s "$OPENFLUX_BUNDLED" ]] ||
        die "Prebuilt OpenFlux binary is missing from assets/ (release bundle was not fetched). The installer no longer downloads OpenFlux on the server."
    echo "      Using OpenFlux included with this release."
    echo "${OPENFLUX_SHA256}  ${OPENFLUX_BUNDLED}" | sha256sum -c - >/dev/null || die "OpenFlux checksum verification failed."
    install -o root -g root -m 0755 "$OPENFLUX_BUNDLED" "$OPENFLUX_BIN"
    printf '%s\n' "$OPENFLUX_VERSION" > "$OPENFLUX_ROOT/version"
    chmod 0644 "$OPENFLUX_ROOT/version"
else
    # The bundled binary is already in place without a component stamp — mark it.
    printf '%s\n' "$OPENFLUX_VERSION" > "$OPENFLUX_ROOT/version"
    chmod 0644 "$OPENFLUX_ROOT/version"
fi
# The OpenFlux binary cannot report its own version — the components manager
# reads this file; component installs rewrite it with the release tag.

# Remove only blocks managed by the former experimental NaiveProxy integration.
# The distribution Caddy binary is retained and used again after this migration.
if [[ -s /etc/caddy/Caddyfile ]]; then
    python3 - /etc/caddy/Caddyfile "$DOMAIN" <<'PY'
from pathlib import Path
import re,sys
p=Path(sys.argv[1]); domain=sys.argv[2]; source=p.read_text(encoding="utf-8")
source=re.sub(r"\n?[ \t]*# ONYX NAIVE GLOBAL BEGIN\n.*?\n[ \t]*# ONYX NAIVE GLOBAL END\n?","\n",source,flags=re.S)
source=re.sub(r"\n?[ \t]*# ONYX NAIVE BEGIN\n.*?\n[ \t]*# ONYX NAIVE END\n?","\n",source,flags=re.S)
legacy_address = re.compile(
    r"(?m)^(?P<indent>\s*)(?:"
    r":443\s*,\s*" + re.escape(domain) +
    r"|" + re.escape(domain) + r"\s*,\s*:443"
    r"|https://" + re.escape(domain) + r"(?::443)?"
    r"|" + re.escape(domain) + r":443)\s*\{\s*$"
)
source=legacy_address.sub(lambda m:m.group("indent")+domain+" {",source,count=1)
tmp=p.with_suffix(".onyx-stable.tmp"); tmp.write_text(source,encoding="utf-8")
tmp.chmod(0o640); tmp.replace(p)
PY
    chown root:caddy /etc/caddy/Caddyfile
fi
command -v caddy >/dev/null 2>&1 || die "Caddy is missing. Run the full installer to restore it."
cat > /etc/systemd/system/caddy.service.d/tproxy.conf <<EOF
[Service]
Environment=TPROXY_HOSTNAME=$DOMAIN
Environment=TPROXY_SITE_ROOT=/srv/tproxy-site
Environment=ACME_EMAIL=$ACME_EMAIL
ReadWritePaths=/etc/caddy
EOF
chmod 0644 /etc/systemd/system/caddy.service.d/tproxy.conf
if [[ ! -s "$XRAY_PATH_FILE" ]]; then
    printf '/vless-%s\n' "$(openssl rand -hex 12)" > "$XRAY_PATH_FILE"
fi
chmod 0600 "$XRAY_PATH_FILE"
XRAY_PATH="$(cat "$XRAY_PATH_FILE")"
[[ "$XRAY_PATH" =~ ^/vless-[a-f0-9]{24}$ ]] || die "Stored VLESS path is invalid."

if [[ "$UPDATING" == "1" ]]; then
    ui_stage "Обновление Onyx Panel"
else
    ui_stage "Настройка Onyx Panel"
fi
INSTALL_CREDENTIALS="/etc/onyx-panel/install-credentials"
if [[ "$UPDATING" == "1" ]]; then
    ADMIN="$(python3 - "$DATA_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    d=json.load(f)
admin=d.get("admin",{})
if not isinstance(admin.get("user"),str) or not admin.get("user") or not isinstance(admin.get("hash"),str) or not admin.get("hash"):
    raise SystemExit(1)
print(admin["user"])
PY
)" || die "Existing administrator data is invalid. Run the full installer instead."
    PASS=""
elif [[ -s "$INSTALL_CREDENTIALS" ]]; then
    ADMIN="$(sed -n '1p' "$INSTALL_CREDENTIALS")"
    PASS="$(sed -n '2p' "$INSTALL_CREDENTIALS")"
    rm -f "$INSTALL_CREDENTIALS"
    [[ -n "$ADMIN" && -n "$PASS" ]] || die "Panel credentials are invalid."
else
    read -r -p "  ${C_ACCENT}${B}▸ Логин администратора${R} ${C_GREY}[admin]${R}: " ADMIN </dev/tty
    ADMIN="${ADMIN:-admin}"
    while true; do
        read -r -s -p "  ${C_ACCENT}${B}▸ Пароль администратора${R}: " PASS </dev/tty
        echo
        [[ ${#PASS} -ge 3 ]] || { ui_warn "Пароль должен содержать минимум 3 символа."; continue; }
        break
    done
fi

ui_stage "Менеджер onyx-panelctl"

for module in panel.py onyx_adblock.py onyx_subscriptions.py onyx_panel_extras.py onyx_i18n.py onyx_ui.py onyx_metrics.py onyx_update.py onyx_webpush.py onyx_nodes.py onyx_openflux.py onyx_awg.py onyx_firewall.py onyx_components.py onyx_cascade.py onyx_routing.py onyx_warp.py onyx_reality.py onyx_telegram.py onyx_totp.py onyx_access.py onyx_webapi.py onyx_failover.py onyx_audit.py onyx_limits.py onyx_cloud.py onyx_bypass.py onyx_censor.py onyx_defense.py onyx_promo.py; do
    [[ -s "$BASE/$module" ]] || die "Package is incomplete: $module is missing."
    install -o root -g root -m 0644 "$BASE/$module" "$APP_DIR/$module"
done
install -d -o root -g root -m 0755 "$APP_DIR/fonts"
for font in dashboard-sans-normal.woff2 dashboard-sans-semibold.woff2 manrope-cyrillic-wght-normal.woff2 manrope-latin-wght-normal.woff2 jetbrains-mono-cyrillic-wght-normal.woff2 jetbrains-mono-latin-wght-normal.woff2; do
    [[ -s "$BASE/fonts/$font" ]] || die "Bundled font is missing: fonts/$font."
    install -o root -g root -m 0644 "$BASE/fonts/$font" "$APP_DIR/fonts/$font"
done
command -v tar >/dev/null 2>&1 || die "tar is required to install panel assets."
FLAG_ENTRIES="$(tar -tzf "$FLAG_ARCHIVE")" || die "Panel flag bundle cannot be read."
if grep -Eq '(^|/)\.\.(/|$)|^/' <<<"$FLAG_ENTRIES"; then
    die "Panel flag bundle contains an unsafe path."
fi
rm -rf -- "$APP_DIR/flags"
tar -xzf "$FLAG_ARCHIVE" -C "$APP_DIR" --no-same-owner
[[ -s "$APP_DIR/flags/fi.svg" && -s "$APP_DIR/flags/un.svg" ]] ||
    die "Panel flag bundle is incomplete."
chown -R root:root "$APP_DIR/flags"
find "$APP_DIR/flags" -type d -exec chmod 0755 {} +
find "$APP_DIR/flags" -type f -exec chmod 0644 {} +

# The service is enabled once and guarded by ConditionPathExists. Until the
# administrator saves a document URL it stays inactive and opens no ports.
python3 - "$APP_DIR" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
import onyx_openflux
onyx_openflux.install_service()
onyx_openflux.restore_if_configured()
PY
systemctl enable onyx-panel-openflux.service >/dev/null

# Detect the VPS country and city once for a new/default location. A failed
# HTTPS lookup is non-fatal and preserves the existing administrator value.
python3 - "$APP_DIR" "${DATA_DIR}/location.json" <<'PY'
import os,sys
sys.path.insert(0,sys.argv[1])
import onyx_nodes
path=sys.argv[2]
current=onyx_nodes.load_location(path)
placeholder=current.get("country_code")=="UN" or current.get("name") in ("Основная локация","Локация","Сервер")
if not os.path.exists(path) or placeholder:
    onyx_nodes.save_location(path,onyx_nodes.detect_location(current))
PY

[[ -s "$BASE/onyx-panelctl" ]] || die "Package is incomplete: onyx-panelctl is missing."
install -o root -g root -m 0755 "$BASE/onyx-panelctl" "$MANAGER"

cat > "$FIREWALL_SERVICE_FILE" <<'EOF'
[Unit]
Description=Onyx Panel persistent user-port firewall
After=nftables.service
PartOf=nftables.service
Before=network-online.target onyx-panel.service

[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=/usr/local/sbin/onyx-panelctl firewall
ExecReload=/usr/local/sbin/onyx-panelctl firewall
ExecStop=/bin/sh -c '/usr/sbin/nft delete table inet onyx_panel 2>/dev/null || true; /usr/sbin/nft delete table ip onyx_awg 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$FIREWALL_SERVICE_FILE"

cat > /etc/systemd/system/onyx-panel-traffic.service <<'EOF'
[Unit]
Description=Onyx Panel traffic collector
After=network-online.target onyx-panel-firewall.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/onyx-panelctl traffic
TimeoutStartSec=60
Nice=10
EOF

cat > /etc/systemd/system/onyx-panel-traffic.timer <<'EOF'
[Unit]
Description=Collect Onyx Panel traffic every 5 seconds

[Timer]
OnActiveSec=2s
OnUnitInactiveSec=5s
AccuracySec=1s
Unit=onyx-panel-traffic.service

[Install]
WantedBy=timers.target
EOF
chmod 0644 /etc/systemd/system/onyx-panel-traffic.service /etc/systemd/system/onyx-panel-traffic.timer

# Resource metrics must not depend on the success of the proxy Stats API.
cat > /etc/systemd/system/onyx-panel-metrics.service <<'EOF'
[Unit]
Description=Onyx Panel VPS metrics

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
ExecStart=/usr/bin/python3 /opt/onyx-panel/onyx_metrics.py collect
TimeoutStartSec=20
Nice=10
EOF
cat > /etc/systemd/system/onyx-panel-metrics.timer <<'EOF'
[Unit]
Description=Collect Onyx Panel VPS metrics every 10 seconds

[Timer]
OnActiveSec=5s
OnUnitInactiveSec=10s
AccuracySec=1s
Unit=onyx-panel-metrics.service

[Install]
WantedBy=timers.target
EOF
chmod 0644 /etc/systemd/system/onyx-panel-metrics.service /etc/systemd/system/onyx-panel-metrics.timer

cat > /usr/local/sbin/onyx-panel-sync-tls <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
MODE="${1:-sync}"
[[ "$MODE" == "sync" || "$MODE" == "--maintain" || "$MODE" == "--force" ]] || {
    echo "Usage: onyx-panel-sync-tls [--maintain|--force]" >&2
    exit 2
}
exec 8>/run/lock/onyx-panel-tls.lock
flock -w 15 8 || { echo "Another TLS check is still running" >&2; exit 1; }
DOMAIN="$(sed -n 's/^Environment=TPROXY_HOSTNAME=//p' /etc/systemd/system/caddy.service.d/tproxy.conf 2>/dev/null | head -n1 || true)"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || { echo "Invalid WEB Proxy domain" >&2; exit 1; }
DEST_DIR="/etc/onyx-panel-xray/tls"
DEST_CERT="$DEST_DIR/domain.crt"
DEST_KEY="$DEST_DIR/domain.key"
LIVE_CERT="$(mktemp /tmp/onyx-live-certificate.XXXXXX)"
trap 'rm -f "$LIVE_CERT"' EXIT

read_live_certificate() {
    : > "$LIVE_CERT"
    timeout 12 openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" </dev/null 2>/dev/null |
        openssl x509 -outform PEM -out "$LIVE_CERT" 2>/dev/null
}

if [[ "$MODE" != "sync" ]]; then
    needs_attention=0
    if ! read_live_certificate || ! openssl x509 -in "$LIVE_CERT" -noout -checkend 1209600 >/dev/null 2>&1; then
        needs_attention=1
    fi
    if [[ "$MODE" == "--force" || "$needs_attention" == 1 ]]; then
        caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
        # The canonical Caddyfile intentionally has `admin off`; SIGUSR1 is
        # Caddy's supported config reload signal for `caddy run` in this mode.
        if ! systemctl kill --signal=SIGUSR1 --kill-who=main caddy.service >/dev/null 2>&1; then
            systemctl restart caddy.service
        fi
        renewed=0
        for _ in $(seq 1 24); do
            if read_live_certificate && openssl x509 -in "$LIVE_CERT" -noout -checkend 0 >/dev/null 2>&1; then
                renewed=1
                break
            fi
            sleep 5
        done
        [[ "$renewed" == 1 ]] || { echo "Caddy did not provide a valid TLS certificate for $DOMAIN" >&2; exit 1; }
    fi
    read_live_certificate || { echo "Could not read the active TLS certificate for $DOMAIN" >&2; exit 1; }
    echo "TLS certificate: $(openssl x509 -in "$LIVE_CERT" -noout -enddate | cut -d= -f2-)"
fi

CADDY_USER="$(systemctl show -p User --value caddy.service 2>/dev/null || true)"
CADDY_USER="${CADDY_USER:-caddy}"
CADDY_HOME="$(getent passwd "$CADDY_USER" | cut -d: -f6 || true)"
SEARCH_ROOTS=(
    "${CADDY_HOME:-/var/lib/caddy}/.local/share/caddy/certificates"
    "/etc/caddy/caddy/.local/share/caddy/certificates"
    "/var/lib/caddy/.local/share/caddy/certificates"
    "/root/.local/share/caddy/certificates"
)
SOURCE_CERT=""
SOURCE_KEY=""
[[ "$MODE" == "sync" ]] || echo "Searching the Caddy certificate store..."
for root in "${SEARCH_ROOTS[@]}"; do
    [[ -d "$root" ]] || continue
    while IFS= read -r candidate; do
        key="${candidate%.crt}.key"
        [[ -s "$key" ]] || continue
        if openssl x509 -in "$candidate" -noout -checkend 86400 >/dev/null 2>&1; then
            SOURCE_CERT="$candidate"
            SOURCE_KEY="$key"
            break 2
        fi
    done < <(timeout 12 find "$root" -xdev -type f -path "*/${DOMAIN}/${DOMAIN}.crt" -print 2>/dev/null || true)
done
# Caddy can be started with a custom HOME/XDG_DATA_HOME by a pre-existing
# service.  Search only the known Caddy state directories as a bounded
# fallback; never scan the whole VPS.
if [[ -z "$SOURCE_CERT" ]]; then
    for root in /etc/caddy /var/lib/caddy /root/.local/share/caddy; do
        [[ -d "$root" ]] || continue
        while IFS= read -r candidate; do
            key="${candidate%.crt}.key"
            [[ -s "$key" ]] || continue
            if openssl x509 -in "$candidate" -noout -checkend 86400 >/dev/null 2>&1; then
                SOURCE_CERT="$candidate"
                SOURCE_KEY="$key"
                break 2
            fi
        done < <(timeout 12 find "$root" -xdev -type f -path "*/${DOMAIN}/${DOMAIN}.crt" -print 2>/dev/null || true)
    done
fi
[[ -n "$SOURCE_CERT" && -n "$SOURCE_KEY" ]] || { echo "Caddy TLS certificate for $DOMAIN was not found" >&2; exit 1; }
CERT_PUB="$(timeout 10 openssl x509 -in "$SOURCE_CERT" -pubkey -noout | openssl sha256)"
KEY_PUB="$(timeout 10 openssl pkey -in "$SOURCE_KEY" -pubout 2>/dev/null | openssl sha256)"
[[ -n "$CERT_PUB" && "$CERT_PUB" == "$KEY_PUB" ]] || { echo "Caddy certificate/private key mismatch" >&2; exit 1; }
install -d -o root -g xray -m 0750 "$DEST_DIR"
changed=0
if ! cmp -s "$SOURCE_CERT" "$DEST_CERT" 2>/dev/null; then
    install -o root -g xray -m 0640 "$SOURCE_CERT" "$DEST_CERT"
    changed=1
fi
if ! cmp -s "$SOURCE_KEY" "$DEST_KEY" 2>/dev/null; then
    install -o root -g xray -m 0640 "$SOURCE_KEY" "$DEST_KEY"
    changed=1
fi
if [[ "$changed" == 1 ]] && systemctl is-active --quiet onyx-panel-xray.service; then
    [[ "$MODE" == "sync" ]] || echo "Applying the renewed certificate to Hysteria2..."
    if [[ -x /opt/onyx-panel/xray/xray && -s /etc/onyx-panel-xray/config.json ]]; then
        /opt/onyx-panel/xray/xray run -test -config /etc/onyx-panel-xray/config.json >/dev/null 2>&1 || { echo "Xray config check failed, not restarting" >&2; exit 1; }
    fi
    timeout 45 systemctl try-restart onyx-panel-xray.service || {
        echo "Xray did not restart within 45 seconds" >&2
        exit 1
    }
fi
[[ "$MODE" == "sync" ]] || echo "TLS maintenance completed."
SH
chmod 0750 /usr/local/sbin/onyx-panel-sync-tls

cat > /etc/systemd/system/onyx-panel-sync-tls.service <<'EOF'
[Unit]
Description=Synchronize Caddy certificate for Onyx Panel Xray
After=caddy.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/onyx-panel-sync-tls --maintain
TimeoutStartSec=180
EOF

cat > /etc/systemd/system/onyx-panel-sync-tls.timer <<'EOF'
[Unit]
Description=Refresh Onyx Panel Xray TLS certificate

[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/onyx-panel-xray.service <<EOF
[Unit]
Description=Onyx Panel Xray (VLESS and Hysteria 2)
After=network-online.target caddy.service
Wants=network-online.target

[Service]
Type=simple
User=xray
Group=xray
ExecStart=$XRAY_BIN run -config $XRAY_CONFIG
Restart=on-failure
RestartSec=2
LimitNOFILE=1048576
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/onyx-panel-xray

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 /etc/systemd/system/onyx-panel-sync-tls.service /etc/systemd/system/onyx-panel-sync-tls.timer /etc/systemd/system/onyx-panel-xray.service

systemctl daemon-reload
systemctl enable --now onyx-panel-sync-tls.timer
if ! /usr/local/sbin/onyx-panel-sync-tls; then
    echo "      NOTE: Caddy certificate is not available to Xray yet; VLESS remains available, while Hysteria 2 can be created after certificate issuance."
fi

ui_stage "Код панели и модули"

# panel.py ships as a regular package file and is installed by the module
# loop above (APP_FILE lives in APP_DIR). The heredoc era is over.

# Embed the panel logo so /__logo always serves even if the file is missing
# (an in-place update from an installation that shipped panel-logo.png).
{ printf 'LOGO_EMBEDDED="%s"\n' "$(base64 -w 0 "$LOGO_SOURCE" 2>/dev/null || openssl base64 -A -in "$LOGO_SOURCE")"; printf 'FAVICON_EMBEDDED="%s"\n' "$(base64 -w 0 "$FAVICON_SOURCE" 2>/dev/null || openssl base64 -A -in "$FAVICON_SOURCE")"; cat "$APP_FILE"; } > "$APP_FILE.newpath" && mv "$APP_FILE.newpath" "$APP_FILE"

if [[ "$UPDATING" != "1" ]]; then
python3 - "${DATA_FILE}" "${ADMIN}" "${PASS}" <<'PY'
import base64,hashlib,json,os,secrets,sys

data_file,admin,password=sys.argv[1],sys.argv[2],sys.argv[3]
salt=secrets.token_bytes(16)
digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
data={
    "admin":{"user":admin,"hash":base64.b64encode(salt+digest).decode()},
    "site":{"html":"<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Система подключения</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#05070b;color:#fff;font:16px system-ui}.card{width:min(700px,88vw);padding:48px;text-align:center;border:1px solid #ffffff14;border-radius:28px;background:#101722e8;box-shadow:0 30px 100px #0009}.ok{color:#65efad}h1{font-size:clamp(34px,6vw,58px)}</style></head><body><div class=\"card\"><div class=\"ok\">● ONLINE</div><h1>Система подключения</h1><p>Безопасное соединение активно.</p></div></body></html>"}
}
tmp=data_file+".tmp"
with open(tmp,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=True,indent=2)
os.chmod(tmp,0o600)
os.replace(tmp,data_file)
PY
fi

python3 -m py_compile "$APP_FILE"
python3 -m py_compile "$APP_DIR/onyx_subscriptions.py" "$APP_DIR/onyx_panel_extras.py" "$APP_DIR/onyx_ui.py" "$APP_DIR/onyx_metrics.py" "$APP_DIR/onyx_update.py" "$APP_DIR/onyx_webpush.py" "$APP_DIR/onyx_nodes.py" "$APP_DIR/onyx_openflux.py" "$APP_DIR/onyx_awg.py" "$APP_DIR/onyx_firewall.py" "$APP_DIR/onyx_components.py" "$APP_DIR/onyx_cascade.py" "$APP_DIR/onyx_routing.py" "$APP_DIR/onyx_warp.py" "$APP_DIR/onyx_reality.py" "$APP_DIR/onyx_telegram.py" "$APP_DIR/onyx_totp.py" "$APP_DIR/onyx_access.py" "$APP_DIR/onyx_webapi.py" "$APP_DIR/onyx_failover.py"

# ---- Web Push: VAPID-ключи для уведомлений колокольчика (идемпотентно) ----
ui_stage "Подготовка ключей Web Push"
python3 - <<PY
import sys
sys.path.insert(0, "$APP_DIR")
try:
    import onyx_webpush
    pub = onyx_webpush.public_key()
    print("      VAPID public key:", (pub[:20] + "...") if pub else "unavailable (push notifications disabled)")
except Exception as exc:
    print("      Web Push keys skipped:", type(exc).__name__)
PY


# ---- Finish installation: service, Caddy route, permissions, start ----
ui_stage "Хранилище данных"
python3 - <<PY
import json
with open("${DATA_FILE}", encoding="utf-8") as f:
    json.load(f)
PY
chown root:root "$DATA_FILE"
chmod 0600 "$DATA_FILE"

ui_stage "Проверка учётных данных администратора"
if [[ "$UPDATING" == "1" ]]; then
python3 - "$DATA_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    d=json.load(f)
assert d["admin"]["user"] and d["admin"]["hash"]
print("      Existing administrator credentials retained.")
PY
else
python3 - "$DATA_FILE" "$ADMIN" "$PASS" <<'PY'
import base64, hashlib, json, secrets, sys
p, user, password = sys.argv[1], sys.argv[2], sys.argv[3]
with open(p, encoding="utf-8") as f:
    d=json.load(f)
assert d["admin"]["user"] == user
raw=base64.b64decode(d["admin"]["hash"])
salt, expected = raw[:16], raw[16:]
actual=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
assert secrets.compare_digest(expected, actual)
print("      Administrator credentials verified.")
PY
fi

ui_stage "Сервис systemd"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Onyx Panel 2.11.0
After=network-online.target caddy.service tproxy-server.service mtproxy.service onyx-panel-firewall.service
Wants=network-online.target
Requires=onyx-panel-firewall.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_FILE
Environment=ONYX_DOMAIN=$DOMAIN
Environment=ONYX_MTPROTO_HOST=$MTPROTO_HOST
Environment=ONYX_PANEL_PATH=$PANEL_PATH
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR /etc/onyx-panel /srv/tproxy-site
[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$SERVICE_FILE"

ui_stage "Установка меню Onyx"
# Replace the updater atomically: the running outer update.sh may still be
# executing from this exact path, so never truncate its inode in place.
install -o root -g root -m 0755 "$BASE/update.sh" /usr/local/sbin/.onyx-panel-update.new
mv -f /usr/local/sbin/.onyx-panel-update.new /usr/local/sbin/onyx-panel-update
cat > /etc/systemd/system/onyx-panel-web-update.service <<'UNIT'
[Unit]
Description=Onyx Panel administrator-requested update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
ExecStart=/usr/bin/python3 /opt/onyx-panel/onyx_update.py run
TimeoutStartSec=infinity
# Separate cgroup: stopping onyx-panel during an update must not kill this job.
# No Install section: this unit runs only after an authenticated admin request.
UNIT
chmod 0644 /etc/systemd/system/onyx-panel-web-update.service
cat > /etc/systemd/system/onyx-panel-component-update.service <<'UNIT'
[Unit]
Description=Onyx Panel component version manager
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
ExecStart=/usr/bin/python3 /opt/onyx-panel/onyx_components.py run
TimeoutStartSec=infinity
UNIT
chmod 0644 /etc/systemd/system/onyx-panel-component-update.service
cat > /usr/local/sbin/ONYX <<'ONYX'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SERVICE="/etc/systemd/system/onyx-panel.service"
DATA="/var/lib/onyx-panel/data.json"
CADDYFILE="/etc/caddy/Caddyfile"
DROPIN="/etc/systemd/system/caddy.service.d/tproxy.conf"
LOCK="/run/lock/onyx-panel.lock"

die(){ echo "ОШИБКА: $*" >&2; exit 1; }
[[ ${EUID:-1} -eq 0 ]] || die "Запустите меню от root: sudo ONYX"
[[ -s "$SERVICE" && -s "$DATA" ]] || die "Onyx Panel не установлен полностью."

domain(){ sed -n 's/^Environment=TPROXY_HOSTNAME=//p' "$DROPIN" 2>/dev/null | head -n1; }
panel_path(){ sed -n 's/^Environment=ONYX_PANEL_PATH=//p' "$SERVICE" 2>/dev/null | head -n1; }
admin_name(){ python3 - "$DATA" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding="utf-8")).get("admin",{}).get("user","не задан"))
PY
}
service_state(){ systemctl is-active "$1" 2>/dev/null || echo "не найден"; }
ssl_expiry(){
    local d
    d="$(domain)"
    timeout 10 openssl s_client -connect 127.0.0.1:443 -servername "$d" </dev/null 2>/dev/null |
        openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2- || echo "не удалось прочитать"
}
port_80_state(){
    if ss -lntp 2>/dev/null | grep -Eq '(^|[[:space:]])[^[:space:]]*:80[[:space:]]'; then
        if ss -lntp 2>/dev/null | grep -E ':80[[:space:]]' | grep -q 'caddy'; then
            echo "занят Caddy (нормально)"
        else
            echo "занят другой программой — конфликт"
        fi
    else
        echo "свободен"
    fi
}
pause(){ echo; read -r -p "Нажмите Enter, чтобы вернуться в меню..." _; }
lock_changes(){ exec 9>"$LOCK"; flock -n 9 || die "Установка, обновление или удаление уже выполняется."; }
unlock_changes(){ flock -u 9 2>/dev/null || true; exec 9>&-; }

show_info(){
    local d p version
    d="$(domain)"; p="$(panel_path)"; version="$(cat /etc/onyx-panel/version 2>/dev/null || echo '2.11.0')"
    echo
    echo "============================================================"
    echo "                 Onyx Panel"
    echo "============================================================"
    printf 'Версия:          %s\n' "$version"
    printf 'Домен:           %s\n' "$d"
    printf 'URL панели:      https://%s%s/login\n' "$d" "$p"
    printf 'Логин:           %s\n' "$(admin_name)"
    echo  "Пароль:          не хранится в открытом виде; его можно сменить"
    echo
    printf 'Панель:          %s\n' "$(service_state onyx-panel.service)"
    printf 'Caddy:           %s\n' "$(service_state caddy.service)"
    printf 'WEB Proxy:       %s\n' "$(service_state tproxy-server.service)"
    printf 'MTProxy:         %s\n' "$(service_state mtproxy.service)"
    printf 'Xray:            %s\n' "$(service_state onyx-panel-xray.service)"
    printf 'OpenFlux:        %s\n' "$(service_state onyx-panel-openflux.service)"
    printf 'SSL действует до:%s\n' " $(ssl_expiry)"
    printf 'TCP/80:          %s\n' "$(port_80_state)"
    echo "============================================================"
}

change_credentials(){
    local current new_user new_pass
    current="$(admin_name)"
    echo
    read -r -p "Новый логин [$current]: " new_user
    new_user="${new_user:-$current}"
    [[ ${#new_user} -ge 1 && ${#new_user} -le 64 ]] || { echo "Логин должен содержать от 1 до 64 символов."; return 1; }
    read -r -s -p "Новый пароль (минимум 3 символа): " new_pass
    echo
    [[ ${#new_pass} -ge 3 ]] || { echo "Пароль должен содержать минимум 3 символа."; return 1; }
    lock_changes
    if ! ONYX_NEW_USER="$new_user" ONYX_NEW_PASS="$new_pass" python3 - "$DATA" <<'PY'
import base64,hashlib,json,os,secrets,sys,tempfile
p=sys.argv[1]
user=os.environ.pop("ONYX_NEW_USER","")
password=os.environ.pop("ONYX_NEW_PASS","")
if not user or len(user)>64 or len(password)<3:
    raise SystemExit("Некорректные учётные данные")
with open(p,encoding="utf-8") as f: d=json.load(f)
salt=secrets.token_bytes(16)
digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
d["admin"]={"user":user,"hash":base64.b64encode(salt+digest).decode()}
fd,tmp=tempfile.mkstemp(prefix="data.json.",dir=os.path.dirname(p))
try:
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(d,f,ensure_ascii=True,indent=2); f.flush(); os.fsync(f.fileno())
    os.chmod(tmp,0o600); os.replace(tmp,p)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY
    then
        unlock_changes
        echo "Не удалось сохранить учётные данные."
        return 1
    fi
    systemctl restart onyx-panel.service
    unlock_changes
    echo "Логин и пароль изменены. Все старые сессии завершены."
}

change_path(){
    local old suffix new backup service_backup d
    old="$(panel_path)"; d="$(domain)"
    echo
    echo "Текущий путь: $old"
    read -r -p "Новый суффикс после panel- (пусто = создать случайный): " suffix
    if [[ -z "$suffix" ]]; then suffix="$(openssl rand -hex 16)"; fi
    suffix="${suffix,,}"
    [[ "$suffix" =~ ^[a-z0-9][a-z0-9-]{2,58}[a-z0-9]$ ]] || {
        echo "Используйте 4–60 символов: латинские буквы, цифры и дефис; без дефиса по краям."
        return 1
    }
    new="/panel-$suffix"
    [[ "$new" != "$old" ]] || { echo "Этот путь уже используется."; return 1; }
    lock_changes
    backup="$(mktemp /tmp/onyx-Caddyfile.XXXXXX)"
    service_backup="$(mktemp /tmp/onyx-panel-service.XXXXXX)"
    cp -a "$CADDYFILE" "$backup"; cp -a "$SERVICE" "$service_backup"
    rollback_path(){
        cp -a "$backup" "$CADDYFILE"; cp -a "$service_backup" "$SERVICE"
        systemctl daemon-reload
        systemctl restart onyx-panel.service caddy.service 2>/dev/null || true
        rm -f "$backup" "$service_backup"
        unlock_changes
    }
    if ! python3 - "$CADDYFILE" "$SERVICE" "$old" "$new" <<'PY'
import re,sys
caddy,service,old,new=sys.argv[1:]
s=open(caddy,encoding="utf-8").read()
pattern=r'\n\s*handle(?:_path)?\s+'+re.escape(old)+r'/\*\s*\{\s*reverse_proxy\s+127\.0\.0\.1:8090\s*\}\s*'
s,n=re.subn(pattern,'\n',s,count=1,flags=re.S)
if n!=1: raise SystemExit("Не найден текущий маршрут панели в Caddy")
m=re.search(r'(?m)^\s*reverse_proxy 127\.0\.0\.1:8080\s*\{',s)
if not m: raise SystemExit("Не найден маршрут WEB Proxy в Caddy")
route="    handle "+new+"/* {\n        reverse_proxy 127.0.0.1:8090\n    }\n\n"
s=s[:m.start()]+route+s[m.start():]
open(caddy,"w",encoding="utf-8").write(s)
u=open(service,encoding="utf-8").read()
u,n=re.subn(r'(?m)^Environment=ONYX_PANEL_PATH=.*$',"Environment=ONYX_PANEL_PATH="+new,u,count=1)
if n!=1: raise SystemExit("Не найден путь панели в systemd-службе")
open(service,"w",encoding="utf-8").write(u)
PY
    then
        rollback_path; echo "Смена пути отменена."; return 1
    fi
    caddy fmt --overwrite "$CADDYFILE" >/dev/null 2>&1 || true
    if ! caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
        rollback_path; echo "Новая конфигурация Caddy не прошла проверку. Старый путь восстановлен."; return 1
    fi
    systemctl daemon-reload
    if ! systemctl restart onyx-panel.service caddy.service ||
       ! curl -fsS --max-time 5 "http://127.0.0.1:8090${new}/__health" >/dev/null ||
       ! curl -kfsS --max-time 10 "https://${d}${new}/__health" >/dev/null; then
        rollback_path; echo "Проверка нового URL не прошла. Старый путь восстановлен."; return 1
    fi
    rm -f "$backup" "$service_backup"
    unlock_changes
    echo "Новый URL панели: https://${d}${new}/login"
}

run_update(){
    echo
    echo "Запускается безопасное обновление до последней опубликованной версии..."
    exec /usr/local/sbin/onyx-panel-update
}

maintain_ssl(){
    echo
    echo "Проверяю HTTPS-сертификат и запускаю обслуживание Caddy..."
    lock_changes
    if /usr/local/sbin/onyx-panel-sync-tls --force; then
        unlock_changes
        echo "SSL-сертификат действителен; копия для Hysteria2 синхронизирована."
        echo "TCP/80 используется для HTTP→HTTPS и обновления сертификата: $(port_80_state)"
    else
        unlock_changes
        echo "Не удалось подтвердить обновление SSL. Проверьте DNS, TCP/80, TCP/443 и журнал Caddy."
        return 1
    fi
}

run_remove(){
    echo
    echo "Запускается полное удаление Onyx Panel..."
    exec /usr/local/sbin/onyx-panel-uninstall
}

while true; do
    clear 2>/dev/null || true
    echo "============================================================"
    echo "              Onyx Panel — WPP MENU"
    echo "============================================================"
    echo "  1) Информация"
    echo "  2) Обновить"
    echo "  3) Сменить логин и пароль"
    echo "  4) Сменить URL-путь панели"
    echo "  5) Проверить SSL-сертификат"
    echo "  6) Удалить"
    echo "  0) Выход"
    echo "============================================================"
    read -r -p "Выберите пункт: " choice
    case "$choice" in
        1) show_info; pause ;;
        2) run_update ;;
        3) change_credentials || true; pause ;;
        4) change_path || true; pause ;;
        5) maintain_ssl || true; pause ;;
        6) run_remove ;;
        0) exit 0 ;;
        *) echo "Неизвестный пункт."; sleep 1 ;;
    esac
done
ONYX
chmod 0755 /usr/local/sbin/ONYX
ln -sfn /usr/local/sbin/ONYX /usr/local/sbin/onyx

ui_stage "Маршрут панели в Caddy"
CADDYFILE="/etc/caddy/Caddyfile"
test -s "$CADDYFILE" || die "Caddyfile is missing."

python3 - "$CADDYFILE" "$PANEL_PATH" "$DOMAIN" "$ACME_EMAIL" "$XRAY_PATH" <<'PY'
import re, sys
from pathlib import Path
p, path, domain, email, xray_path = sys.argv[1:]
s = Path(p).read_text(encoding="utf-8")
legacy_address = re.compile(
    r"(?m)^(?P<indent>\s*)(?:"
    r":443\s*,\s*" + re.escape(domain) +
    r"|" + re.escape(domain) + r"\s*,\s*:443"
    r"|https://" + re.escape(domain) + r"(?::443)?"
    r"|" + re.escape(domain) + r":443)\s*\{\s*$"
)
s = legacy_address.sub(lambda m:m.group("indent")+domain+" {",s,count=1)

# Materialize the core Caddyfile placeholders before validation.
s = s.replace("{$TPROXY_HOSTNAME}", domain)
s = s.replace("{$ACME_EMAIL}", email)
s = re.sub(
    r'\n\s*handle /onyx-sub/\*\s*\{\s*reverse_proxy 127\.0\.0\.1:8090\s*\}\s*',
    '\n', s, flags=re.S,
)
s = re.sub(
    r'\n\s*handle /onyx-api/\*\s*\{\s*reverse_proxy 127\.0\.0\.1:8090\s*\}\s*',
    '\n', s, flags=re.S,
)
s = re.sub(
    r'\n\s*handle(?:_path)? /panel-[a-z0-9-]{3,64}/\*\s*\{\s*reverse_proxy 127\.0\.0\.1:8090\s*\}\s*',
    '\n',
    s,
    flags=re.S,
)
# Remove the complete managed block written by the experimental V2.2 router
# before installing the single stable VLESS XHTTP route.
s = re.sub(
    r'\n?\s*# ONYX XRAY ROUTES BEGIN\n.*?\n\s*# ONYX XRAY ROUTES END\n?',
    '\n',
    s,
    flags=re.S,
)
s = re.sub(
    r'\n\s*@web_panel_vless\s+path\s+/vless-[a-f0-9]{24}(?:\s+/vless-[a-f0-9]{24}/\*)?\s*\n\s*reverse_proxy\s+@web_panel_vless\s+h2c://127\.0\.0\.1:10000\s*\{\s*flush_interval\s+-1\s*\}\s*',
    '\n',
    s,
    flags=re.S,
)
s = re.sub(
    r'\n\s*@web_panel_vless\s+path\s+/vless-[a-f0-9]{24}(?:\s+/vless-[a-f0-9]{24}/\*)?\s*\n\s*handle\s+@web_panel_vless\s*\{\s*reverse_proxy\s+127\.0\.0\.1:10000\s*\{\s*transport\s+http\s*\{\s*versions\s+h2c\s*\}\s*flush_interval\s+-1\s*\}\s*\}\s*',
    '\n',
    s,
    flags=re.S,
)
m = re.search(r'(?m)^\s*reverse_proxy 127\.0\.0\.1:8080\s*\{', s)
if not m:
    raise SystemExit("Could not locate tproxy relay reverse_proxy in Caddyfile")
route = (
    "    handle /onyx-api/* {\n"
    "        reverse_proxy 127.0.0.1:8090\n"
    "    }\n\n"
    "    handle /onyx-sub/* {\n"
    "        reverse_proxy 127.0.0.1:8090\n"
    "    }\n\n"
    "    handle /onyx-invite/* {\n"
    "        reverse_proxy 127.0.0.1:8090\n"
    "    }\n\n"
    "    @web_panel_vless path " + xray_path + " " + xray_path + "/*\n"
    "    handle @web_panel_vless {\n"
    "        reverse_proxy 127.0.0.1:10000 {\n"
    "            transport http {\n"
    "                versions h2c\n"
    "            }\n"
    "            flush_interval -1\n"
    "        }\n"
    "    }\n\n"
    "    handle " + path + "/* {\n"
    "        reverse_proxy 127.0.0.1:8090\n"
    "    }\n\n"
)
s = s[:m.start()] + route + s[m.start():]

# Restore standard Caddy automatic HTTPS. TCP/80 remains open for redirects
# and HTTP-01 validation; TCP/443 serves the panel and proxy traffic.
s = re.sub(
    r'\n?\s*# ONYX TLS WITHOUT PORT 80 BEGIN\n.*?\n\s*# ONYX TLS WITHOUT PORT 80 END\n?',
    '\n', s, flags=re.S,
)
s = re.sub(r'(?m)^\s*auto_https\s+disable_redirects\s*\n?', '', s)
s = re.sub(r'\A\s*\{\s*\}\s*', '', s, count=1)
s = re.sub(
    r'\n?\s*# ONYX HTTP REDIRECT BEGIN\n.*?\n\s*# ONYX HTTP REDIRECT END\n?',
    '\n', s, flags=re.S,
)
def enable_http_challenge(text, hostname):
    match = re.search(r'(?m)^\s*' + re.escape(hostname) + r'\s*\{\s*$', text)
    if not match:
        raise SystemExit("Onyx Panel Caddy site block was not found")
    opening = text.find('{', match.start(), match.end())
    depth = 0
    closing = None
    for index in range(opening, len(text)):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                closing = index + 1
                break
    if closing is None:
        raise SystemExit("Onyx Panel Caddy site block is incomplete")
    block = text[match.start():closing]
    block = re.sub(r'(?m)^\s*disable_http_challenge\s*\n?', '', block)
    return text[:match.start()] + block + text[closing:]
s = enable_http_challenge(s, domain)
redirect = (
    "# ONYX HTTP REDIRECT BEGIN\n"
    "http://" + domain + " {\n"
    "    redir https://" + domain + "{uri} permanent\n"
    "}\n"
    "# ONYX HTTP REDIRECT END\n"
)
s = s.rstrip() + "\n\n" + redirect
Path(p).write_text(s, encoding="utf-8")
PY

if grep -Eq '\{\$(TPROXY_HOSTNAME|ACME_EMAIL)\}' "$CADDYFILE"; then
    echo "ERROR: unresolved Caddy environment placeholders remain."
    sed -n '1,100p' "$CADDYFILE" || true
    exit 1
fi

caddy fmt --overwrite "$CADDYFILE" >/dev/null 2>&1 || true

if ! caddy validate --config "$CADDYFILE" --adapter caddyfile; then
    echo
    echo "ERROR: Caddy validation failed."
    sed -n '1,100p' "$CADDYFILE" || true
    exit 1
fi

systemctl daemon-reload
if [[ "$FAST" == 1 ]]; then
    echo "      Reloading Caddy (fast update)..."
    # The canonical Caddyfile intentionally has `admin off`; SIGUSR1 is
    # Caddy's supported config reload signal in this mode, and unlike a
    # restart it keeps established client connections.
    if ! systemctl kill --signal=SIGUSR1 --kill-who=main caddy.service >/dev/null 2>&1 ||
       ! systemctl is-active --quiet caddy.service; then
        systemctl restart caddy.service || {
            systemctl --no-pager --full status caddy.service >&2 || true
            journalctl -u caddy.service -n 60 --no-pager >&2 || true
            die "Caddy failed to reload."
        }
    fi
else
    echo "      Activating Caddy..."
    if ! systemctl restart caddy.service; then
        systemctl --no-pager --full status caddy.service >&2 || true
        journalctl -u caddy.service -n 60 --no-pager >&2 || true
        die "Caddy failed to start."
    fi
fi
systemctl enable onyx-panel-firewall.service
systemctl restart onyx-panel-firewall.service
systemctl enable onyx-panel-traffic.timer onyx-panel-metrics.timer
systemctl stop onyx-panel-traffic.timer onyx-panel-metrics.timer
systemctl reset-failed onyx-panel-traffic.service onyx-panel-metrics.service 2>/dev/null || true
systemctl enable onyx-panel.service
systemctl restart onyx-panel.service

echo "      Initializing user/secret manager..."
"$MANAGER" init

echo "      Checking initial traffic collection..."
systemctl restart onyx-panel-traffic.service || {
    journalctl -u onyx-panel-traffic.service -n 30 --no-pager >&2 || true
    die "Traffic collection failed; update cannot be considered successful."
}
echo "      Checking initial VPS metrics..."
systemctl restart onyx-panel-metrics.service || {
    journalctl -u onyx-panel-metrics.service -n 30 --no-pager >&2 || true
    die "VPS metrics failed; update cannot be considered successful."
}
systemctl restart onyx-panel-traffic.timer onyx-panel-metrics.timer
for timer in onyx-panel-traffic.timer onyx-panel-metrics.timer; do
    systemctl is-active --quiet "$timer" || die "Collector timer did not start: $timer"
    timer_state="$(systemctl show --property=SubState --value "$timer")"
    case "$timer_state" in
        waiting|running) ;;
        *) die "Collector timer has no future trigger: $timer ($timer_state)" ;;
    esac
done

chown -R root:tproxy /srv/tproxy-site
find /srv/tproxy-site -type d -exec chmod 0750 {} +
find /srv/tproxy-site -type f -exec chmod 0640 {} +

ui_stage "Запуск сервиса"
if [[ "$FAST" == 1 ]]; then
    # Component pins are unchanged, so the proxy configs are untouched:
    # restarting tproxy-server/mtproxy would only drop live connections.
    # Caddy was already reloaded above via SIGUSR1.
    echo "      Proxy services left running (fast update)."
else
    systemctl restart caddy.service
    systemctl restart tproxy-server.service
    systemctl restart mtproxy.service
fi

# Re-publish the retained author source through the current CSP-safe renderer.
# This automatically repairs pages saved by the affected release where the
# HTML loaded but its inline styles and scripts were blocked by the browser.
ONYX_DOMAIN="$DOMAIN" \
ONYX_MTPROTO_HOST="$MTPROTO_HOST" \
ONYX_PANEL_PATH="$PANEL_PATH" \
    python3 "$APP_FILE" --repair-site

# Ensure no stale copy of this exact panel occupies 127.0.0.1:8090.
systemctl stop onyx-panel.service 2>/dev/null || true
pkill -f '[/]opt/onyx-panel/panel\.py' 2>/dev/null || true
sleep 0.3

if ss -lntp 2>/dev/null | grep -Eq ':8090\b'; then
    echo "ERROR: 127.0.0.1:8090 is still occupied before starting the panel."
    ss -lntp 2>/dev/null | grep -E ':8090\b' || true
    exit 1
fi

systemctl start onyx-panel.service
sleep 1

for unit in caddy.service tproxy-server.service mtproxy.service onyx-panel.service; do
    systemctl is-active --quiet "$unit" || {
        echo "ERROR: $unit failed to become active."
        systemctl --no-pager --full status "$unit" || true
        if [[ "$unit" == "onyx-panel.service" ]]; then
            echo "--- Current panel journal ---"
            journalctl -u onyx-panel.service --since "2 minutes ago" -n 120 --no-pager || true
        fi
        exit 1
    }
done

ui_stage "Финальная проверка панели"
if ! systemctl is-active --quiet onyx-panel.service; then
    echo "ERROR: onyx-panel.service is not active."
    systemctl --no-pager --full status onyx-panel.service || true
    journalctl -u onyx-panel.service --since "10 minutes ago" -n 80 --no-pager || true
    exit 1
fi

if ! curl -fsS --max-time 5 "http://127.0.0.1:8090${PANEL_PATH}/__health" >/dev/null; then
    echo "ERROR: panel is not answering on 127.0.0.1:8090."
    ss -lntp 2>/dev/null | grep -E ':8090\b' || true
    journalctl -u onyx-panel.service --since "10 minutes ago" -n 80 --no-pager || true
    exit 1
fi

if ! curl -k -fsS --max-time 10 "https://${DOMAIN}${PANEL_PATH}/__health" >/dev/null; then
    echo "ERROR: Caddy panel route returned an error."
    echo "--- Caddy route ---"
    grep -n -A4 -B2 "${PANEL_PATH}" "$CADDYFILE" || true
    echo "--- panel service ---"
    systemctl --no-pager --full status onyx-panel.service || true
    journalctl -u onyx-panel.service --since "10 minutes ago" -n 50 --no-pager || true
    exit 1
fi

# Retire components owned by the withdrawn experimental integrations only
# after the stable panel, Caddy and proxy services have passed health checks.
if [[ -e /etc/onyx-panel/mieru-ufw-owned ]]; then
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
        while read -r old_port; do
            [[ "$old_port" =~ ^[0-9]+$ ]] || continue
            (( old_port >= 1 && old_port <= 65535 )) || continue
            ufw --force delete allow "${old_port}/tcp" >/dev/null 2>&1 || true
        done < /etc/onyx-panel/mieru-ufw-owned
    fi
    rm -f /etc/onyx-panel/mieru-ufw-owned
fi
if [[ -e /etc/onyx-panel/mita-package-owned ]]; then
    command -v mita >/dev/null 2>&1 && mita stop >/dev/null 2>&1 || true
    apt-get -o DPkg::Lock::Timeout=600 remove -y mita >/dev/null || die "Could not remove the Onyx-owned Mieru package."
    rm -f /etc/onyx-panel/mita-package-owned
fi
rm -f /etc/onyx-panel/mieru-server.json /etc/onyx-panel/mieru-port
if [[ -e /etc/onyx-panel/naive-caddy-owned ]]; then
    rm -rf -- /opt/onyx-panel/caddy-naive
    rm -f /etc/onyx-panel/naive-caddy-owned
fi

NODE_API_TOKEN="$(python3 - "$DOMAIN" <<'PY'
import sys
sys.path.insert(0,"/opt/onyx-panel")
import onyx_nodes
try:
    key=open("/var/lib/onyx-panel/api.key",encoding="ascii").read().strip()
    print(onyx_nodes.make_connection_token(sys.argv[1],key))
except (OSError,ValueError):
    pass
PY
)"

if [[ "$UPDATING" == "1" ]]; then
    ui_success_begin "ONYX PANEL — ОБНОВЛЕНО"
else
    ui_success_begin "ONYX PANEL — УСТАНОВЛЕНО"
fi
ui_kv "Адрес панели" "https://${DOMAIN}${PANEL_PATH}/login"
if [[ "$UPDATING" != "1" && "$NODE_API_TOKEN" == onyxnode1_* ]]; then
    ui_kv "Node API token" "${NODE_API_TOKEN}"
fi
ui_kv "Логин администратора" "${ADMIN}"
if [[ "$UPDATING" == "1" ]]; then
    ui_kv "Пароль" "сохранён без изменений"
else
    # The operator just typed this password: do not echo it into the
    # terminal scrollback or session logs again.
    ui_kv "Пароль" "задан при установке (не отображается)"
fi
ui_success_end
