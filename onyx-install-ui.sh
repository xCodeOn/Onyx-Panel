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
UI_STEP_N=0
ui_stage() {
    UI_STEP_N=$((UI_STEP_N + 1))
    printf '\n%s\n' "${C_ACCENT}${B}──[ ${UI_STEP_N} ]${R}${C_WHITE}${B} $* ${R}"
}
ui_ok()   { printf '%s\n' "${C_GREEN}  ✔${R} $*"; }
ui_info() { printf '%s\n' "${C_GREY}  ·${R} $*"; }
ui_warn() { printf '%s\n' "${C_AMBER}  ▲${R} $*"; }
ui_err()  { printf '%s\n' "${C_RED}  ✗${R} $*" >&2; }
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
