"""Месячные квоты трафика на клиента (ГБ) и учёт потребления.

Квота хранится в состоянии панели (data.json → "traffic_limits"),
месячные базы — в отдельном файле 0600 traffic-month.json. Потребление
за месяц считается как «текущие счётчики − база на начало месяца»:
сброс счётчика (перезапуск служб) трактуется как продолжение с нуля,
а не как отрицательное потребление. Модуль не трогает службы — он
возвращает список операций, а выполняет их вызывающий код.
"""
import time

GB = 1024 ** 3
MAX_LIMIT_GB = 1_000_000
WARN_SHARE = 0.8


class LimitError(ValueError):
    pass


def month_key(ts=None):
    return time.strftime("%Y-%m", time.gmtime(ts if ts is not None else time.time()))


def validate(gb):
    try:
        number = int(str(gb).strip())
    except (TypeError, ValueError):
        raise LimitError("Лимит должен быть целым числом гигабайтов.")
    if not 0 <= number <= MAX_LIMIT_GB:
        raise LimitError("Лимит должен быть от 0 до %d ГБ; 0 — без лимита." % MAX_LIMIT_GB)
    return number


def limits(state):
    value = state.get("traffic_limits") if isinstance(state.get("traffic_limits"), dict) else {}
    return {str(key): int(value[key]) for key in value if str(value[key]).isdigit()}


def profile_ids(client_id, subs, users):
    """Идентификаторы профилей клиента: у подписки — все её профили,
    у отдельного подключения — сам профиль."""
    for sub in subs:
        if sub.get("id") == client_id:
            return [str(pid) for pid in sub.get("profile_ids", [])]
    return [str(client_id)] if any(u.get("id") == client_id for u in users) else []


def client_name(client_id, subs, users):
    for sub in subs:
        if sub.get("id") == client_id:
            return sub.get("name", client_id)
    for user in users:
        if user.get("id") == client_id:
            return user.get("name", client_id)
    return client_id


def is_enabled(client_id, subs, users):
    for sub in subs:
        if sub.get("id") == client_id:
            return bool(sub.get("enabled", True))
    for user in users:
        if user.get("id") == client_id:
            return bool(user.get("enabled", True))
    return False


def load_bases(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = __import__("json").load(f)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_bases(path, value):
    import json, os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=True, separators=(",", ":"))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def usage_for(client_id, bases, traffic, ids, now=None):
    """Байты за текущий месяц: суммарно по профилям клиента."""
    now = int(time.time()) if now is None else now
    month = month_key(now)
    total = 0
    for pid in ids:
        item = traffic.get(pid) if isinstance(traffic.get(pid), dict) else {}
        up = max(0, int(item.get("up", 0)))
        down = max(0, int(item.get("down", 0)))
        base = bases.get(pid) if isinstance(bases.get(pid), dict) else {}
        if base.get("month") == month:
            total += max(0, up - int(base.get("up", 0))) + max(0, down - int(base.get("down", 0)))
        # месяц не совпал: база ещё не записана (запишет roll), пока считаем всё
        else:
            total += up + down
    return total


def roll(bases, traffic, all_profile_ids, now=None):
    """Первая выборка месяца фиксирует базу по каждому профилю."""
    now = int(time.time()) if now is None else now
    month = month_key(now)
    changed = False
    for pid in all_profile_ids:
        item = traffic.get(pid) if isinstance(traffic.get(pid), dict) else {}
        up = max(0, int(item.get("up", 0)))
        down = max(0, int(item.get("down", 0)))
        entry = bases.get(pid) if isinstance(bases.get(pid), dict) else {}
        if entry.get("month") != month:
            bases[pid] = {"month": month, "up": up, "down": down}
            changed = True
    # профили удалённых клиентов вытесняются, чтобы файл не рос
    for pid in [key for key in bases if key not in set(all_profile_ids)]:
        bases.pop(pid, None)
        changed = True
    return changed


def marks(state):
    value = state.get("limit_marks") if isinstance(state.get("limit_marks"), dict) else {}
    return value if isinstance(value, dict) else {}


def evaluate(client_id, gb, usage, now=None):
    """Что делать с клиентом: None | 'warn' | 'stop'."""
    now = int(time.time()) if now is None else now
    if gb <= 0:
        return None
    ceiling = int(gb) * GB
    if usage >= ceiling:
        return "stop"
    if usage >= ceiling * WARN_SHARE:
        return "warn"
    return None


def expired_marks(state, now=None):
    """Пометки уведомлений и флаги отключения прошлого месяца — в мусор."""
    now = int(time.time()) if now is None else now
    month = month_key(now)
    state_marks = state.get("limit_marks")
    stale_marks = []
    if isinstance(state_marks, dict):
        for key, value in list(state_marks.items()):
            if not isinstance(value, dict) or value.get("month") != month:
                stale_marks.append(key)
                state_marks.pop(key, None)
    disabled = state.get("limit_disabled") if isinstance(state.get("limit_disabled"), dict) else {}
    stale_disabled = [key for key, value in disabled.items()
                      if not isinstance(value, dict) or value.get("month") != month]
    return stale_marks, stale_disabled
