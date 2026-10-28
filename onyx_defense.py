"""Perimeter defense: a silent honeypot listener plus an nftables ban set.

The honeypot binds a decoy TCP port (nothing is served — the socket accepts,
records the peer, reads a first slice of bytes and closes). Repeated hits
from one source are pushed into the dynamic nft set "ban4"/ban6 (timeout
24h) so the input chain drops them before any real service sees a packet.
Every hit lands in the attacks journal in data.json for the Security tab.

The listener itself runs as a daemon thread inside panel.py; everything
stateful and everything that touches nft is here so it can be unit-tested
without sockets or root.
"""
import socket
import threading
import time

DEFAULT_PORT = 8081
HITS_PER_WINDOW = 5
WINDOW_SECONDS = 300
BAN_TIMEOUT = "24h"
BAN_THRESHOLD = 3
JOURNAL_LIMIT = 200
READ_BYTES = 512
# First bytes that tell the operator what was knocking at the door.
SIGNATURES = ((b"SSH-", "SSH"), (b"GET ", "HTTP"), (b"POST ", "HTTP"),
              (b"HEAD ", "HTTP"), (b"EHLO", "SMTP"), (b"\x16\x03", "TLS"),
              (b"CAPABILITY", "IMAP"), (b"USER ", "POP3/FTP"))


def _signature(first_bytes):
    for prefix, name in SIGNATURES:
        if first_bytes.startswith(prefix):
            return name
    return "Прочее"


def classify(source_ip):
    """ban6 for IPv6 literals, ban4 otherwise."""
    return "ban6" if ":" in str(source_ip) else "ban4"


def ban_command(source_ip, timeout=BAN_TIMEOUT):
    """nft command adding one host to the panel's dynamic ban set.

    The set is created by sync_firewall with `flags timeout` so the element
    expires on its own — no cleanup job, no risk of a permanent lockout.
    """
    source_ip = str(source_ip).strip()
    if not source_ip or any(ch in source_ip for ch in " {};\n"):
        raise ValueError("Некорректный IP для бан-набора: %s" % source_ip)
    return 'nft add element inet onyx_panel %s { %s timeout %s }' % (
        classify(source_ip), source_ip, timeout)


def _key(source_ip):
    return str(source_ip)


def note_hit(journal, source_ip, port, kind, now_ts=None):
    """Append one journal entry; returns (journal, entry) and prunes old hits.

    The journal doubles as the sliding-window counter: the caller counts
    entries per IP inside the window to decide on a ban.
    """
    journal = [entry for entry in (journal or []) if isinstance(entry, dict)]
    entry = {"ip": _key(source_ip), "ts": int(now_ts or time.time()),
             "port": int(port or 0), "kind": str(kind or "Прочее")[:40]}
    journal.append(entry)
    cutoff = entry["ts"] - 3600
    journal = [item for item in journal if int(item.get("ts", 0)) >= cutoff]
    return journal[-JOURNAL_LIMIT:], entry


def hit_count(journal, source_ip, window=WINDOW_SECONDS, now_ts=None):
    """How many hits this IP made inside the sliding window."""
    now_ts = int(now_ts or time.time())
    source_ip = _key(source_ip)
    return sum(1 for item in (journal or [])
               if isinstance(item, dict) and _key(item.get("ip")) == source_ip
               and now_ts - int(item.get("ts", 0)) <= window)


def should_ban(journal, source_ip, threshold=BAN_THRESHOLD, window=WINDOW_SECONDS,
               now_ts=None):
    return hit_count(journal, source_ip, window, now_ts) >= threshold


def recent(journal, limit=40):
    """Newest-first slice for the Security tab."""
    entries = [entry for entry in (journal or []) if isinstance(entry, dict)]
    entries.sort(key=lambda item: int(item.get("ts", 0)), reverse=True)
    return entries[:max(1, min(JOURNAL_LIMIT, int(limit)))]


def summarize(journal, banned=0, now_ts=None):
    """One-line header: total hits in the last day plus the live ban count."""
    now_ts = int(now_ts or time.time())
    day = sum(1 for item in (journal or [])
              if isinstance(item, dict) and now_ts - int(item.get("ts", 0)) <= 86400)
    return "Атак за сутки: %d · в бане: %d" % (day, int(banned or 0))


def normalize_state(state):
    """data.json "defense" section."""
    state = state if isinstance(state, dict) else {}
    journal = [entry for entry in (state.get("journal") or []) if isinstance(entry, dict)]
    try:
        port = int(state.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not 1024 <= port <= 65535:
        port = DEFAULT_PORT
    return {"enabled": bool(state.get("enabled", False)),
            "port": port,
            "journal": journal[-JOURNAL_LIMIT:]}


_listener = None
_lock = threading.Lock()


def start(port=DEFAULT_PORT, on_hit=None, on_ban=None, threshold=BAN_THRESHOLD):
    """Launch the honeypot thread; safe to call once per process.

    on_hit(ip, port, kind) reports every connection; on_ban(ip, command) is
    called when the sliding window trips the threshold — panel.py wires it
    to subprocess nft. Both callbacks must not raise.
    """
    global _listener
    with _lock:
        if _listener is not None and _listener.is_alive():
            return False
        _listener = threading.Thread(target=_run, name="onyx-honeypot",
                                     args=(int(port), on_hit, on_ban, int(threshold)),
                                     daemon=True)
        _listener.start()
    return True


def stop():
    """Ask the listener to exit (it closes its accept socket on next loop)."""
    global _listener
    with _lock:
        listener, _listener = _listener, None
    return listener is not None


def _run(port, on_hit, on_ban, threshold):
    counts = {}
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", port))
        server.listen(64)
        server.settimeout(1.0)
    except OSError as exc:
        print("honeypot bind failed:", exc, flush=True)
        return
    while _listener is not None:
        try:
            conn, addr = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        source_ip = addr[0]
        kind = "Прочее"
        try:
            conn.settimeout(1.5)
            data = conn.recv(READ_BYTES)
            kind = _signature(data)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
        counts[source_ip] = counts.get(source_ip, 0) + 1
        for callback, args in ((on_hit, (source_ip, port, kind)),
                               (on_ban, (source_ip, counts[source_ip], threshold))
                               if counts.get(source_ip, 0) >= threshold else ()):
            if callback is None:
                continue
            try:
                callback(*args)
            except Exception as exc:  # a dead callback must not kill the listener
                print("honeypot callback failed:", type(exc).__name__, flush=True)
    try:
        server.close()
    except OSError:
        pass
