"""Резервные копии в облачные хранилища: Яндекс Диск, Облако Mail.ru, Google Drive.

Каждое хранилище подключается независимо — свои ключи лежат в
cloud-backup.json (права 0600) и не зависят от раздела OpenFlux:
Яндекс Диск живёт на своём OAuth-токене, Mail.ru — на своей паре
«почта + пароль для внешних приложений» (официальный OAuth Облака),
Google Drive — на своём OAuth-клиенте (scope drive.file — видны только
файлы, созданные панелью). Подключение всегда проверяется живым
запросом, протухший токен обновляется сам. Все запросы — чистый
urllib, панель остаётся без зависимостей.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import onyx_openflux as oa

CONFIG_FILE = "/var/lib/onyx-panel/cloud-backup.json"
YANDEX_DIR = "disk:/OnyxPanel-Backup"
YANDEX_DIR_BARE = "/OnyxPanel-Backup"
MAILRU_DIR = "/OnyxPanel-Backup"
GDRIVE_FOLDER = "OnyxPanel-Backup"
GDRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GDRIVE_TOKEN = "https://oauth2.googleapis.com/token"
GDRIVE_API = "https://www.googleapis.com/drive/v3"
GDRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
UA = "OnyxPanel-Backup/2.1"
TARGETS = ("yandex", "mailru", "gdrive")
TIMEOUT = 120


class CloudError(RuntimeError):
    pass


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(value):
    value = value if isinstance(value, dict) else {}
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=True, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_FILE)


def _drop_config(key):
    config = load_config()
    config.pop(key, None)
    save_config(config)


def _human_gb(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "?"
    return ("%.1f" % (value / (1024 ** 3))).rstrip("0").rstrip(".").replace(".", ",") + " ГБ"


# ----------------------------- Яндекс Диск -----------------------------

def yandex_config():
    value = load_config().get("yandex")
    return value if isinstance(value, dict) else {}


def _yandex_own_token():
    token = str(yandex_config().get("token", "")).strip()
    return token if re.fullmatch(r"[A-Za-z0-9_.\-]{20,}", token) else ""


def yandex_save_token(token):
    """Проверить токен живым запросом к Диску и сохранить как свой (0600)."""
    token = oa.validate_yandex_token(token)
    config = load_config()
    config["yandex"] = {"token": token, "connected_at": int(time.time())}
    save_config(config)
    return config["yandex"]


def yandex_disconnect():
    _drop_config("yandex")


def yandex_status():
    token = _yandex_own_token()
    if not token:
        return {"connected": False,
                "detail": "Свой токен не подключён — вставьте OAuth-токен Яндекс Диска ниже."}
    try:
        info = oa._yandex_request(token, "/", timeout=15)
    except Exception as exc:
        return {"connected": False, "detail": "Токен не принят Диском: " + str(exc)[:120]}
    login = str(((info or {}).get("user") or {}).get("login", "") or "")
    detail = "Подключено" + (": " + login if login else "")
    total = (info or {}).get("total_space")
    used = (info or {}).get("used_space")
    if total:
        free = "свободно " + _human_gb(int(total) - int(used or 0)) if used is not None else "всего " + _human_gb(total)
        detail += " · " + free
    return {"connected": True, "email": login, "detail": detail}


def yandex_upload(filename, blob, keep=7):
    token = _yandex_own_token()
    if not token:
        raise CloudError("Яндекс Диск не подключён — добавьте свой OAuth-токен в «Облачных копиях».")
    home = YANDEX_DIR_BARE + "/" + filename
    try:
        oa._yandex_request(token, "/resources", method="PUT", params={"path": YANDEX_DIR})
    except Exception:
        pass  # папка уже есть или создание не требуется перед upload-url
    answer = oa._yandex_request(token, "/resources/upload",
                                params={"path": home, "overwrite": "true"})
    href = str((answer or {}).get("href", ""))
    if not href:
        raise CloudError("Яндекс Диск не выдал адрес загрузки.")
    oa._upload_plain(href, blob)
    yandex_prune(token, keep)


def yandex_prune(token, keep):
    try:
        listing = oa._yandex_request(token, "/resources",
                                     params={"path": YANDEX_DIR, "limit": 100, "sort": "created"})
        items = (((listing or {}).get("_embedded") or {}).get("items")) or []
    except Exception:
        return
    files = [item for item in items if str(item.get("name", "")).endswith(".tar.gz")]
    for item in sorted(files, key=lambda x: x.get("created", ""), reverse=True)[max(0, int(keep)):]:
        try:
            oa._yandex_request(token, "/resources", method="DELETE",
                               params={"path": "disk:" + str(item.get("path", "")), "permanently": "true"})
        except Exception:
            pass


# --------------------------- Облако Mail.ru ---------------------------

def mailru_config():
    value = load_config().get("mailru")
    return value if isinstance(value, dict) else {}


def mailru_connect(email, password):
    """Живой OAuth-вход в Облако Mail.ru и сохранение своей пары ключей (0600)."""
    email = str(email or "").strip()
    password = str(password or "")
    if not email or "@" not in email or not password:
        raise CloudError("Укажите почту Mail.ru и «пароль для внешних приложений» — обычный пароль почты сторонним приложениям не подходит.")
    envelope = oa._mailru_grant({"grant_type": "password", "username": email,
                                 "password": password, "client_id": oa.MAILRU_CLIENT_ID})
    expires = int(envelope.get("expires_in") or 0)
    config = load_config()
    config["mailru"] = {
        "email": email, "password": password,
        "access_token": envelope.get("access_token", ""),
        "refresh_token": envelope.get("refresh_token", ""),
        "expires_at": int(time.time()) + expires - 300 if expires else 0,
        "connected_at": int(time.time()),
    }
    save_config(config)
    return email


def mailru_disconnect():
    _drop_config("mailru")


def _mailru_own_token(force_refresh=False):
    """Живой OAuth-токен своих облачных копий; протухший обновляет сам."""
    cfg = mailru_config()
    if not cfg.get("email") or not cfg.get("password"):
        raise CloudError("Аккаунт Mail.ru не подключён — войдите в «Облачных копиях».")
    if not force_refresh:
        expires_at = int(cfg.get("expires_at") or 0)
        if cfg.get("access_token") and (not expires_at or expires_at - 60 > time.time()):
            return cfg["access_token"]
        if cfg.get("refresh_token"):
            try:
                envelope = oa._mailru_grant({"grant_type": "refresh_token",
                                             "refresh_token": cfg["refresh_token"],
                                             "client_id": oa.MAILRU_CLIENT_ID})
            except oa.OpenFluxError:
                envelope = None  # refresh не принят — пробуем заново по паролю
            if envelope and envelope.get("access_token"):
                _mailru_store_envelope(cfg["email"], cfg["password"], envelope)
                return envelope["access_token"]
    envelope = oa._mailru_grant({"grant_type": "password", "username": cfg["email"],
                                 "password": cfg["password"], "client_id": oa.MAILRU_CLIENT_ID})
    _mailru_store_envelope(cfg["email"], cfg["password"], envelope)
    return envelope["access_token"]


def _mailru_store_envelope(email, password, envelope):
    expires = int(envelope.get("expires_in") or 0)
    config = load_config()
    cfg = config.get("mailru") if isinstance(config.get("mailru"), dict) else {}
    cfg.update({"email": email, "password": password,
                "access_token": envelope.get("access_token", ""),
                "refresh_token": envelope.get("refresh_token", "") or cfg.get("refresh_token", ""),
                "expires_at": int(time.time()) + expires - 300 if expires else 0})
    config["mailru"] = cfg
    save_config(config)


def mailru_status():
    cfg = mailru_config()
    if not cfg.get("email"):
        return {"connected": False,
                "detail": "Свой аккаунт не подключён — войдите почтой и «паролем для внешних приложений»."}
    try:
        _mailru_own_token()
    except Exception as exc:
        return {"connected": False, "email": cfg.get("email"),
                "detail": "Mail.ru не принял вход: " + str(exc)[:120]}
    return {"connected": True, "email": cfg.get("email"),
            "detail": "Подключено: " + str(cfg.get("email", ""))}


def mailru_upload(filename, blob, keep=7):
    if not mailru_config().get("email"):
        raise CloudError("Аккаунт Mail.ru не подключён — войдите в «Облачных копиях».")
    home = MAILRU_DIR + "/" + filename

    def call(command, data=None):
        return oa._mailru_call(command, params={"api": "2"}, data=data,
                               token_provider=_mailru_own_token)

    try:
        call("folder/add", {"home": MAILRU_DIR})
    except oa.OpenFluxError:
        pass  # существующая папка — норма
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://dispatcher.cloud.mail.ru/u", headers={"User-Agent": oa.MAILRU_UA}),
                timeout=30) as response:
            upload_url = response.read().decode("utf-8", "replace").strip().split(" ")[0]
    except (urllib.error.URLError, OSError) as exc:
        raise CloudError("Mail.ru не выдал адрес загрузчика: " + str(exc)) from exc
    if not upload_url:
        raise CloudError("Mail.ru не выдал адрес загрузчика.")
    token = _mailru_own_token()
    try:
        digest = oa._mailru_upload(upload_url, token, blob)
    except oa.OpenFluxError as exc:
        if "403" not in str(exc):
            raise
        digest = oa._mailru_upload(upload_url, _mailru_own_token(force_refresh=True), blob)
    call("file/add", {"home": home, "hash": digest, "size": str(len(blob)), "conflict": "rename"})
    mailru_prune()


def mailru_prune(keep=7):
    try:
        body = oa._mailru_call("folder/files", params={"api": "2", "home": MAILRU_DIR},
                               token_provider=_mailru_own_token)
        items = [item for item in (body.get("list") or []) if isinstance(item, dict)]
    except Exception:
        return
    files = [item for item in items if str(item.get("name", "")).endswith(".tar.gz")]
    for item in sorted(files, key=lambda x: int(x.get("mtime", 0) or 0), reverse=True)[max(0, int(keep)):]:
        try:
            oa._mailru_call("file/remove", params={"api": "2"},
                            data={"home": MAILRU_DIR + "/" + str(item.get("name", ""))},
                            token_provider=_mailru_own_token)
        except Exception:
            pass


# ---------------------------- Google Drive ----------------------------

def gdrive_config():
    value = load_config().get("gdrive")
    return value if isinstance(value, dict) else {}


def gdrive_disconnect():
    _drop_config("gdrive")


def gdrive_status():
    cfg = gdrive_config()
    if not cfg.get("client_id") or not cfg.get("refresh_token"):
        return {"connected": False,
                "detail": "Нужен OAuth-клиент Google Cloud и разрешение панели (scope drive.file)."}
    try:
        info = gdrive_call(cfg, "/about", params={"fields": "user"})
    except Exception as exc:
        return {"connected": False, "detail": "Google отклонил доступ: " + str(exc)[:120]}
    user = ((info or {}).get("user") or {}).get("emailAddress", "")
    return {"connected": True, "email": user, "detail": "Подключено: " + (user or "аккаунт Google")}


def _gdrive_token(cfg):
    body = urllib.parse.urlencode({"client_id": cfg.get("client_id", ""),
                                   "client_secret": cfg.get("client_secret", ""),
                                   "refresh_token": cfg.get("refresh_token", ""),
                                   "grant_type": "refresh_token"}).encode()
    request = urllib.request.Request(GDRIVE_TOKEN, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise CloudError("Google не выдал токен: " + detail) from exc
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise CloudError("Google недоступен: " + str(exc)) from exc
    if not answer.get("access_token"):
        raise CloudError("Google не выдал access_token.")
    return answer["access_token"]


def gdrive_call(cfg, path, params=None, method="GET", body=None, content_type="application/json"):
    url = GDRIVE_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + _gdrive_token(cfg), "Content-Type": content_type, "User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise CloudError("Google Drive ответил " + str(exc.code) + ": " + detail) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise CloudError("Google Drive недоступен: " + str(exc)) from exc
    return json.loads(raw) if raw.strip() else {}


def gdrive_folder_id(cfg):
    found = gdrive_call(cfg, "/files", params={
        "q": "name='%s' and mimeType='application/vnd.google-apps.folder' and trashed=false" % GDRIVE_FOLDER,
        "fields": "files(id,name)", "pageSize": "5"})
    items = found.get("files") or []
    if items:
        return items[0]["id"]
    created = gdrive_call(cfg, "/files", method="POST", body=json.dumps({
        "name": GDRIVE_FOLDER, "mimeType": "application/vnd.google-apps.folder"}).encode())
    return created.get("id", "")


def gdrive_upload(filename, blob, keep=7):
    cfg = gdrive_config()
    folder = gdrive_folder_id(cfg)
    if not folder:
        raise CloudError("Не удалось создать папку на Google Drive.")
    boundary = "onyx" + str(int(time.time() * 1000))
    metadata = json.dumps({"name": filename, "parents": [folder]})
    parts = (("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n"
              "--%s\r\nContent-Type: application/gzip\r\n\r\n" % (boundary, metadata, boundary)).encode(),
             blob, ("\r\n--%s--\r\n" % boundary).encode())
    # uploadType=multipart идёт на upload-домен, а не на /files без пути
    url = GDRIVE_UPLOAD + "?" + urllib.parse.urlencode({"uploadType": "multipart"})
    request = urllib.request.Request(url, data=b"".join(parts), method="POST", headers={
        "Authorization": "Bearer " + _gdrive_token(cfg),
        "Content-Type": "multipart/related; boundary=" + boundary, "User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise CloudError("Загрузка на Google Drive не удалась: " + detail) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise CloudError("Google Drive недоступен: " + str(exc)) from exc
    gdrive_prune(cfg, folder, keep)


def gdrive_prune(cfg, folder, keep):
    try:
        listing = gdrive_call(cfg, "/files", params={
            "q": "'%s' in parents and trashed=false" % folder,
            "fields": "files(id,name,createdTime)", "pageSize": 100,
            "orderBy": "createdTime desc"})
        files = listing.get("files") or []
    except Exception:
        return
    for item in files[max(0, int(keep)):]:
        if not str(item.get("name", "")).endswith(".tar.gz"):
            continue
        try:
            gdrive_call(cfg, "/files/" + item["id"], method="DELETE")
        except Exception:
            pass


def gdrive_save_client(client_id, client_secret):
    cfg = gdrive_config()
    cfg["client_id"] = str(client_id or "").strip()
    cfg["client_secret"] = str(client_secret or "").strip()
    cfg.pop("refresh_token", None)
    config = load_config()
    config["gdrive"] = cfg
    save_config(config)
    return cfg


def gdrive_oauth_url(redirect_uri):
    cfg = gdrive_config()
    if not cfg.get("client_id"):
        raise CloudError("Сначала укажите Client ID и Client Secret OAuth-клиента.")
    params = urllib.parse.urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": GDRIVE_SCOPE, "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "false"})
    return "https://accounts.google.com/o/oauth2/v2/auth?" + params


def gdrive_exchange(code, redirect_uri):
    cfg = gdrive_config()
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        raise CloudError("OAuth-клиент не настроен.")
    body = urllib.parse.urlencode({"code": str(code or ""), "client_id": cfg["client_id"],
                                   "client_secret": cfg["client_secret"], "redirect_uri": redirect_uri,
                                   "grant_type": "authorization_code"}).encode()
    request = urllib.request.Request(GDRIVE_TOKEN, data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise CloudError("Обмен кода не удался: " + detail) from exc
    refresh = str(answer.get("refresh_token", ""))
    if not refresh:
        raise CloudError("Google не вернул refresh_token. Отзовите доступ приложению в настройках аккаунта и повторите.")
    cfg["refresh_token"] = refresh
    config = load_config()
    config["gdrive"] = cfg
    save_config(config)
    return cfg


def status():
    return {"yandex": yandex_status(), "mailru": mailru_status(), "gdrive": gdrive_status()}


def upload(target, filename, blob, keep=7):
    """Загрузить копию в выбранное хранилище; возвращает человекочитаемую ошибку."""
    if target == "yandex":
        yandex_upload(filename, blob, keep)
    elif target == "mailru":
        mailru_upload(filename, blob, keep)
    elif target == "gdrive":
        gdrive_upload(filename, blob, keep)
    else:
        raise CloudError("Неизвестное хранилище: " + str(target))
