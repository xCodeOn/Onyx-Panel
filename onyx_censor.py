"""Censorship monitor: cheap probes of the panel's own public endpoints.

The worker in panel.py calls run_probes() every few minutes from the server
itself, so a "blocked" verdict here means the endpoint stopped answering
*from the outside* (RST on connect, dead TLS after SNI, dropped packets) —
the classic TSPU fingerprint. Each probe is a plain TCP connect plus an
optional TLS handshake carrying the SNI the clients actually use; the panel
never sends application data, so the check cannot look like client traffic.

Results land in data.json under "censor" and feed the Security tab and the
"censorship" Telegram event on every state transition.
"""
import json
import socket
import ssl
import time

PROBE_TIMEOUT = 6.0
HISTORY_LIMIT = 200
# A source keeps this verdict until the next probe round rewrites it.
VERDICTS = ("ok", "blocked", "timeout")
VERDICT_LABELS = {"ok": "Работает", "blocked": "Блокируется", "timeout": "Не отвечает"}


class CensorError(ValueError):
    pass


def normalize_target(host, port, sni="", label=""):
    host = str(host or "").strip()
    if not host:
        raise CensorError("Пустой адрес проверки.")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise CensorError("Порт проверки должен быть числом.")
    if not 1 <= port <= 65535:
        raise CensorError("Порт проверки вне диапазона 1-65535.")
    # An empty sni means "TCP connect only" (Reality, plain ports); a set sni
    # makes the probe carry a real TLS handshake like the clients do.
    return {"host": host, "port": port, "sni": str(sni or "").strip(),
            "label": str(label or host).strip()[:80]}


def default_targets(domain, reality_port=0, xray_port=443):
    """The three probes every install has: panel TCP, domain TLS, Reality TCP."""
    targets = []
    domain = str(domain or "").strip()
    try:
        reality_port = int(reality_port)
    except (TypeError, ValueError):
        reality_port = 0
    try:
        xray_port = int(xray_port)
    except (TypeError, ValueError):
        xray_port = 443
    if domain:
        targets.append({"host": domain, "port": xray_port, "sni": domain,
                        "label": "Домен (TLS)"})
        if reality_port:
            targets.append({"host": domain, "port": reality_port, "sni": "",
                            "label": "Reality"})
    return targets


def probe_tcp(host, port, timeout=PROBE_TIMEOUT):
    """TCP connect only. Returns (verdict, detail, elapsed_ms)."""
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except ConnectionRefusedError:
        return "blocked", "отказ в соединении", _elapsed(started)
    except socket.timeout:
        return "timeout", "таймаут соединения", _elapsed(started)
    except OSError as exc:
        return "timeout", (str(exc) or exc.__class__.__name__)[:120], _elapsed(started)
    return "ok", "", _elapsed(started)


def probe_tls(host, port, sni, timeout=PROBE_TIMEOUT):
    """TCP + TLS handshake with the clients' SNI. A RST mid-handshake is the
    TSPU signature; a refused/dropped connect is reported like probe_tcp."""
    started = time.monotonic()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=sni):
                pass
    except ssl.SSLError:
        # Handshake started (TCP fine) then died: DPI matched the SNI/ClientHello.
        return "blocked", "TLS прерван (осознанная подмена/сброс)", _elapsed(started)
    except ConnectionRefusedError:
        return "blocked", "отказ в соединении", _elapsed(started)
    except socket.timeout:
        return "timeout", "таймаут соединения", _elapsed(started)
    except OSError as exc:
        return "timeout", (str(exc) or exc.__class__.__name__)[:120], _elapsed(started)
    return "ok", "", _elapsed(started)


def probe(target, timeout=PROBE_TIMEOUT):
    """One target, verdict ok/blocked/timeout plus latency."""
    target = normalize_target(target.get("host"), target.get("port"),
                              target.get("sni"), target.get("label"))
    if target["sni"]:
        verdict, detail, elapsed = probe_tls(target["host"], target["port"],
                                             target["sni"], timeout)
    else:
        verdict, detail, elapsed = probe_tcp(target["host"], target["port"], timeout)
    return {"host": target["host"], "port": target["port"], "label": target["label"],
            "verdict": verdict, "detail": detail, "elapsed_ms": elapsed,
            "ts": int(time.time())}


def run_probes(targets, timeout=PROBE_TIMEOUT):
    """Round of probes; returns the fresh results list (never raises)."""
    results = []
    for raw in (targets or []):
        if not isinstance(raw, dict):
            continue
        try:
            results.append(probe(raw, timeout))
        except CensorError:
            continue
        except Exception as exc:  # a broken probe must not kill the worker
            results.append({"host": str(raw.get("host", "?")), "port": raw.get("port"),
                            "label": str(raw.get("label", "?")),
                            "verdict": "timeout", "detail": type(exc).__name__,
                            "elapsed_ms": 0, "ts": int(time.time())})
    return results


def transitions(previous, results):
    """(label, old, new) triples for verdicts that changed since last round."""
    previous = previous if isinstance(previous, list) else []
    old = {str(item.get("host", "")) + ":" + str(item.get("port")): item.get("verdict")
           for item in previous if isinstance(item, dict)}
    changed = []
    for item in results:
        key = str(item.get("host", "")) + ":" + str(item.get("port"))
        prior = old.get(key)
        if prior is not None and prior != item.get("verdict"):
            changed.append((item.get("label") or item.get("host"), prior, item.get("verdict")))
    return changed


def summarize(results):
    """One line for the Security tab header / Telegram: counts per verdict."""
    counts = {"ok": 0, "blocked": 0, "timeout": 0}
    for item in results or []:
        verdict = item.get("verdict") if isinstance(item, dict) else None
        if verdict in counts:
            counts[verdict] += 1
    return "Проверок: %d · работает %d · блокируется %d · не отвечает %d" % (
        sum(counts.values()), counts["ok"], counts["blocked"], counts["timeout"])


def normalize_state(state):
    """data.json "censor" section: enabled flag, interval, targets, history."""
    state = state if isinstance(state, dict) else {}
    targets = []
    for raw in (state.get("targets") or []):
        if not isinstance(raw, dict):
            continue
        try:
            targets.append(normalize_target(raw.get("host"), raw.get("port"),
                                            raw.get("sni"), raw.get("label")))
        except CensorError:
            continue
    try:
        interval = max(60, min(3600, int(state.get("interval", 600))))
    except (TypeError, ValueError):
        interval = 600
    history = [item for item in (state.get("history") or []) if isinstance(item, dict)]
    return {"enabled": bool(state.get("enabled", False)),
            "interval": interval,
            "targets": targets,
            "results": [r for r in (state.get("results") or []) if isinstance(r, dict)],
            "history": history[-HISTORY_LIMIT:]}


def record_round(state, results):
    """Store a fresh round: overwrite results, append a history row per target."""
    state = normalize_state(state)
    state["results"] = results
    for item in results:
        entry = dict(item)
        entry.pop("detail", None)
        state["history"].append(entry)
    state["history"] = state["history"][-HISTORY_LIMIT:]
    return state


def history_json(state):
    """Compact history for the dashboard chart (host, ts, verdict)."""
    state = normalize_state(state)
    return json.dumps([{"h": item.get("host"), "t": item.get("ts"),
                        "v": item.get("verdict")} for item in state["history"]],
                      ensure_ascii=False)


def _elapsed(started):
    return int((time.monotonic() - started) * 1000)
