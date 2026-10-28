# -*- coding: utf-8 -*-
"""Прекомпиляция всех python-хередоков в установщиках + извлечённого приложения."""
import ast
import sys

def heredocs(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<'PY'" in line or '<<"PY"' in line:
            start = i + 1
            end = None
            for j in range(start, len(lines)):
                if lines[j].strip() == "PY":
                    end = j
                    break
            if end is None:
                out.append((path, start, None, "\n".join(lines[start:])))
                i = len(lines)
                continue
            out.append((path, start + 1, end, "\n".join(lines[start:end])))
            i = end + 1
        else:
            i += 1
    return out

failures = 0
for path in ("install-panel.sh", "install-core.sh", "install-final.sh", "update.sh", "uninstall-onyx-panel.sh"):
    for start, end, code in [(a, b, c) for (_, a, b, c) in heredocs(path)]:
        if code.strip() == "":
            continue
        try:
            ast.parse(code, feature_version=(3, 10))
        except SyntaxError as exc:
            failures += 1
            print("SYNTAX FAIL %s:%s — %s" % (path, start, exc))
            snippet = code.split("\n")
            ln = (exc.lineno or 1) - 1
            for k in range(max(0, ln - 2), min(len(snippet), ln + 3)):
                print("   ", k + 1, snippet[k][:140])
        else:
            first = code.strip().splitlines()[0][:60]
            print("OK   %s:%s (%d строк) — %s" % (path, start, len(code.splitlines()), first))

# извлечённое приложение
lines = open("install-panel.sh", encoding="utf-8").read().split("\n")
start = lines.index('cat > "$APP_FILE" <<\'PY\'')
end = lines.index("PY", start + 1)
try:
    ast.parse("\n".join(lines[start + 1:end]), feature_version=(3, 10))
    print("OK   app heredoc (%d строк)" % (end - start - 1))
except SyntaxError as exc:
    failures += 1
    print("SYNTAX FAIL app —", exc)

if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ALL HEREDOCS COMPILE")
