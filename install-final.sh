#!/usr/bin/env bash
set -Eeuo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
umask 077

# Visual kit: banner, colored stages, explained red errors. When the kit file
# is missing (offline update from an old package) fall back to plain output.
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
            curl --fail --silent --show-error --location                 --proto '=https' --proto-redir '=https' --tlsv1.2                 --retry 2 --retry-all-errors --connect-timeout 15                 --output "$out" "$candidate" && return 0
            rm -f "$out"
        done
        return 1
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
for file in install-panel.sh install-core.sh uninstall-onyx-panel.sh update.sh onyx-logo.png onyx_subscriptions.py onyx_panel_extras.py onyx_ui.py onyx_metrics.py onyx_update.py onyx_webpush.py onyx_nodes.py onyx_openflux.py onyx_awg.py onyx_firewall.py onyx_components.py; do
    [[ -s "$BASE/$file" ]] || die "Package is incomplete: missing $file. Extract the complete archive."
done
[[ -s "$BASE/assets/OpenFlux-linux-amd64" || -s "$BASE/OpenFlux-linux-amd64" ]] ||
    die "Package is incomplete: missing OpenFlux-linux-amd64. Extract the complete archive."
for asset in amneziawg-go-linux-amd64 awg-linux-amd64 awg-quick-linux-amd64; do
    [[ -s "$BASE/assets/$asset" ]] || die "Package is incomplete: missing assets/$asset. Extract the complete archive."
done
[[ -s "$BASE/onyx-panel/flags.tar.gz" ]] ||
    die "Package is incomplete: onyx-panel/flags.tar.gz is missing. Extract the complete archive."
for font in manrope-cyrillic-wght-normal.woff2 manrope-latin-wght-normal.woff2 jetbrains-mono-cyrillic-wght-normal.woff2 jetbrains-mono-latin-wght-normal.woff2; do
    [[ -s "$BASE/fonts/$font" ]] || die "Package is incomplete: missing fonts/$font. Extract the complete archive."
done
command -v flock >/dev/null 2>&1 || die "flock is required (package: util-linux)."
exec 9>/run/lock/onyx-panel.lock
flock -n 9 || die "Another Onyx Panel install, update or removal is already running."
cleanup_credentials() {
    if [[ -f /etc/onyx-panel/install-credentials ]]; then
        command -v shred >/dev/null 2>&1 && shred -u /etc/onyx-panel/install-credentials 2>/dev/null || \
            rm -f /etc/onyx-panel/install-credentials
    fi
}
trap cleanup_credentials EXIT

ui_banner "v2.1.46"
ui_stage "Подготовка сервера"

PANEL_UPDATE=0
if [[ -s /var/lib/onyx-panel/data.json ]] &&
   [[ -f /etc/systemd/system/onyx-panel.service ]] &&
   sed -n 's/^Environment=ONYX_PANEL_PATH=//p' /etc/systemd/system/onyx-panel.service |
       head -n1 | grep -Eq '^/[a-z0-9][a-z0-9-]{2,58}[a-z0-9]$'; then
    PANEL_UPDATE=1
    ui_info "Найдена установленная панель: пользователи, пароль, адрес и HTML сайта будут сохранены."
else
    ui_info "Режим установки/продолжения: совместимые сервисы будут переиспользованы, недостающие — установлены."
fi

# Install the recovery command before making system changes so even an
# interrupted first installation can be cleaned up deterministically.
install -d -m 0700 /etc/onyx-panel
install -o root -g root -m 0755 \
    "$BASE/uninstall-onyx-panel.sh" \
    /usr/local/sbin/onyx-panel-uninstall

ui_stage "Прокси-сервисы: MTProxy · релей tproxy · Caddy"
bash "$BASE/install-core.sh"

ui_stage "Панель управления Onyx Panel"
if [[ "$PANEL_UPDATE" == 1 ]]; then
    ONYX_PANEL_UPDATE=1 bash "$BASE/install-panel.sh"
else
    bash "$BASE/install-panel.sh"
fi

for unit in caddy.service mtproxy.service tproxy-server.service onyx-panel.service onyx-panel-firewall.service; do
    systemctl is-active --quiet "$unit" || die "Service $unit did not start."
done
systemctl is-enabled --quiet onyx-panel-firewall.service ||
    die "Persistent user firewall is not enabled."
nft list table inet onyx_panel >/dev/null 2>&1 ||
    die "Persistent user firewall table is missing."
nft list table ip onyx_awg >/dev/null 2>&1 ||
    die "AWG routing firewall table is missing."
[[ -x /opt/onyx-panel/xray/xray ]] || die "Xray binary was not installed."
[[ -x /usr/local/bin/amneziawg-go && -x /usr/local/bin/awg ]] || die "AmneziaWG was not installed."
/usr/local/bin/awg --version >/dev/null || die "AmneziaWG tools check failed."
[[ -s /etc/onyx-panel-xray/config.json ]] || die "Xray configuration was not created."
[[ -x /usr/local/sbin/ONYX ]] || die "Onyx console menu was not installed."
systemctl is-active --quiet onyx-panel-sync-tls.timer ||
    die "The Xray TLS synchronization timer did not start."
ui_ok "Все проверки пройдены — установка завершена."
printf '%s\n' '2.1.46' > /etc/onyx-panel/version
chmod 0600 /etc/onyx-panel/version

# Keep a private copy of the complete package on the server so the panel can
# reinstall or roll back later without any external downloads.
if [[ "$BASE" != "/opt/onyx-panel-package" ]]; then
    rm -rf /opt/onyx-panel-package.tmp
    install -d -o root -g root -m 0700 /opt/onyx-panel-package.tmp
    cp -a "$BASE/." /opt/onyx-panel-package.tmp/
    rm -rf /opt/onyx-panel-package
    mv /opt/onyx-panel-package.tmp /opt/onyx-panel-package
    printf '%s\n' '2.1.46' > /opt/onyx-panel-package/version
    chmod 0600 /opt/onyx-panel-package/version
fi
