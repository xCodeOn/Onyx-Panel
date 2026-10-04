"""Unit tests for the standalone Onyx Panel modules (no server needed).

Run from the repository root:  python tests/test_modules.py
"""
import base64, hashlib, hmac, os, sys, tempfile
import urllib.request as _ur

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import onyx_totp, onyx_webapi, onyx_access, onyx_failover, onyx_cascade, onyx_telegram

# ---- TOTP: RFC 6238 reference secret, cross-checked against a local HMAC ref
secret = base64.b32encode(b'12345678901234567890').decode().rstrip('=')
def _ref(counter, digits=6):
    key = base64.b32decode(secret + '=' * (-len(secret) % 8))
    d = hmac.new(key, counter.to_bytes(8, 'big'), hashlib.sha1).digest()
    o = d[-1] & 15
    return str((int.from_bytes(d[o:o+4], 'big') & 0x7fffffff) % (10 ** digits)).zfill(digits)
assert onyx_totp.totp_at(secret, 30) == _ref(1)
assert onyx_totp.verify(secret, _ref(1), timestamp=30)
assert onyx_totp.verify(secret, _ref(2), timestamp=45)      # +-1 window
assert not onyx_totp.verify(secret, _ref(5), timestamp=30)  # wrong code
assert not onyx_totp.verify(secret, 'abc', timestamp=30)    # non digits
assert 'otpauth://totp/Onyx%20Panel:admin?secret=' in onyx_totp.provisioning_uri(secret, 'admin')
print('TOTP OK')

# ---- webapi: tokens, hashing, validation
tok = onyx_webapi.create_token()
k, tok2 = onyx_webapi.new_key('test')
assert tok2.startswith('onx_') and onyx_webapi.find_key([k], tok2) is k
assert onyx_webapi.find_key([k], 'onx_wrong') is None
assert onyx_webapi.public_keys([k]) == [{'id': k['id'], 'name': 'test', 'created': k['created'], 'last_used': 0}]
name, proto, dev = onyx_webapi.check_create({'name': ' client ', 'protocol': 'VLESS', 'devices': '3'})
assert (name, proto, dev) == ('client', 'vless', 3)
for bad in ({'name': ''}, {'name': 'x', 'protocol': 'mieru'}, {'name': 'x', 'protocol': 'vless', 'devices': 99}):
    try:
        onyx_webapi.check_create(bad); raise SystemExit('should fail')
    except ValueError:
        pass
assert onyx_webapi.check_renew({'days': '30'}) == 30
try:
    onyx_webapi.check_renew({'days': 0}); raise SystemExit('should fail')
except ValueError:
    pass
print('WEBAPI OK')

# ---- access journal
st = {}
fresh1 = onyx_access.record_login(st, 'admin', 'admin', '1.2.3.4', 'Mozilla/5.0 test')
fresh2 = onyx_access.record_login(st, 'admin', 'admin', '1.2.3.4', 'Mozilla/5.0 test')
assert fresh1 and not fresh2 and len(st['logins']) == 2 and st['logins'][-1]['new_device'] is False
assert len(onyx_access.last_logins(st, 10)) == 2
assert onyx_access.observer_can_get('/dashboard') and not onyx_access.observer_can_get('/settings')
assert onyx_access.observer_can_post('/logout') and not onyx_access.observer_can_post('/add-user')
print('ACCESS OK')

# ---- failover decisions
cascades = [{'id': 'a', 'enabled': True, 'mode': 'all'},
            {'id': 'b', 'enabled': False, 'mode': 'all'},
            {'id': 'c', 'enabled': False, 'mode': 'users'}]
f = {}
f = onyx_failover.note_result(f, 'a', False); assert onyx_failover.decide(cascades, f) is None
f = onyx_failover.note_result(f, 'a', False)
assert onyx_failover.decide(cascades, f) == {'disable': 'a', 'enable': 'b'}
f = onyx_failover.note_result(f, 'a', True); assert onyx_failover.decide(cascades, f) is None
assert onyx_failover.decide([{'id': 'a', 'enabled': True, 'mode': 'all'}], {'a': 5}) is None  # no standby
print('FAILOVER OK')

# ---- telegram: multipart upload shape (network mocked)
captured = {}
import io as _io
class _Resp(_io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
captured = {}
def fake_urlopen(req, timeout=None):
    captured['body'] = req.data; captured['headers'] = req.headers
    return _Resp(b'{"ok":true}')
_urlopen_orig = _ur.urlopen; _ur.urlopen = fake_urlopen
try:
    onyx_telegram.send_document('123:tok', '42', 'backup.tar.gz', b'PAYLOAD')
finally:
    _ur.urlopen = _urlopen_orig
assert 'multipart/form-data' in captured['headers']['Content-type']
assert b'PAYLOAD' in captured['body'] and b'filename="backup.tar.gz"' in captured['body']
# notify() respects event toggles
_ur.urlopen = fake_urlopen
try:
    assert onyx_telegram.notify({'token': '123:tok', 'chat': '42', 'events': {'logins': False}}, 'logins', 'x') is False
    assert onyx_telegram.notify({'token': '123:tok', 'chat': '42', 'events': {'logins': True}}, 'logins', 'x') is True
finally:
    _ur.urlopen = _urlopen_orig
print('TELEGRAM OK')

# ---- cascade speedtest: clean error path without a real xray binary
res = onyx_cascade.speedtest({'uuid': '0' * 32, 'address': 'example.com', 'port': 443,
                              'network': 'tcp', 'security': 'none', 'stream': {}, 'flow': ''},
                             xray_bin='/nonexistent/xray')
assert not res['ok'] and res['message'], res
print('SPEEDTEST OK')

# ---- update bell notifications: temp paths, no network, no systemctl
import onyx_update
from pathlib import Path
upd_tmp = Path(tempfile.mkdtemp())
onyx_update.ROOT = upd_tmp
onyx_update.STATUS = upd_tmp / 'status.json'
onyx_update.NOTES = upd_tmp / 'notifications.json'
onyx_update.VERSION = upd_tmp / 'version'
onyx_update.VERSION.write_text('1.6.0', encoding='ascii')
onyx_update.REPO = 'https://github.com/xCodeOn/Onyx-Panel.git'

# release markdown -> plain lines
assert onyx_update.parse_notes('## Изменения\n\n- fix a (abc1234)\n\n* feat b (def5678)\n# Заголовок\nтекст без маркера') == \
    ['fix a (abc1234)', 'feat b (def5678)', 'текст без маркера']
assert onyx_update.parse_notes('') == []
assert onyx_update.repo_slug() == 'xCodeOn/Onyx-Panel'
onyx_update.REPO = 'https://gitlab.com/x/panel.git'
assert onyx_update.repo_slug() == ''
onyx_update.REPO = 'https://github.com/xCodeOn/Onyx-Panel.git'

# add/dedupe/read/clear
onyx_update.add_note('available', 'v1.7.0')
onyx_update.add_note('available', 'v1.7.0')          # dedupe by (kind, version)
assert len(onyx_update.load_notes()) == 1
assert onyx_update.notes_public()['unread'] == 1
onyx_update.mark_notes_read()
assert onyx_update.notes_public()['unread'] == 0
onyx_update.add_note('available', 'v1.7.0')          # re-check keeps read flag
assert onyx_update.load_notes()[0]['read'] is True
assert onyx_update.load_notes()[0]['current'] == '1.6.0'
onyx_update.clear_notes()
assert onyx_update.load_notes() == []

# prune: "available" notes for installed versions disappear, others stay
onyx_update.add_note('available', 'v1.7.0')
onyx_update.add_note('changelog', 'v1.6.9')
onyx_update.prune_available('1.7.0')
kinds = sorted(item['kind'] for item in onyx_update.load_notes())
assert kinds == ['changelog'], kinds
onyx_update.clear_notes()

# finished update -> changelog note, available note pruned, announced marker set
onyx_update.VERSION.write_text('1.7.0', encoding='ascii')
onyx_update.add_note('available', 'v1.7.0')
onyx_update.atomic_json(onyx_update.STATUS, {'phase': 'done', 'target': 'v1.7.0'})
onyx_update.release_notes = lambda tag: (['Новая функция колокольчика (ab12cd3)'], 'https://github.com/xCodeOn/Onyx-Panel/releases/tag/v1.7.0')
status = onyx_update.get_status()
notes = onyx_update.load_notes()
assert status['announced'] == 'v1.7.0', status
assert [n['kind'] for n in notes] == ['changelog'], notes
assert notes[0]['version'] == 'v1.7.0' and notes[0]['changes'] == ['Новая функция колокольчика (ab12cd3)']
assert notes[0]['link'].endswith('/releases/tag/v1.7.0')
assert onyx_update.get_status()['announced'] == 'v1.7.0'      # announced only once
assert len(notes) == 1

# update finished but panel runs a different version -> no announcement
onyx_update.clear_notes()
onyx_update.atomic_json(onyx_update.STATUS, {'phase': 'done', 'target': 'v9.9.9'})
onyx_update.get_status()
assert onyx_update.load_notes() == []
onyx_update.clear_notes()
print('UPDATE NOTIFICATIONS OK')
# ---- node registry sync: version refresh + heal of stale enabled=false
import onyx_nodes
nodes_tmp = Path(tempfile.mkdtemp())
registry = str(nodes_tmp / 'nodes.json')
registry_state = [
    {'id': 'aaaa', 'url': 'https://a.example.com', 'token': 'a' * 43, 'country_code': 'FI',
     'country_name': 'Финляндия', 'name': 'Хельсинки', 'version': '1.8.8', 'enabled': True},
    {'id': 'bbbb', 'url': 'https://b.example.com', 'token': 'b' * 43, 'country_code': 'DE',
     'country_name': 'Германия', 'name': 'Франкфурт', 'version': '1.8.8', 'enabled': False},
]
onyx_nodes.save_nodes(registry, registry_state)

# live probes: a answered with a fresh version, b answers despite enabled=false
live_a = {'id': 'aaaa', 'enabled': True, 'online': True, 'version': '1.9.6'}
live_b = {'id': 'bbbb', 'enabled': True, 'online': True, 'version': '1.9.6'}
assert onyx_nodes.sync_registry(registry, [registry_state[0], registry_state[1]], [live_a, live_b]) is True
merged = {n['id']: n for n in onyx_nodes.load_nodes(registry)}
assert merged['aaaa']['version'] == '1.9.6'
assert merged['bbbb']['version'] == '1.9.6' and merged['bbbb']['enabled'] is True
print('NODE REGISTRY SYNC OK')

# no changes -> no write
assert onyx_nodes.sync_registry(registry, list(merged.values()), [live_a, live_b]) is False
# failed probe (offline, no version) does not heal or touch the record
dead = {'id': 'aaaa', 'enabled': True, 'online': False, 'version': ''}
assert onyx_nodes.sync_registry(registry, [merged['aaaa']], [dead]) is False
# a node deleted while the refresh ran is not resurrected by stale snapshots
onyx_nodes.save_nodes(registry, [dict(merged['bbbb'], version='1.9.6')])
assert onyx_nodes.sync_registry(registry, [merged['aaaa']], [live_a]) is False
assert [n['id'] for n in onyx_nodes.load_nodes(registry)] == ['bbbb']
print('NODE REGISTRY EDGE CASES OK')

# ---- routing rules: normalization and Xray merge shape
import onyx_routing
# typographic dashes normalize to the ASCII hyphen
assert onyx_routing._clean_entry('domain:xn\u2014\u2014p1ai') == 'domain:xn--p1ai'
assert onyx_routing._clean_entry('geoip:ru\u2013test') == 'geoip:ru-test'
assert onyx_routing._clean_entry('') == '' and onyx_routing._clean_entry('  ') == ''
for bad in ('a b', 'do"main', 'x' * 121):
    try:
        onyx_routing._clean_entry(bad); raise SystemExit('should fail: ' + repr(bad))
    except onyx_routing.RoutingError:
        pass
assert onyx_routing._clean_list(['', '  ', 'domain:ru']) == ['domain:ru']
try:
    onyx_routing._clean_list(['domain:ru', 'a b']); raise SystemExit('should fail')
except onyx_routing.RoutingError:
    pass
norm = onyx_routing.normalize({'direct_ips': ['GeoIP:RU', 'geoip:ru', '1.2.3.4', ''],
                               'direct_domains': ['domain:\u0440\u0444'],
                               'ipv4_domains': ['domain:example.com'], 'block_torrents': 1})
assert norm['direct_ips'] == ['GeoIP:RU', '1.2.3.4'] and norm['direct_domains'] == ['domain:\u0440\u0444']
assert norm['ipv4_domains'] == ['domain:example.com'] and norm['block_torrents'] is True
outbounds, rules = onyx_routing.xray_additions(norm)
assert [o['tag'] for o in outbounds] == ['blocked', 'ipv4'], outbounds
assert [r['outboundTag'] for r in rules] == ['blocked', 'direct', 'direct', 'ipv4'], rules
assert onyx_routing.xray_additions(onyx_routing.normalize({})) == ([], [])
routing_tmp = str(Path(tempfile.mkdtemp()) / 'routing.json')
onyx_routing.save(routing_tmp, norm)
reloaded = onyx_routing.load(routing_tmp)
assert reloaded['direct_ips'] == norm['direct_ips'] and reloaded['block_torrents'] is True
assert onyx_routing.load(routing_tmp + '.missing') == onyx_routing.normalize({})
print('ROUTING OK')

# ---- WARP: X25519 (RFC 7748 §6.1), парсер конфига, форма правил для Xray
import onyx_warp
alice_priv = bytes.fromhex('77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a')
assert onyx_warp._x25519_base(alice_priv).hex() == \
    '8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a'
priv_key, pub_key = onyx_warp.keypair()
assert onyx_warp.KEY_RE.match(priv_key) and onyx_warp.KEY_RE.match(pub_key)
assert onyx_warp._x25519_base(bytes.fromhex(base64.b64decode(priv_key).hex())).hex() == \
    base64.b64decode(pub_key).hex()          # pubkey = base * priv

warp_tmp = str(Path(tempfile.mkdtemp()) / 'warp.json')
wgconf = ('[Interface]\nPrivateKey = %s\nAddress = 172.16.0.2/32, fd01::2/128\n'
          'DNS = 1.1.1.1\n\n[Peer]\nPublicKey = %s\nAllowedIPs = 0.0.0.0/0\n'
          'Endpoint = engage.cloudflareclient.com:2408\n' % (priv_key, pub_key))
state = onyx_warp.import_config(warp_tmp, wgconf)
assert state['private_key'] == priv_key and state['address'] == '172.16.0.2'
assert state['endpoint'] == 'engage.cloudflareclient.com:2408' and state['users'] == []
for bad in ('[Interface]\nPrivateKey = short\n', '[Peer]\nPublicKey = ' + pub_key,
            '[Interface]\nPrivateKey = %s\n' % priv_key +
            '[Peer]\nPublicKey = %s\nEndpoint = bad host:port\n' % pub_key):
    try:
        onyx_warp.import_config(warp_tmp, bad); raise SystemExit('should fail')
    except onyx_warp.WarpError:
        pass

outbounds, rules = onyx_warp.xray_additions(state, ['aabbccddeeff0011'])
assert outbounds[0]['tag'] == 'warp' and outbounds[0]['protocol'] == 'wireguard'
assert outbounds[0]['settings']['peers'][0]['endpoint'] == 'engage.cloudflareclient.com:2408'
assert rules[0]['user'] == ['panel:aabbccddeeff0011'] and rules[0]['outboundTag'] == 'warp'
assert onyx_warp.xray_additions(state, []) == ([], [])
assert not onyx_warp.configured(onyx_warp.load(warp_tmp + '.missing'))

onyx_warp.set_users(warp_tmp, ['aabbccddeeff0011', '1122334455667788'], True)
assert onyx_warp.has_users(warp_tmp, ['aabbccddeeff0011'])
onyx_warp.set_users(warp_tmp, ['aabbccddeeff0011'], False)
assert not onyx_warp.has_users(warp_tmp, ['aabbccddeeff0011'])
assert onyx_warp.has_users(warp_tmp, ['1122334455667788'])
onyx_warp.reset(warp_tmp)
assert not onyx_warp.configured(onyx_warp.load(warp_tmp))
print('WARP OK')

# ---- Reality: валидация, инбаунд, ссылка
import onyx_reality
priv_r, pub_r = onyx_warp.keypair()
r_tmp = str(Path(tempfile.mkdtemp()) / 'reality.json')
state = onyx_reality.setup(r_tmp, port=2053, dest='www.wildberries.ru:443')
assert state['enabled'] is True and state['port'] == 2053
assert all(onyx_reality.SHORT_ID_RE.match(i) for i in state['short_ids']) and len(state['short_ids']) == 4
assert state['private_key'] != priv_r.rstrip('=')         # каждый setup — новые ключи
assert not state['private_key'].endswith('=')             # Xray 26+ требует ключ без padding
for bad in ({'port': 80, 'dest': 'a.com:443'}, {'port': 2053, 'dest': 'no port'},
            {'port': 2053, 'dest': 'a.com:443', 'private_key': 'short', 'public_key': pub_r}):
    try:
        s2 = dict(state); s2.update(bad)
        onyx_reality.validate(s2); raise SystemExit('should fail: ' + repr(bad))
    except onyx_reality.RealityError:
        pass
users_r = [{'id': 'aabbccddeeff0011', 'secret': 'S1' * 8, 'protocol': 'vless', 'enabled': True},
           {'id': 'bbbbccccdddd0000', 'secret': 'S2' * 8, 'protocol': 'vless', 'enabled': False},
           {'id': 'ccccdddd0000eeee', 'secret': 'S3' * 8, 'protocol': 'hysteria', 'enabled': True}]
inb = onyx_reality.inbound(state, users_r)
assert inb['tag'] == 'vless-reality' and inb['port'] == 2053 and len(inb['settings']['clients']) == 1
assert inb['settings']['clients'][0]['flow'] == 'xtls-rprx-vision'
assert inb['streamSettings']['realitySettings']['dest'] == 'www.wildberries.ru:443'
assert onyx_reality.inbound(state, []) is None
disabled = dict(state, enabled=False)
assert onyx_reality.inbound(disabled, users_r) is None
link = onyx_reality.link(state, 'S1' * 8, 'Тест · Reality', host='panel.example.com')
assert 'security=reality' in link and 'flow=xtls-rprx-vision' in link
assert '@panel.example.com:2053' in link and 'sni=www.wildberries.ru' in link
from urllib.parse import urlsplit, parse_qs
query = parse_qs(urlsplit(link).query)
assert query['pbk'][0] == state['public_key'] and query['sid'][0] == state['short_ids'][0]
assert query['fp'][0] == 'chrome' and query['spx'][0] == '/'
assert onyx_reality.link(disabled, 'S1' * 8, 'x', host='h') == ''
onyx_reality.reset(r_tmp)
assert not onyx_reality.enabled(onyx_reality.load(r_tmp))
print('REALITY OK')

# ---- embedded code: sync_xray/sync_firewall исполняются со стабами (ловля NameError)
import ast as _ast, types as _types
import json as json, re as re, time as time
_lines = open(os.path.join(ROOT, "install-panel.sh"), encoding="utf-8").read().split("\n")
_man = _lines.index('cat > "$MANAGER" <<\'PY\'')
_man_end = _lines.index("PY", _man + 1)
_man_code = "\n".join(_lines[_man + 1:_man_end])
_tree = _ast.parse(_man_code, feature_version=(3, 10))

class _OsShim:
    def __getattr__(self, name): return getattr(os, name)
    def chown(self, *a, **k): pass

_ns = {'onyx_routing': _types.SimpleNamespace(load=lambda p: {}, xray_additions=lambda d: ([], [])),
       'onyx_warp': _types.SimpleNamespace(load=lambda p: {'users': []}, EMAIL_PREFIX='panel:', xray_additions=lambda s, u: ([], [])),
       'onyx_reality': _types.SimpleNamespace(load=lambda p: {'enabled': True, 'port': 2053, 'dest': 'www.wildberries.ru:443',
                                                              'server_names': ['www.wildberries.ru'],
                                                              'private_key': 'P' * 42 + 'A=', 'public_key': 'Q' * 42 + 'A=',
                                                              'short_ids': ['abcd1234']},
                                              inbound=lambda s, u: onyx_reality.inbound(s, u)),
       'onyx_cascade': _types.SimpleNamespace(load_cascades=lambda p: [], xray_additions=lambda c, u: ([], [])),
       'onyx_awg': _types.SimpleNamespace(PROTOCOLS=('awg20', 'awg31')),
       'XRAY_PATH_FILE': os.path.join(ROOT, 'tests', 'xray-path-stub'), 'XRAY_VLESS_PORT': 10000,
       'HYSTERIA_PORT': 8443, 'XRAY_CERT': os.path.join(ROOT, 'tests', 'cert-stub'),
       'XRAY_KEY': os.path.join(ROOT, 'tests', 'key-stub'),
       'XRAY_CONFIG': os.path.join(tempfile.mkdtemp(), 'config.json'),
       'XRAY_API': '127.0.0.1:10085', 'XRAY_BIN': '/bin/true', 'XRAY_SERVICE': 'onyx-panel-xray',
       'ROUTING_FILE': '/tmp/none1', 'WARP_FILE': '/tmp/none2', 'REALITY_FILE': '/tmp/none3',
       'CASCADES_FILE': '/tmp/none4', 'FIREWALL_SCRIPT': '/tmp/fw-stub.sh',
       'run': lambda *a, **k: _types.SimpleNamespace(returncode=0, stdout='OK', stderr=''),
       'os': _OsShim(), 're': re, 'json': json, 'time': time, 'sys': sys,
       'grp': _types.SimpleNamespace(getgrnam=lambda name: _types.SimpleNamespace(gr_gid=0))}

def _extract_fn(name):
    fn = next(n for n in _tree.body if isinstance(n, _ast.FunctionDef) and n.name == name)
    return _ast.Module(body=[fn], type_ignores=[])

# sync_xray: включённый Reality даёт инбаунд перед xhttp, выключенный — ничего
_path_dir = os.path.dirname(_ns['XRAY_PATH_FILE'])
os.makedirs(_path_dir, exist_ok=True)
open(_ns['XRAY_PATH_FILE'], 'w').write('/vless-' + 'a' * 24)
open(_ns['XRAY_CERT'], 'w').write('x'); open(_ns['XRAY_KEY'], 'w').write('x')
exec(compile(_extract_fn('sync_xray'), 'sync_xray', 'exec'), _ns)
_d = {'users': [{'id': 'aabbccddeeff0011', 'secret': 'S1' * 8, 'protocol': 'vless', 'enabled': True}]}
_ns['sync_xray'](_d)
_cfg = json.load(open(_ns['XRAY_CONFIG']))
_tags = [i['tag'] for i in _cfg['inbounds']]
assert _tags[0] == 'vless-reality' and 'vless-xhttp' in _tags, _tags
_ns['onyx_reality'].load = lambda p: {'enabled': False}
_ns['sync_xray'](_d)
assert 'vless-reality' not in [i['tag'] for i in json.load(open(_ns['XRAY_CONFIG']))['inbounds']]

# sync_firewall: порт Reality попадает в UFW tcp-набор только при включённом
_recon = {}
_ns['onyx_firewall'] = _types.SimpleNamespace(reconcile=lambda **kw: _recon.update(kw))
_ns['onyx_reality'].load = lambda p: {'enabled': True, 'port': 2053}
_ns['onyx_routing'].load = lambda p: {}
exec(compile(_extract_fn('sync_firewall'), 'sync_firewall', 'exec'), _ns)
_ns['collect_traffic'] = lambda d: None
_ns['sync_firewall']({'users': [{'id': 'x', 'protocol': 'hysteria', 'enabled': True, 'backend_port': 8443}]})
assert 2053 in _recon['tcp']
_ns['onyx_reality'].load = lambda p: {'enabled': False}
_ns['sync_firewall']({'users': [{'id': 'x', 'protocol': 'hysteria', 'enabled': True, 'backend_port': 8443}]})
assert 2053 not in _recon['tcp']
print('EMBEDDED SYNC OK')

# ---- openflux: проверка документа, срок, резервный документ, watchdog, Яндекс Диск
import time
import pathlib
import onyx_openflux as onyx_openflux

_of = onyx_openflux
assert _of.DEAD_AFTER == 2 and _of.SWAP_COOLDOWN == 3600

# валидация ссылок строга; check_document — статус-машина ok/dead/unknown
for _bad in ('http://disk.yandex.ru/i/x', 'https://evil.example/i/x', 'https://disk.yandex.ru/i/x#f'):
    try:
        _of.validate_document_url(_bad); raise SystemExit('should fail: ' + _bad)
    except _of.OpenFluxError:
        pass
assert _of.check_document('https://disk.yandex.ru/i/abc', 'yandex', fetch=lambda u, t: (200, None))['status'] == 'ok'
assert _of.check_document('https://cloud.mail.ru/public/a/b', 'mailru', fetch=lambda u, t: (404, None))['status'] == 'dead'
assert _of.check_document('https://cloud.mail.ru/public/a/b', 'mailru', fetch=lambda u, t: (None, 'timeout'))['status'] == 'unknown'
# Яндекс: API публичных ресурсов 404 -> dead; 400 (не файл Диска) -> контрольный GET страницы
_seen = []
def _ya_fetch(url, timeout):
    _seen.append(url)
    return (404, None) if 'cloud-api.yandex' in url else (400, None)
assert _of.check_document('https://disk.yandex.ru/i/abc', 'yandex', fetch=_ya_fetch)['status'] == 'dead' and len(_seen) == 1
_seen.clear()
def _ya_fetch2(url, timeout):
    _seen.append(url)
    return (400, None) if 'cloud-api.yandex' in url else (200, None)
assert _of.check_document('https://disk.yandex.ru/i/abc', 'yandex', fetch=_ya_fetch2)['status'] == 'ok' and len(_seen) == 2

# срок: разбор даты и выборка истёкших
_exp = _of._clean_expires('2030-01-31')
assert _exp and _exp > time.time() and _of._clean_expires('') is None
try:
    _of._clean_expires('31.01.2030'); raise SystemExit('bad date accepted')
except _of.OpenFluxError:
    pass
_now = 10 ** 6
_mixed = [{'id': 'p1', 'name': 'A', 'enabled': True, 'expires_at': _now - 10},
          {'id': 'p2', 'name': 'B', 'enabled': False, 'expires_at': _now - 10},
          {'id': 'p3', 'name': 'C', 'enabled': True, 'expires_at': _now + 10},
          {'id': 'p4', 'name': 'D', 'enabled': True}]
assert [p['id'] for p in _of.expired_profiles(_mixed, _now)] == ['p1']

# watchdog на временных путях: смерть документа, автовключение резерва,
# восстановление и отзыв по сроку
_tmp = pathlib.Path(tempfile.mkdtemp()); (_tmp / 'profiles').mkdir()
_of.CONFIG_DIR = _tmp
_of.STATE_FILE = _tmp / 'config.json'; _of.URL_FILE = _tmp / 'document-url'
_of.KEY_FILE = _tmp / 'key'; _of.ENABLED_FILE = _tmp / 'enabled'
_of.PROFILES_DIR = _tmp / 'profiles'; _of.HEALTH_FILE = _tmp / 'health.json'
_of.TOKEN_FILE = _tmp / 'yandex-token'; _of.UNIT = _tmp / 'unit.service'
_paths_orig = _of._extra_paths
_of._extra_paths = lambda pid: {**_paths_orig(pid), 'dir': _tmp / 'profiles' / pid, 'unit': _tmp / ('unit-' + pid + '.service')}
_of._identity = lambda: (os.getuid(), os.getgid())
_of._start = lambda: None
_of._start_extra = lambda config: None
_of._service_active = lambda: True
_of._extra_active = lambda pid: True
_of.os.chown = lambda *a, **k: None   # тест не root
def _test_atomic(path, value, mode, uid=0, gid=0):
    path = pathlib.Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    with os.fdopen(fd, 'w') as handle: handle.write(value)
    os.chmod(tmp_name, mode); os.replace(tmp_name, path)
_of._atomic = _test_atomic
_of._fetch_page = lambda u, t: (200, None)   # сеть не трогаем

_state = _of.configure('https://disk.yandex.ru/i/abc', transport='yandex')
assert _state['configured']
_of._fetch_page = lambda u, t: (404, None)
try:
    _of.configure('https://disk.yandex.ru/i/gone', transport='yandex'); raise SystemExit('dead doc accepted')
except _of.OpenFluxError:
    pass
_of._fetch_page = lambda u, t: (200, None)
_config = _of.create_profile('Анна', 'https://cloud.mail.ru/public/a/b', 'android', transport='mailru', expires='2030-01-31')
assert _config['expires_at'] > time.time() and (_tmp / 'profiles' / _config['id']).is_dir()
try:
    _of.create_profile('Двойник', 'https://cloud.mail.ru/public/a/b', 'ios', transport='mailru')
    raise SystemExit('dup accepted')
except _of.OpenFluxError:
    pass

try:
    _of.set_fallback(_config['id'], 'https://cloud.mail.ru/public/c/d', 'mailru')
    raise SystemExit('same transport accepted')
except _of.OpenFluxError:
    pass
_of.set_fallback(_config['id'], 'https://disk.yandex.ru/i/reserve', 'yandex')
assert _of.profile_states()[1]['fallback_url'].endswith('/i/reserve')

_old_url = _config['url']
_switched = _of.apply_fallback(_config['id'])
assert _switched['url'].endswith('/i/reserve') and _switched['fallback_url'] == _old_url and _switched['transport'] == 'yandex'

_of._fetch_page = lambda u, t: (404, None)
_health = {}
assert _of.watchdog_tick(now=_now, profiles=None, health=_health) == []
assert _health[_config['id']]['failures'] == 1
_events = _of.watchdog_tick(now=_now + 300, profiles=None, health=_health)
_kinds = [event['event'] for event in _events]
assert 'dead' in _kinds and 'switched' in _kinds, _events
_swap = [event for event in _events if event['event'] == 'switched'][0]
assert _swap['url'] == _old_url, (_swap, _old_url)
assert _health[_config['id']]['swapped_at'] == _now + 300
_live = _of._extra_configs()[0]
assert _live['url'] == _old_url and _live['transport'] == 'mailru', (_live['url'], _live['transport'])

_of._fetch_page = lambda u, t: (200, None)
_events = _of.watchdog_tick(now=_now + 600, profiles=None, health=_health)
assert any(event['event'] == 'recovered' for event in _events) and _health[_config['id']]['failures'] == 0

_disabled = []
_profile = [p for p in _of.profile_states() if p['id'] == _config['id']][0]
_profile2 = dict(_profile); _profile2['expires_at'] = _now - 1
_events = _of.watchdog_tick(now=_now, profiles=[_profile2], health={}, fetch=lambda u, t: (200, None),
                            disable=lambda pid: _disabled.append(pid))
assert _events and _events[0]['event'] == 'revoked' and _disabled == [_config['id']], (_events, _disabled)

# токен Яндекса: формат, хранение 0600, создание документа
_of._yandex_request = lambda *a, **k: {}
_token = _of.save_yandex_token('y0_AgAAAA' * 4)
assert _token == _of.load_yandex_token() and oct(_of.TOKEN_FILE.stat().st_mode & 0o777) == '0o600'
try:
    _of.save_yandex_token('bad'); raise SystemExit('bad token accepted')
except _of.OpenFluxError:
    pass
# формы ответов как у живого API: publish отдаёт объект-Link, public_url
# лежит в метаданных ресурса (GET /resources) и может доезжать с ретраями
_calls = []
def _fake_api(token, path, method='GET', params=None, data=None, timeout=20):
    _calls.append((method, path, tuple(sorted((params or {}).items()))))
    if path == '/resources/upload': return {'href': 'https://uploader.example/put'}
    if path == '/resources/publish': return {'href': 'https://api.example/resources?path=x', 'method': 'GET'}
    if path == '/resources' and (params or {}).get('path'):
        _meta_calls = _calls.count((method, path, tuple(sorted((params or {}).items()))))
        if _meta_calls == 1: return {}   # первая попытка — пусто (гонка публикации)
        return {'public_url': 'https://yadi.sk/i/XYZ'}
    return {}
_of._yandex_request = _fake_api
_of._upload_plain = lambda href, payload: None
_real_sleep = _of.time.sleep
_of.time.sleep = lambda seconds: None
try:
    _doc_url = _of.create_yandex_document('Анна iPhone')
finally:
    _of.time.sleep = _real_sleep
assert _doc_url.startswith('https://disk.yandex.ru/i/'), _doc_url
assert any(m == 'PUT' and p == '/resources/publish' for m, p, _ in _calls), _calls
assert any(p == '/resources' and ('fields', 'public_url') in q for _, p, q in _calls), _calls
_of.clear_fallback(_config['id'])
assert all(not p.get('fallback_url') for p in _of.profile_states())
print('OPENFLUX OK')

# ---- openflux Mail.ru: OAuth «пароль для внешних приложений», статус, создание
_of.os.chown = lambda *a, **k: None
_mr_file = _tmp / 'mailru-credentials'
_of.MAILRU_CREDENTIALS_FILE = _mr_file
_of.MAILRU_SESSION_FILE = _tmp / 'mailru-session'
_of.MAILRU_COOKIE_FILE = _tmp / 'mailru-cookies'
import json as _json, time as _mtime
# пустые поля — внятная ошибка без сети
try:
    _of.save_mailru_credentials('', ''); raise SystemExit('empty credentials accepted')
except _of.OpenFluxError:
    pass
# OAuth-грант: успех хранит токены (0600)
_real_grant = _of._mailru_grant
_grants = []
def _grant_ok(data):
    _grants.append(dict(data))
    assert data['client_id'] == _of.MAILRU_CLIENT_ID
    return {'access_token': 'AT1', 'refresh_token': 'RT1', 'expires_in': 3600}
_of._mailru_grant = _grant_ok
_of.save_mailru_credentials('user@mail.ru', 'app-password-1')
_saved = _of.load_mailru_credentials()
assert _saved['email'] == 'user@mail.ru' and _saved['password'] == 'app-password-1'
assert _saved['access_token'] == 'AT1' and _saved['refresh_token'] == 'RT1'
assert _saved['expires_at'] > _mtime.time()
assert oct(_mr_file.stat().st_mode & 0o777) == '0o600'
# обычный пароль отклонён (error_code 3) -> подсказка про «пароль для внешних приложений»
# (проверяем настоящий _mailru_grant через фейковый urlopen: проверка ошибки живёт в нём)
class _FakeHTTPResponse:
    def __init__(self, body): self._body = body.encode()
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False
def _fake_grant_urlopen(url, data=None, headers=None, method=None, timeout=0):
    return _FakeHTTPResponse('{"error":"invalid username or password","error_code":3,'
                             '"error_description":"username or password is incorrect"}')
_of._mailru_grant = _real_grant   # проверяем настоящий грант, а не заглушку
_real_urlopen = _of.urllib.request.urlopen
_of.urllib.request.urlopen = _fake_grant_urlopen
try:
    _of._mailru_grant({'grant_type': 'password', 'username': 'user@mail.ru',
                       'password': 'main-password', 'client_id': _of.MAILRU_CLIENT_ID})
    raise SystemExit('main password accepted by grant')
except _of.OpenFluxError as _e:
    assert 'внешних приложений' in str(_e), _e
finally:
    _of.urllib.request.urlopen = _real_urlopen
# живой токен — из файла без сети; протухший — refresh-токеном
_of._mailru_grant = _grant_ok
assert _of._mailru_access_token() == 'AT1'
_stale = _of.load_mailru_credentials(); _stale['expires_at'] = int(_mtime.time()) - 10
_of.MAILRU_CREDENTIALS_FILE.write_text(_json.dumps(_stale))
def _grant_refresh(data):
    _grants.append(dict(data))
    assert data['grant_type'] == 'refresh_token' and data['refresh_token'] in ('RT1', 'RT2'), _grants
    return {'access_token': 'AT2', 'refresh_token': 'RT2', 'expires_in': 3600}
_of._mailru_grant = _grant_refresh
assert _of._mailru_access_token() == 'AT2'
_refreshed = _of.load_mailru_credentials()
assert _refreshed['access_token'] == 'AT2' and _refreshed['refresh_token'] == 'RT2'
assert _of.mailru_status() == {'connected': True, 'email': 'user@mail.ru'}
# API-вызовы ходят с access_token+client_id в query; 403 -> refresh и повтор
class _FakeResponse:
    def __init__(self, body): self._body = body.encode()
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False
_seen = []
_real_urlopen = _of.urllib.request.urlopen
def _fake_urlopen(url, data=None, headers=None, method=None, timeout=0):
    full = url.full_url if hasattr(url, 'full_url') else url
    body = (url.data if hasattr(url, 'data') else data) or b''
    _seen.append((full, body.decode()))
    return _FakeResponse('{"status":200,"body":"OK"}')
_of.urllib.request.urlopen = _fake_urlopen
try:
    _body = _of._mailru_call('folder/add', params={'api': '2'}, data={'home': '/x'})
    assert _body == 'OK'
    _url, _post = _seen[-1]
    assert 'access_token=AT2' in _url and 'client_id=cloud-win' in _url, _url
    assert 'home=%2Fx' in _post, _post
    import io as _io
    _err = _of.urllib.error.HTTPError('http://x', 403, 'forbidden', {}, _io.BytesIO(b'denied'))
    _flaky = {'n': 0}
    def _fake_urlopen_403(url, data=None, headers=None, method=None, timeout=0):
        _flaky['n'] += 1
        if _flaky['n'] == 1: raise _err
        return _FakeResponse('{"status":200,"body":"RETRY"}')
    _of.urllib.request.urlopen = _fake_urlopen_403
    assert _of._mailru_call('file/add', data={'home': '/y'}) == 'RETRY'
    assert _flaky['n'] == 2
finally:
    _of.urllib.request.urlopen = _real_urlopen
# пламбинг создания: folder/add -> dispatcher/u -> загрузка с токеном -> file/add -> publish
def _mr_cmd(cmd, params=None, data=None):
    if cmd == 'folder/add': return {}
    if cmd == 'file/add': return {}
    if cmd == 'file/publish': return '6Piv/KCVSUz6BC'
    raise SystemExit('unexpected mailru command ' + cmd)
_of._mailru_call = _mr_cmd
_real_access_token = _of._mailru_access_token
_of._mailru_access_token = lambda force_refresh=False: 'TOKEN1'
_uploaded = {}
def _mr_up(url, token, payload):
    _uploaded['url'] = url; _uploaded['token'] = token; _uploaded['size'] = len(payload)
    return 'B' * 40
_of._mailru_upload = _mr_up
_dispatcher_urls = []
def _fake_dispatcher_urlopen(req, timeout=0):
    _dispatcher_urls.append(req.full_url)
    return _FakeHTTPResponse('https://uploader.example/put\n')
_real_urlopen_2 = _of.urllib.request.urlopen
_of.urllib.request.urlopen = _fake_dispatcher_urlopen
try:
    _mr_url = _of.create_mailru_document('Тест Иван')
finally:
    _of.urllib.request.urlopen = _real_urlopen_2
assert _mr_url == 'https://cloud.mail.ru/public/6Piv/KCVSUz6BC', _mr_url
assert _uploaded['url'] == 'https://uploader.example/put' and _uploaded['token'] == 'TOKEN1'
assert _uploaded['size'] > 900
assert any('dispatcher.cloud.mail.ru/u' in u for u in _dispatcher_urls), _dispatcher_urls
# существующая папка (400 exists) глотается — создание продолжается
def _mr_cmd_exists(cmd, params=None, data=None):
    if cmd == 'folder/add':
        raise _of.OpenFluxError('Mail.ru ответил 400 на folder/add: {"home":{"error":"exists"}}')
    return _mr_cmd(cmd, params, data)
_of._mailru_call = _mr_cmd_exists
_mr_url = _of.create_mailru_document('Тест exists')
assert _mr_url == 'https://cloud.mail.ru/public/6Piv/KCVSUz6BC', _mr_url
# пустой weblink -> внятная ошибка
def _mr_cmd_empty(cmd, params=None, data=None):
    if cmd == 'file/publish': return ''
    return _mr_cmd(cmd, params, data)
_of._mailru_call = _mr_cmd_empty
try:
    _of.create_mailru_document(); raise SystemExit('empty weblink accepted')
except _of.OpenFluxError as _e:
    assert 'weblink' in str(_e), _e
# отключение аккаунта стирает токены и старые файлы сессии
_of.MAILRU_SESSION_FILE.write_text('legacy')
_of.MAILRU_COOKIE_FILE.write_text('legacy')
_of._mailru_access_token = _real_access_token   # без заглушки: файл удалён -> не подключено
_of.disconnect_mailru()
assert not _mr_file.exists() and not _of.MAILRU_SESSION_FILE.exists() and not _of.MAILRU_COOKIE_FILE.exists()
assert _of.mailru_status() == {'connected': False, 'email': None}
# минимальный docx — валидный zip
_payload = _of._mailru_docx()
import zipfile as _zf, io as _io
_zip = _zf.ZipFile(_io.BytesIO(_payload))
assert _zip.testzip() is None and 'word/document.xml' in _zip.namelist()
print('OPENFLUX MAILRU OK')

# ---- components: каталог OpenFlux с пререлизами апстрима
import onyx_components as _oc
_oc.STATUS = _tmp / 'components-status.json'
_oc._VERIFY_CACHE.clear()
_fork = [
    {'tag_name': 'v1.1.3', 'assets': []},
    {'tag_name': 'v1.1.2', 'assets': []},
    {'tag_name': 'v1.1.1', 'assets': [{'name': 'openflux-linux-amd64'}]},
]
_upstream = [
    {'tag_name': 'nightly-20261004-f9d4d75', 'prerelease': True, 'assets': [{'name': 'openflux-linux-amd64'}]},
    {'tag_name': 'node-v1.2.0', 'prerelease': False, 'assets': [{'name': 'openflux-linux-amd64'}]},
    {'tag_name': 'v0.3.0', 'prerelease': False, 'assets': [{'name': 'openflux-linux-amd64'}]},
    {'tag_name': 'nightly-no-bin', 'prerelease': True, 'assets': []},
]
_oc._fetch_releases = lambda repo: _fork if 'Android' in repo else _upstream
suitable, unsuitable, prerelease = _oc._openflux_releases()
assert suitable == ['v1.1.1'], suitable
assert unsuitable == ['v1.1.3', 'v1.1.2'], unsuitable
assert [e['tag'] for e in prerelease] == ['node-v1.2.0', 'nightly-20261004-f9d4d75'], prerelease
assert all(e['url'].startswith('https://github.com/p1neappleXpress/OpenFlux/releases/download/') for e in prerelease)
# verify: пререлиз проверяется по сохранённому URL, версия без Linux-сборки отклоняется
_oc.STATUS.parent.mkdir(parents=True, exist_ok=True)
_oc.atomic_json(_oc.STATUS, {'catalog': {'openflux': ['v1.1.1']},
                             'unsuitable': {'openflux': ['v1.1.3']},
                             'prerelease': {'openflux': prerelease},
                             'checked': _oc.time.time()})
_curl_calls = []
def _fake_run_curl(args, **kwargs):
    if args[:2] == ['curl', '-sIL']:
        url = args[-1]
        _curl_calls.append(url)
        code = '200\n' if ('node-v1.2.0' in url or 'v1.1.1' in url) else '404\n'
        return _oc.subprocess.CompletedProcess(args, 0, code, '')
    return _oc.subprocess.CompletedProcess(args, 0, '', '')
_oc._run = _fake_run_curl
_oc._VERIFY_CACHE.clear()
_res = _oc.verify('openflux', 'node-v1.2.0')
assert _res['ok'] is True and _curl_calls[-1].startswith('https://github.com/p1neappleXpress/OpenFlux/releases/download/node-v1.2.0/'), (_res, _curl_calls)
_res = _oc.verify('openflux', 'v1.1.3')
assert _res['ok'] is False and 'нет сборки' in _res['message']
# start(): пререлиз принимается, asset_url сохраняется для установщика
_oc._run = lambda args, **kwargs: _oc.subprocess.CompletedProcess(args, 0, '', '')
_oc._VERIFY_CACHE.clear()
try:
    _oc.start('openflux', 'node-v1.2.0')
except ValueError:
    pass   # в тестовом окружении служба не поднимется — состояние уже записано
_st = _oc.read_state(_oc.STATUS)
assert _st.get('target') == 'node-v1.2.0' and 'p1neappleXpress' in (_st.get('asset_url') or ''), _st
try:
    _oc.start('openflux', 'v9.9.9'); raise SystemExit('unknown tag accepted')
except ValueError:
    pass
print('COMPONENTS PRERELEASE OK')

# ETXTBSY-фикс: профильные юниты OpenFlux останавливаются, замена атомарная
def _fake_units(args, **kwargs):
    if args[:3] == ['systemctl', 'list-units', '--type=service']:
        out = ('onyx-panel-openflux.service loaded active running Onyx Panel OpenFlux\n'
               'onyx-panel-openflux-fc120f65241a5f1e.service loaded active running Onyx Panel OpenFlux profile\n'
               'onyx-panel-xray.service loaded active running Xray\n')
        return _oc.subprocess.CompletedProcess(args, 0, out, '')
    return _oc.subprocess.CompletedProcess(args, 0, '', '')
_oc._run = _fake_units
assert _oc._active_openflux_units() == ['onyx-panel-openflux.service', 'onyx-panel-openflux-fc120f65241a5f1e.service']
_swapdir = _tmp / 'swaptest'
_swapdir.mkdir(exist_ok=True)
_target = _swapdir / 'openflux'
_target.write_bytes(b'OLD-BINARY')
_newsrc = _swapdir / 'candidate'
_newsrc.write_bytes(b'NEW-BINARY')
_oc._replace_file(_newsrc, _target)
assert _target.read_bytes() == b'NEW-BINARY'
import stat as _stat
assert _target.stat().st_mode & 0o111
assert not (_swapdir / 'openflux.new').exists()
# и откат тем же путём
(_swapdir / 'previous').write_bytes(b'OLD-BINARY')
_oc._replace_file(_swapdir / 'previous', _target)
assert _target.read_bytes() == b'OLD-BINARY'
print('COMPONENTS ETXTBSY FIX OK')

# ---- Python 3.10 grammar check (Ubuntu 22.04 target): no 3.12+ f-string syntax
import ast
import glob
for _path in sorted(glob.glob(os.path.join(ROOT, 'onyx_*.py'))):
    try:
        ast.parse(open(_path, encoding='utf-8').read(), feature_version=(3, 10))
    except SyntaxError as _e:
        raise SystemExit(_path + ' is not Python 3.10 compatible: ' + _e.msg + ' (line ' + str(_e.lineno) + ')')
print('PY310 GRAMMAR OK')
print('ALL MODULE TESTS PASSED')
