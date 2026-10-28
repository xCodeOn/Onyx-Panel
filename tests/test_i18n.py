"""i18n acceptance gate for the panel server.

Boots the same extracted server as tests/test_server.py, renders every HTML
page and the JSON endpoints in English mode (Accept-Language and cookie both
exercised) and requires zero visible Cyrillic.  Run with --report to print
the leftover Russian phrases instead of failing, which is how the dictionary
in onyx_i18n.py is extended.  Run from the repository root:
    python tests/test_i18n.py [--report]
"""
import io
import json
import os
import re
import sys
import types
import html as html_mod
import importlib.util
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

REPORT = "--report" in sys.argv

_lines = open("install-panel.sh", encoding="utf-8").read().split("\n")
_start = _lines.index("cat > \"$APP_FILE\" <<'PY'")
_end = _lines.index("PY", _start + 1)
_code = "\n".join(_lines[_start + 1:_end])
for _m in ('grp', 'pwd'):
    sys.modules.setdefault(_m, types.ModuleType(_m))
_tmp = tempfile.mkdtemp()
for _sysdir in ('/var/lib/onyx-panel', '/etc/onyx-panel'):
    _code = _code.replace(_sysdir, os.path.join(_tmp, _sysdir.strip('/').replace('/', '_')))
_extract_path = os.path.join(_tmp, "onyx_server_extract_i18n.py")
open(_extract_path, "w", encoding="utf-8").write(_code)
_spec = importlib.util.spec_from_file_location("onyx_server_extract_i18n", _extract_path)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

if not hasattr(__import__("hashlib"), "scrypt"):
    import hashlib
    def _scrypt_shim(password, *, salt, n, r, p, dklen):
        return hashlib.pbkdf2_hmac("sha256", password, salt, max(10000, n // 16), dklen=dklen)
    hashlib.scrypt = _scrypt_shim

store = {"admin": {"user": "admin", "hash": srv.hash_password("secret1")},
         "expires": {}, "logins": [], "seen_devices": [], "api_keys": [],
         "telegram": {}, "totp": {}}
srv.load = lambda: json.loads(json.dumps(store))
def _save(d):
    store.clear()
    store.update(json.loads(json.dumps(d)))
srv.save = _save
profiles = [
    {"id": "aabbccddeeff0011", "name": "Клиент Тест", "protocol": "vless", "enabled": True, "secret": "SECR", "backend_port": 443},
    {"id": "bbccddee00112233", "name": "Ivan Phone", "protocol": "hysteria", "enabled": False, "secret": "HS2", "backend_port": 8443},
]
srv.users = lambda: json.loads(json.dumps(profiles))
srv.traffic = lambda: {"aabbccddeeff0011": {"up": 1200, "down": 340000, "last_change": 1, "service_active": True}}
srv.xray_path = lambda: "/vless-abcdefabcdefabcdefabcdef"
srv.ctl = lambda *a: ({"id": "newid0001", "name": "x"} if a[0] == "add" else {"ok": True})
srv.ctl_manager_json = lambda cmd, req: {"id": "mtid0001", "name": req.get("name")}
srv.DOMAIN = "panel.example.com"
srv.load_routing = lambda: {"domains": [], "ips": [], "geoip": [], "geosite": [], "block": [], "direct": []} if hasattr(srv, "load_routing") else {}


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
    def body(self):
        return self.output().split("\r\n\r\n", 1)[1]
    def resp_cookie(self):
        m = re.search(r"Set-Cookie: (sid=[^;\r\n]+)", self.output())
        return ("Cookie", m.group(1)) if m else None


def login_post(user="admin", pw="secret1"):
    form = f"user={user}&password={pw}"
    body = form.encode()
    h = FakeHandler("POST", srv.PANEL_PATH + "/login", {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))}, body)
    h.do_POST()
    return h


RUN_RE = re.compile(r"[а-яёА-ЯЁ][а-яёА-ЯЁ0-9 \t.,%:;!?«»()…\-—–№'\"+/=]*[.?!…»,;:а-яёА-ЯЁ]|[а-яёА-ЯЁ]")
SCRIPT_RE = re.compile(r"<script\b.*?</script>", re.S | re.I)
STYLE_RE = re.compile(r"<style\b.*?</style>", re.S | re.I)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
# A real JS line comment: not preceded by ':' (https://), so string literals
# with URLs survive; start-of-line or after whitespace/operator only.
LINE_COMMENT_RE = re.compile(r"(?<![:\w])//[^\n]*", re.M)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# Visible attribute values survive tag stripping: users see them.
ATTR_RE = re.compile(
    r'<[^>]*?\b(placeholder|title|aria-label|data-tip|data-confirm)'
    r'="([^"]*)"[^>]*?>', re.S)
TAG_RE = re.compile(r"<[^>]*>")


def visible_cyrillic(page):
    """Cyrillic runs a user can see: HTML text, visible attribute values and
    JS string literals; comments and stylesheet internals are ignored."""
    page = STYLE_RE.sub(" ", page)
    page = HTML_COMMENT_RE.sub(" ", page)
    page = ATTR_RE.sub(lambda m: "\n" + m.group(2) + "\n", page)
    page = SCRIPT_RE.sub(lambda m: BLOCK_COMMENT_RE.sub(" ", LINE_COMMENT_RE.sub(" ", m.group(0))), page)
    page = TAG_RE.sub("\n", page)
    page = html_mod.unescape(page)
    found = []
    for m in RUN_RE.finditer(page):
        run = re.sub(r"\s+", " ", m.group(0)).strip(" .,:;—–-·| ")
        if run and len(run) > 1:
            found.append(run)
    return found


PAGES = ["/login", "/dashboard", "/users", "/nodes", "/cascade", "/routing", "/updates", "/settings", "/logs", "/diagnostics", "/subscriptions"]
JSON_ROUTES = ["/update-status", "/notifications", "/nodes-state", "/cascade-state", "/restart-status", "/component-status", "/diagnostics-status"]

EN_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
RU_HEADERS = {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

leftovers = {}
failures = []

h = login_post()
assert "303" in h.status(), h.status()
cookie = h.resp_cookie()
sid_cookie = cookie[1] if cookie else ""

for route in PAGES:
    headers = dict([cookie]) if route != "/login" else {}
    headers.update(EN_HEADERS)
    page = FakeHandler("GET", srv.PANEL_PATH + route, headers)
    page.do_GET()
    status = page.status()
    if route == "/subscriptions" and "303" in status:
        continue  # redirects to the users page
    if "200" not in status:
        failures.append(f"GET {route} -> {status}")
        continue
    body = page.body()
    runs = visible_cyrillic(body)
    if runs:
        leftovers[route] = sorted(set(runs))
    if route == "/login":
        if '<html lang="en">' not in body:
            failures.append("login: html lang not switched to en")
    else:
        if '<html lang="en">' not in body:
            failures.append(f"{route}: html lang not switched to en")

for route in JSON_ROUTES:
    headers = dict([cookie]) if route != "/login" else {}
    headers.update(EN_HEADERS)
    page = FakeHandler("GET", srv.PANEL_PATH + route, headers)
    page.do_GET()
    status = page.status()
    if "200" not in status:
        continue  # state endpoints may legitimately 503 without live services
    try:
        payload = json.loads(page.body())
    except Exception:
        continue
    texts = []
    def _collect(value):
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                _collect(v)
        elif isinstance(value, list):
            for v in value:
                _collect(v)
    _collect(payload)
    runs = []
    for text in texts:
        runs.extend(visible_cyrillic(text))
    if runs:
        leftovers[route] = sorted(set(leftovers.get(route, [])) | set(runs))

# Dashboard live stream (SSE) payload: rebuilt body + update status must be
# translated too — the stream bypasses send_html/send_json.  The html fragment
# is scanned as-is (comments stripped like in a real document); update status
# values are scanned as plain strings.
srv.i18n.set_request("en-US,en;q=0.9")
stream = srv.dashboard_stream_payload("static-test-csrf", 1)
runs = visible_cyrillic(stream["html"])
for value in (stream["update"] or {}).values() if isinstance(stream["update"], dict) else []:
    if isinstance(value, str):
        runs.extend(visible_cyrillic(value))
if runs:
    leftovers["/dashboard-stream"] = sorted(set(runs))

# Client-facing pages: subscription page and invite page (all states)
import time as _time
import onyx_ui as _onyx_ui
sub_fake = {"name": "Клиент Тест", "token": "tok123"}
qrs_fake = [{"label": "VLESS XHTTP", "hint": "Быстрый канал", "link": "https://panel.example.com/s/tok123?vless=x", "png": "aGk="}]
extra_pages = {
    "/subscription": _onyx_ui.subscription_page_html(sub_fake, profiles, 123, 50, _time.time() + 86400, "panel.example.com", qrs_fake),
    "/subscription-nolimit": _onyx_ui.subscription_page_html(sub_fake, profiles, 0, 0, None, "panel.example.com", qrs_fake),
    "/invite-dead": _onyx_ui.invite_page_html(None, None, "panel.example.com", "/onyx-invite/tok123"),
    "/invite": _onyx_ui.invite_page_html({"token": "t2", "name": "Гость", "max_uses": 3, "uses": 0, "ttl_days": 7, "protocols": ["vless"]}, None, "panel.example.com", "/onyx-invite/t2"),
    "/invite-claimed": _onyx_ui.invite_page_html(None, {"token": "t3", "name": "Гость"}, "panel.example.com", "/onyx-invite/t3"),
}
for label, doc in extra_pages.items():
    en_doc = _onyx_ui.i18n.document(doc)
    runs = visible_cyrillic(en_doc)
    if runs:
        leftovers[label] = sorted(set(leftovers.get(label, [])) | set(runs))

# Observer session: same pages, observer-specific chrome
store["observer"] = {"user": "helper", "hash": srv.hash_password("view123")}
h_obs = login_post("helper", "view123")
obs_cookie = h_obs.resp_cookie()
if obs_cookie:
    page = FakeHandler("GET", srv.PANEL_PATH + "/users", {"Cookie": obs_cookie[1], **EN_HEADERS})
    page.do_GET()
    if "200" in page.status():
        runs = visible_cyrillic(page.body())
        if runs:
            leftovers["/users-observer"] = sorted(set(runs))

# Login page with the 2FA code field enabled
page = _onyx_ui.i18n.document(_onyx_ui.login_ui("", totp=True))
runs = visible_cyrillic(page)
if runs:
    leftovers["/login-2fa"] = sorted(set(runs))

# POST JSON responses in EN mode (representative endpoints)
def _post_json(path, fields):
    hh = FakeHandler("POST", srv.PANEL_PATH + path, {"Cookie": sid_cookie, **EN_HEADERS})
    data = dict(fields)
    data["csrf"] = hh.csrf()
    body = urllib.parse.urlencode(data).encode()
    hh.headers["Content-Type"] = "application/x-www-form-urlencoded"
    hh.headers["Content-Length"] = str(len(body))
    hh.rfile = io.BytesIO(body)
    hh.do_POST()
    return hh

for label, path, fields in (
    ("/POST-backups-save", "/backups-save", {"mode": "telegram", "hour": "9", "keep": "10"}),
    ("/POST-totp-setup", "/totp-setup", {}),
    ("/POST-totp-wrong-code", "/totp-enable", {"code": "000000"}),
    ("/POST-telegram-empty", "/telegram-save", {"action": "save", "token": "", "chat": ""}),
):
    try:
        hh = _post_json(path, fields)
        payload = hh.body()
        try:
            texts = json.dumps(json.loads(payload), ensure_ascii=False)
        except Exception:
            texts = payload  # HTML error page
        runs = visible_cyrillic(texts)
        if runs:
            leftovers[label] = sorted(set(runs))
    except Exception as exc:
        failures.append(f"{label}: {type(exc).__name__}: {exc}")

# Russian must remain Russian: sanity check the dictionary did not leak
page = FakeHandler("GET", srv.PANEL_PATH + "/users", {"Cookie": sid_cookie + "; onyx_lang=ru"})
page.do_GET()
if '<html lang="ru">' not in page.body() or "Клиенты" not in page.body():
    failures.append("RU mode damaged: lang attr or nav labels missing")

# A stale onyx_lang cookie (from the removed sidebar toggle) must be ignored:
# language now comes from the system (Accept-Language) only.
page = FakeHandler("GET", srv.PANEL_PATH + "/users", {"Cookie": sid_cookie + "; onyx_lang=en"})
page.do_GET()
if "Клиенты" not in page.body() or ">Clients<" in page.body():
    failures.append("stale onyx_lang cookie must not force English anymore")

# no Accept-Language and no cookie -> historical Russian default
page = FakeHandler("GET", srv.PANEL_PATH + "/users", {"Cookie": sid_cookie})
page.do_GET()
if "Клиенты" not in page.body():
    failures.append("default (no headers) must stay Russian")

if REPORT:
    print(json.dumps(leftovers, ensure_ascii=False, indent=1))
    print(f"pages with leftover Cyrillic: {len(leftovers)}")
    sys.exit(0)

if leftovers:
    total = sum(len(v) for v in leftovers.values())
    print(f"LEFTOVER CYRILLIC: {total} phrases on {len(leftovers)} routes")
    for route, runs in leftovers.items():
        print(f"--- {route}")
        for run in runs[:200]:
            print("   ", run)
    sys.exit(1)

if failures:
    print("FAILURES:")
    for f in failures:
        print("  ", f)
    sys.exit(1)

print("i18n OK: all pages render Cyrillic-free in English, RU mode intact, language follows Accept-Language only")
