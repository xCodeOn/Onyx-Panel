#!/usr/bin/env bash
# Visual kit for Onyx Panel installers: banner, colored stages, prompts and
# a red error box that explains what a failure means and what to do next.
#
# This file is sourced, never executed. It must be safe under
# `set -Eeuo pipefail` and must not print anything on load.
#
# Every installer falls back to a quiet no-color shim (see onyx-install-ui.sh
# guard in install*.sh) when this file is missing, so an offline update from
# an older package keeps working.

# ── Color detection ─────────────────────────────────────────────────────────
# Colors only for a real interactive terminal; NO_COLOR wins, dumb terminals
# get plain text. Both stdout and stderr must be TTYs so redirects stay clean.
if [[ -t 1 && -t 2 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]]; then
    R=$'\e[0m'
    B=$'\e[1m'
    DIM=$'\e[2m'
    C_RED=$'\e[1;38;5;203m'
    C_GREEN=$'\e[1;38;5;114m'
    C_AMBER=$'\e[1;38;5;215m'
    C_ACCENT=$'\e[1;38;5;80m'
    C_BLUE=$'\e[1;38;5;75m'
    C_VIOLET=$'\e[1;38;5;140m'
    C_GREY=$'\e[38;5;245m'
    C_WHITE=$'\e[1;38;5;252m'
else
    R='' B='' DIM=''
    C_RED='' C_GREEN='' C_AMBER='' C_ACCENT='' C_BLUE='' C_VIOLET='' C_GREY='' C_WHITE=''
fi

# UTF-8 terminals get box-drawing art; everything else gets ASCII.
UI_UTF8=0
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *[Uu][Tt][Ff]*|*utf8*) UI_UTF8=1 ;;
esac
if [[ "$UI_UTF8" == 1 ]]; then
    UI_HR="────────────────────────────────────────────────────────────"
else
    UI_HR="------------------------------------------------------------"
fi

# ── Banner ──────────────────────────────────────────────────────────────────
ui_banner() {
    local version="${1:-}"
    local tagline="VPN-панель: AmneziaWG · Xray · MTProxy · Hysteria · Caddy"
    if [[ "$UI_UTF8" == 1 ]]; then
        printf '%s\n' \
"${C_ACCENT} ██████╗  ███╗   ██╗ ██╗   ██╗ ██╗  ██╗${R}" \
"${C_BLUE}██╔═══██╗ ████╗  ██║ ╚██╗ ██╔╝ ╚██╗██╔╝${R}" \
"${C_ACCENT}██║   ██║ ██╔██╗ ██║  ╚████╔╝   ╚███╔╝${R}" \
"${C_BLUE}██║   ██║ ██║╚██╗██║   ╚██╔╝    ██╔██╗${R}" \
"${C_ACCENT}╚██████╔╝ ██║ ╚████║    ██║    ██╔╝ ██╗${R}" \
"${C_BLUE} ╚═════╝  ╚═╝  ╚═══╝    ╚═╝    ╚═╝  ╚═╝${R}" \
"${C_WHITE} ██████╗  █████╗  ███╗   ██╗ ███████╗ ██╗${R}" \
"${C_GREY} ██╔══██╗ ██╔══██╗ ████╗  ██║ ██╔════╝ ██║${R}" \
"${C_WHITE} ██████╔╝ ███████║ ██╔██╗ ██║ █████╗   ██║${R}" \
"${C_GREY} ██╔═══╝  ██╔══██║ ██║╚██╗██║ ██╔══╝   ██║${R}" \
"${C_WHITE} ██║      ██║  ██║ ██║ ╚████║ ███████╗ ███████╗${R}" \
"${C_GREY} ╚═╝      ╚═╝  ╚═╝ ╚═╝  ╚═══╝ ╚══════╝ ╚══════╝${R}"
        printf '\n%s\n' "${C_AMBER}${B}  ${version}${R}  ${C_GREY}${tagline}${R}"
    else
        printf '\n%s\n' "${C_ACCENT}${B}  == ONYX PANEL ==  ${version}${R}  ${C_GREY}${tagline}${R}"
    fi
    printf '%s\n\n' "${C_GREY}  ${UI_HR}${R}"
}

# ── Stages and lines ────────────────────────────────────────────────────────
UI_STEP_N="${UI_STEP_N:-0}"
UI_STAGE_STATE="${ONYX_STAGE_STATE:-}"
UI_PROGRESS_PID=""

ui_progress_stop() {
    [[ -n "$UI_PROGRESS_PID" ]] || return 0
    kill "$UI_PROGRESS_PID" 2>/dev/null || true
    wait "$UI_PROGRESS_PID" 2>/dev/null || true
    UI_PROGRESS_PID=""
    if [[ -t 2 ]]; then printf '\r\033[K' >&2; fi
}

ui_progress_start() {
    local message="${1:-Выполняется}"
    ui_progress_stop
    [[ -t 2 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]] || return 0
    (
        local frames='|/-\\' index=0
        while true; do
            printf '\r  %s %s' "${frames:index++%4:1}" "$message" >&2
            sleep 0.2
        done
    ) &
    UI_PROGRESS_PID=$!
}

ui_run_with_progress() {
    local message="$1" rc
    shift
    ui_progress_start "$message"
    if "$@"; then
        ui_progress_stop
    else
        rc=$?
        ui_progress_stop
        return "$rc"
    fi
}

ui_stage() {
    ui_progress_stop
    UI_STAGE_STATE="${ONYX_STAGE_STATE:-}"
    if [[ -n "$UI_STAGE_STATE" && -s "$UI_STAGE_STATE" ]]; then
        UI_STEP_N="$(<"$UI_STAGE_STATE")"
        [[ "$UI_STEP_N" =~ ^[0-9]+$ ]] || UI_STEP_N=0
    fi
    UI_STEP_N=$((UI_STEP_N + 1))
    if [[ -n "$UI_STAGE_STATE" ]]; then
        printf '%s\n' "$UI_STEP_N" > "${UI_STAGE_STATE}.tmp.$$"
        mv -f "${UI_STAGE_STATE}.tmp.$$" "$UI_STAGE_STATE"
    fi
    printf '\n%s\n' "${C_ACCENT}${B}──[ ${UI_STEP_N} ]${R}${C_WHITE}${B} $* ${R}"
}
ui_ok()   { ui_progress_stop; printf '%s\n' "${C_GREEN}  ✔${R} $*"; }
ui_info() { printf '%s\n' "${C_GREY}  ·${R} $*"; }
ui_warn() { ui_progress_stop; printf '%s\n' "${C_AMBER}  ▲${R} $*"; }
ui_err()  { ui_progress_stop; printf '%s\n' "${C_RED}  ✗${R} $*" >&2; }
ui_kv()   { printf '%s\n' "${C_GREY}  ${1}:${R} ${C_WHITE}${2}${R}"; }

# ── Error explanations ──────────────────────────────────────────────────────
# Prints a red frame with a human explanation for known failure messages.
# Unknown messages get generic but useful advice. Languages follow the
# installers themselves: explanations for the user are in Russian.
ui_explain() {
    local msg
    msg="$(printf '%s' "$*" | tr '[:upper:]' '[:lower:]')"
    local meaning="" hint=""
    case "$msg" in
        *sudo*root*|*run\ as\ root*|*run\ this\ command\ with*|*run\ this\ installer\ as\ root*)
            meaning="Установщику нужны права суперпользователя — он ставит системные пакеты, сервисы systemd и правила nftables."
            hint="Запустите от root: sudo -i, затем повторите команду установки."
            ;;
        *ubuntu\ 22*|*debian*version*)
            meaning="Слишком старая операционная система: панели нужны systemd, nftables и свежий Caddy."
            hint="Поддерживаются Ubuntu 22.04+ и Debian 11+. Проверить свою: cat /etc/os-release"
            ;;
        *x86_64*)
            meaning="Установщик собран только для 64-битных серверов amd64."
            hint="Возьмите сервер с архитектурой x86_64 (amd64). ARM пока не поддерживается."
            ;;
        *port*occupied*|*port*is\ in\ use*|*address\ already\ in\ use*)
            meaning="Нужный установщику порт слушает чужой процесс — сервис не сможет привязаться."
            hint="Найдите занявший порт: ss -lntp | grep ':ПОРТ' — остановите его или поменяйте порт в настройках."
            ;;
        *already\ running*|*already\ be*installed*|*is\ already\ running*)
            meaning="Параллельно уже идёт другая установка, обновление или удаление Onyx Panel — одновременный запуск мог бы повредить данные."
            hint="Дождитесь окончания процесса. Если он завис: ps aux | grep install, затем удалите /run/lock/onyx-panel.lock"
            ;;
        *package\ is\ incomplete*|*missing\ *extract*)
            meaning="Пакет распакован не полностью — не хватает файлов установщика или бинарников из assets/."
            hint="Скачайте релиз заново и распакуйте архив целиком: tar -xzf onyx-panel.tar.gz"
            ;;
        *curl\ is\ required*|*tar\ is\ required*|*flock\ is\ required*|*qrencode*)
            meaning="В системе нет утилиты, без которой установщик не может работать."
            hint="Доустановите её: apt-get update && apt-get install -y curl tar util-linux qrencode"
            ;;
        *sha256*|*checksum*|*hash\ mismatch*|*integrity*)
            meaning="Скачанный файл не совпал по контрольной сумме — он повреждён при передаче."
            hint="Повторите установку (она докачает файлы). Если повторяется — проверьте место на диске: df -h"
            ;;
        *did\ not\ start*|*failed\ to\ start*|*did\ not\ start\ after*|*not\ active*)
            meaning="Системная служба не поднялась. Обычно это занятый порт, ошибка в конфиге или нехватка памяти."
            hint="Точная причина в журнале: journalctl -u ИМЯ_СЛУЖБЫ -n 50 --no-pager"
            ;;
        *certificate*|*acme*|*tls*|*challenge*)
            meaning="Не удалось выпустить TLS-сертификат Let's Encrypt."
            hint="Проверьте: 1) A-запись домена указывает на IP этого сервера; 2) порты 80 и 443 открыты извне; 3) email указан корректный."
            ;;
        *dns*|*resolve*|*name\ or\ service\ not\ known*|*name\ does\ not\ resolve*)
            meaning="Сервер не смог разрешить доменное имя в IP-адрес."
            hint="Проверьте DNS: resolvectl status или cat /etc/resolv.conf; попробуйте nslookup домен"
            ;;
        *disk*|*no\ space*|*enosp*|*not\ enough\ space*)
            meaning="На диске закончилось место — пакеты и конфиги не записались."
            hint="Освободите место: df -h; du -sh /var/log/* | sort -h | tail"
            ;;
        *apt*|*dpkg*|*package\ manager*)
            meaning="Менеджер пакетов apt завершился с ошибкой — битые зависимости или параллельный apt-процесс."
            hint="Выполните: apt-get update && dpkg --configure -a, затем запустите установку снова."
            ;;
        *network*|*connection\ refused*|*timed\ out*|*timeout*|*could\ not\ resolve*|*failed\ to\ download*|*could\ not\ download*)
            meaning="Не получилось скачать нужный компонент — нет доступа в интернет или DNS не работает."
            hint="Проверьте: curl -I https://github.com — и настройки файрвола/прокси на сервере."
            ;;
        *зеркала*)
            meaning="Основной источник и все зеркала недоступны одновременно — на сервере нет рабочего интернета."
            hint="Проверьте: curl -I https://github.com; при необходимости задайте свои зеркала: ONYX_GH_MIRRORS='https://ghproxy.net https://gh-proxy.com' bash install.sh"
            ;;
        *invalid\ domain*|*valid\ domain*)
            meaning="Домен введён неверно: он должен быть вида proxy.example.com, без http://, порта и подчёркиваний."
            hint="Пример правильного ввода: proxy.example.com"
            ;;
        *invalid\ email*|*valid\ email*)
            meaning="Email введён неверно — на него Let's Encrypt присылает уведомления о сертификате."
            hint="Пример правильного ввода: admin@example.com"
            ;;
        *password*short*|*least\ 3\ characters*)
            meaning="Пароль панели слишком короткий."
            hint="Введите минимум 3 символа (лучше 12+; генератор: openssl rand -base64 12)"
            ;;
        *)
            meaning="Команда установщика завершилась с ошибкой — точная причина напечатана выше в логе."
            hint="Пере-запуск установки безопасен: она продолжится с того же места. Подробности: journalctl -xe"
            ;;
    esac
    if [[ "$UI_UTF8" == 1 ]]; then
        printf '%s\n' \
"${C_RED}  ┌─${B} что это значит ${R}${C_RED}────────────────────────────────────────${R}" \
"${C_RED}  │${R} ${meaning}" \
"${C_RED}  │${R}" \
"${C_RED}  │${R} ${C_AMBER}${hint}${R}" \
"${C_RED}  └──────────────────────────────────────────────────────────${R}" >&2
    else
        printf '%s\n' \
"${C_RED}  +-- what this means ---------------------------------------${R}" \
"${C_RED}  |${R} ${meaning}" \
"${C_RED}  |${R} ${C_AMBER}${hint}${R}" >&2
    fi
}

# ── die / failure reporting ─────────────────────────────────────────────────
# Installers route their die() here: red message + explanation + exit 1.
ui_die() {
    ui_progress_stop
    printf '%s\n' "" >&2
    printf '%s\n' "${C_RED}${B}  ✗ ОШИБКА:${R} ${C_RED}$*${R}" >&2
    printf '%s\n' "${C_RED}  ${UI_HR}${R}" >&2
    ui_explain "$@"
    printf '%s\n' "" >&2
    exit 1
}

# ERR trap body: fires for any command failure that is about to abort the
# script (set -e). Call as:  trap 'ui_trap_error $?' ERR
ui_trap_error() {
    local code="${1:-$?}"
    ui_progress_stop
    trap - ERR
    printf '%s\n' "" >&2
    printf '%s\n' "${C_RED}${B}  ╔═══${R}${C_RED}${B} УСТАНОВКА ПРЕРВАНА — код ${code} ${R}" >&2
    printf '%s\n' "${C_GREY}  команда:${R} ${BASH_COMMAND:-?}" >&2
    printf '%s\n' "${C_GREY}  строка: ${R} ${BASH_LINENO[0]:-?} (${BASH_SOURCE[1]:-installer})" >&2
    printf '%s\n' "${C_RED}  ${UI_HR}${R}" >&2
    ui_explain ""
    # Optional detailed dump (journalctl, service status) provided by the caller.
    if declare -F ui_failure_dump >/dev/null 2>&1; then
        ui_failure_dump
    fi
    exit "$code"
}

# ── Final success box ───────────────────────────────────────────────────────
# Usage: ui_success_begin <title>; ui_kv ...; ui_success_end
ui_success_begin() {
    printf '%s\n' ""
    printf '%s\n' "${C_GREEN}${B}  ╔═══${R}${C_GREEN}${B} $* ${R}"
}
ui_success_end() {
    printf '%s\n' "${C_GREEN}${B}  ╚══════════════════════════════════════════════════════════╝${R}"
    printf '%s\n' ""
}

# ── Resilient downloads ─────────────────────────────────────────────────────
# GitHub is intermittently unreachable from some networks (RU, CN, mobile
# carriers). Every caller still verifies checksums, so mirrors are transport
# only. Override ONYX_GH_MIRRORS (space-separated prefixes) to customize;
# set ONYX_FETCH_SKIP_DIRECT=1 to force the mirror path.
ONYX_GH_MIRRORS="${ONYX_GH_MIRRORS:-https://ghproxy.net https://gh-proxy.com https://ghfast.top}"

onyx_fetch() {
    # onyx_fetch OUTPUT URL [EXTRA_URLS...]
    # Direct URL first, then GitHub mirror prefixes for github.com/codeload/
    # raw URLs, then explicit extra URLs. Returns non-zero when everything
    # fails; the caller decides whether that is fatal (die) or tolerable.
    local out="$1" url="$2" candidate
    shift 2
    local -a candidates=("$url")
    if [[ "$url" == *github.com* || "$url" == *codeload.github.com* ]]; then
        local m
        for m in $ONYX_GH_MIRRORS; do
            candidates+=("${m%/}/${url}")
        done
    fi
    candidates+=("$@")
    if [[ "${ONYX_FETCH_SKIP_DIRECT:-0}" == 1 && ${#candidates[@]} -gt 1 ]]; then
        candidates=("${candidates[@]:1}")
    fi
    for candidate in "${candidates[@]}"; do
        if curl --fail --silent --show-error --location \
            --proto '=https' --proto-redir '=https' --tlsv1.2 \
            --retry 2 --retry-all-errors --connect-timeout 15 \
            --output "$out" "$candidate"; then
            return 0
        fi
        rm -f "$out"
    done
    ui_err "Не удалось скачать: $url — GitHub и зеркала недоступны."
    return 1
}

onyx_git_fetch_pinned() {
    # onyx_git_fetch_pinned DIR REPO_URL REF MODE COMMIT [SHA256]
    # Fetch a pinned source tree: plain git first; when GitHub git endpoints
    # are unreachable, fall back to the pinned github archive tarball through
    # the mirrors. MODE is "commit" or "tag". SHA256, when given, must match
    # the github archive tarball (release bundles ship exactly that archive,
    # so bundle checksums are valid for it). Leaves DIR with sources; .git
    # exists only on the git path.
    local dir="$1" repo="$2" ref="$3" mode="$4" commit="$5" want_sha="${6:-}"
    if [[ ! -d "$dir/.git" ]]; then
        rm -rf "$dir"
        mkdir -p "$dir"
        git -C "$dir" init -q
    fi
    git -C "$dir" remote remove origin 2>/dev/null || true
    git -C "$dir" remote add origin "$repo"
    if [[ "$mode" == "tag" ]]; then
        git -C "$dir" fetch -q --depth 1 origin tag "$ref" 2>/dev/null &&
            git -C "$dir" checkout -q --detach FETCH_HEAD 2>/dev/null || true
    else
        git -C "$dir" fetch -q --depth 1 origin "$ref" 2>/dev/null &&
            git -C "$dir" checkout -q --detach --force FETCH_HEAD 2>/dev/null || true
    fi
    [[ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" == "$commit" ]] && return 0
    ui_warn "git-эндпоинты GitHub недоступны — качаю закреплённый архив через зеркала..."
    local tarball repo_path
    repo_path="${repo#https://github.com/}"
    repo_path="${repo_path%.git}"
    tarball="$(mktemp /tmp/onyx-pinned-src.XXXXXX.tar.gz)"
    if ! onyx_fetch "$tarball" "https://github.com/${repo_path}/archive/${commit}.tar.gz"; then
        rm -f "$tarball"
        return 1
    fi
    if [[ -n "$want_sha" ]]; then
        echo "$want_sha  $tarball" | sha256sum -c - >/dev/null 2>&1 || {
            rm -f "$tarball"
            ui_err "Контрольная сумма архивного tarball не совпала (${repo_path}@${commit})."
            return 1
        }
    fi
    rm -rf "$dir"
    mkdir -p "$dir"
    tar -xzf "$tarball" -C "$dir" --strip-components=1 --no-same-owner
    rm -f "$tarball"
}

onyx_curl_shim_dir() {
    # onyx_curl_shim_dir DIR — create a directory with a `curl` shim that
    # behaves like real curl, then retries GitHub URLs through the mirrors,
    # then falls back to bundled Telegram files (proxy-secret/proxy-multi.conf)
    # for core.telegram.org endpoints. Used to run the pinned MTProxy
    # installer unmodified on networks where GitHub/Telegram are blocked:
    #   PATH="$DIR:$PATH" ONYX_REAL_CURL="$(command -v curl)" \
    #       ONYX_BUNDLED_DIR="$BASE/assets/mtproxy" "$MT_INSTALLER"
    local dir="$1"
    install -d -m 0755 "$dir"
    cat > "$dir/curl" <<'SHIM'
#!/usr/bin/env bash
# Onyx Panel curl shim: transparent mirror/bundle fallback. See
# onyx_curl_shim_dir in onyx-install-ui.sh.
set -uo pipefail
REAL_CURL="${ONYX_REAL_CURL:-/usr/bin/curl}"
"$REAL_CURL" "$@"
rc=$?
[[ $rc -eq 0 ]] && exit 0
url=""
out=""
prev=""
for a in "$@"; do
    if [[ "$prev" == "--output" || "$prev" == "-o" ]]; then
        out="$a"
    elif [[ "$a" == --output=* ]]; then
        out="${a#*=}"
    elif [[ "$a" != -* ]]; then
        url="$a"
    fi
    prev="$a"
done
if [[ -n "$out" && -n "$url" && "$url" == *github.com/* ]]; then
    for m in ${ONYX_GH_MIRRORS:-}; do
        "$REAL_CURL" --fail --silent --show-error --location \
            --proto '=https' --proto-redir '=https' --tlsv1.2 \
            --retry 2 --retry-all-errors --connect-timeout 15 \
            --output "$out" "${m%/}/${url}" && exit 0
    done
fi
if [[ -n "$out" && -n "${ONYX_BUNDLED_DIR:-}" && -d "$ONYX_BUNDLED_DIR" ]]; then
    case "$url" in
        *getProxySecret) [[ -s "$ONYX_BUNDLED_DIR/proxy-secret" ]] && \
            cp "$ONYX_BUNDLED_DIR/proxy-secret" "$out" && exit 0 ;;
        *getProxyConfig) [[ -s "$ONYX_BUNDLED_DIR/proxy-multi.conf" ]] && \
            cp "$ONYX_BUNDLED_DIR/proxy-multi.conf" "$out" && exit 0 ;;
    esac
fi
exit "$rc"
SHIM
    chmod 0755 "$dir/curl"
}
