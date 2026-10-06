"""Web Push для колокольчика: панели достаточно «пинга» без payload —
service worker по пробуждению сам забирает свежие уведомления через
/notifications и показывает их. Так вся криптография сводится к VAPID (ES256):
ключи P-256 выпускает системный openssl, шифрование payload (RFC 8291) не нужно.
"""
import base64
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path('/var/lib/onyx-panel-update')
PEM_PATH = ROOT / 'push-vapid.pem'
PUB_PATH = ROOT / 'push-vapid.json'
SUBS_PATH = ROOT / 'push-subscriptions.json'
CONTACT = 'mailto:onyx-panel@users.noreply.github.com'
JWT_TTL = 12 * 3600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _atomic_json(path: Path, payload):
    tmp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            json.dump(payload, handle)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='ascii'))
    except (OSError, ValueError):
        return default


def _openssl(args, stdin=b''):
    return subprocess.run(['openssl', *args], input=stdin, capture_output=True, timeout=15)


def _generate_keys():
    """P-256 ключ VAPID: PEM приватный (0600) и публичная точка в base64url."""
    key = _openssl(['ecparam', '-name', 'prime256v1', '-genkey', '-noout'])
    if key.returncode:
        raise ValueError('openssl не смог создать ключ: ' + key.stderr.decode('ascii', 'replace')[:200])
    fd = os.open(PEM_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(key.stdout)
    pub = _openssl(['ec', '-in', str(PEM_PATH), '-pubout', '-text', '-noout'])
    if pub.returncode:
        raise ValueError('openssl не смог отдать публичный ключ')
    text = pub.stdout.decode('ascii', 'replace')
    if 'pub:' not in text:
        raise ValueError('не удалось разобрать публичный ключ openssl')
    part = text.split('pub:', 1)[1].split('ASN1', 1)[0]
    hex_only = re.sub(r'[^0-9a-fA-F]', '', part)
    point = bytes.fromhex(hex_only)
    if len(point) != 65 or point[0] != 4:
        raise ValueError('публичный ключ VAPID не является несжатой точкой P-256')
    _atomic_json(PUB_PATH, {'public': _b64url(point), 'created': int(time.time())})


def public_key():
    """VAPID public key (base64url) для pushManager.subscribe; '' если push недоступен."""
    try:
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (PEM_PATH.is_file() and PUB_PATH.is_file()):
            _generate_keys()
        data = _read_json(PUB_PATH, {})
        return str(data.get('public', ''))
    except Exception as exc:
        print('webpush public_key failed:', type(exc).__name__, file=__import__('sys').stderr, flush=True)
        return ''


def _der_to_raw(signature: bytes) -> bytes:
    """DER ECDSA-подпись из openssl -> конкатенация r||s по 32 байта (ES256)."""
    if len(signature) < 8 or signature[0] != 0x30:
        raise ValueError('неожиданный формат подписи')
    index = 2
    numbers = []
    while index < len(signature) and len(numbers) < 2:
        if signature[index] != 0x02: raise ValueError('неожиданный формат подписи')
        length = signature[index + 1]
        value = signature[index + 2:index + 2 + length].lstrip(b'\x00')
        numbers.append(value.rjust(32, b'\x00'))
        index += 2 + length
    if len(numbers) != 2: raise ValueError('неожиданный формат подписи')
    return numbers[0] + numbers[1]


def _vapid_jwt(audience: str) -> str:
    import hashlib
    header = _b64url(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode())
    claims = _b64url(json.dumps({'aud': audience, 'exp': int(time.time()) + JWT_TTL,
                                 'sub': CONTACT}, separators=(',', ':')).encode())
    signing_input = (header + '.' + claims).encode('ascii')
    # OpenSSL 3.x: -rawin (сам хеширует SHA-256); 1.1.x: подаём готовый дайджест.
    signed = _openssl(['pkeyutl', '-sign', '-inkey', str(PEM_PATH), '-rawin',
                       '-pkeyopt', 'digest:sha256'], stdin=signing_input)
    if signed.returncode:
        signed = _openssl(['pkeyutl', '-sign', '-inkey', str(PEM_PATH),
                           '-pkeyopt', 'digest:sha256'],
                          stdin=hashlib.sha256(signing_input).digest())
    if signed.returncode:
        raise ValueError('openssl не смог подписать VAPID JWT')
    return header + '.' + claims + '.' + _b64url(_der_to_raw(signed.stdout))


def _load_subs():
    subs = _read_json(SUBS_PATH, [])
    return [item for item in subs if isinstance(item, dict) and item.get('endpoint', '').startswith('https://')]


def _save_subs(subs):
    _atomic_json(SUBS_PATH, subs[-500:])


def save_subscription(endpoint: str, keys):
    if not str(endpoint).startswith('https://'):
        raise ValueError('Подписка push должна использовать HTTPS-эндпоинт.')
    data = keys if isinstance(keys, dict) else json.loads(keys or '{}')
    if not data.get('p256dh') or not data.get('auth'):
        raise ValueError('В подписке push нет ключей p256dh/auth.')
    subs = _load_subs()
    record = {'endpoint': str(endpoint), 'keys': {'p256dh': str(data['p256dh'])[:400],
              'auth': str(data['auth'])[:200]}, 'created': int(time.time())}
    subs = [item for item in subs if item.get('endpoint') != record['endpoint']]
    subs.append(record)
    _save_subs(subs)


def remove_subscription(endpoint: str):
    subs = _load_subs()
    _save_subs([item for item in subs if item.get('endpoint') != str(endpoint)])


def _send(sub: dict):
    """Пустой push (ping): Content-Length 0, worker сам сходится за содержимым."""
    endpoint = sub['endpoint']
    audience = re.match(r'https://[^/]+', endpoint).group(0)
    jwt = _vapid_jwt(audience)
    request = urllib.request.Request(endpoint, data=b'', method='POST', headers={
        'TTL': '120', 'Urgency': 'normal', 'Content-Length': '0',
        'Authorization': 'vapid t=' + jwt + ', k=' + str(public_key())})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status in (200, 201)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return False  # подписка мертва — удаляем
        print('webpush send failed:', exc.code, file=__import__('sys').stderr, flush=True)
        return True
    except Exception as exc:
        print('webpush send failed:', type(exc).__name__, file=__import__('sys').stderr, flush=True)
        return True


def notify_new(note=None):
    """Разослать пинг всем подпискам в фоне; мёртвые эндпоинты вычищаются."""
    def worker():
        alive = []
        changed = False
        for sub in _load_subs():
            try:
                if _send(sub): alive.append(sub)
                else: changed = True
            except Exception as exc:
                alive.append(sub)
                print('webpush worker:', type(exc).__name__, file=__import__('sys').stderr, flush=True)
        if changed:
            try: _save_subs(alive)
            except OSError: pass
    try:
        if not _load_subs(): return
        threading.Thread(target=worker, daemon=True).start()
    except Exception as exc:
        print('webpush notify:', type(exc).__name__, file=__import__('sys').stderr, flush=True)
