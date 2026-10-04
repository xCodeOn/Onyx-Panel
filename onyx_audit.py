"""Журнал действий администратора (audit log).

Записи живут в состоянии панели (data.json) под ключом "audit": одно
событие — одна строка {ts, actor, action, target, details}. Журнал
ограничен MAX_ENTRIES (старое вытесняется), поэтому файл состояния не
растёт бесконтрольно. Модуль только накапливает и отдаёт записи —
решение о том, что логировать, принимает вызывающий код.
"""
import time

MAX_ENTRIES = 500
ACTIONS = (
    "login", "login-failed", "logout", "client-create", "client-delete",
    "client-toggle", "client-rename", "client-secret", "client-expiry",
    "client-limit", "client-check", "subscription-update", "subscription-rotate",
    "invite-create", "invite-delete", "invite-claim", "cascade-add",
    "cascade-delete", "cascade-toggle", "routing-save", "routing-torrent",
    "reality", "warp", "node-add", "node-delete", "panel-path", "panel-login",
    "panel-password", "totp-enable", "totp-disable", "observer-save",
    "api-key-create", "api-key-delete", "telegram-save", "alerts-save",
    "backups-save", "backup-run", "backup-cloud-save", "import", "export",
    "site-html", "preset-apply", "openflux", "component-update", "panel-update",
    "firewall-port", "diagnostics",
)


def entries(state, limit=120, action=""):
    """Свежие записи, новые сверху; action фильтрует по типу события."""
    items = state.get("audit") if isinstance(state.get("audit"), list) else []
    if action:
        items = [item for item in items if item.get("action") == action]
    return list(reversed(items))[:max(0, int(limit))]


def record(state, action, target="", details="", actor="admin"):
    """Добавить событие в журнал (мутирует state; сохраняет вызывающий код)."""
    if not isinstance(state, dict):
        return
    audit = state.setdefault("audit", [])
    if not isinstance(audit, list):
        audit = state["audit"] = []
    audit.append({"ts": int(time.time()), "actor": str(actor or "admin")[:64],
                  "action": str(action or "")[:64], "target": str(target or "")[:120],
                  "details": str(details or "")[:300]})
    del audit[:-MAX_ENTRIES]
