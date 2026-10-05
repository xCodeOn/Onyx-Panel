"""Static and runtime checks for names that are used but never defined.

The runtime NameError class of bug hides well: several call sites are guarded
by runtime conditions (a client with a traffic limit, a backup that has run,
a login notification) and the smoke tests never took those branches.  This
test parses every source (including the app heredoc) and fails on any name
that is loaded but never bound, then exercises the branches that bit users:
the diagnostics backup check and the users-page limit bar.
Run from the repository root:  python tests/test_static.py
"""
import ast
import builtins
import glob
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

APP_LINES = open("install-panel.sh", encoding="utf-8").read().split("\n")
_start = APP_LINES.index("cat > \"$APP_FILE\" <<'PY'")
_end = APP_LINES.index("PY", _start + 1)
APP_CODE = "\n".join(APP_LINES[_start + 1:_end])
SOURCES = [("install-panel.sh(app)", APP_CODE)] + [(p, open(p, encoding="utf-8").read()) for p in sorted(glob.glob("onyx_*.py"))]

BUILTINS = set(dir(builtins))


class BindingScanner(ast.NodeVisitor):
    """Collect bound names and all Name loads; anything loaded but never
    bound is a latent NameError (subject to runtime-only injection, of which
    this codebase now has none)."""

    def __init__(self):
        self.bound = set()
        self.loaded = {}

    def _bind(self, name):
        self.bound.add(name)

    def visit_FunctionDef(self, node):
        self._bind(node.name)
        for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            self._bind(a.arg)
        if node.args.vararg:
            self._bind(node.args.vararg.arg)
        if node.args.kwarg:
            self._bind(node.args.kwarg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._bind(node.name)
        self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self._bind(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name != "*":
                self._bind(a.asname or a.name)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.loaded.setdefault(node.id, node.lineno)
        else:
            self._bind(node.id)

    def visit_For(self, node):
        for t in ast.walk(node.target):
            if isinstance(t, ast.Name):
                self._bind(t.id)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        self.generic_visit(node)
        for item in node.items:
            if item.optional_vars:
                for t in ast.walk(item.optional_vars):
                    if isinstance(t, ast.Name):
                        self._bind(t.id)

    def visit_ExceptHandler(self, node):
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_Global(self, node):
        for n in node.names:
            self._bind(n)

    visit_Nonlocal = visit_Global

    def visit_arg(self, node):
        self._bind(node.arg)


problems = []
for name, code in SOURCES:
    scanner = BindingScanner()
    scanner.visit(ast.parse(code))
    for ident, lineno in scanner.loaded.items():
        if ident not in scanner.bound and ident not in BUILTINS:
            problems.append(f"{name}:{lineno}  '{ident}' is loaded but never defined/imported")

if problems:
    print("UNDEFINED NAME USES FOUND:")
    for p in problems:
        print("  ", p)
    sys.exit(1)
print("1) static scan: no loaded-but-undefined names in app or modules OK")

# --- runtime exercise of the branches that bit users -----------------------
for _m in ('grp', 'pwd'):
    sys.modules.setdefault(_m, types.ModuleType(_m))
_tmp = tempfile.mkdtemp()
_code = APP_CODE
for _sysdir in ('/var/lib/onyx-panel', '/etc/onyx-panel'):
    _code = _code.replace(_sysdir, os.path.join(_tmp, _sysdir.strip('/').replace('/', '_')))
import hashlib
if not hasattr(hashlib, "scrypt"):
    def _scrypt_shim(password, *, salt, n, r, p, dklen):
        return hashlib.pbkdf2_hmac("sha256", password, salt, max(10000, n // 16), dklen=dklen)
    hashlib.scrypt = _scrypt_shim
_extract_path = os.path.join(_tmp, "onyx_server_static.py")
open(_extract_path, "w", encoding="utf-8").write(_code)
_spec = importlib.util.spec_from_file_location("onyx_server_static", _extract_path)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

store = {"admin": {"user": "admin", "hash": srv.hash_password("secret1")},
         "expires": {}, "logins": [], "seen_devices": [], "api_keys": [],
         "telegram": {}, "totp": {}}
srv.load = lambda: json.loads(json.dumps(store))
srv.save = lambda d: store.update(d)

# 1. diagnostics backup check: no backup yet, fresh backup, stale backup
ok, msg = srv._diag_backup()
assert isinstance(ok, bool) and isinstance(msg, str) and "ни разу" in msg, (ok, msg)
store["backups"] = {"last": {"ts": int(time.time()) - 3600, "message": "backup ok"}}
ok, msg = srv._diag_backup()
assert ok is True and "Последняя копия" in msg, (ok, msg)
store["backups"] = {"last": {"ts": int(time.time()) - 3 * 86400, "message": "old"}}
ok, msg = srv._diag_backup()
assert ok is False and "старше двух суток" in msg, (ok, msg)
print("2) diagnostics _diag_backup runs without NameError in all states OK")

# 2. users-page limit bar (previously NameError for any limited client)
html = srv.limit_bar(150 * 2**30, 200)
assert "limit-bar" in html and "<i" in html, html
print("3) limit_bar renders OK")

# 3. the login notification branch must use the imported alias
assert "onyx_telegram.configured" not in APP_CODE, "bare onyx_telegram use is back"
print("4) telegram login notify uses telegram_api alias OK")

print("ALL STATIC/NAME TESTS PASSED")
