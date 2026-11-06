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
    return {"codes": codes}


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
