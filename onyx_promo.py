"""Promo codes and referral links for subscription sign-ups.

Promo codes are one-shot-per-use bonus-day coupons the operator creates on
the Security tab; a new subscription created with ?promo=CODE extends its
expiry by the code's bonus. Referral codes are stable per-operator tokens:
a client who signs up with ?ref=CODE both gets the bonus and is remembered
as the referrer's invitee, so the operator can see where clients came from.

State lives in data.json under "promo": {"codes": {...}, "used": {...},
"referrals": {...}}. Pure logic only — the panel owns persistence.
"""
import re
import time

MAX_CODES = 50
MAX_USES_UNLIMITED = 0
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class PromoError(ValueError):
    pass


def _code(value):
    code = str(value or "").strip().upper()
    if not CODE_RE.match(code):
        raise PromoError("Код: 3-32 символа, латиница, цифры, дефис и подчёркивание.")
    return code


def normalize(state):
    """Validated copy of the promo section."""
    state = state if isinstance(state, dict) else {}
    codes = {}
    raw_codes = state.get("codes") if isinstance(state.get("codes"), dict) else {}
    for key, record in list(raw_codes.items())[:MAX_CODES]:
        if not isinstance(record, dict):
            continue
        try:
            code = _code(key)
        except PromoError:
            continue
        try:
            bonus = max(1, min(3650, int(record.get("bonus_days", 0))))
        except (TypeError, ValueError):
            continue
        try:
            max_uses = max(0, min(10 ** 6, int(record.get("max_uses", 0))))
        except (TypeError, ValueError):
            max_uses = 0
        try:
            expires = max(0, int(record.get("expires_ts", 0)))
        except (TypeError, ValueError):
            expires = 0
        try:
            used = max(0, int(record.get("used", 0)))
        except (TypeError, ValueError):
            used = 0
        codes[code] = {"bonus_days": bonus, "max_uses": max_uses,
                       "used": used, "expires_ts": expires,
                       "note": str(record.get("note", ""))[:120]}
    referrals = {}
    raw_ref = state.get("referrals") if isinstance(state.get("referrals"), dict) else {}
    for key, record in list(raw_ref.items())[:MAX_CODES]:
        if not isinstance(record, dict):
            continue
        try:
            code = _code(key)
        except PromoError:
            continue
        try:
            bonus = max(0, min(3650, int(record.get("bonus_days", 0))))
        except (TypeError, ValueError):
            bonus = 0
        invites = [str(uid) for uid in (record.get("invites") or [])][:1000]
        referrals[code] = {"bonus_days": bonus, "invites": invites}
    return {"codes": codes, "referrals": referrals}


def create(state, code, bonus_days, max_uses=0, expires_ts=0, note=""):
    """Add or overwrite a promo code; returns the updated section."""
    state = normalize(state)
    code = _code(code)
    try:
        bonus_days = int(bonus_days)
    except (TypeError, ValueError):
        raise PromoError("Бонусные дни должны быть числом.")
    if not 1 <= bonus_days <= 3650:
        raise PromoError("Бонус: от 1 до 3650 дней.")
    try:
        max_uses = max(0, int(max_uses or 0))
    except (TypeError, ValueError):
        raise PromoError("Лимит использований должен быть числом.")
    try:
        expires_ts = max(0, int(expires_ts or 0))
    except (TypeError, ValueError):
        raise PromoError("Срок действия должен быть меткой времени.")
    if len(state["codes"]) >= MAX_CODES and code not in state["codes"]:
        raise PromoError("Достигнут лимит промокодов (%d)." % MAX_CODES)
    state["codes"][code] = {"bonus_days": bonus_days, "max_uses": max_uses,
                            "used": state["codes"].get(code, {}).get("used", 0),
                            "expires_ts": expires_ts,
                            "note": str(note or "")[:120]}
    return state


def delete(state, code):
    state = normalize(state)
    state["codes"].pop(_code(code), None)
    return state


def check(state, code, now_ts=None):
    """(ok, message, bonus_days) without consuming a use."""
    state = normalize(state)
    try:
        code = _code(code)
    except PromoError as exc:
        return False, str(exc), 0
    record = state["codes"].get(code)
    if record is None:
        return False, "Промокод не найден.", 0
    now_ts = int(now_ts or time.time())
    if record["expires_ts"] and now_ts > record["expires_ts"]:
        return False, "Срок действия промокода истёк.", 0
    if record["max_uses"] and record["used"] >= record["max_uses"]:
        return False, "Промокод уже использован максимальное число раз.", 0
    return True, "", record["bonus_days"]


def redeem(state, code, now_ts=None):
    """Consume one use; returns (state, ok, message, bonus_days)."""
    ok, message, bonus = check(state, code, now_ts)
    state = normalize(state)
    if not ok:
        return state, False, message, 0
    code = _code(code)
    state["codes"][code]["used"] += 1
    return state, True, "", bonus


def referral_code(state, bonus_days=0):
    """Stable operator referral token (created once, reused forever)."""
    state = normalize(state)
    existing = next(iter(state["referrals"]), None)
    if existing:
        return existing, state
    code = _code("ref%d" % (int(time.time()) % 100000))
    state["referrals"][code] = {"bonus_days": max(0, int(bonus_days or 0)), "invites": []}
    return code, state


def set_referral_bonus(state, code, bonus_days):
    state = normalize(state)
    code = _code(code)
    record = state["referrals"].get(code)
    if record is None:
        raise PromoError("Реферальный код не найден.")
    record["bonus_days"] = max(0, min(3650, int(bonus_days or 0)))
    return state


def apply_referral(state, code, uid, now_ts=None):
    """Register an invite under the referral code; returns (state, bonus_days)."""
    state = normalize(state)
    try:
        code = _code(code)
    except PromoError:
        return state, 0
    record = state["referrals"].get(code)
    if record is None:
        return state, 0
    uid = str(uid)
    if uid not in record["invites"] and len(record["invites"]) < 1000:
        record["invites"].append(uid)
    return state, record["bonus_days"]


def stats(state):
    """Compact counters for the Security tab."""
    state = normalize(state)
    active = sum(1 for record in state["codes"].values()
                 if not record["max_uses"] or record["used"] < record["max_uses"])
    invites = sum(len(record["invites"]) for record in state["referrals"].values())
    return {"codes": len(state["codes"]), "active": active, "invites": invites}
