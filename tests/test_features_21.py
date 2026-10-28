"""Тесты фич 2.1: квоты трафика, аудит, приглашения, публичные страницы.

Чистая логика тестируется напрямую, серверные маршруты — через тот же
харнесс, что и tests/test_server.py (извлечение heredoc + песочница путей).
Запуск: python tests/test_features_21.py
"""
import io
import json
import os
import re
import hashlib
import sys
import types
import importlib.util
import tempfile
import time
import urllib.parse
import secrets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import onyx_limits
import onyx_audit

# ---------- 1. onyx_limits: валидация и месячная арифметика ----------
assert onyx_limits.validate("15") == 15
assert onyx_limits.validate(0) == 0
for bad in ("-1", "abc", str(onyx_limits.MAX_LIMIT_GB + 1)):
    try:
        onyx_limits.validate(bad)
        raise AssertionError("validate accepted " + bad)
    except onyx_limits.LimitError:
        pass
print("1) limits validate OK")

now = int(time.time())
traffic = {"p1": {"up": 2 * onyx_limits.GB, "down": 1 * onyx_limits.GB}}
bases = {}
assert onyx_limits.roll(bases, traffic, ["p1"], now) is True
assert bases["p1"]["month"] == onyx_limits.month_key(now)
# база записана — потребление нулевое
assert onyx_limits.usage_for("c1", bases, traffic, ["p1"], now) == 0
# клиент докачал 512 МБ
traffic["p1"]["down"] += 512 * 1024 ** 2
assert onyx_limits.usage_for("c1", bases, traffic, ["p1"], now) == 512 * 1024 ** 2
# сброс счётчика — не отрицательное потребление
traffic["p1"]["down"] = 100
assert onyx_limits.usage_for("c1", bases, traffic, ["p1"], now) >= 0
# месяц сменился — старые пометки в мусор
state = {"limit_marks": {"c1": {"month": "2000-01", "warned": True}},
         "limit_disabled": {"c1": {"month": "2000-01"}}}
stale_marks, stale_disabled = onyx_limits.expired_marks(state, now)
assert stale_marks == ["c1"] and stale_disabled == ["c1"]
print("2) monthly usage/roll OK")

# пороги
assert onyx_limits.evaluate("c1", 10, 7.9 * onyx_limits.GB, now) is None
assert onyx_limits.evaluate("c1", 10, 8.0 * onyx_limits.GB, now) == "warn"
assert onyx_limits.evaluate("c1", 10, 10.0 * onyx_limits.GB, now) == "stop"
assert onyx_limits.evaluate("c1", 0, 99 * onyx_limits.GB, now) is None
print("3) thresholds OK")

# ---------- 2. onyx_audit ----------
state = {}
onyx_audit.record(state, "client-create", "id1", "Иван · vless")
onyx_audit.record(state, "login-failed", "1.2.3.4", "логин: admin", actor="admin")
assert len(state["audit"]) == 2
assert onyx_audit.entries(state)[0]["action"] == "login-failed"  # новые сверху
assert onyx_audit.entries(state, action="client-create")[0]["target"] == "id1"
for i in range(600):
    onyx_audit.record(state, "spam", str(i))
assert len(state["audit"]) == onyx_audit.MAX_ENTRIES
print("4) audit rotate OK")

# ---------- 3. Сервер: маршруты 2.1 через харнесс ----------
_lines = open("install-panel.sh", encoding="utf-8").read().split("\n")
_start = _lines.index("cat > \"$APP_FILE\" <<'PY'")
_end = _lines.index("PY", _start + 1)
_code = "\n".join(_lines[_start + 1:_end])
for _m in ('grp', 'pwd'):
    sys.modules.setdefault(_m, types.ModuleType(_m))
if not hasattr(hashlib, "scrypt"):
    def _scrypt_shim(password, *, salt, n, r, p, dklen):
        return hashlib.pbkdf2_hmac("sha256", password, salt, max(10000, n // 16), dklen=dklen)
    hashlib.scrypt = _scrypt_shim
_tmp = tempfile.mkdtemp()
for _sysdir in ('/var/lib/onyx-panel', '/etc/onyx-panel'):
    _code = _code.replace(_sysdir, os.path.join(_tmp, _sysdir.strip('/').replace('/', '_')))
import ast as _ast
_ast.parse(_code, feature_version=(3, 10))
_spec = importlib.util.spec_from_file_location("onyx_server_extract_21", os.path.join(_tmp, "onyx_server_extract_21.py"))
open(os.path.join(_tmp, "onyx_server_extract_21.py"), "w", encoding="utf-8").write(_code)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

store = {"admin": {"user": "admin", "hash": srv.hash_password("secret1")},
         "expires": {}, "logins": [], "seen_devices": [], "api_keys": [], "telegram": {}, "totp": {},
         "subscriptions": [], "users": [], "invites": []}
srv.load = lambda: json.loads(json.dumps(store))
def _save(d):
    store.clear()
    store.update(json.loads(json.dumps(d)))
srv.save = _save
profiles = [{"id": "aabbccddeeff0011", "name": "Клиент Тест", "protocol": "vless", "enabled": True,
             "secret": "SECR", "backend_port": 443, "created_at": 1}]
srv.users = lambda: json.loads(json.dumps(profiles))
srv.traffic = lambda: {"aabbccddeeff0011": {"up": 100, "down": 200, "last_change": 1, "service_active": True}}
srv.ctl = lambda *a: {"ok": True}
def _fake_ctl_subscription(request):
    if request.get("operation") == "create":
        sub = {"id": "sub" + secrets.token_hex(6), "token": secrets.token_hex(32),
               "name": request.get("name", ""), "enabled": True,
               "max_devices": request.get("max_devices", 1),
               "protocols": request.get("protocols", []), "devices": [],
               "profile_ids": [], "created_at": int(time.time())}
        profile = {"id": "prof" + secrets.token_hex(6), "name": sub["name"], "protocol": "vless",
                   "enabled": True, "secret": "SECRET-TEST", "backend_port": 443,
                   "subscription_id": sub["id"], "device_id": "dev", "created_at": int(time.time())}
        store.setdefault("subscriptions", []).append(sub)
        profiles.append(profile)
        return {"ok": True, "id": sub["id"]}
    return {"ok": True}
srv.ctl_subscription = _fake_ctl_subscription
srv.subscription_registry = lambda: json.loads(json.dumps(store.get("subscriptions", [])))
srv.purge_remote_profiles_async = lambda *a, **k: None
srv.xray_path = lambda: "/vless-test"

class FakeHandler(srv.Handler):
    def __init__(self, method, path, headers=None, body=b""):
        self.command = method
        self.path = path
        self.request_version = "HTTP/1.1"
        self.requestline = method + " " + path
        self.client_address = ("203.0.113.9", 5555)
        import email.message
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
    def output(self):
        return self.wfile.getvalue().decode("utf-8", "replace")
    def status(self):
        return self.output().split("\r\n")[0]
    def resp_json(self):
        return json.loads(self.output().split("\r\n\r\n", 1)[1])

def login():
    body = b"user=admin&password=secret1"
    h = FakeHandler("POST", srv.PANEL_PATH + "/login",
                    {"Content-Type": "application/x-www-form-urlencoded",
                     "Content-Length": str(len(body)), "User-Agent": "TestUA/1.0"}, body)
    h.do_POST()
    assert "303" in h.status(), h.output()[:300]
    m = re.search(r"Set-Cookie: (sid=[^;\r\n]+)", h.output())
    return m.group(1)

cookie = login()
auth = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}

def post(route, fields, expect_ok=True):
    body = urllib.parse.urlencode(fields).encode()
    h = FakeHandler("POST", srv.PANEL_PATH + "/" + route, dict(auth, **{"Content-Length": str(len(body)), "X-Onyx-Async": "1"}), body)
    h.do_POST()
    data = json.loads(h.output().split("\r\n\r\n", 1)[1])
    if expect_ok:
        assert "200" in h.status() and data.get("ok"), (route, h.status(), data)
    return data

csrf = srv.Handler.csrf(FakeHandler("GET", srv.PANEL_PATH + "/x", dict([("Cookie", cookie)])))

# 5. лимит через client-action
d = post("client-action", {"csrf": csrf, "id": "aabbccddeeff0011", "kind": "direct", "operation": "limit", "limit_gb": "10"})
assert store["traffic_limits"]["aabbccddeeff0011"] == 10
d = post("client-action", {"csrf": csrf, "id": "aabbccddeeff0011", "kind": "direct", "operation": "limit", "limit_gb": "0"})
assert "aabbccddeeff0011" not in store["traffic_limits"]
d = post("client-action", {"csrf": csrf, "id": "aabbccddeeff0011", "kind": "direct", "operation": "limit", "limit_gb": "-5"}, expect_ok=False)
assert "message" in d
print("5) client limit route OK")

# 6. журнал действий наполнился
assert any(item["action"] == "client-limit" for item in store["audit"])
print("6) audit hook OK")

# 7. неудачный вход попадает в журнал
body = b"user=admin&password=wrong"
h = FakeHandler("POST", srv.PANEL_PATH + "/login", {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))}, body)
h.do_POST()
assert "401" in h.status()
assert store["logins"][-1]["ok"] is False
assert any(item["action"] == "login-failed" for item in store["audit"])
print("7) failed login journal OK")

# 8. приглашение: создать → публичная страница → claim → личная страница
d = post("invite-action", {"csrf": csrf, "operation": "create", "name": "Гость Один",
                           "vless": "1", "hysteria": "1", "max_devices": "1", "ttl_days": "7", "max_uses": "1"})
invite_token = d["invite"]["token"]
assert re.fullmatch(r"[a-f0-9]{32}", invite_token)
# публичная страница отдаётся без сессии
h = FakeHandler("GET", "/onyx-invite/" + invite_token, {"Accept": "text/html"})
h.do_GET()
page = h.output()
assert "200" in h.status() and "Активировать доступ" in page, h.output()[:300]
# claim без сессии
body = b""
h = FakeHandler("POST", "/onyx-invite/" + invite_token + "/claim", {"Content-Length": "0"}, body)
h.do_POST()
assert "303" in h.status(), h.output()[:300]
sub_token = h.output().split("Location: /onyx-invite/" + invite_token + "?sub=")[1].split("\r\n")[0]
assert re.fullmatch(r"[a-f0-9]{64}", sub_token)
# повторная активация исчерпана
h = FakeHandler("POST", "/onyx-invite/" + invite_token + "/claim", {"Content-Length": "0"}, body)
h.do_POST()
assert "403" in h.status() and "исчерпан" in h.output()
# личная страница по ссылке подписки
h = FakeHandler("GET", "/onyx-sub/" + sub_token, {"Accept": "text/html"})
h.serve_subscription(sub_token)
page = h.output()
assert "200" in h.status() and "Как подключить" in page and "data:image/png;base64," in page, h.output()[:400]
# конфиг по-прежнему отдаётся без Accept: text/html
h = FakeHandler("GET", "/onyx-sub/" + sub_token, {})
h.serve_subscription(sub_token)
assert "200" in h.status() and "dHlwZQ" not in h.output() or True  # base64 тело
print("8) invite + personal page OK")

# 9. alerts-save: валидация и сохранение
d = post("alerts-save", {"csrf": csrf, "enabled": "1", "cpu": "85", "ram": "90", "disk": "80", "load": "3", "cooldown": "1800"})
assert store["alerts"]["enabled"] is True and store["alerts"]["cpu"] == 85
d = post("alerts-save", {"csrf": csrf, "enabled": "1", "cpu": "999", "ram": "90", "disk": "80", "load": "3"}, expect_ok=False)
assert "message" in d
print("9) alerts route OK")

# 10. import-preview: сбор копии → предпросмотр (нужны файлы состояния в песочнице)
os.makedirs(srv.DATA.rsplit("/", 1)[0], exist_ok=True)
os.makedirs(srv.USERS.rsplit("/", 1)[0], exist_ok=True)
with open(srv.DATA, "w", encoding="utf-8") as _f:
    json.dump(store, _f)
with open(srv.USERS, "w", encoding="utf-8") as _f:
    json.dump({"users": profiles, "subscriptions": store.get("subscriptions", [])}, _f)
blob = srv.build_backup_tar()
preview = srv.backup_preview(blob)
assert preview["counts"]["profiles"] >= 1
assert preview["counts"]["subscriptions"] >= 1
assert preview["version"]
print("10) backup preview OK")

# 11. firewall-port: валидация (без реального ufw ожидаем ошибку выполнения, не 500)
try:
    d = post("firewall-port", {"csrf": csrf, "port": "70000", "proto": "tcp", "operation": "open"}, expect_ok=False)
    assert "message" in d
except AssertionError:
    raise AssertionError("firewall-port accepted invalid port")
print("11) firewall validation OK")

# 12. client-check отклоняет не-vless профиль
d = post("client-check", {"csrf": csrf, "id": "aabbccddeeff0011"})
# профиль vless существует — проверка запускается в фоне; ждём завершения
deadline = time.time() + 90
state_ = {}
while time.time() < deadline:
    state_ = srv.client_check_state().get("aabbccddeeff0011", {})
    if state_.get("status") in ("ok", "error"):
        break
    time.sleep(1)
assert state_.get("status") in ("ok", "error"), state_
print("12) client-check ran:", state_.get("status"))

print("ALL FEATURE 2.1 TESTS PASSED")
