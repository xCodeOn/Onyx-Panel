#!/usr/bin/env python3
"""Small, root-only OpenFlux exit-node controller used by Onyx Panel."""

import io
import json
import os
import pwd
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import http.cookiejar
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

CONFIG_DIR = Path("/etc/onyx-panel/openflux")
STATE_FILE = CONFIG_DIR / "config.json"
URL_FILE = CONFIG_DIR / "document-url"
KEY_FILE = CONFIG_DIR / "encryption-key"
ENABLED_FILE = CONFIG_DIR / "enabled"
BIN = Path("/opt/onyx-panel/openflux/openflux")
UNIT = Path("/etc/systemd/system/onyx-panel-openflux.service")
SERVICE = "onyx-panel-openflux.service"
SERVICE_USER = "onyx-openflux"
LEGACY_IOS_DROPIN = Path("/etc/systemd/system/onyx-panel-openflux.service.d/ios.conf")
PROFILES_DIR = CONFIG_DIR / "profiles"
VERSION = "1.0.0"
MAX_OPENFLUX_PROFILES = 32
TRANSPORT = "yandex"
TRANSPORTS = {"yandex", "mailru"}
CODEC = "batched"
MODE = "l4"
# Watchdog: the public document is the single point of failure of the
# transport, so the panel periodically makes sure it is still reachable.
HEALTH_FILE = CONFIG_DIR / "health.json"
TOKEN_FILE = CONFIG_DIR / "yandex-token"
YANDEX_API = "https://cloud-api.yandex.net/v1/disk"
YANDEX_FOLDER = "disk:/OnyxPanel-OpenFlux"
MAILRU_CREDENTIALS_FILE = CONFIG_DIR / "mailru-credentials"
MAILRU_COOKIE_FILE = CONFIG_DIR / "mailru-cookies"
MAILRU_SESSION_FILE = CONFIG_DIR / "mailru-session"
MAILRU_API = "https://cloud.mail.ru/api/v2"
MAILRU_OAUTH = "https://o2.mail.ru/token"
MAILRU_CLIENT_ID = "cloud-win"
MAILRU_FOLDER = "/OnyxPanel-OpenFlux"
# Mail.ru мягче к браузерному User-Agent: служебный UA усиливает анти-бот проверки
MAILRU_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
DEAD_AFTER = 2          # consecutive dead probes before the bell rings
SWAP_COOLDOWN = 3600    # minimum seconds between automatic fallback swaps
CHECK_TIMEOUT = 8
USER_AGENT = "Onyx-Panel OpenFlux watchdog"
_CONFIG_LOCK = threading.Lock()
_MAILRU_TOKEN_LOCK = threading.Lock()


class OpenFluxError(RuntimeError):
    pass


class MailruCaptchaNeeded(OpenFluxError):
    """Устарело: веб-логин Mail.ru больше не работает с серверов.

    Форма входа отдаёт капчу типа Google reCAPTCHA и отклоняет любые
    запросы без браузерного окружения (errno 609 при любом ответе).
    Класс оставлен для совместимости HTTP-обработчика панели; вход
    теперь только через OAuth «пароля для внешних приложений»."""

    def __init__(self, token="", image=b""):
        super().__init__("Вход Mail.ru по логину и паролю почты больше не поддерживается — используйте «пароль для внешних приложений».")
        self.token = token
        self.image = image


def _mailru_grant(data):
    """POST o2.mail.ru/token (официальный OAuth Облака); JSON с access_token."""
    request = urllib.request.Request(MAILRU_OAUTH, data=urlencode(data).encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": MAILRU_UA})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise OpenFluxError("Mail.ru недоступен: " + str(exc)) from exc
    try:
        envelope = json.loads(raw)
    except ValueError as exc:
        raise OpenFluxError("Mail.ru OAuth вернул не JSON: " + raw[:120]) from exc
    if not envelope.get("access_token"):
        if envelope.get("error_code") == 3 or envelope.get("error") in ("invalid_grant", "invalid username or password"):
            raise OpenFluxError("Mail.ru отклонил логин или пароль. Сторонним приложениям нужен «пароль для внешних приложений» — обычный пароль почты не подходит. Создайте его в настройках почты: Настройки → «Безопасность» → «Пароли для внешних приложений» (help.mail.ru/mail/security/protection/external) и введите здесь.")
        raise OpenFluxError("Mail.ru не выдал токен: " +
                            str(envelope.get("error_description") or envelope.get("error") or raw)[:200])
    return envelope


def _store_mailru_credentials(email, password, envelope):
    expires = int(envelope.get("expires_in") or 0)
    _atomic(MAILRU_CREDENTIALS_FILE, json.dumps({
        "email": email, "password": password,
        "access_token": envelope.get("access_token", ""),
        "refresh_token": envelope.get("refresh_token", ""),
        "expires_at": int(time.time()) + expires - 300 if expires else 0,
    }, ensure_ascii=True) + chr(10), 0o600)


def save_mailru_credentials(email, password, code=None, captcha=None, captcha_token=None):
    """Проверить почту и «пароль для внешних приложений» живым OAuth-входом (0600).

    code/captcha приняты для совместимости HTTP-обработчика и не используются:
    веб-логин Mail.ru с капчей больше не работает с серверов."""
    email = str(email or "").strip()
    password = str(password or "")
    if not email or "@" not in email or not password:
        raise OpenFluxError("Укажите почту Mail.ru и «пароль для внешних приложений» — обычный пароль почты сторонним приложениям не подходит.")
    envelope = _mailru_grant({"grant_type": "password", "username": email,
                              "password": password, "client_id": MAILRU_CLIENT_ID})
    _store_mailru_credentials(email, password, envelope)
    return email


def load_mailru_credentials():
    try:
        data = json.loads(MAILRU_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("email") and data.get("password"):
        return data
    return None


def _mailru_access_token(force_refresh=False):
    """Живой OAuth-токен Облака; протухший обновляет refresh-токеном или паролем."""
    with _MAILRU_TOKEN_LOCK:
        credentials = load_mailru_credentials()
        if not credentials:
            raise OpenFluxError("Аккаунт Mail.ru не подключён — войдите в блоке «Создать документ автоматически».")
        if not force_refresh:
            expires_at = int(credentials.get("expires_at") or 0)
            if credentials.get("access_token") and (not expires_at or expires_at - 60 > time.time()):
                return credentials["access_token"]
        if credentials.get("refresh_token"):
            envelope = _mailru_grant({"grant_type": "refresh_token",
                                      "refresh_token": credentials["refresh_token"],
                                      "client_id": MAILRU_CLIENT_ID})
        else:
            envelope = _mailru_grant({"grant_type": "password",
                                      "username": credentials["email"],
                                      "password": credentials["password"],
                                      "client_id": MAILRU_CLIENT_ID})
        _store_mailru_credentials(credentials["email"], credentials["password"], envelope)
        return envelope["access_token"]


def mailru_status():
    """Статус подключения Облака Mail.ru для карточки: {connected, email}."""
    try:
        _mailru_access_token()
        credentials = load_mailru_credentials()
        return {"connected": True,
                "email": credentials.get("email") if credentials else None}
    except (OpenFluxError, FileNotFoundError, OSError, ValueError):
        return {"connected": False, "email": None}


def disconnect_mailru():
    """Отключить аккаунт: стереть сохранённые токены и пароль приложения."""
    for path in (MAILRU_CREDENTIALS_FILE, MAILRU_COOKIE_FILE, MAILRU_SESSION_FILE):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _codec_for(config):
    """Use the codec built into the current iOS and Android clients."""
    return CODEC


def _clean_transport(value):
    value = str(value or TRANSPORT).strip().lower()
    if value not in TRANSPORTS:
        raise OpenFluxError("Выберите Яндекс Документы или Mail.ru Документы.")
    return value


def _transport_for(config):
    value = str((config or {}).get("transport", TRANSPORT)).lower()
    return value if value in TRANSPORTS else TRANSPORT


def _run(args, *, check=True, timeout=20):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=check)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise OpenFluxError(detail.strip()[-1200:] or "OpenFlux command failed") from exc


def _atomic(path, value, mode, uid=0, gid=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def validate_document_url(value, transport=TRANSPORT):
    transport = _clean_transport(transport)
    value = str(value or "").strip()
    if not value or len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise OpenFluxError("Укажите корректную ссылку на документ Яндекса.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise OpenFluxError("Ссылка на документ имеет неверный формат.") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if transport == "mailru":
        allowed = host == "cloud.mail.ru" and bool(re.fullmatch(r"/public/[^/]+/[^/]+/?", parsed.path))
        provider = "Mail.ru"
    else:
        allowed = host in {"yandex.ru", "yandex.com"} or host.endswith(".yandex.ru") or host.endswith(".yandex.com")
        provider = "Яндекса"
    if parsed.scheme != "https" or not allowed or port not in (None, 443):
        raise OpenFluxError(f"Укажите публичную HTTPS-ссылку на документ {provider}.")
    if parsed.username or parsed.password or parsed.fragment or not parsed.path or parsed.path == "/":
        raise OpenFluxError("Используйте публичную ссылку на конкретный документ без логина и фрагмента.")
    return value


def _load():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise OpenFluxError("Конфигурация OpenFlux повреждена.") from exc
    return data if isinstance(data, dict) else {}


def _service_active():
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    return result.returncode == 0


def _identity():
    try:
        account = pwd.getpwnam(SERVICE_USER)
    except KeyError as exc:
        raise OpenFluxError("Системный пользователь OpenFlux не создан. Повторите обновление Onyx Panel.") from exc
    return account.pw_uid, account.pw_gid


def _write_private_files(config):
    uid, gid = _identity()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(CONFIG_DIR, 0, gid)
    os.chmod(CONFIG_DIR, 0o750)
    _atomic(URL_FILE, config["url"] + "\n", 0o640, 0, gid)
    _atomic(KEY_FILE, config["key"] + "\n", 0o640, 0, gid)
    _atomic(STATE_FILE, json.dumps(config, ensure_ascii=True, indent=2) + "\n", 0o600)
    if config.get("enabled", True):
        _atomic(ENABLED_FILE, "enabled\n", 0o600)
    else:
        try:
            ENABLED_FILE.unlink()
        except FileNotFoundError:
            pass


def _unit_text(config=None):
    config = config if isinstance(config, dict) else _load()
    encryption_argument = "" if config.get("ios_compatible", False) else f" --encryption-key-file={KEY_FILE}"
    codec = _codec_for(config)
    transport = _transport_for(config)
    return f"""[Unit]
Description=Onyx Panel OpenFlux exit node
After=network-online.target
Wants=network-online.target
ConditionPathExists={ENABLED_FILE}

[Service]
Type=simple
User={SERVICE_USER}
Group={SERVICE_USER}
UMask=0077
ExecStart=/bin/sh -c 'exec {BIN} --role=exit --mode={MODE} --codec={codec} --transport={transport} --url "$$(cat {URL_FILE})"{encryption_argument}'
Restart=on-failure
RestartSec=4
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
"""


def install_service():
    if not BIN.is_file() or not os.access(BIN, os.X_OK):
        raise OpenFluxError("Бинарник OpenFlux не установлен. Повторите обновление Onyx Panel.")
    config = _load()
    try:
        legacy_dropin_text = LEGACY_IOS_DROPIN.read_text(encoding="utf-8")
    except FileNotFoundError:
        legacy_dropin_text = ""
    if legacy_dropin_text and "--encryption-key-file" not in legacy_dropin_text:
        config["ios_compatible"] = True
        if config.get("url") and config.get("key"):
            _write_private_files(config)
    expected = _unit_text(config)
    try:
        current = UNIT.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    legacy_dropin_removed = False
    try:
        LEGACY_IOS_DROPIN.unlink()
        legacy_dropin_removed = True
    except FileNotFoundError:
        pass
    if current != expected:
        _atomic(UNIT, expected, 0o644)
        legacy_dropin_removed = True
    if legacy_dropin_removed:
        _run(["systemctl", "daemon-reload"])


def _start():
    install_service()
    _run(["systemctl", "restart", SERVICE])
    stable = 0
    for _ in range(20):
        if _service_active():
            stable += 1
            if stable >= 4:
                return
        else:
            stable = 0
        time.sleep(0.25)
    journal = _run(["journalctl", "-u", SERVICE, "-n", "30", "--no-pager"], check=False).stdout
    raise OpenFluxError("OpenFlux не запустился. " + journal.strip()[-900:])


def configure(document_url, ios_compatible=False, transport=TRANSPORT):
    transport = _clean_transport(transport)
    url = validate_document_url(document_url, transport)
    check = check_document(url, transport)
    if check["status"] == "dead":
        raise OpenFluxError("Документ не отвечает как публичный: " + check["detail"] + " Сохранение прервано.")
    previous = _load()
    config = {
        "enabled": True,
        "url": url,
        "key": previous.get("key") if isinstance(previous.get("key"), str) and len(previous["key"]) >= 24 else secrets.token_urlsafe(32),
        "transport": transport,
        "codec": CODEC,
        "mode": MODE,
        "ios_compatible": bool(ios_compatible),
        "version": VERSION,
        "updated_at": int(time.time()),
    }
    _write_private_files(config)
    try:
        _start()
    except Exception:
        if previous.get("url") and previous.get("key"):
            _write_private_files(previous)
            try:
                _start()
            except Exception:
                pass
        else:
            subprocess.run(["systemctl", "stop", SERVICE], check=False, timeout=15)
        raise
    return state()


def set_enabled(enabled):
    config = _load()
    if not config.get("url") or not config.get("key"):
        raise OpenFluxError("Сначала сохраните ссылку на документ.")
    config["enabled"] = bool(enabled)
    config["updated_at"] = int(time.time())
    _write_private_files(config)
    if enabled:
        try:
            _start()
        except Exception:
            config["enabled"] = False
            _write_private_files(config)
            subprocess.run(["systemctl", "stop", SERVICE], check=False, timeout=15)
            raise
    else:
        _run(["systemctl", "stop", SERVICE], check=False)
    return state()


def rotate_key():
    config = _load()
    if not config.get("url"):
        raise OpenFluxError("Сначала сохраните ссылку на документ.")
    previous = dict(config)
    config["key"] = secrets.token_urlsafe(32)
    config["enabled"] = True
    config["updated_at"] = int(time.time())
    _write_private_files(config)
    try:
        _start()
    except Exception:
        _write_private_files(previous)
        if previous.get("enabled", True):
            try:
                _start()
            except Exception:
                pass
        raise
    return state()


def restore_if_configured():
    config = _load()
    if not config.get("url") or not config.get("key"):
        restored = False
    else:
        _write_private_files(config)
        install_service()
        if config.get("enabled", True):
            _start()
        else:
            _run(["systemctl", "stop", SERVICE], check=False)
        restored = True
    for profile in _extra_configs():
        _write_extra_files(profile)
        _install_extra_service(profile)
        if profile.get("enabled", True):
            _start_extra(profile)
        else:
            _run(["systemctl", "stop", _extra_service(profile["id"])], check=False)
    return restored or bool(_extra_configs())


def state():
    config = _load()
    configured = bool(config.get("url") and config.get("key"))
    return {
        "configured": configured,
        "enabled": bool(config.get("enabled", True)) if configured else False,
        "active": _service_active() if configured else False,
        "url": config.get("url", "") if configured else "",
        "key": config.get("key", "") if configured else "",
        "transport": _transport_for(config),
        "codec": _codec_for(config),
        "mode": MODE,
        "ios_compatible": bool(config.get("ios_compatible", False)),
        "encrypted": not bool(config.get("ios_compatible", False)),
        "version": VERSION,
    }


def _clean_profile_name(value):
    value = str(value or "").strip()
    if not value or len(value) > 80 or any(ord(char) < 32 for char in value):
        raise OpenFluxError("Укажите имя профиля длиной от 1 до 80 символов.")
    return value


def _clean_platform(value):
    value = str(value or "").lower()
    if value not in ("ios", "android"):
        raise OpenFluxError("Выберите iOS или Android.")
    return value


def _extra_id(value):
    value = str(value or "")
    if not re.fullmatch(r"[a-f0-9]{16}", value):
        raise OpenFluxError("Профиль OpenFlux не найден.")
    return value


def _extra_paths(profile_id):
    directory = PROFILES_DIR / _extra_id(profile_id)
    return {
        "dir": directory,
        "state": directory / "config.json",
        "url": directory / "document-url",
        "key": directory / "encryption-key",
        "enabled": directory / "enabled",
        "unit": Path("/etc/systemd/system") / ("onyx-panel-openflux-" + profile_id + ".service"),
    }


def _extra_service(profile_id):
    return "onyx-panel-openflux-" + _extra_id(profile_id) + ".service"


def _extra_configs():
    result = []
    try:
        entries = list(PROFILES_DIR.iterdir())
    except FileNotFoundError:
        return result
    for entry in entries:
        if not entry.is_dir() or not re.fullmatch(r"[a-f0-9]{16}", entry.name):
            continue
        try:
            value = json.loads((entry / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("id") == entry.name:
            result.append(value)
    return sorted(result, key=lambda item: (int(item.get("created_at", 0)), item["id"]))


def _write_extra_files(config):
    paths = _extra_paths(config["id"])
    _, gid = _identity()
    paths["dir"].mkdir(parents=True, exist_ok=True)
    os.chown(paths["dir"], 0, gid)
    os.chmod(paths["dir"], 0o750)
    _atomic(paths["url"], config["url"] + "\n", 0o640, 0, gid)
    _atomic(paths["key"], config["key"] + "\n", 0o640, 0, gid)
    _atomic(paths["state"], json.dumps(config, ensure_ascii=True, indent=2) + "\n", 0o600)
    if config.get("enabled", True):
        _atomic(paths["enabled"], "enabled\n", 0o600)
    else:
        try:
            paths["enabled"].unlink()
        except FileNotFoundError:
            pass


def _extra_unit_text(config):
    paths = _extra_paths(config["id"])
    encryption = "" if config.get("platform") == "ios" else " --encryption-key-file=" + str(paths["key"])
    codec = _codec_for(config)
    transport = _transport_for(config)
    return f"""[Unit]
Description=Onyx Panel OpenFlux profile {config['id']}
After=network-online.target
Wants=network-online.target
ConditionPathExists={paths['enabled']}

[Service]
Type=simple
User={SERVICE_USER}
Group={SERVICE_USER}
UMask=0077
ExecStart=/bin/sh -c 'exec {BIN} --role=exit --mode={MODE} --codec={codec} --transport={transport} --url "$$(cat {paths['url']})"{encryption}'
Restart=on-failure
RestartSec=4
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
"""


def _install_extra_service(config):
    if not BIN.is_file() or not os.access(BIN, os.X_OK):
        raise OpenFluxError("Бинарник OpenFlux не установлен. Повторите обновление Onyx Panel.")
    paths = _extra_paths(config["id"])
    expected = _extra_unit_text(config)
    try:
        current = paths["unit"].read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if current != expected:
        _atomic(paths["unit"], expected, 0o644)
        _run(["systemctl", "daemon-reload"])


def _extra_active(profile_id):
    return subprocess.run(["systemctl", "is-active", "--quiet", _extra_service(profile_id)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=5, check=False).returncode == 0


def _start_extra(config):
    _install_extra_service(config)
    service = _extra_service(config["id"])
    _run(["systemctl", "enable", service])
    _run(["systemctl", "restart", service])
    stable = 0
    for _ in range(20):
        if _extra_active(config["id"]):
            stable += 1
            if stable >= 4:
                return
        else:
            stable = 0
        time.sleep(0.25)
    journal = _run(["journalctl", "-u", service, "-n", "30", "--no-pager"], check=False).stdout
    raise OpenFluxError("Профиль OpenFlux не запустился. " + journal.strip()[-900:])


def profile_states():
    result = []
    health = _load_health()
    main = state()
    if main["configured"]:
        config = _load()
        main.update({"id": "main", "name": config.get("name", "OpenFlux"),
                     "platform": "ios" if main["ios_compatible"] else "android",
                     "expires_at": config.get("expires_at"),
                     "fallback_url": config.get("fallback_url", ""),
                     "fallback_transport": config.get("fallback_transport", ""),
                     "health": health.get("main", {})})
        result.append(main)
    for config in _extra_configs():
        result.append({
            "id": config["id"], "name": config.get("name", "OpenFlux"),
            "platform": config.get("platform", "android"), "url": config.get("url", ""),
            "key": config.get("key", ""), "configured": True,
            "enabled": bool(config.get("enabled", True)), "active": _extra_active(config["id"]),
            "encrypted": config.get("platform") != "ios", "transport": _transport_for(config),
            "codec": _codec_for(config), "version": VERSION,
            "created_at": int(config.get("created_at", 0) or 0),
            "expires_at": config.get("expires_at"),
            "fallback_url": config.get("fallback_url", ""),
            "fallback_transport": config.get("fallback_transport", ""),
            "health": health.get(config["id"], {}),
        })
    return result


def create_profile(name, document_url, platform, transport=TRANSPORT, expires=""):
    if len(_extra_configs()) >= MAX_OPENFLUX_PROFILES:
        raise OpenFluxError("Достигнут лимит профилей OpenFlux.")
    profile_id = secrets.token_hex(8)
    transport = _clean_transport(transport)
    document_url = validate_document_url(document_url, transport)
    check = check_document(document_url, transport)
    if check["status"] == "dead":
        raise OpenFluxError("Документ не отвечает как публичный: " + check["detail"] + " Проверьте ссылку.")
    platform = _clean_platform(platform)
    expires_at = _clean_expires(expires)
    existing_urls = {item.get("url") for item in _extra_configs()}
    existing_urls.add(_load().get("url"))
    if document_url in existing_urls:
        raise OpenFluxError("Этот документ уже используется другим профилем OpenFlux.")
    config = {"id": profile_id, "name": _clean_profile_name(name),
              "url": document_url, "platform": platform,
              "key": secrets.token_urlsafe(32), "enabled": True, "transport": transport,
              "codec": CODEC, "mode": MODE, "version": VERSION,
              "created_at": int(time.time()), "updated_at": int(time.time())}
    if expires_at:
        config["expires_at"] = expires_at
    _write_extra_files(config)
    try:
        _start_extra(config)
    except Exception:
        _run(["systemctl", "disable", "--now", _extra_service(profile_id)], check=False)
        shutil.rmtree(_extra_paths(profile_id)["dir"], ignore_errors=True)
        try:
            _extra_paths(profile_id)["unit"].unlink()
        except FileNotFoundError:
            pass
        _run(["systemctl", "daemon-reload"], check=False)
        raise
    return config


def profile_set_enabled(profile_id, enabled):
    if profile_id == "main":
        return set_enabled(enabled)
    config = next((item for item in _extra_configs() if item["id"] == _extra_id(profile_id)), None)
    if config is None:
        raise OpenFluxError("Профиль OpenFlux не найден.")
    config["enabled"] = bool(enabled)
    config["updated_at"] = int(time.time())
    _write_extra_files(config)
    if enabled:
        try:
            _start_extra(config)
        except Exception:
            config["enabled"] = False
            _write_extra_files(config)
            raise
    else:
        _run(["systemctl", "stop", _extra_service(config["id"])], check=False)


def profile_rotate(profile_id):
    if profile_id == "main":
        return rotate_key()
    config = next((item for item in _extra_configs() if item["id"] == _extra_id(profile_id)), None)
    if config is None:
        raise OpenFluxError("Профиль OpenFlux не найден.")
    config["key"] = secrets.token_urlsafe(32)
    config["enabled"] = True
    config["updated_at"] = int(time.time())
    _write_extra_files(config)
    _start_extra(config)


def delete_profile(profile_id):
    if profile_id == "main":
        config = _load()
        if not config.get("url"):
            raise OpenFluxError("Профиль OpenFlux не найден.")
        _run(["systemctl", "disable", "--now", SERVICE], check=False)
        for path in (STATE_FILE, URL_FILE, KEY_FILE, ENABLED_FILE):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        # Keep the unit template installed so a new main profile can be
        # created later without reinstalling the panel.
        install_service()
        return
    profile_id = _extra_id(profile_id)
    paths = _extra_paths(profile_id)
    if not paths["state"].exists():
        raise OpenFluxError("Профиль OpenFlux не найден.")
    _run(["systemctl", "disable", "--now", _extra_service(profile_id)], check=False)
    try:
        paths["unit"].unlink()
    except FileNotFoundError:
        pass
    shutil.rmtree(paths["dir"], ignore_errors=True)
    _run(["systemctl", "daemon-reload"], check=False)


# ---------------------------------------------------------------------------
# Watchdog, резервный документ и автосоздание (Яндекс Диск).
# ---------------------------------------------------------------------------
#
# Публичный документ — единственная точка отказа транспорта: сервис жив, пока
# жив документ. Панель периодически проверяет, что ссылка всё ещё отвечает,
# оповещает колокольчик и Telegram, а при настроенном резервном документе
# переключает профиль на него (основной и резервный слоты меняются местами).


def _clean_expires(value):
    """'YYYY-MM-DD' -> конец этого дня (epoch) или None для пустой строки."""
    value = str(value or "").strip()
    if not value:
        return None
    try:
        stamp = time.mktime(time.strptime(value, "%Y-%m-%d"))
    except ValueError as exc:
        raise OpenFluxError("Дата отключения — в формате ГГГГ-ММ-ДД, например 2030-01-31.") from exc
    return int(stamp) + 86399


def expired_profiles(profiles, now=None):
    """Активные профили с прошедшим сроком; чистая функция — для тестов."""
    now = int(now or time.time())
    return [dict(profile) for profile in (profiles or [])
            if profile.get("expires_at") and profile.get("enabled", True)
            and now > int(profile["expires_at"])]


def _fetch_page(url, timeout):
    """GET страницы: (код ответа, текст ошибки). Ничего не бросает."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(4096)
        return response.getcode(), None
    except urllib.error.HTTPError as exc:
        try:
            exc.read(4096)
        except Exception:
            pass
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, str(exc)


def _page_state(url, timeout, fetch):
    code, error = fetch(url, timeout)
    if code == 200:
        return "ok", "Документ отвечает."
    if code in (404, 410):
        return "dead", "Документ не найден — ссылка отозвана или файл удалён."
    if code is None:
        return "unknown", "Нет связи с провайдером: " + (error or "таймаут")
    return "unknown", "Провайдер ответил кодом " + str(code) + "."


def _yandex_public_state(url, timeout, fetch):
    # API публичных ресурсов Диска отвечает уверенно для файлов Диска;
    # страницы редактора «Яндекс Документов» им не резолвятся — тогда
    # остается проверка самой страницы.
    api = YANDEX_API + "/resources?public_key=" + quote(url, safe="")
    code, _ = fetch(api, timeout)
    if code == 200:
        return "ok", "Яндекс Диск подтверждает, что файл публичный."
    if code == 404:
        return "dead", "Яндекс не находит файл — ссылка отозвана или удалена."
    return _page_state(url, timeout, fetch)


def check_document(url, transport=TRANSPORT, timeout=CHECK_TIMEOUT, fetch=None):
    """Проверка публичной доступности документа без всяких токенов.

    ok — отвечает; dead — провайдер уверенно отвечает, что документа больше
    нет; unknown — сеть или сам сервис не дали ответа (считаем живым, чтобы
    не будить колокольчик из-за случайного сбоя)."""
    url = validate_document_url(url, transport)
    transport = _clean_transport(transport)
    fetch = fetch or _fetch_page
    if transport == "yandex":
        status, detail = _yandex_public_state(url, timeout, fetch)
    else:
        status, detail = _page_state(url, timeout, fetch)
    return {"status": status, "detail": detail}


def _load_health():
    try:
        data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_health(health):
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic(HEALTH_FILE, json.dumps(health, ensure_ascii=True, sort_keys=True, indent=2) + "\n", 0o600)


def _profile_config(profile_id):
    """Конфиг профиля на изменение: 'main' — главный, иначе из profiles/."""
    if profile_id == "main":
        config = dict(_load())
    else:
        config = next((dict(item) for item in _extra_configs() if item["id"] == _extra_id(profile_id)), None)
    if not config or not config.get("url") or not config.get("key"):
        raise OpenFluxError("Профиль OpenFlux не найден.")
    return config


def _store_profile(config):
    if config.get("id") == "main" or (config.get("url") == _load().get("url") and "id" not in config):
        config.pop("id", None)
        _write_private_files(config)
    else:
        _write_extra_files(config)


def _used_urls():
    urls = set()
    for profile in profile_states():
        if profile.get("url"):
            urls.add(profile["url"])
        if profile.get("fallback_url"):
            urls.add(profile["fallback_url"])
    return urls


def _start_profile(config):
    if config.get("id") == "main" or "id" not in config:
        _start()
    else:
        _start_extra(config)


def set_fallback(profile_id, fallback_url, fallback_transport):
    """Запомнить резервный документ другого транспорта на случай смерти основного."""
    fallback_transport = _clean_transport(fallback_transport)
    fallback_url = validate_document_url(fallback_url, fallback_transport)
    with _CONFIG_LOCK:
        config = _profile_config(profile_id)
        if config.get("transport") == fallback_transport:
            raise OpenFluxError("Резервный транспорт должен отличаться от основного: Яндекс ↔ Mail.ru.")
        if fallback_url == config.get("url"):
            raise OpenFluxError("Резервный документ должен отличаться от основного.")
        others = _used_urls() - {config.get("url"), config.get("fallback_url")}
        if fallback_url in others:
            raise OpenFluxError("Этот документ уже используется другим профилем OpenFlux.")
        check = check_document(fallback_url, fallback_transport)
        if check["status"] == "dead":
            raise OpenFluxError("Резервный документ недоступен: " + check["detail"])
        config["fallback_url"] = fallback_url
        config["fallback_transport"] = fallback_transport
        config["updated_at"] = int(time.time())
        _store_profile(config)
    return config


def clear_fallback(profile_id):
    with _CONFIG_LOCK:
        config = _profile_config(profile_id)
        if not config.get("fallback_url"):
            return config
        config.pop("fallback_url", None)
        config.pop("fallback_transport", None)
        config["updated_at"] = int(time.time())
        _store_profile(config)
    return config


def apply_fallback(profile_id):
    """Переключить профиль на резервный документ; слоты меняются местами,
    чтобы смерть второго документа вернула профиль на первый."""
    with _CONFIG_LOCK:
        config = _profile_config(profile_id)
        fallback_url = config.get("fallback_url")
        fallback_transport = config.get("fallback_transport")
        if not fallback_url or fallback_transport not in TRANSPORTS:
            raise OpenFluxError("У профиля нет резервного документа.")
        previous = dict(config)
        config["url"], config["transport"] = fallback_url, fallback_transport
        config["fallback_url"], config["fallback_transport"] = previous["url"], previous["transport"]
        config["swapped_at"] = int(time.time())
        config["swaps"] = int(config.get("swaps", 0)) + 1
        config["enabled"] = True
        config["updated_at"] = int(time.time())
        _store_profile(config)
        _start_profile(config)
    return config


def watchdog_tick(now=None, profiles=None, health=None, fetch=None, disable=None):
    """Один проход проверки всех активных профилей.

    Возвращает список событий для колокольчика и Telegram:
    revoked / dead / switched / swap-failed / recovered.
    profiles, health, fetch и disable — точки внедрения для тестов."""
    now = int(now or time.time())
    profiles = profile_states() if profiles is None else profiles
    health = _load_health() if health is None else health
    disable = disable or (lambda pid: profile_set_enabled(pid, False))
    events = []
    for profile in expired_profiles(profiles, now):
        try:
            disable(profile["id"])
        except OpenFluxError:
            continue
        events.append({"id": profile["id"], "name": profile.get("name", "OpenFlux"), "event": "revoked",
                       "message": "Срок доступа истёк — профиль OpenFlux «%s» отключён автоматически." % profile.get("name", "OpenFlux")})
    for profile in profiles:
        if not (profile.get("enabled", True) and profile.get("active", False)):
            continue
        if not profile.get("url"):
            continue
        try:
            check = check_document(profile["url"], profile.get("transport", TRANSPORT), fetch=fetch)
        except OpenFluxError:
            continue  # ссылка перестала проходить валидацию — разберёмся вручную
        pid = str(profile.get("id", "main"))
        name = str(profile.get("name", "OpenFlux"))
        state = health.setdefault(pid, {})
        state["checked_at"] = now
        state["detail"] = check["detail"]
        state["status"] = check["status"]
        if check["status"] == "ok":
            if state.get("alerted"):
                state["alerted"] = False
                events.append({"id": pid, "name": name, "event": "recovered",
                               "message": "Документ OpenFlux «%s» снова доступен." % name})
            state["failures"] = 0
        elif check["status"] == "dead":
            failures = int(state.get("failures", 0)) + 1
            state["failures"] = failures
            if failures == DEAD_AFTER and not state.get("alerted"):
                state["alerted"] = True
                events.append({"id": pid, "name": name, "event": "dead",
                               "message": "Транспорт OpenFlux «%s» потерял документ: %s" % (name, check["detail"])})
                if profile.get("fallback_url") and now - int(state.get("swapped_at", 0) or 0) >= SWAP_COOLDOWN:
                    try:
                        switched = apply_fallback(pid)
                        state["swapped_at"] = now
                        state["alerted"] = False
                        state["failures"] = 0
                        events.append({"id": pid, "name": name, "event": "switched", "url": switched["url"],
                                       "message": "OpenFlux «%s» переключён на резервный документ (%s). Обновите ссылку в приложении: %s"
                                                  % (name, _transport_label(switched["transport"]), switched["url"])})
                    except OpenFluxError as exc:
                        events.append({"id": pid, "name": name, "event": "swap-failed",
                                       "message": "Не удалось переключить OpenFlux «%s» на резерв: %s" % (name, exc)})
    if health:
        _save_health(health)
    return events


def _transport_label(transport):
    try:
        return "Mail.ru" if _clean_transport(transport) == "mailru" else "Яндекс"
    except OpenFluxError:
        return str(transport or "")


def _yandex_request(token, path, method="GET", params=None, data=None, timeout=20):
    """Запрос к API Яндекс Диска от имени токена; JSON-ответ или OpenFluxError."""
    url = YANDEX_API + path
    if params:
        url += "?" + urlencode(params)
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": "OAuth " + token,
                                              "Accept": "application/json",
                                              "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = str(json.loads(exc.read().decode("utf-8", "replace")).get("message", ""))[:200]
        except Exception:
            pass
        if exc.code == 401:
            raise OpenFluxError("Токен Яндекс Диска недействителен или истёк.")
        if exc.code == 403:
            raise OpenFluxError("Яндекс Диск отказал: " + (detail or "нет места или не приняты условия использования."))
        if exc.code == 404:
            raise OpenFluxError("Яндекс Диск: путь не найден.")
        if exc.code == 409 and method == "PUT" and path == "/resources" and (params or {}).get("path") == YANDEX_FOLDER:
            return {}   # папка панели уже существует — это норма
        raise OpenFluxError("Яндекс Диск ответил " + str(exc.code) + (": " + detail if detail else "") + ".") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OpenFluxError("Яндекс Диск недоступен: " + str(exc)) from exc
    return json.loads(raw) if raw.strip() else {}


def validate_yandex_token(token):
    token = str(token or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{20,}", token):
        raise OpenFluxError("Токен Яндекс Диска — длинная строка вида y0_AgAAAA…, без пробелов.")
    _yandex_request(token, "/", timeout=15)
    return token


def load_yandex_token():
    try:
        return TOKEN_FILE.read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError):
        return ""


def save_yandex_token(token):
    token = validate_yandex_token(token)
    _atomic(TOKEN_FILE, token + "\n", 0o600)
    return token


def _upload_plain(href, payload):
    request = urllib.request.Request(href, data=payload, method="PUT",
                                     headers={"Content-Type": "text/plain; charset=utf-8",
                                              "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except (urllib.error.URLError, OSError) as exc:
        raise OpenFluxError("Не удалось залить файл на Яндекс Диск: " + str(exc)) from exc


def _resolve_public_url(url):
    """yadi.sk-короткие ссылки доводим до канонического disk.yandex.ru-вида:
    у них совпадает идентификатор, так что сначала переписываем хост, а сеть
    используем только для прочих коротких доменов."""
    host = (urlsplit(url).hostname or "").lower()
    if host in {"yandex.ru", "yandex.com"} or host.endswith(".yandex.ru") or host.endswith(".yandex.com"):
        return url
    rewritten = re.sub(r"(?i)^https?://(www\.)?yadi\.sk", "https://disk.yandex.ru", url)
    if rewritten != url and (urlsplit(rewritten).hostname or "").endswith(".yandex.ru"):
        return rewritten
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=15) as response:
            return response.geturl() or url
    except (urllib.error.URLError, OSError):
        return url


def create_yandex_document(name="OpenFlux"):
    """Создать текстовый файл на Диске, залить болванку, опубликовать — вернуть ссылку."""
    token = load_yandex_token()
    if not token:
        raise OpenFluxError("Сначала сохраните OAuth-токен Яндекс Диска: панель создаёт документ от имени токена.")
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "openflux").lower()).strip("-")
    path = YANDEX_FOLDER + "/" + (slug or "openflux") + "-" + secrets.token_hex(3) + ".txt"
    try:
        _yandex_request(token, "/resources", method="PUT", params={"path": YANDEX_FOLDER})
        upload = _yandex_request(token, "/resources/upload", params={"path": path, "overwrite": "true"})
        href = str((upload or {}).get("href", ""))
        if not href:
            raise OpenFluxError("Яндекс Диск не выдал ссылку для загрузки файла.")
        _upload_plain(href, ("Onyx Panel OpenFlux document. Created %s. Do not edit.\n"
                             % time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())).encode())
        _yandex_request(token, "/resources/publish", method="PUT", params={"path": path})
        # API публикации возвращает не публичную ссылку, а объект-Link с адресом
        # ресурса: public_url дочитываем из метаданных. Публикация иногда
        # доезжает не мгновенно — несколько вежливых попыток.
        public_url = ""
        for attempt in range(5):
            meta = _yandex_request(token, "/resources", params={"path": path, "fields": "public_url"})
            public_url = str((meta or {}).get("public_url", "") or "")
            if public_url:
                break
            time.sleep(1.0 + attempt)
        if not public_url:
            raise OpenFluxError("Документ создан, но Яндекс не отдал публичную ссылку — попробуйте ещё раз через минуту.")
        url = _resolve_public_url(public_url)
        return validate_document_url(url, "yandex")
    except OpenFluxError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise OpenFluxError("Яндекс Диск ответил неожиданно: " + str(exc)) from exc




# --------------------------- Mail.ru Облако ---------------------------
#
# Официального API у Облака Mail.ru нет — используется внутренний v2 API
# веб-клиента (как в rclone): логин+пароль -> куки -> csrf -> dispatcher ->
# загрузка -> file/add -> file/publish -> weblink. Транспорту OpenFlux нужен
# офисный документ, поэтому загружаем минимальный .docx: публичная ссылка на
# него открывается редактором R7 с правом правки (editor_mode "edit"), что и
# требуется потоку mailru. Пароль хранится на сервере 0600 — scoped-токенов
# Mail.ru не предоставляет.




def _mailru_docx():
    """Минимальный валидный .docx — R7 открывает его как редактируемый документ."""
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Onyx Panel OpenFlux document. Do not edit.</w:t></w:r></w:p></w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _mailru_call(command, params=None, data=None):
    """Вызов cloud.mail.ru/api/v2/<command> с OAuth-токеном; JSON-конверт {status, body}.

    Отказ 401/403 означает протухший токен — один раз обновляем и повторяем."""
    def call(token):
        query = dict(params or {})
        query["access_token"] = token
        query["client_id"] = MAILRU_CLIENT_ID
        url = MAILRU_API + "/" + command + "?" + urlencode(query)
        headers = {"User-Agent": MAILRU_UA, "Referer": "https://cloud.mail.ru/",
                   "Accept": "application/json"}
        body = None
        if data is not None:
            body = urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise OpenFluxError("Mail.ru ответил " + str(exc.code) + " на " + command + ": " + detail) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OpenFluxError("Mail.ru недоступен: " + str(exc)) from exc

    def parse(raw):
        try:
            envelope = json.loads(raw)
        except ValueError as exc:
            raise OpenFluxError("Mail.ru ответил не JSON: " + raw[:120]) from exc
        if int(envelope.get("status", 0) or 0) != 200:
            raise OpenFluxError("Mail.ru отклонил " + command + ": " +
                                json.dumps(envelope.get("body", ""), ensure_ascii=False)[:200])
        return envelope.get("body")

    try:
        return parse(call(_mailru_access_token()))
    except OpenFluxError as exc:
        if "401" not in str(exc) and "403" not in str(exc):
            raise
    return parse(call(_mailru_access_token(force_refresh=True)))


def _mailru_upload(upload_url, token, payload):
    """Сырой PUT файла на загрузчик с авторизацией client_id+token; ответ — mrhash.

    Без token в query загрузчик отвечает 403 Forbidden."""
    sep = "&" if "?" in upload_url else "?"
    request = urllib.request.Request(
        upload_url + sep + urlencode({"client_id": MAILRU_CLIENT_ID, "token": token}),
        data=payload, method="PUT", headers={
            "Content-Type": "application/octet-stream", "Accept": "*/*",
            "User-Agent": MAILRU_UA})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", "replace").strip()
    except (urllib.error.URLError, OSError) as exc:
        raise OpenFluxError("Не удалось загрузить файл в Облако Mail.ru: " + str(exc)) from exc
    if not re.fullmatch(r"[A-F0-9]{40}", raw or ""):
        raise OpenFluxError("Загрузчик Mail.ru вернул неожиданный ответ: " + raw[:120])
    return raw


def create_mailru_document(name="OpenFlux"):
    """Создать .docx в Облаке Mail.ru, опубликовать, вернуть публичную ссылку."""
    def call(command, data=None):
        return _mailru_call(command, params={"api": "2"}, data=data)

    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "openflux").lower()).strip("-")
    home = MAILRU_FOLDER + "/" + (slug or "openflux") + "-" + secrets.token_hex(3) + ".docx"
    try:
        call("folder/add", {"home": MAILRU_FOLDER})
    except OpenFluxError:
        pass   # существующая папка — норма; проблемы токена увидим на следующих шагах
    # адрес загрузчика: отдельный диспетчер загрузок, авторизация не нужна
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://dispatcher.cloud.mail.ru/u", headers={"User-Agent": MAILRU_UA}),
                timeout=30) as response:
            upload_url = response.read().decode("utf-8", "replace").strip().split(" ")[0]
    except (urllib.error.URLError, OSError) as exc:
        raise OpenFluxError("Mail.ru не выдал адрес загрузчика (dispatcher): " + str(exc)) from exc
    if not upload_url:
        raise OpenFluxError("Mail.ru не выдал адрес загрузчика (dispatcher).")
    payload = _mailru_docx()
    token = _mailru_access_token()
    try:
        digest = _mailru_upload(upload_url, token, payload)
    except OpenFluxError as exc:
        if "403" not in str(exc):
            raise
        # токен мог отозваться между шагами — обновляем и грузим ещё раз
        digest = _mailru_upload(upload_url, _mailru_access_token(force_refresh=True), payload)
    call("file/add", {"home": home, "hash": digest, "size": str(len(payload)),
                      "conflict": "rename"})
    weblink = call("file/publish", {"home": home})
    weblink = str(weblink or "").strip().strip('"')
    if not weblink:
        raise OpenFluxError("Mail.ru не вернул weblink опубликованного документа.")
    url = "https://cloud.mail.ru/public/" + weblink.lstrip("/")
    return validate_document_url(url, "mailru")
