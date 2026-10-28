"""TSPU-bypass core: fingerprint rotation, ClientHello fragmentation, hopping.

Fragmentation splits the *client's* TLS ClientHello before it hits the wire,
so the server can never apply it to traffic it already received: this module
owns the server-side toggles (stored in data.json under "bypass"), the
rotated fingerprint that goes into generated links, and the full Xray client
configs the panel publishes for import into v2rayN/Xray-based apps.

Everything here is pure logic — no subprocess, no network — so the panel and
the manager both import it and the tests exercise it directly.
"""
import time
import zlib

FRAGMENT_LEVELS = ("off", "light", "strong")
# Well-tested splits: 1-3 first fragments of 50-100 bytes every 10-20ms is
# the classic TLS ClientHello recipe; "strong" widens it for stubborn DPI.
FRAGMENTS = {
    "light": {"packets": "1-3", "length": "50-100", "interval": "10-20"},
    "strong": {"packets": "2-4", "length": "80-120", "interval": "10-30"},
}
FINGERPRINTS = ("chrome", "firefox", "safari", "edge", "ios", "android", "random")
FP_DEFAULT = "chrome"
MAX_HOPPING_PORTS = 6
# Ports the panel itself terminates: a hopping port must never collide with
# them or with the private panel path behind Caddy.
RESERVED_PORTS = frozenset({80, 443, 2398, 8080, 8090, 8443, 9443})
AWG_PORT_RANGE = range(52000, 53000)


class BypassError(ValueError):
    pass


def _port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise BypassError("Порт hopping должен быть числом.")
    if not 1024 <= port <= 65535:
        raise BypassError("Порт hopping должен быть в диапазоне 1024-65535.")
    if port in RESERVED_PORTS or port in AWG_PORT_RANGE:
        raise BypassError("Порт %d занят службами панели." % port)
    return port


def normalize(state):
    """Validated copy of the bypass section; safe defaults for a fresh panel."""
    state = state if isinstance(state, dict) else {}
    level = str(state.get("fragment", "off"))
    if level not in FRAGMENT_LEVELS:
        level = "off"
    pool = state.get("fp_pool")
    if not isinstance(pool, list):
        pool = []
    cleaned = []
    for value in pool:
        value = str(value)
        if value in FINGERPRINTS and value not in cleaned:
            cleaned.append(value)
    ports = []
    for value in (state.get("hopping_ports") or []):
        try:
            port = _port(value)
        except BypassError:
            continue
        if port not in ports:
            ports.append(port)
    ports = sorted(ports)[:MAX_HOPPING_PORTS]
    return {"fragment": level,
            "fp_rotation": bool(state.get("fp_rotation", False)),
            "fp_pool": cleaned or [FP_DEFAULT],
            "least_ping": bool(state.get("least_ping", False)),
            "hopping_ports": ports,
            "honeypot": bool(state.get("honeypot", False)),
            "ipv6": bool(state.get("ipv6", False))}


def apply_form(state, form):
    """Merge a settings form into state and return the validated result.

    form is the flat dict from the panel: fragment (level), fp_pool (comma
    separated names), hopping_ports (comma separated numbers) and the three
    checkbox flags. Unknown values are rejected loudly so the operator sees
    the mistake instead of silently keeping the old value.
    """
    state = state if isinstance(state, dict) else {}
    level = str(form.get("fragment", state.get("fragment", "off"))).strip()
    if level not in FRAGMENT_LEVELS:
        raise BypassError("Неизвестный уровень фрагментации: %s" % level)
    raw_pool = form.get("fp_pool")
    if raw_pool is None:
        pool = list(state.get("fp_pool") or [FP_DEFAULT])
    else:
        pool = [value.strip() for value in str(raw_pool).split(",") if value.strip()]
        unknown = [value for value in pool if value not in FINGERPRINTS]
        if unknown:
            raise BypassError("Неизвестный TLS-отпечаток: %s" % ", ".join(unknown))
        if not pool:
            raise BypassError("Пул отпечатков не может быть пустым.")
    raw_ports = form.get("hopping_ports")
    if raw_ports is None:
        ports = list(state.get("hopping_ports") or [])
    else:
        entries = [value.strip() for value in str(raw_ports).split(",") if value.strip()]
        ports = [_port(value) for value in entries]
        if len(ports) > MAX_HOPPING_PORTS:
            raise BypassError("Можно открыть не более %d портов hopping." % MAX_HOPPING_PORTS)
        if len(set(ports)) != len(ports):
            raise BypassError("Порты hopping не должны повторяться.")
    merged = dict(state)
    merged.update({"fragment": level,
                   "fp_rotation": bool(form.get("fp_rotation", state.get("fp_rotation", False))),
                   "fp_pool": pool,
                   "least_ping": bool(form.get("least_ping", state.get("least_ping", False))),
                   "hopping_ports": sorted(ports),
                   "honeypot": bool(form.get("honeypot", state.get("honeypot", False))),
                   "ipv6": bool(form.get("ipv6", state.get("ipv6", False)))})
    return normalize(merged)


def fragment_settings(level):
    """sockopt.fragment payload for the given level; None when off."""
    level = level if level in FRAGMENT_LEVELS else "off"
    if level == "off":
        return None
    return dict(FRAGMENTS[level])


def fp_for(uid, state, now_ts=None):
    """Fingerprint published in this user's links.

    Rotation re-rolls once a month from the pool: a stable pick per user per
    month (a daily roll would make support tickets unreadable), and the
    panel-side links always carry exactly what the client will send.
    """
    state = normalize(state)
    if not state["fp_rotation"]:
        return FP_DEFAULT
    pool = state["fp_pool"] or [FP_DEFAULT]
    month = time.strftime("%Y-%m", time.localtime(now_ts) if now_ts else time.localtime())
    seed = ("%s:%s" % (str(uid), month)).encode("utf-8")
    # crc32, not hash(): the builtin is salted per process, so a panel restart
    # would silently re-roll every client's fingerprint mid-month.
    return pool[zlib.crc32(seed) % len(pool)]


def xray_client_config(secret, uid, *, domain, path, level="off", fp=FP_DEFAULT,
                       reality=None, variant="reality", tag="proxy"):
    """Full Xray client config ready for import into v2rayN and friends.

    variant "reality" — VLESS + Reality + Vision over TCP with the panel's
    server names; variant "xhttp" — VLESS over XHTTP behind the domain's TLS.
    A fragment level other than "off" attaches sockopt.fragment so the
    client's own ClientHello is split before it reaches the DPI.
    """
    level = level if level in FRAGMENT_LEVELS else "off"
    fragment = fragment_settings(level)
    if variant not in ("reality", "xhttp"):
        raise BypassError("Неизвестный вариант конфигурации: %s" % variant)
    if variant == "reality":
        reality = reality if isinstance(reality, dict) else {}
        if not reality.get("enabled"):
            raise BypassError("Reality выключен на сервере.")
        user = {"id": secret, "encryption": "none", "flow": "xtls-rprx-vision", "level": 0}
        outbound = {"protocol": "vless", "settings": {"vnext": [
            {"address": domain, "port": int(reality.get("port", 2053)), "users": [user]}]},
            "streamSettings": {"network": "tcp", "security": "reality",
                               "realitySettings": {"show": False,
                                                   "fingerprint": fp,
                                                   "serverName": (reality.get("server_names") or [domain])[0],
                                                   "publicKey": reality.get("public_key", ""),
                                                   "shortId": (reality.get("short_ids") or [""])[0],
                                                   "spiderX": "/"}}}
    else:
        user = {"id": secret, "encryption": "none", "level": 0}
        outbound = {"protocol": "vless", "settings": {"vnext": [
            {"address": domain, "port": 443, "users": [user]}]},
            "streamSettings": {"network": "xhttp", "security": "tls",
                               "tlsSettings": {"serverName": domain, "fingerprint": fp,
                                               "alpn": ["h2"]},
                               "xhttpSettings": {"path": path, "mode": "auto", "host": domain}}}
    if fragment:
        outbound["sockopt"] = {"fragment": fragment,
                               "tcpFastOpen": True, "domainStrategy": "UseIPv4"}
    return {"log": {"loglevel": "warning"},
            "inbounds": [{"tag": "socks", "listen": "127.0.0.1", "port": 10808,
                          "protocol": "socks", "settings": {"udp": True}},
                         {"tag": "http", "listen": "127.0.0.1", "port": 10809,
                          "protocol": "http"}],
            "outbounds": [outbound, {"tag": "direct", "protocol": "freedom"}],
            "routing": {"domainStrategy": "AsIs",
                        "rules": [{"type": "field", "inboundTag": ["socks", "http"],
                                   "outboundTag": tag}]},
            "_onyx": {"uid": str(uid), "variant": variant, "fragment": level, "fp": fp}}


def preset_apply(level):
    """Preset bundles for the one-button "max bypass" switch.

    Returns the bypass-section keys the panel merges into data.json; the
    caller re-applies Xray (leastPing needs a config rebuild) and re-renders
    links (fp rotation changes them).
    """
    presets = {
        "off": {"fragment": "off", "fp_rotation": False, "least_ping": False},
        "standard": {"fragment": "light", "fp_rotation": True, "least_ping": False},
        "max": {"fragment": "light", "fp_rotation": True, "least_ping": True},
    }
    if level not in presets:
        raise BypassError("Неизвестный пресет: %s" % level)
    return dict(presets[level])
