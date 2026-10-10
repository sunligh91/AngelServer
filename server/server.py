

import os, sys, json, base64, io, time, re, threading, random, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))

_USER_SCOPE = (os.environ.get('ANGELICA_USER') or '').strip()
DB = os.path.join(HERE, 'db' if not _USER_SCOPE else 'db_' + _USER_SCOPE)
CAP  = os.path.join(HERE, 'captured')
LOG  = os.path.join(HERE, 'server.log')
PORT = int(os.environ.get('ANGELICA_PORT', '18080'))

_DELAY_MS = int(os.environ.get('ANGELICA_DELAY_MS', '0') or '0')

_SUBEVENT_DELAY_MS = int(os.environ.get('ANGELICA_SUBEVENT_DELAY_MS', '1200') or '0')

_LOUNGE_DELAY_MS = int(os.environ.get('ANGELICA_LOUNGE_DELAY_MS', '1200') or '0')

_LOUNGE_PATHS = ('/api/lounge/my', '/api/lounge/enter', '/api/lounge/update',
                 '/api/social/profile/other')

os.makedirs(DB, exist_ok=True)
if _USER_SCOPE:
    print('[scope] ANGELICA_USER=%s -> serving %s' % (_USER_SCOPE, DB))
LOCK = threading.RLock()

CDN_FILE = os.path.join(HERE, 'cdn_index.json')
CDN = {}
if os.path.exists(CDN_FILE):
    try:
        CDN = json.loads(io.open(CDN_FILE, encoding='utf-8').read())
    except Exception as _e:
        log('cdn index load failed', _e)
MASTERDATA_BIN = os.path.join(HERE, 'masterdata.bundle')

def _resolve(p):

    if not isinstance(p, str) or not p:
        return p
    if os.path.isabs(p):
        return p
    return os.path.join(os.path.dirname(HERE), p.replace('/', os.sep))

def _auto_extra_roots():

    cands = []

    _ov = (os.environ.get('ANGELICA_EXTRA_BUNDLES') or '').strip()
    if _ov:
        for _part in _ov.split(os.pathsep):
            if _part.strip():
                cands.append(_part.strip())

    for _r in (CDN.get('extra_bundle_roots') or []):
        if isinstance(_r, str) and _r:
            cands.append(_r)

    _pkgroot = os.path.dirname(HERE)
    try:
        _entries = sorted(os.listdir(_pkgroot))
    except Exception:
        _entries = []
    for _e in _entries:
        _sub = os.path.join(_pkgroot, _e)
        if not os.path.isdir(_sub):
            continue

        try:
            _inner = sorted(os.listdir(_sub))
        except Exception:
            _inner = []
        for _d in _inner:
            if _d.endswith('_Data'):
                cands.append(os.path.join(_sub, _d, 'Caches'))

        cands.append(os.path.join(_sub, 'Caches'))

    # A client folder is recognised by the SHAPE of its contents, not by a
    # hard-coded product name: the game always ships a single '<something>_Data'
    # directory next to the executable.  Scanning for that pattern keeps the
    # search working for any product name (and keeps this source free of them).
    for _nm in ('client_r', 'client', 'r18', 'R18'):
        try:
            for _d in sorted(os.listdir(os.path.join(_pkgroot, _nm))):
                if _d.endswith('_Data'):
                    cands.append(os.path.join(_pkgroot, _nm, _d, 'Caches'))
        except Exception:
            pass

    # A normal DMM install: <drive>/dmm/<product>/<product>_Data/Caches.
    # One level of scanning finds the product folder whatever it is called.
    for _drv in ('C:', 'D:', 'E:', 'F:', 'G:'):
        _dmm = os.path.join(_drv + os.sep, 'dmm')
        try:
            _prods = sorted(os.listdir(_dmm))
        except Exception:
            continue
        for _p in _prods:
            _pd = os.path.join(_dmm, _p)
            if not os.path.isdir(_pd):
                continue
            try:
                for _d in sorted(os.listdir(_pd)):
                    if _d.endswith('_Data'):
                        cands.append(os.path.join(_pd, _d, 'Caches'))
            except Exception:
                pass

    cands.append(os.path.join(HERE, 'extra_bundles'))

    seen = set()
    found = []
    for _c in cands:
        try:
            _q = _c if os.path.isabs(_c) else _resolve(_c)
        except Exception:
            continue
        if isinstance(_q, str) and os.path.isdir(_q) and _q not in seen:
            seen.add(_q)
            found.append(_q)
    return found

_cb = CDN.get('catalog_bin') or ''
if _cb and not os.path.isabs(_cb):
    _cb = os.path.join(HERE, _cb.replace('/', os.sep))
CATALOG_BIN = _cb if (_cb and os.path.exists(_cb)) else os.path.join(HERE, 'catalog.bin')
CATALOG_R   = CDN.get('catalog_r', '1.72.3')
CATALOG_H   = CDN.get('catalog_h', '')

_cb_r = CDN.get('catalog_r_bin') or 'catalog_r.bin'
if _cb_r and not os.path.isabs(_cb_r):
    _cb_r = os.path.join(HERE, _cb_r.replace('/', os.sep))
CATALOG_BIN_R = _cb_r if (_cb_r and os.path.exists(_cb_r)) else ''
CATALOG_H_R   = CDN.get('catalog_h', '') or ('27f3577e92d725f410e27526a0e8d44fcd02dcc8cc984d1d59dc28e644f5eaed')

CATALOG_H_N = CDN.get('catalog_h_n', '86539787d9a1594b41a21ec6eef4a6fc')
BUNDLES     = CDN.get('index', {})

PROFILE_MEMBERS = (
    'Id', 'ArrivalAt', 'Rank', 'Name', 'Stand', 'Eff', 'Icon', 'Messages',
    'LoginAt', 'LoginDay', 'Following', 'Follower', 'Angels', 'SpaLvs',
    'Supports', 'JobLvs', 'Frame', 'Back', 'Stamps', 'Sha', 'Ver',
)

PROFILE_VALUE_TYPES = {
    'Rank': 1, 'LoginDay': 0, 'Following': 0, 'Follower': 0,
    'Angels': 0, 'Sha': 0, 'Eff': False, 'Ver': 0,
}

PROFILE_VALUE_TYPES = dict(PROFILE_VALUE_TYPES)
PROFILE_CONTAINERS = {
    'Messages': None, 'SpaLvs': [1, 1, 1, 1, 1, 1], 'Supports': None,
    'JobLvs': {}, 'Stamps': {},
}

def _self_profile_entity():

    rows = db_get('User')
    if isinstance(rows, dict):
        u = rows
    elif isinstance(rows, list) and rows and isinstance(rows[0], dict):
        u = rows[0]
    else:
        u = {}
    uid = u.get('Id') or user_id()

    charas = db_get('Chara')
    if not isinstance(charas, list):
        charas = []
    angels = len([c for c in charas if isinstance(c, dict) and c.get('Id')])

    job_lvs = {}
    for c in charas:
        if not isinstance(c, dict):
            continue
        jid = c.get('JobId') or c.get('CurrentJobId')
        jl = c.get('JLv')
        if jid is None or jl is None:
            continue
        try:
            job_lvs[str(jid)] = max(int(job_lvs.get(str(jid)) or 0), int(jl))
        except Exception:
            pass

    fol = db_get('Follow')
    fer = db_get('Follower')
    following = len(fol) if isinstance(fol, list) else 0
    follower = len(fer) if isinstance(fer, list) else 0

    p = {}
    for k in PROFILE_MEMBERS:
        if k in PROFILE_VALUE_TYPES:
            p[k] = PROFILE_VALUE_TYPES[k]
        elif k in PROFILE_CONTAINERS:

            _dv = PROFILE_CONTAINERS[k]
            if isinstance(_dv, list):
                p[k] = list(_dv)
            elif isinstance(_dv, dict):
                p[k] = dict(_dv)
            else:
                p[k] = None
        else:
            p[k] = None

    prows = db_get('Profile')
    if isinstance(prows, list):
        prows = prows[0] if prows and isinstance(prows[0], dict) else None
    if isinstance(prows, dict) and isinstance(prows.get('Profile'), dict):
        prows = prows['Profile']
    stored = prows if isinstance(prows, dict) else {}

    p['Id'] = uid
    p['Name'] = stored.get('Name') or u.get('Name')
    try:
        p['Rank'] = int(u.get('Rank') or 1)
    except Exception:
        p['Rank'] = 1
    p['Angels'] = angels
    p['Following'] = following
    p['Follower'] = follower
    p['JobLvs'] = job_lvs

    p['Stand'] = stored.get('Stand') if stored.get('Stand') is not None else u.get('Stand')
    p['Icon'] = stored.get('Icon') if stored.get('Icon') is not None else u.get('Icon')
    _eff = stored.get('Eff') if stored.get('Eff') is not None else u.get('Eff')
    p['Eff'] = bool(_eff) if _eff is not None else False

    for _k in ('ArrivalAt', 'LoginAt', 'Frame', 'Back'):
        if stored.get(_k) is not None:
            p[_k] = stored[_k]
    for _k in ('Stamps', 'Messages', 'SpaLvs', 'Supports'):
        if isinstance(stored.get(_k), (dict, list)):
            p[_k] = stored[_k]

    if not p.get('ArrivalAt'):
        p['ArrivalAt'] = u.get('GameStartAt') or u.get('LastLoginAt') or ''
    if not p.get('LoginAt'):
        _la = u.get('LastLoginAt') or ''

        p['LoginAt'] = str(_la).replace('-', '').replace(':', '').replace('T', '')[:12] if _la else ''
    if not isinstance(p.get('Stamps'), dict):
        p['Stamps'] = {}

    if p.get('Supports') == []:
        p['Supports'] = None
    if not isinstance(p.get('SpaLvs'), list):

        p['SpaLvs'] = [1, 1, 1, 1, 1, 1]

    if not p.get('JobLvs'):
        _jt = (master_tables().get('chara') or {})
        _owned = db_get('Chara')
        _jl = {}
        if isinstance(_owned, list):
            for _c in _owned:
                if not isinstance(_c, dict):
                    continue
                _cid = str(_c.get('Id') or '')
                _md = _jt.get(_cid)
                if not isinstance(_md, dict):
                    continue
                _jid = str(_md.get('JobId') or '')
                if not _jid:
                    continue
                try:
                    _lv = int(_c.get('Level') or 1)
                except Exception:
                    _lv = 1
                _jl[_jid] = max(int(_jl.get(_jid) or 0), _lv)
        if _jl:
            p['JobLvs'] = _jl
    if not p.get('Sha'):
        p['Sha'] = 1
    if not p.get('Icon'):
        _ch = db_get('Chara')
        _c0 = None
        if isinstance(_ch, list):
            for _c in _ch:
                if isinstance(_c, dict) and _c.get('Id'):
                    _c0 = str(_c['Id']); break
        p['Icon'] = _c0 or ''

    _picons = str(p.get('Icon') or '')
    _pstand = str(p.get('Stand') or '')
    _powned = owned_item_ids()
    _pchars = [str(_c.get('Id')) for _c in (db_get('Chara') or [])
               if isinstance(_c, dict) and _c.get('Id')]
    if _picons not in _pchars and _pchars:
        _picons = _pchars[0]
        p['Icon'] = _picons
    if _pstand not in _powned:
        _pno = _picons.split('.')[-1] if '.' in _picons else ''
        _cands = [c for c in sorted(_powned)
                  if str(c).startswith('chara_costume.%s.' % _pno)]
        if not _cands:
            _cands = [c for c in sorted(_powned) if str(c).startswith('chara_costume.')]
        if _cands:
            p['Stand'] = str(_cands[0])
        elif not _pstand:
            p['Stand'] = _picons
    p.setdefault('Sha', 1)
    if stored.get('LoginDay') is not None:
        p['LoginDay'] = stored['LoginDay']

    if isinstance(u.get('SpaLvs'), list):
        p['SpaLvs'] = u['SpaLvs']
    return p

READ_API = {
    '/api/user/get':                    'User',
    '/api/item/list':                   'Item',
    '/api/chara/list':                  'Chara',
    '/api/quest/record/list':           'QuestRecord',
    '/api/unique-quest/list':           'UniqueQuest',
    '/api/present/list':                'Present',
    '/api/login-bonus/list':            'LoginBonus',
    '/api/quest/score-attack/list':     'ScoreAttack',
    '/api/oopart/list':                 'Oopart',
    '/api/team/list':                   'Team',

    '/api/lyric/angel/list':            'Lyric',
    '/api/lyric/god/list':              'GodLyric',

    '/api/chara/get-panzer':            'Panzer',
    '/api/chara/get-panzers':           'Panzer',
    '/api/exchange/list':               'Exchange',

    '/api/exchange/master':             'ExchangeMaster',
    '/api/minigame/list':               'MiniGame',
    '/api/homesetting/list':            'HomeSetting',
    '/api/itemgeneration/list':         'ItemGeneration',
    '/api/mission/list':                'Mission',
    '/api/mission-pass/list':           'MissionPass',

    '/api/user-behavior/get':           'user-behavior_get',
    '/api/challenge-mission/list':      'ChallengeMission',
    '/api/user-message/list':           'UserMessage',
    '/api/historia/list':               'Historia',
    '/api/fragment/list':               'Fragment',
    '/api/quest/multi-team/list':       'MultiTeamQuest',
    '/api/quest/big-battle/list':       'BigBattle',

    '/api/social/follower/list':        'Follower',
    '/api/social/following/list':       'Follow',
    '/api/user-possession/list':        'UserPossession',
    '/api/shop/hold':                   'ShopHold',

    '/api/gacha/hold':                  'GachaHold',

    '/api/gacha/list':                  'Gacha',

    '/api/social/blogs':                'Blog',
    '/api/social/views':                'Views',
    '/api/lounge/my':                   'lounge_my',
}

def _load_datasource_config():

    try:
        with io.open(os.path.join(HERE, "datasource_config.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}, {}
    by_api, by_key = {}, {}
    for e in data.get("entries", []):
        api, key = e.get("api"), e.get("key")
        if api and key:
            by_api[api] = key
            by_key[key] = api
    return by_api, by_key

DATASOURCE_BY_API, DATASOURCE_BY_KEY = _load_datasource_config()

UNWRAP = {
    '/api/user-behavior/get': 'UserBehavior',
}

WRAP = {
}

LIST_API = set(p for p in READ_API if p.endswith('/list')) | {
    '/api/social/following/list',
}

try:
    API_TYPES = json.load(open(os.path.join(HERE, 'api_types.json'), encoding='utf-8'))
except Exception:
    API_TYPES = {}

try:
    RESPONSE_MEMBERS = json.load(open(os.path.join(HERE, 'response_members.json'), encoding='utf-8'))
except Exception:
    RESPONSE_MEMBERS = {}

try:
    ENTITY_MEMBERS = json.load(open(os.path.join(HERE, 'entity_members.json'), encoding='utf-8'))
except Exception:
    ENTITY_MEMBERS = {}

try:
    SYNC_MEMBERS = json.load(open(os.path.join(HERE, 'syncdata_members.json'), encoding='utf-8'))
except Exception:
    SYNC_MEMBERS = {}

LIST_API |= set(u for u, v in API_TYPES.items() if v.get('shape') == 'list')

LOG_ROTATE_BYTES = 64 * 1024 * 1024

def _rotate_log():

    try:
        if not os.path.exists(LOG) or os.path.getsize(LOG) < LOG_ROTATE_BYTES:
            return
        for n in (3, 2, 1):
            a, b = LOG + (".%d" % n if n > 0 else ""), LOG + ".%d" % (n + 1)
            if os.path.exists(a):
                try: os.replace(a, b)
                except Exception: pass
        os.replace(LOG, LOG + ".1")
    except Exception:
        pass

def log(*a):
    msg = time.strftime('[%H:%M:%S] ') + ' '.join(str(x) for x in a)

    for _sink in (_stdout_write, _file_write):
        try:
            _sink(msg)
        except Exception:
            pass

def _stdout_write(msg):
    print(msg)

def _file_write(msg):
    _rotate_log()
    with io.open(LOG, 'a', encoding='utf-8') as f: f.write(msg + '\n')

def db_path(name): return os.path.join(DB, name + '.json')

def db_get(name, default=None):
    p = db_path(name)
    if not os.path.exists(p):
        return default

    last = None
    for _attempt in (0, 1, 2):
        try:
            txt = io.open(p, encoding='utf-8').read()
            if not txt.strip():
                raise ValueError('empty file')
            return json.loads(txt)
        except Exception as e:
            last = e
            time.sleep(0.02)
    log('db read fail', name, last)
    return default

import datetime

JST = datetime.timezone(datetime.timedelta(hours=9))
GAME_RESET_HOUR = 5

def _ingame_day(ts=None):

    d = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), JST)
    if d.hour < GAME_RESET_HOUR:
        d -= datetime.timedelta(days=1)
    return d.strftime('%Y-%m-%d')

def _ingame_day_of(iso):

    if not iso:
        return None
    try:
        d = datetime.datetime.fromisoformat(iso)
    except Exception:
        return None
    d = d.replace(tzinfo=JST) if d.tzinfo is None else d.astimezone(JST)
    if d.hour < GAME_RESET_HOUR:
        d -= datetime.timedelta(days=1)
    return d.strftime('%Y-%m-%d')

def _now_iso():
    return datetime.datetime.now(JST).replace(microsecond=0).isoformat()

def _next_update_iso():

    now = datetime.datetime.now(JST)
    nxt = now.replace(hour=GAME_RESET_HOUR, minute=0, second=0, microsecond=0)
    if now >= nxt:
        nxt += datetime.timedelta(days=1)
    return nxt.strftime('%Y-%m-%dT%H:%M:%S')

def _exchange_period_start(reset_type, now=None):

    now = now or datetime.datetime.now(JST)
    try:
        rt = int(reset_type or 0)
    except Exception:
        rt = 0
    if rt == 1:
        d = now.replace(hour=GAME_RESET_HOUR, minute=0, second=0, microsecond=0)
        if now < d:
            d -= datetime.timedelta(days=1)
        return d
    if rt == 2:
        d = now.replace(hour=GAME_RESET_HOUR, minute=0, second=0, microsecond=0)
        if now < d:
            d -= datetime.timedelta(days=1)
        return d - datetime.timedelta(days=d.weekday())
    if rt == 3:
        d = now.replace(day=1, hour=GAME_RESET_HOUR, minute=0, second=0,
                        microsecond=0)
        if now < d:
            d = (d - datetime.timedelta(days=1)).replace(day=1)
        return d
    return None

def _exchange_defs():

    try:
        md = db_get('ExchangeMaster')
    except Exception:
        return {}
    if not isinstance(md, dict):
        return {}
    out = {}
    for r in (md.get('Records') or []):
        if not isinstance(r, dict):
            continue
        ex = r.get('Exchange')
        if isinstance(ex, dict) and ex.get('Id'):
            out[ex['Id']] = ex
    return out

def _exchange_reset_type(rows_defs, xid, xno):

    mrow = (rows_defs or {}).get(xid) or {}
    for ed in (mrow.get('Records') or []):
        try:
            if int(ed.get('No') or 0) == int(xno):
                return int(ed.get('ResetType') or 0)
        except Exception:
            continue
    return 0

BEHAVIOR_NULL_ONLY = (
    '/api/lounge/my',
)

ACTIVITY_BEHAVIOR = (
    '/api/box-gacha/exec',
    '/api/chara/enhance-auto-orb', '/api/chara/enhance-orb',
    '/api/chara/lottery-harmonia',
    '/api/dungeon/end', '/api/dungeon/end-battle', '/api/dungeon/start',
    '/api/dungeon/treasure',
    '/api/exchange/change', '/api/exchange/change-multi', '/api/gacha/lottery',
    '/api/homesetting/set-homesetting-record',
    '/api/item/open-orb', '/api/item/open-orbs', '/api/item/open-stack-orb',
    '/api/itemgeneration/receive', '/api/itemgeneration/start',
    '/api/lounge/enhance-param', '/api/lounge/enter',
    '/api/lounge/item-mixing', '/api/lounge/red-coin-gacha',
    '/api/minigame/fish-add',
    '/api/quest/big-battle/skip', '/api/quest/demigod/end', '/api/quest/end',
    '/api/quest/multi-team/end', '/api/quest/score-attack/end', '/api/quest/skip',
    '/api/social/profile/update',
    '/api/unique-quest/end', '/api/unique-quest/end-battle',
    '/api/unique-quest/select-phantom', '/api/unique-quest/select-treasure',
)

_BEHAVIOR_UNDECLARED = ('EventId', 'EventStartAt', '_WeekAt')

UQ_USERBEHAVIOR_PATHS = (
    '/api/unique-quest/select-chara',
    '/api/unique-quest/select-phantom',
    '/api/unique-quest/select-treasure',
)

def _uq_tail_sync(path=None):

    sync = flush_touched(empty_sync())
    if path is not None and path not in UQ_USERBEHAVIOR_PATHS:
        return sync
    try:
        brow, brows = _behavior_row()
        _behavior_rollover(brow)
        brow["Ver"] = int(brow.get("Ver") or 0) + 1
        db_put("UserBehavior", brows if brows is not None else brow)
        sync["UserBehavior"] = _behavior_clean(dict(brow))
    except Exception as e:
        log("    behavior tail echo failed: %s" % e)
    return sync

def _behavior_row():

    rows = db_get("UserBehavior")
    r = None
    _slot = None
    if isinstance(rows, list):
        for _idx, x in enumerate(rows):
            if isinstance(x, dict) and str(x.get("Id") or "") == "1":
                r = x
                _slot = _idx
                break
    elif isinstance(rows, dict):
        r = rows
        rows = None

    _seed_needed = (r is None
                    or not (isinstance(r.get("MainRecords"), dict)
                            and r.get("MainRecords")))
    if _seed_needed:
        seeded = _behavior_captured()
        if seeded and (isinstance(seeded.get("MainRecords"), dict)
                       and seeded.get("MainRecords")):

            if r is None:
                r = dict(seeded)
            else:
                r = _behavior_merge(seeded, r)
                if _slot is not None:
                    rows[_slot] = r
            log("  behavior: seeded from the captured save (Daily=%d Weekly=%d "
                "Main=%d Event=%d)"
                % (len(r.get("DailyRecords") or {}),
                   len(r.get("WeeklyRecords") or {}),
                   len(r.get("MainRecords") or {}),
                   len(r.get("EventRecords") or {})))

            try:
                if isinstance(rows, list):
                    db_put("UserBehavior", rows)
                else:
                    db_put("UserBehavior", r)
            except Exception as _e:
                log("    behavior seed persist failed: %s" % _e)
    if r is None:
        r = {}
    if isinstance(rows, list) and r not in rows:

        rows.append(r)

    r["UserId"] = r.get("UserId") or user_id()
    r["Id"] = "1"
    for k in _BEHAVIOR_MAPS:
        if not isinstance(r.get(k), dict):
            r[k] = {}
    return r, rows

def _behavior_captured():

    try:
        raw = db_get("user-behavior_get")
    except Exception:
        return {}
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("UserBehavior")
    return inner if isinstance(inner, dict) else raw

_BEHAVIOR_MAPS = ("DailyRecords", "WeeklyRecords", "MainRecords",
                  "EventRecords", "ChallengeRecords")

def _behavior_merge(base, live):

    if not isinstance(base, dict) or not base:
        return dict(live) if isinstance(live, dict) else {}
    if not isinstance(live, dict):
        live = {}
    out = dict(base)
    for k, v in live.items():
        if v is not None:
            out[k] = v
    for m in _BEHAVIOR_MAPS:
        merged = {}
        b = base.get(m)
        if isinstance(b, dict):
            merged.update(b)
        l = live.get(m)
        if isinstance(l, dict):
            if m == "EventRecords":
                for ev, inner in l.items():
                    cur = merged.get(ev)
                    if isinstance(cur, dict) and isinstance(inner, dict):
                        nxt = dict(cur)
                        nxt.update(inner)
                        merged[ev] = nxt
                    else:
                        merged[ev] = inner
            else:
                merged.update(l)
        out[m] = merged
    out["Id"] = "1"
    try:
        out["Ver"] = max(int(base.get("Ver") or 0), int(live.get("Ver") or 0))
    except Exception:
        pass
    return out

def _behavior_clean(r):

    if not isinstance(r, dict):
        return r
    for k in _BEHAVIOR_UNDECLARED:
        r.pop(k, None)
    r['UserId'] = r.get('UserId') or user_id()
    r['Id'] = '1'
    for k in ('DailyRecords', 'WeeklyRecords', 'MainRecords',
              'EventRecords', 'ChallengeRecords'):
        if not isinstance(r.get(k), dict):
            r[k] = {}
    if not isinstance(r.get('DailyAt'), str):
        r['DailyAt'] = ''
    if not isinstance(r.get('IsLog'), bool):
        r['IsLog'] = bool(r.get('IsLog') or False)
    return r

def _behavior_rollover(r=None):

    if r is None:
        r, _ = _behavior_row()
    now = datetime.datetime.now(JST)
    day = now.replace(hour=GAME_RESET_HOUR, minute=0, second=0, microsecond=0)
    if now < day:
        day -= datetime.timedelta(days=1)
    day_s = day.strftime('%Y/%m/%d %H:%M:%S')
    changed = False
    stored = str(r.get('DailyAt') or '')
    if stored != day_s:

        if r.get('DailyRecords'):
            r['DailyRecords'] = {}
            changed = True
        r['DailyAt'] = day_s
        changed = True

        try:
            d = r.get('DailyRecords')
            if not isinstance(d, dict):
                d = {}
            for _k, (_v, _kind) in _MISSION_SEED.items():
                if _kind in ('both', 'daily'):
                    d[_k] = max(int(d.get(_k) or 0), int(_v))
            r['DailyRecords'] = d
        except Exception:
            pass
    week = day - datetime.timedelta(days=day.weekday())
    week_s = week.strftime('%Y/%m/%d %H:%M:%S')

    _wm = db_get('BehaviorMark')
    if not isinstance(_wm, dict):
        _wm = {}
    if str(_wm.get('WeekAt') or '') != week_s:
        if r.get('WeeklyRecords'):
            r['WeeklyRecords'] = {}
            changed = True

        try:
            for _k, (_v, _kind) in _MISSION_SEED.items():
                if _kind in ('both', 'weekly'):
                    d = r.get('WeeklyRecords')
                    if not isinstance(d, dict):
                        d = {}
                    d[_k] = max(int(d.get(_k) or 0), int(_v))
                    r['WeeklyRecords'] = d
                    changed = True
        except Exception:
            pass
        _wm['WeekAt'] = week_s
        try:
            db_put('BehaviorMark', _wm)
        except Exception:
            pass
        changed = True
    return changed

def _behavior_record(key, count=1, kind='daily'):

    try:
        r, rows = _behavior_row()
        _behavior_rollover(r)
        if kind == 'both':
            fields = ('DailyRecords', 'WeeklyRecords')
        else:
            fields = ({'daily': 'DailyRecords', 'weekly': 'WeeklyRecords',
                       'main': 'MainRecords'}.get(kind, 'DailyRecords'),)
        k = str(key)
        for field in fields:
            d = r.get(field)
            if not isinstance(d, dict):
                d = {}
            d[k] = int(d.get(k) or 0) + int(count or 1)
            r[field] = d
        r['UserId'] = r.get('UserId') or user_id()
        r['Id'] = '1'
        r['Ver'] = int(r.get('Ver') or 0) + 1
        if rows is None:
            db_put('UserBehavior', r)
        else:
            db_put('UserBehavior', rows)
        log('    behavior %s[%s] += %d' % ('+'.join(fields), k, int(count or 1)))
    except Exception as e:
        log('    behavior record failed (%s): %s' % (key, e))

_MISSION_KEYS = {
    'login':          '3',
    'any_battle':     '4',
    'friend_cafe':    '6_',

    'item_generation':'7',
    'alchemy':        '8',
    'exchange':       '10',
}

_MISSION_SEED = {

    '6_': ('6', 'both'),
    '11': ('1', 'weekly'),
}

def _seed_unreachable_missions():

    try:
        r, rows = _behavior_row()
        _behavior_rollover(r)
        touched = False

        for _f in ('DailyRecords', 'WeeklyRecords'):
            _d = r.get(_f)
            if isinstance(_d, dict) and '6' in _d and '6_' not in _d:
                del _d['6']
                touched = True
        for k, (val, kind) in _MISSION_SEED.items():
            fields = ('DailyRecords', 'WeeklyRecords') if kind == 'both' else ('WeeklyRecords',)
            for field in fields:
                d = r.get(field)
                if not isinstance(d, dict):
                    d = {}
                if int(d.get(k) or 0) < int(val):
                    d[k] = int(val)
                    r[field] = d
                    touched = True
        if touched:
            r['UserId'] = r.get('UserId') or user_id()
            r['Id'] = '1'
            r['Ver'] = int(r.get('Ver') or 0) + 1
            if rows is None:
                db_put('UserBehavior', r)
            else:
                db_put('UserBehavior', rows)
            log('    missions: seeded unreachable conditions %s'
                % ', '.join('%s=%s' % (k, v) for k, (v, _) in _MISSION_SEED.items()))
        return touched
    except Exception as e:
        log('    mission seed failed: %s' % e)
        return False

def _mission_progress(which, count=1, kind=None):

    k = _MISSION_KEYS.get(which)
    if k is None:
        return
    if kind is None:
        kind = 'daily' if which == 'login' else 'both'
    _behavior_record(k, count, kind)

def _behavior_event_record(event_id, key, count=1, absolute=False):

    try:
        r, rows = _behavior_row()
        _behavior_rollover(r)
        er = r.get('EventRecords')
        if not isinstance(er, dict):
            er = {}
        ev = er.get(str(event_id))
        if not isinstance(ev, dict):
            ev = {}
        k = str(key)
        if absolute:

            ev[k] = max(int(ev.get(k) or 0), int(count or 0))
        else:
            ev[k] = int(ev.get(k) or 0) + int(count or 0)
        er[str(event_id)] = ev
        r['EventRecords'] = er
        r['UserId'] = r.get('UserId') or user_id()
        r['Id'] = '1'
        r['Ver'] = int(r.get('Ver') or 0) + 1
        db_put("UserBehavior", rows if rows is not None else r)
    except Exception as e:
        log('    behavior event record failed (%s/%s): %s' % (event_id, key, e))

def _challenge_defs():

    out = {}
    for rid, mast in (master_tables().get("challenge_title") or {}).items():
        if not isinstance(mast, dict):
            continue

        if int(mast.get("ChallengeUniqueType") or 0) != 1:
            continue
        open_at = _master_at(mast.get("StartAt"))
        close_at = _master_at(mast.get("EndAt"))
        now = datetime.datetime.now(JST)
        if open_at is not None and now < open_at:
            continue
        if close_at is not None and now > close_at:
            continue
        out[rid] = mast
    return out

def _behavior_challenge_record(challenge_id, key, count=1, absolute=False):

    try:
        r, rows = _behavior_row()
        _behavior_rollover(r)
        cr = r.get("ChallengeRecords")
        if not isinstance(cr, dict):
            cr = {}
        cv = cr.get(str(challenge_id))
        if not isinstance(cv, dict):
            cv = {}
        k = str(key)
        if absolute:
            cv[k] = int(count)
        else:
            cv[k] = int(cv.get(k) or 0) + int(count)
        cr[str(challenge_id)] = cv
        r["ChallengeRecords"] = cr
        r["Ver"] = int(r.get("Ver") or 0) + 1
        db_put("UserBehavior", rows if rows is not None else r)
        return True
    except Exception as e:
        log("    challenge record failed:", e)
        return False

def _shop_period_start(limit_type, now=None):

    now = now or datetime.datetime.now(JST)
    try:
        lt = int(limit_type or 0)
    except Exception:
        lt = 0
    if lt == 2:
        d = now.replace(hour=GAME_RESET_HOUR, minute=0, second=0, microsecond=0)
        if now < d:
            d -= datetime.timedelta(days=1)
        return d
    if lt == 3:
        d = now.replace(hour=GAME_RESET_HOUR, minute=0, second=0, microsecond=0)
        if now < d:
            d -= datetime.timedelta(days=1)
        return d - datetime.timedelta(days=d.weekday())
    if lt == 4:
        d = now.replace(day=1, hour=GAME_RESET_HOUR, minute=0, second=0,
                        microsecond=0)
        if now < d:
            d = (d - datetime.timedelta(days=1)).replace(day=1)
        return d
    return None

def _shop_rows():

    rows = db_get('Shop')
    if not isinstance(rows, list):
        rows = []
    return rows

def _shop_entity_for(rows, rid, create=True):

    for r in rows:
        if isinstance(r, dict) and r.get('Id') == rid:
            return r
    if not create:
        return None
    row = {'UserId': user_id(), 'Id': rid, 'Count': 0, 'Total': 0,
           'UpdatedAt': '', 'OpenAt': '', 'Ver': 0}
    rows.append(row)
    return row

def _inject_shop_entities(sh):

    if not isinstance(sh, dict):
        return sh
    try:
        rows = _shop_rows()
        changed = False
        for r in (sh.get("Records") or []):
            if not isinstance(r, dict):
                continue
            rid = r.get("ResourceId")
            if not rid:
                continue
            if r.get("Shop") is None:
                r["Shop"] = _shop_entity_for(rows, rid)
                changed = True
        if changed:
            db_put("Shop", rows)
    except Exception as e:
        log("  shop entity inject failed:", e)
    return sh

def seed_challenge_missions():

    try:
        tables = master_tables()
        created = 0

        rows = db_get("ChallengeMission")
        if not isinstance(rows, list):
            rows = []
        have = set(r.get("Id") for r in rows if isinstance(r, dict))
        for rid, mast in (tables.get("challenge_title") or {}).items():
            if not isinstance(mast, dict) or rid in have:
                continue

            if int(mast.get("ChallengeUniqueType") or 0) != 1:
                continue
            if _master_at(mast.get("EndAt")) is not None:
                continue
            rows.append({"UserId": user_id(), "Id": rid, "StartAt": _now_iso(),
                         "ClearRewardReceived": False, "OpenedDayTab": 0,
                         "Fix": None, "Ver": 0})
            created += 1
        if created:
            db_put("ChallengeMission", rows)

        mrows = db_get("Mission")
        if not isinstance(mrows, list):
            mrows = []
        mhave = set(r.get("Id") for r in mrows if isinstance(r, dict))
        made = 0
        for rid, mast in (tables.get("mission_challenge") or {}).items():
            if not isinstance(mast, dict) or rid in mhave:
                continue
            mrows.append({"UserId": user_id(), "Id": rid, "MissionType": 5,
                          "State": 0, "Ver": 0})
            made += 1
        if made:
            db_put("Mission", mrows)
        if created or made:
            log("  challenge seed: %d challenge row(s), %d mission row(s)"
                % (created, made))
        return created, made
    except Exception as e:
        log("  challenge seed failed:", e)
        return 0, 0

def _shop_limit_for(rid):

    try:
        sh = db_get('ShopHold')
    except Exception:
        return (0, None)
    if not isinstance(sh, dict):
        return (0, None)
    for r in (sh.get('Records') or []):
        if isinstance(r, dict) and r.get('ResourceId') == rid:
            try:
                return (int(r.get('LimitType') or 0), r.get('LimitValue'))
            except Exception:
                return (0, None)
    return (0, None)

def _shop_expire(row, limit_type, now=None):

    if not isinstance(row, dict):
        return False
    if int(limit_type or 0) == 1:
        return False
    start = _shop_period_start(limit_type, now)
    if start is None:
        return False
    prev = _parse_iso(row.get('UpdatedAt'))
    if prev is None or prev >= start:
        return False
    row['Count'] = 0
    row['UpdatedAt'] = start.strftime('%Y-%m-%dT%H:%M:%S')
    row['Ver'] = int(row.get('Ver') or 0) + 1
    return True

def _seed_exchange_rows():

    try:
        tables = _exchange_defs()
    except Exception:
        tables = {}
    if not tables:
        return []
    import datetime as _dt
    _now = _dt.datetime.now()
    _d = _now if _now.hour >= GAME_RESET_HOUR else _now - _dt.timedelta(days=1)
    _upd = _d.strftime('%Y-%m-%dT05:00:00') + '+09:00'

    _friend_lineup = {'0_1': 0, '0_2': 0, '0_3': 3, '0_8': 2, '0_10': 0,
                      '0_11': 1, '0_12': 4, '0_15': 0, '0_17': 1, '0_19': 0,
                      '0_20': 0, '0_21': 0, '0_22': 2}
    rows = []
    for rid in tables:
        rows.append({
            'UserId': user_id(),
            'Id': rid,
            'No': 0,
            'Records': {},
            'Lineup': dict(_friend_lineup) if rid == 'exchange.friend' else {},
            'UpdatedAt': _upd,
            'Ver': 1,
        })
    if rows:
        db_put('Exchange', rows)
        log('    exchange: SEEDED %d row(s); an empty list crashes this screen'
            % len(rows))
    return rows

def _exchange_refresh_lineup(rows, master=None):

    if not isinstance(rows, list):
        return 0
    tables = master if master is not None else _exchange_defs()
    day_start = _exchange_period_start(1)
    if day_start is None:
        return 0
    day_s = day_start.strftime('%Y-%m-%dT%H:%M:%S') + '+09:00'
    touched = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        mrow = tables.get(row.get('Id')) or {}
        types = []
        for ed in (mrow.get('Records') or []):
            try:
                types.append(int(ed.get('ResetType') or 0))
            except Exception:
                continue
        if not any(t > 0 for t in types):
            continue
        prev = _parse_iso(row.get('UpdatedAt'))
        if prev is not None and prev >= day_start:
            continue
        row['UpdatedAt'] = day_s
        touched += 1
    return touched

def _exchange_reset_counters(rows, master=None):

    if not isinstance(rows, list):
        return 0
    tables = master if master is not None else _exchange_defs()
    now = datetime.datetime.now(JST)
    cleared = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        recs = row.get('Records')
        if not isinstance(recs, dict) or not recs:
            continue

        mrow = tables.get(row.get('Id')) or {}
        by_no = {}
        for ed in (mrow.get('Records') or []):
            try:
                by_no[str(int(ed.get('No') or 0))] = int(ed.get('ResetType') or 0)
            except Exception:
                continue
        keep = {}
        for key, val in recs.items():
            no = str(key).split('_')[0]
            rt = by_no.get(no)
            if rt is None or rt == 0:
                keep[key] = val
                continue
            start = _exchange_period_start(rt, now)
            prev = _parse_iso(row.get('UpdatedAt'))
            if start is not None and prev is not None and prev < start:
                cleared += 1
                continue
            keep[key] = val
        if cleared:
            row['Records'] = keep
    return cleared

def _parse_iso(txt):

    if not txt:
        return None
    try:
        d = datetime.datetime.fromisoformat(str(txt))
    except Exception:
        return None
    return d.replace(tzinfo=JST) if d.tzinfo is None else d.astimezone(JST)

_MD = None
def master_tables():

    global _MD
    if _MD is None:
        idx = {}
        try:
            path = os.path.join(HERE, 'masterdata.json')
            for r in json.load(io.open(path, encoding='utf-8')):
                k, rid = r.get('MasterKey'), r.get('ResourceId')
                if k and rid:
                    idx.setdefault(k, {})[rid] = r
        except Exception as e:
            log('masterdata index fail:', e)
        _MD = idx
    return _MD

_UID = None
def user_id():
    global _UID
    if _UID is None:
        u = db_get('User')
        if isinstance(u, dict) and u.get('Id'):
            _UID = u['Id']
        elif isinstance(u, list) and u and isinstance(u[0], dict) and u[0].get('Id'):
            _UID = u[0]['Id']
        else:

            raise RuntimeError(
                'db has no User.Id -- refusing to guess a player id '
                '(check that %s/User.json exists and has "Id")' % os.path.basename(DB))
    return _UID

def _row_id(item_id):

    if item_id == 'item.jewel_paid':
        return 'item.jewel_paid_dmm'

    if item_id == 'item.jewel':

        best = None
        try:
            rows = db_get('Item') or []
            for _cand in (_row_id('item.jewel_free'), _row_id('item.jewel_paid')):
                if _cand == 'item.jewel':
                    continue
                for _r in (rows if isinstance(rows, list) else []):
                    if not (isinstance(_r, dict) and _r.get('Id') == _cand):
                        continue
                    if int(_r.get('Count') or 0) > 0:
                        return _cand
                    if best is None:
                        best = _cand
        except Exception:
            pass
        return best or 'item.jewel_free'
    return item_id

OOPART_PLUS_STATS = (
    ('PlusHitPoint', 'HpMin', 'HpMax'),
    ('PlusAttack', 'AttackMin', 'AttackMax'),
    ('PlusDefence', 'DefenceMin', 'DefenceMax'),
    ('PlusMagicalDefence', 'MagicalDefenceMin', 'MagicalDefenceMax'),
    ('PlusAgility', 'AgilityMin', 'AgilityMax'),
    ('PlusCritical', 'CriticalMin', 'CriticalMax'),
)

OOPART_PLUS_CAP = {
    'PlusHitPoint': 400,
    'PlusAttack': 100,
    'PlusDefence': 100,
    'PlusMagicalDefence': 100,
    'PlusAgility': 12,
    'PlusCritical': 12,
}

_OOPART_PLUS_KEYS = ("PlusHitPoint", "PlusAttack", "PlusDefence",
                    "PlusMagicalDefence", "PlusAgility", "PlusCritical")

_OOPART_PLUS_SHORT = {
    "PlusHitPoint": "HitPoint", "PlusAttack": "Attack", "PlusDefence": "Defence",
    "PlusMagicalDefence": "MagicalDefence", "PlusAgility": "Agility",
    "PlusCritical": "Critical",
}

def _roll_oopart_plus(plus_id):

    out = {'PlusHitPoint': 0, 'PlusAttack': 0, 'PlusDefence': 0,
           'PlusMagicalDefence': 0, 'PlusAgility': 0, 'PlusCritical': 0}
    row = None
    try:
        for _r in (master_tables().get('oopart_plus') or {}).values():
            if isinstance(_r, dict) and _r.get('ResourceId') == plus_id:
                row = _r
                break
    except Exception:
        row = None
    if not isinstance(row, dict):

        return out

    eligible = []
    for field, kmin, kmax in OOPART_PLUS_STATS:
        try:
            lo = int(row.get(kmin) or 0)
            hi = int(row.get(kmax) or 0)
        except Exception:
            continue
        if hi > 0:
            eligible.append((field, max(0, lo), hi))
    if not eligible:
        return out

    rate = row.get('NumRate')
    want = len(eligible)
    if isinstance(rate, list) and rate:
        _w = []
        for i, v in enumerate(rate):
            try:
                _w.append(max(0, int(v)))
            except Exception:
                _w.append(0)
        if sum(_w) > 0:
            _tot = sum(_w)
            _pick = random.randint(1, _tot)
            _acc = 0
            for i, w in enumerate(_w):
                _acc += w
                if _pick <= _acc:
                    want = i + 1
                    break
        want = max(1, min(want, len(eligible)))
    for field, lo, hi in random.sample(eligible, want):
        out[field] = random.randint(lo, hi) if hi > lo else hi

    for _k, _cap in OOPART_PLUS_CAP.items():
        if int(out.get(_k) or 0) > _cap:
            out[_k] = _cap
    return out

_OOPART_PLUS_LOG = threading.local()

def _oopart_plus_log():
    d = getattr(_OOPART_PLUS_LOG, "items", None)
    if d is None:
        d = _OOPART_PLUS_LOG.items = []
    return d

def _oopart_plus_take():

    out = list(_oopart_plus_log())
    del _oopart_plus_log()[:]
    return out

_TRUTH_QUEST_END = None

def _apply_truth_replay(payload, mode):

    global _TRUTH_QUEST_END
    try:
        if _TRUTH_QUEST_END is None:
            p = os.path.join(HERE, "_truth_quest_end.json")
            if not os.path.exists(p):
                log("    REPLAY: %s missing -- no-op" % p)
                return payload
            _TRUTH_QUEST_END = json.loads(io.open(p, encoding="utf-8").read())
        T = _TRUTH_QUEST_END
        m = (mode or "").lower()
        if m == "all":

            out = json.loads(json.dumps(T))
            try:
                if isinstance(out.get("SyncData"), dict) and isinstance(payload.get("SyncData"), dict):
                    if out["SyncData"].get("User") and payload["SyncData"].get("User"):
                        out["SyncData"]["User"]["Id"] = payload["SyncData"]["User"].get("Id")
            except Exception:
                pass
            log("    REPLAY all    -> official body (%d bytes serialized)" % len(json.dumps(out)))
            return out
        if m == "syncdata":
            if isinstance(T.get("SyncData"), dict) and isinstance(payload.get("SyncData"), dict):
                keep = {k: payload["SyncData"].get(k) for k in ("User", "QuestRecords", "Items")}
                payload["SyncData"] = T["SyncData"]
                for k, v in keep.items():
                    if v is not None:
                        payload["SyncData"][k] = v
                log("    REPLAY SyncData -> official SyncData (kept our User/QuestRecords/Items)")
            return payload
        if m in ("ooparts", "oopartpluses"):
            tsd = T.get("SyncData") or {}
            for k in ("Ooparts", "OopartPluses"):
                if tsd.get(k):
                    payload["SyncData"][k] = tsd[k]
            log("    REPLAY Ooparts -> official Ooparts=%d OopartPluses=%d"
                % (len(tsd.get("Ooparts") or []), len(tsd.get("OopartPluses") or [])))
            return payload
        if m == "userpossession":
            tsd = T.get("SyncData") or {}
            if tsd.get("UserPossession"):
                payload["SyncData"]["UserPossession"] = tsd["UserPossession"]
            log("    REPLAY UserPossession -> official (%d rows)" % len(tsd.get("UserPossession") or []))
            return payload
        if m == "receivedrewards":
            if T.get("ReceivedRewards"):
                payload["ReceivedRewards"] = T["ReceivedRewards"]
            log("    REPLAY ReceivedRewards -> official (%d rows)" % len(T.get("ReceivedRewards") or []))
            return payload
        log("    REPLAY: unknown mode %r -- no-op" % mode)
    except Exception as e:
        log("    REPLAY failed: %s" % e)
    return payload

def _oopart_pluses_sync(sync):

    try:
        logged = _oopart_plus_take()
        if not logged:

            if sync.get("Ooparts"):
                sync["OopartPluses"] = []
            return sync
        rows = []
        for x in logged:
            rows.append({
                "Id": x.get("Id"),
                "PlusId": x.get("PlusId"),
                "BefPlus": {_OOPART_PLUS_SHORT[k]: int((x.get("BefPlus") or {}).get(k) or 0)
                            for k in _OOPART_PLUS_KEYS},
                "AddPlus": {_OOPART_PLUS_SHORT[k]: int((x.get("AddPlus") or {}).get(k) or 0)
                            for k in _OOPART_PLUS_KEYS},
                "AddCount": int(x.get("AddCount") or 1),
            })
        sync["OopartPluses"] = rows
    except Exception as e:
        log("    oopart pluses sync failed: %s" % e)
    return sync

def _grant_oopart(oopart_id, plus_id=None):

    if not isinstance(oopart_id, str) or not oopart_id.startswith('oopart.'):
        return None
    rows = db_get('Oopart')
    if not isinstance(rows, list):
        rows = []
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == oopart_id:

            r['Ver'] = int(r.get('Ver') or 0) + 1
            _np = _roll_oopart_plus(plus_id) if plus_id else {}
            _bef = {k: int(r.get(k) or 0) for k in _OOPART_PLUS_KEYS}
            for _pk, _pv in (_np or {}).items():

                _capped = min(int(_pv or 0), OOPART_PLUS_CAP.get(_pk, int(_pv or 0)))
                if _capped > int(r.get(_pk) or 0):
                    r[_pk] = _capped
            _aft = {k: int(r.get(k) or 0) for k in _OOPART_PLUS_KEYS}

            _oopart_plus_log().append({
                'Id': oopart_id,
                'PlusId': plus_id,
                'BefPlus': dict(_bef),
                'AddPlus': {k: _aft[k] - _bef[k] for k in _OOPART_PLUS_KEYS},
                'AddCount': 1,
            })
            touch(oopart_id, r, 'Ooparts')
            db_put('Oopart', rows)
            return r

    plus = _roll_oopart_plus(plus_id)
    fresh = {'UserId': user_id(), 'Id': oopart_id, 'Level': 1, 'Experience': 0,
             'SkillLevel': 1, 'ReceivedAt': _now_iso(), 'IsNewOopart': True,
             'LimitOver': 0, 'Ver': 0}
    fresh.update(plus)

    rows.append(fresh)

    _oopart_plus_log().append({
        'Id': oopart_id,
        'PlusId': plus_id,
        'BefPlus': {k: 0 for k in _OOPART_PLUS_KEYS},
        'AddPlus': {k: int(fresh.get(k) or 0) for k in _OOPART_PLUS_KEYS},
        'AddCount': 1,
    })
    touch(oopart_id, fresh, 'Ooparts')
    db_put('Oopart', rows)
    return fresh

UNLOCK_ITEM_PREFIXES = (
    'item.furniture.', 'item.illust.', 'item.bg.', 'item.clock.',
    'item.effect.', 'item.verse_bg.', 'item.job_panzer.',
)

def _is_unlock_item(item_id):

    return any(str(item_id).startswith(p) for p in UNLOCK_ITEM_PREFIXES)

def owned_item_ids():

    owned = set()
    try:
        for tbl in ('Item', 'UserPossession'):
            rows = db_get(tbl)
            if isinstance(rows, list):
                owned |= set(str(x.get('Id')) for x in rows if isinstance(x, dict))
    except Exception:
        pass
    return owned

def _grant_unlock_item(item_id):

    rows = db_get('UserPossession')
    if not isinstance(rows, list):
        return 0
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == item_id:
            return 1

    fresh = {'UserId': user_id(), 'Id': item_id, 'ReadFlag': False,
             'Records': {datetime.datetime.now().strftime('%Y%m%d%H%M%S'): 0},
             'Ver': 1}
    rows.append(fresh)
    touch(item_id, fresh, 'UserPossession')
    db_put('UserPossession', rows)
    return 1

def _grant_item(item_id, count):

    if not isinstance(item_id, str) or not item_id.startswith('item.'):
        return 0
    item_id = _row_id(item_id)
    if _is_unlock_item(item_id):
        return _grant_unlock_item(item_id)
    items = db_get('Item')
    if not isinstance(items, list):
        return 0
    for it in items:
        if isinstance(it, dict) and it.get('Id') == item_id:
            it['Count'] = int(it.get('Count') or 0) + count
            it['Ver'] = int(it.get('Ver') or 0) + 1
            _rec = it.get('Records')
            if isinstance(_rec, dict) and _rec:
                _k = sorted(_rec.keys())[0]
                it['Records'] = {_k: it['Count']}
            touch(item_id, it)
            db_put('Item', items)
            return it['Count']

    fresh = {'UserId': user_id(), 'Id': item_id, 'Count': count,
             'Records': None, 'IsRead': False, 'Ver': 0}
    items.append(fresh)
    touch(item_id, fresh)
    db_put('Item', items)
    return count

def _item_count_of(rid):

    rid = str(rid or '')
    if not rid:
        return 0
    try:
        if rid.startswith('orb.'):
            key = str(_orb_reward_to_id(rid))
            for r in db_get('UserPossession') or []:
                if isinstance(r, dict) and r.get('Id') == 'orbs':
                    return int((r.get('Records') or {}).get(key) or 0)
            return 0
        if rid.startswith('item.'):
            ent = _item_entity(rid)
            return int((ent or {}).get('Count') or 0)

        if rid.startswith('fragment.'):
            for r in db_get('Fragment') or []:
                if isinstance(r, dict) and r.get('Id') == rid:
                    return 1
            return 0
    except Exception:
        return 0
    return 0

def _spend_jewels(cost, label=''):

    cost = int(cost or 0)
    if cost <= 0:
        return True
    left = cost
    for cur in ('item.jewel_free', 'item.jewel_paid'):
        if left <= 0:
            break
        ent = _item_entity(cur)
        have = int(ent.get('Count') or 0)
        if have <= 0:
            continue
        use = min(have, left)
        _consume_item(cur, use, strict=False)
        left -= use
    if left > 0:
        log('    %s REFUSED: need %d jewel(s), short by %d' % (label, cost, left))
        return False
    log('    %s paid %d jewel(s)' % (label, cost))
    return True

def _have_item(item_id):

    ent = _item_entity(item_id) or {}
    return int(ent.get('Count') or 0)

def _consume_item(item_id, count, strict=True):

    item_id = _row_id(item_id)
    items = db_get('Item')
    if not isinstance(items, list):
        return None if strict else 0
    want = int(count or 0)
    if want <= 0:
        return None if strict else 0
    for it in items:
        if isinstance(it, dict) and it.get('Id') == item_id:
            have = int(it.get('Count') or 0)
            if have < want:
                if strict:
                    log('    consume %s REFUSED: need %d, have %d' % (item_id, want, have))
                    return None
                want = have
            it['Count'] = have - want
            it['Ver'] = int(it.get('Ver') or 0) + 1

            _rec = it.get('Records')
            if isinstance(_rec, dict) and _rec:
                _k = sorted(_rec.keys())[0]
                it['Records'] = {_k: it['Count']}
            touch(item_id, it)
            db_put('Item', items)
            log('    consumed %s x%d -> %d left' % (item_id, want, it['Count']))
            return it['Count']
    if strict:
        log('    consume %s REFUSED: item not owned (need %d)' % (item_id, want))
        return None
    return 0

QUEST_RECEIVED_TYPE = {'first': 1, 'bounty': 2}

def quest_reward_rows(reward_id, difficulty):

    try:
        row = (master_tables().get('quest_reward') or {}).get(reward_id)
    except Exception:
        return []
    if not row:
        return []
    out = []
    for r in (row.get('Records') or []):
        if int(r.get('Difficulty') or 0) == int(difficulty or 0):
            out.append(r)
    return out

REWARD_TYPE_FIRST_TIME = 1
REWARD_TYPE_BOUNTY = 2

def merge_received_rewards(rewards):

    merged = {}
    order = []
    for r in rewards:
        key = (r.get('RewardType'), r.get('RewardItemId'), r.get('RewardParam'))
        hit = merged.get(key)
        if hit is None:
            merged[key] = dict(r)
            order.append(key)
        else:
            hit['RewardItemCount'] = (int(hit.get('RewardItemCount') or 0)
                                      + int(r.get('RewardItemCount') or 0))
    return [merged[k] for k in order]

def _received_reward_type(master_rt, odds):
    rt = int(master_rt or 0)
    if rt == 1:
        return 1
    if rt == 2:
        return 3
    if rt == 4:
        return 13
    if rt == 5:
        return 14
    if rt == 3:
        try:
            o = int(odds)
        except Exception:
            o = 100
        if o >= 100:
            return 6
        if o >= 75:
            return 7
        if o >= 65:
            return 8
        if o >= 35:
            return 9
        return 10

    return rt

def roll_quest_rewards(quest_id, difficulty, first_time=False):

    try:
        q = (master_tables().get('quest') or {}).get(quest_id) or {}
    except Exception:
        q = {}
    got = []
    plan = [(q.get('QuestFirstTimeRewardId'), True)] if first_time else []
    plan.append((q.get('QuestRewardId'), False))
    for rid, is_first_table in plan:
        for r in quest_reward_rows(rid, difficulty):

            rtype = int(r.get('RewardType') or 0)

            _out_rt = _received_reward_type(rtype, r.get('RewardOdds'))

            if rtype == REWARD_TYPE_BOUNTY and not first_time:
                continue
            odds = int(r.get('RewardOdds') or 100)
            if odds < 100 and random.randint(1, 100) > odds:
                continue
            lo = int(r.get('MinCount') or 1)
            hi = int(r.get('MaxCount') or lo)
            cnt = lo if hi <= lo else random.randint(lo, hi)
            item_id = r.get('RewardId')
            if not item_id:
                continue

            _grant_reward(item_id, cnt, r.get('RewardParam'))
            got.append({'RewardType': _out_rt, 'RewardItemId': item_id,
                        'RewardParam': r.get('RewardParam'),
                        'RewardItemCount': cnt, 'CelebRate': 0, 'EvDrop': 0,
                        'CType': 0, 'GroupNo': int(r.get('GroupNo') or 0),
                        'GroupRewards': []})

    _stam_cost = _quest_stamina_cost(quest_id, difficulty)
    if _stam_cost:

        _cut = 0
        while _cut < len(got) and int(got[_cut].get('RewardType') or 0) == 1:
            _cut += 1
        got.insert(_cut, {'RewardType': 2, 'RewardItemId': 'item.gold',
                          'RewardParam': '', 'RewardItemCount': _stam_cost * 300,
                          'CelebRate': 0, 'EvDrop': 0, 'CType': 0, 'GroupNo': 0,
                          'GroupRewards': []})
        got.append({'RewardType': 6, 'RewardItemId': 'item.magara',
                    'RewardParam': '', 'RewardItemCount': _stam_cost,
                    'CelebRate': 0, 'EvDrop': 0, 'CType': 0, 'GroupNo': 0,
                    'GroupRewards': []})
        _grant_reward('item.gold', _stam_cost * 300)
        _grant_reward('item.magara', _stam_cost)
    return got

_REWARD_TYPE_BY_PREFIX = (('item.jewel', 1), ('item.gold', 1), ('oopart.', 6),
                          ('chara.', 3), ('item.box.', 17), ('item.oopartsexp', 7),
                          ('item.charaexp', 7), ('item.general_god_part', 14))

def _rank_reward_rows(reward_id, amount, key='Rank'):

    tbl = (master_tables().get('rank_reward') or {}).get(reward_id) or {}
    try:
        amount = int(amount or 0)
    except Exception:
        amount = 0

    order = []
    merged = {}
    for rec in (tbl.get('Records') or []):
        try:
            need = int(rec.get(key) if rec.get(key) is not None
                       else (rec.get('Rank') or rec.get('Score') or 0))
        except Exception:
            continue
        if amount < need:
            continue
        rid = rec.get('RewardId')
        if not rid:
            continue
        cnt = int(rec.get('Count') or 1)
        _grant_reward(rid, cnt)
        cur = merged.get(rid)
        if cur is None:
            merged[rid] = {'RewardType': _reward_type_of(rid),
                           'RewardItemId': rid,
                           'RewardParam': rec.get('RewardParam'),
                           'RewardItemCount': cnt,
                           'CelebRate': 0, 'EvDrop': 0, 'CType': 0,
                           'GroupNo': 0, 'GroupRewards': None}
            order.append(rid)
        else:
            cur['RewardItemCount'] = int(cur['RewardItemCount']) + cnt
    return [merged[r] for r in order]

def _reward_type_of(rid):

    r = str(rid or '')
    for pre, t in _REWARD_TYPE_BY_PREFIX:
        if r.startswith(pre):
            return t
    return 2 if r.startswith('item.') else 17

def _grant_weapon(weapon_id):

    if not isinstance(weapon_id, str) or not weapon_id.startswith('weapon.'):
        return None
    rows = db_get('UserPossession')
    if not isinstance(rows, list):
        rows = []
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == weapon_id:
            return r
    fresh = {'UserId': user_id(), 'Id': weapon_id, 'ReadFlag': True,
             'Records': {'Lv': 1, 'Orb0': 0, 'Orb1': 0, 'Orb2': 0}, 'Ver': 0}
    rows.append(fresh)
    touch(weapon_id, fresh, 'UserPossession')
    db_put('UserPossession', rows)
    return fresh

def _grant_possession_flag(rid):

    if not isinstance(rid, str) or '.' not in rid:
        return None
    rows = db_get('UserPossession')
    if not isinstance(rows, list):
        rows = []
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == rid:
            return r
    fresh = {'UserId': user_id(), 'Id': rid, 'ReadFlag': True,
             'Records': None, 'Ver': 0}
    rows.append(fresh)
    touch(rid, fresh, 'UserPossession')
    db_put('UserPossession', rows)
    return fresh

def _grant_word_possession(rid):

    key = str(rid).split(".", 1)[1] if "." in str(rid) else str(rid)
    try:
        rows = db_get("UserPossession")
        if not isinstance(rows, list):
            rows = []
        row = None
        for r in rows:
            if isinstance(r, dict) and r.get("Id") == "word_possessions":
                row = r
                break
        if row is None:

            row = {"UserId": user_id(), "Id": "word_possessions",
                   "ReadFlag": False, "Records": None, "Ver": 0}
            rows.append(row)
        recs = row.get("Records")
        if not isinstance(recs, dict):
            recs = {}

        recs[key] = max(int(recs.get(key) or 0), 1)
        row["Records"] = recs
        row["Ver"] = int(row.get("Ver") or 0) + 1
        db_put("UserPossession", rows)
        touch("word_possessions", row, "UserPossession")
    except Exception as e:
        log("    word_possession grant failed (%s): %s" % (rid, e))

def _grant_pass_exp(pass_id, count):

    if not pass_id:
        return
    try:
        rows = db_get("MissionPass")
        if not isinstance(rows, list):
            rows = []
        row = None
        for r in rows:
            if isinstance(r, dict) and r.get("Id") == pass_id:
                row = r
                break
        if row is None:
            row = {"UserId": user_id(), "Id": pass_id, "PassExp": 0,
                   "IsSpecial": False, "IsPremium": False,
                   "ReceivedLevel": {}, "Ver": 0}
            rows.append(row)
        row["PassExp"] = int(row.get("PassExp") or 0) + int(count or 0)
        if not isinstance(row.get("ReceivedLevel"), dict):
            row["ReceivedLevel"] = {}
        row["Ver"] = int(row.get("Ver") or 0) + 1
        db_put("MissionPass", rows)
        touch(pass_id, row, "MissionPasses")
    except Exception as e:
        log("    pass_exp grant failed (%s): %s" % (pass_id, e))

def _grant_reward(reward_id, count, plus_param=None):

    rid = str(reward_id or '')
    try:

        if _is_orb_box_id(rid):

            _orb_box_add(rid, count)
            return

        if rid.startswith('orb.'):
            oid = _orb_reward_to_id(rid)
            if oid is not None:
                _orb_inventory_add(oid, count)
            return

        if rid.startswith('item.'):
            _grant_item(rid, count)
        elif rid.startswith('chara.'):
            for _ in range(count):
                _grant_chara(rid)
        elif rid.startswith('oopart.'):

            _grant_oopart(rid, plus_param)
        elif rid.startswith('weapon.'):
            _grant_weapon(rid)
        elif rid.startswith('stamp.') or rid.startswith('frame.'):

            _grant_possession_flag(rid)
        elif rid.startswith('fragment.'):

            rows = db_get('Fragment')
            if not isinstance(rows, list):
                rows = []
            if not any(isinstance(x, dict) and x.get('Id') == rid for x in rows):
                rows.append({'UserId': user_id(), 'Id': rid, 'Level': 1,
                             'IsNew': True, 'ReceivedAt': _now_iso(), 'Ver': 0})
                db_put('Fragment', rows)
        elif rid.startswith('word_possession.'):

            _grant_word_possession(rid)
        elif rid.startswith('mission_pass_exp.') or rid.startswith('season_pass_exp.'):

            _grant_pass_exp(rid.split('.', 1)[1], count)
        else:

            log('    quest reward: UNHANDLED namespace for %s x%d' % (rid, count))
    except Exception as e:
        log('    quest reward grant failed', rid, e)

def _quest_record(quest_id):

    rows = db_get('QuestRecord')
    if not isinstance(rows, list):
        return None
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == quest_id:
            return r
    return None

def _quest_cleared(quest_id, difficulty=0):

    r = _quest_record(quest_id)
    if not r:
        return False
    try:
        d = int(difficulty or 0)
    except Exception:
        d = 0
    for rec in (r.get('Records') or []):
        if not isinstance(rec, dict):
            continue
        if int(rec.get('Difficulty') or 0) != d:
            continue
        if rec.get('IsCleared'):
            return True
        if int(rec.get('ClearCount') or 0) > 0:
            return True

    return False

def record_quest_clear(quest_id, difficulty):

    if not quest_id:
        log('    record_quest_clear: refused (empty quest id)')
        return
    try:
        rows = db_get('QuestRecord')
        if not isinstance(rows, list):
            return
        r = None
        for x in rows:
            if isinstance(x, dict) and x.get('Id') == quest_id:
                r = x
                break
        if r is None:

            r = {'UserId': user_id(), 'Id': quest_id, 'Records': [],
                 'MissionAchievement': 0,
                 'QuestStartAt': _now_iso(), 'QuestEndAt': None,
                 'QuestStartDifficulty': -1,
                 'QuestClearCount': 0,
                 'HazardNos': [], 'MaxRank': None, 'EnemyTurn': None,
                 'Ver': 0}
            rows.append(r)

        try:
            d = int(difficulty or 0)
        except Exception:
            d = 0
        recs = r.get('Records')
        if not isinstance(recs, list):
            recs = []
            r['Records'] = recs
        hit = None
        for rec in recs:
            if isinstance(rec, dict) and int(rec.get('Difficulty') or 0) == d:
                hit = rec
                break
        if hit is None:
            hit = {'Difficulty': d, 'TryCount': 0, 'ClearCount': 0,
                   'SkipCount': 0, 'MaxDrop': None, 'EventId': None,
                   'IsCleared': False}
            recs.append(hit)
        hit['ClearCount'] = int(hit.get('ClearCount') or 0) + 1
        hit['TryCount'] = int(hit.get('TryCount') or 0) + 1
        hit['IsCleared'] = True
        r['QuestClearCount'] = int(r.get('QuestClearCount') or 0) + 1

        r['QuestEndAt'] = _now_iso()

        n_cleared = 0
        for _rec in recs:
            if not isinstance(_rec, dict):
                continue
            if _rec.get('IsCleared') or int(_rec.get('ClearCount') or 0) > 0:
                n_cleared += 1
        r['MissionAchievement'] = max(int(r.get('MissionAchievement') or 0),
                                      0 if n_cleared == 0 else min(5, 3 + (n_cleared - 1)))
        r['Ver'] = int(r.get('Ver') or 0) + 1
        touch(quest_id, r)
        db_put('QuestRecord', rows)
    except Exception as e:
        log('    quest clear record failed', e)

def _mission_achievement(quest_id):

    r = _quest_record(quest_id)
    if not r:
        return 0
    try:
        return int(r.get('MissionAchievement') or 0)
    except Exception:
        return 0

def _rank_exp_state():

    u = db_get('User') or {}
    return int(u.get('Rank') or 0), int(u.get('Exp') or 0)

def _add_exp(amount):

    u = db_get('User') or {}
    rank = int(u.get('Rank') or 0)
    _rank_before = rank
    exp = int(u.get('Exp') or 0) + amount
    try:
        table = master_tables().get('user_rank') or {}
        rows = []
        for row in table.values():
            rows.extend(row.get('Records') or [])
        rows.sort(key=lambda r: int(r.get('Rank') or 0))
        for r in rows:
            rk = int(r.get('Rank') or 0)
            if rk == rank + 1 and exp >= int(r.get('NeedExp') or 0):
                rank = rk

        for r in rows:
            if int(r.get('Rank') or 0) == rank + 1:
                exp = min(exp, max(0, int(r.get('NeedExp') or 0) - 1))
                break
    except Exception as e:
        log('    exp: rank table failed', e)

    _gained_rank = rank - int(_rank_before or rank)
    if _gained_rank > 0:
        try:
            _lim = _stamina_recover_limit(rank)
            _cur = int(u.get('Stamina') or 0)

            u['Stamina'] = _cur + _lim * _gained_rank
            log('    RANK UP %d -> %d: stamina %d -> %d (+%d x%d)'
                % (_rank_before, rank, _cur, u['Stamina'], _lim, _gained_rank))
        except Exception as _e:
            log('    rank-up stamina grant failed: %s' % _e)
    u['Rank'] = rank
    u['Exp'] = exp
    db_put('User', u)
    touch_singleton('User', u)
    return rank, exp

CONVERT_PREFIX = 'item.ringo_'

def _convert_rates():

    out = {}
    try:
        for rid, row in (master_tables().get('game_setting') or {}).items():
            if not str(rid).startswith('game_setting.exchange_duplicate_item_'):
                continue
            try:
                rar = int(str(rid).rsplit('_', 1)[1])
            except Exception:
                continue
            parts = str(row.get('Value') or '').split(',')
            if len(parts) >= 2:
                out[rar] = (parts[0].strip(), int(parts[1]))
    except Exception as e:
        log('    convert: rate table failed', e)
    return out

CHARA_RESERVE_SELF = 1
CHARA_MAX_OVERLAP = 6

def _convertible_duplicates(count, overlap):

    need = max(0, CHARA_MAX_OVERLAP - int(overlap or 0))
    return int(count or 0) - CHARA_RESERVE_SELF - need

def convert_duplicate_items():

    rates = _convert_rates()
    if not rates:
        log('    convert: no rate rows in master -- nothing to do')
        return []
    charas = db_get('Chara')
    if not isinstance(charas, list):
        return []
    ctable = master_tables().get('chara') or {}
    gained = {}
    report = []
    changed = False
    for ch in charas:
        if not isinstance(ch, dict):
            continue
        cid = str(ch.get('Id') or '')
        n = _convertible_duplicates(ch.get('Count'), ch.get('Overlap'))
        if n <= 0:
            continue
        rar = int((ctable.get(cid) or {}).get('Rarity') or 0)
        rate = rates.get(rar)
        if not rate:
            log('    convert: %s rarity %s has no rate row' % (cid, rar))
            continue
        ash_id, per = rate
        gain = n * per
        gained[ash_id] = gained.get(ash_id, 0) + gain
        report.append('%s r%s x%d -> %s x%d' % (cid, rar, n, ash_id, gain))

        ch['Count'] = (CHARA_RESERVE_SELF
                       + max(0, CHARA_MAX_OVERLAP - int(ch.get('Overlap') or 0)))
        ch['Ver'] = int(ch.get('Ver') or 0) + 1
        touch(cid, ch)
        changed = True

    if changed:
        db_put('Chara', charas)
    for ash_id, total in gained.items():
        _grant_item(ash_id, total)
    return report

def _quest_stamina_cost(quest_id, difficulty):

    try:
        q = (master_tables().get('quest') or {}).get(quest_id) or {}
        dd = q.get('DifficultyData') or []
        i = int(difficulty or 0)
        if not dd:
            return 0
        return max(0, int((dd[i] if i < len(dd) else dd[0]).get('Stamina') or 0))
    except Exception:
        return 0

def _spend_stamina(amount, strict=True):

    global _USER_CACHE
    u = db_get('User')
    if not isinstance(u, dict):
        return None if strict else 0
    want = int(amount or 0)
    if want <= 0:
        return int(u.get('Stamina') or 0)
    cur = int(u.get('Stamina') or 0)
    if cur < want:
        if strict:
            log('    stamina REFUSED: need %d, have %d' % (want, cur))
            return None
        want = cur
    u['Stamina'] = cur - want
    u['StaminaUpdateTime'] = int(time.time())
    db_put('User', u)
    _USER_CACHE = u
    touch_singleton('User', u)
    return u['Stamina']

def _uq_offered_phantoms(prev_selected, pool_keys, want=3):

    try:
        want = int(want or 0)
    except Exception:
        want = 3
    have = set()
    for k in (pool_keys or []):
        s = str(k)
        if s.startswith('phantom.'):
            have.add(s)
    if want <= 0 or not have:
        return []
    used = set()
    for x in (prev_selected or []):
        s = str(x)
        if s.startswith('phantom.'):
            used.add(s)
    cand = sorted(have - used)
    if len(cand) < want:
        cand = sorted(have)
    if len(cand) <= want:
        return cand

    off = int(time.time()) // 60
    start = off % len(cand)
    out = []
    for i in range(want):
        out.append(cand[(start + i) % len(cand)])
    return out

def _uq_phantom_pool():

    try:
        rows = db_get('UserPossession')
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    for x in rows:
        if isinstance(x, dict) and x.get('Id') == 'phantom':
            rec = x.get('Records')
            if isinstance(rec, dict):
                return sorted(rec.keys())
    return []

def _uq_received_type(master_rt):

    try:
        rt = int(master_rt)
    except Exception:
        return 6
    if rt >= 6:
        return rt
    return {3: 6, 4: 13}.get(rt, 6)

def _uq_treasure_rewards(no):

    _mt = _uq_node_mtype(no)
    if _mt != 1:
        return None
    try:
        _n = int(no)
    except Exception:
        return None
    _rid = None
    for _row in (master_tables().get('unique_quest') or {}).values():
        for _rec in (_row.get('Records') or []):
            if isinstance(_rec, dict) and _rec.get('No') == _n:
                _rid = _rec.get('RId')
                break
        if _rid:
            break
    if not _rid:
        return None
    _row = (master_tables().get('unique_quest_reward') or {}).get('uq_reward.%s' % _rid)
    if not _row:
        return None
    _rrt = {3: 6, 4: 13}
    out = []
    for _d in (_row.get('Records') or []):
        if not isinstance(_d, dict):
            continue
        _did = _d.get('RewardId')
        if not _did:
            continue
        try:
            _odds = int(_d.get('RewardOdds') if _d.get('RewardOdds') is not None else 100)
        except Exception:
            _odds = 100
        if _odds < 100 and random.randint(1, 100) > _odds:
            continue
        try:
            _mn = int(_d.get('MinCount') or 1)
            _mx = int(_d.get('MaxCount') or _mn)
        except Exception:
            _mn = _mx = 1
        _ct = random.randint(_mn, _mx) if _mx > _mn else _mn
        if _ct <= 0:
            continue
        _rt = _d.get('RewardType')
        try:
            _rt = int(_rt)
        except Exception:
            _rt = 3
        out.append('%d,%d,%s,%s,%d' % (_rt, _rrt.get(_rt, 6), _did,
                                       _d.get('RewardParam') or '', _ct))
    return out

def _uq_heal(ent):

    if not isinstance(ent, dict):
        return False
    if ent.get('Status') != 3:
        return False
    logs = ent.get('Logs')
    maps = ent.get('Maps')
    if not isinstance(logs, list) or not isinstance(maps, dict) or not maps:
        return False
    rows = []
    for k in maps:
        try:
            rows.append(int(str(k).split('_')[0]))
        except Exception:
            continue
    if not rows:
        return False
    if len(logs) >= max(rows):
        ent['Status'] = 6
        log('    UQ-HEAL id=%s: progress %d >= last row %d -> Status 3 -> 6 (Complete)'
            % (ent.get('Id'), len(logs), max(rows)))
        return True
    return False

def _uq_develop_counts():

    counts = {}
    try:
        rows = db_get('UserPossession') or []
    except Exception:
        rows = []
    dev = {}
    for _row in (master_tables().get('unique_quest_develop') or {}).values():
        for _rec in (_row.get('Records') or []):
            if isinstance(_rec, dict) and _rec.get('No') is not None:
                dev[_rec['No']] = _rec.get('UniqueQuestDevelop')
    for r in rows:
        if not isinstance(r, dict) or r.get('Id') != 'unique_quest_develop':
            continue
        for k in (r.get('Records') or {}):
            try:
                t = dev.get(int(k))
            except Exception:
                continue
            if t is not None:
                counts[t] = counts.get(t, 0) + 1
    return counts

def _uq_develop_lv(dtype):

    return _uq_develop_counts().get(dtype, 0)

UQ_INIT_UNIT_COST = 6
UQ_MAX_UNIT_COST = 10

UQ_UNIT_COST_BY_RARITY = {2: 0, 3: 0, 4: 1, 5: 3, 6: 6}
UQ_UNIT_COST_STEP = 6
UQ_DEVELOP_PLUS_INIT = 10
UQ_DEVELOP_PLUS_MAX = 11

def _uq_unit_cost_init():

    return UQ_INIT_UNIT_COST + _uq_develop_lv(UQ_DEVELOP_PLUS_INIT)

def _uq_chara_rarity(chara_id):

    cid = str(chara_id)
    for _row in (master_tables().get('chara') or {}).values():
        if str(_row.get('ResourceId')) == cid:
            return _row.get('Rarity')
    return None

def _uq_angel_cost(chara_id):

    return UQ_UNIT_COST_BY_RARITY.get(_uq_chara_rarity(chara_id), UQ_UNIT_COST_STEP)

def _uq_unit_cost_max():

    return UQ_MAX_UNIT_COST + _uq_develop_lv(UQ_DEVELOP_PLUS_MAX)

def _uq_node_mtype(no):

    try:
        n = int(no)
    except Exception:
        return None
    for _row in (master_tables().get('unique_quest') or {}).values():
        for _rec in (_row.get('Records') or []):
            if isinstance(_rec, dict) and _rec.get('No') == n:
                return _rec.get('MType')
    return None

def _uq_status_after_node(no):

    mt = _uq_node_mtype(no)
    if mt == 1:
        return 7
    if mt == 3:
        return 6
    if mt in (0, 2):
        return 4
    return None

def _stamina_recover_limit(rank=None):

    try:
        u = db_get('User') or {}
        r = int(u.get('Rank') or 1) if rank is None else int(rank)
    except Exception:
        r = 1
    best = None

    _tbl = master_tables().get('user_rank') or {}
    tab = {}
    for _v in _tbl.values():
        if isinstance(_v, dict) and (_v.get('Records')):
            tab = _v
            break
    if not tab:
        tab = _tbl.get('rank.player') or _tbl.get('user_rank.1') or {}
    for rec in (tab.get('Records') or []):
        try:
            nr = int(rec.get('Rank') or 0)
        except Exception:
            continue
        if nr <= r:
            best = rec
    if best is None:
        recs = tab.get('Records') or []
        best = recs[0] if recs else {}
    try:
        return int(best.get('StaminaRecoveryLimit') or 150)
    except Exception:
        return 150

STAMINA_RECOVER_SECONDS = 360

def _regen_stamina():

    u = db_get('User')
    if not isinstance(u, dict):
        return None
    try:
        cur = int(u.get('Stamina') or 0)
    except Exception:
        cur = 0
    try:
        upd = int(u.get('StaminaUpdateTime') or 0)
    except Exception:
        upd = 0
    cap = _stamina_recover_limit()
    if upd <= 0:

        u['StaminaUpdateTime'] = int(time.time())
        db_put('User', u)
        _USER_CACHE = u
        return cur
    if cur >= cap:

        _now = int(time.time())
        if _now - upd >= STAMINA_RECOVER_SECONDS:
            u['StaminaUpdateTime'] = _now
            db_put('User', u)
            _USER_CACHE = u
        return cur
    elapsed = int(time.time()) - upd
    if elapsed < STAMINA_RECOVER_SECONDS:
        return cur
    gained = elapsed // STAMINA_RECOVER_SECONDS
    new = min(cap, cur + gained)
    u['Stamina'] = new

    u['StaminaUpdateTime'] = upd + gained * STAMINA_RECOVER_SECONDS
    db_put('User', u)
    _USER_CACHE = u
    touch_singleton('User', u)
    log('    stamina regen: %d -> %d (cap %d, +%d after %ds)'
        % (cur, new, cap, gained, elapsed))
    return new

def _quest_exp_reward(quest_id, difficulty):

    try:
        q = (master_tables().get('quest') or {}).get(quest_id) or {}
        dd = q.get('DifficultyData') or []
        i = int(difficulty or 0)
        row = dd[i] if 0 <= i < len(dd) else (dd[0] if dd else {})
        _st = int(row.get('Stamina') or 0)
    except Exception:
        _st = 0
    if _st <= 0:

        try:
            dd = ((master_tables().get('quest') or {}).get(quest_id) or {}).get('DifficultyData') or []
            _st = int((dd[0] if dd else {}).get('Stamina') or 0)
        except Exception:
            _st = 0
    return max(1, _st * 10)

DUNGEON_STATUS = {
    'None': 0, 'SelectMap': 1, 'Complete': 2, 'Shop': 3,
    'Battle': 4, 'Engage': 5, 'Lose': 6,
}

def _dungeon_group(group_id):

    try:
        return (master_tables().get('dungeon_group') or {}).get(group_id) or {}
    except Exception:
        return {}

def _dungeon_stamina_cost(group_id, rate):

    try:
        base_cost = int((_dungeon_group(group_id) or {}).get('Stamina') or 0)
    except Exception:
        base_cost = 0
    return base_cost * max(1, int(rate or 1))

def _dungeon_group_rec(group_id, no):

    g = _dungeon_group(group_id)
    for r in (g.get('Records') or []):
        try:
            if int(r.get('No')) == int(no):
                return r
        except Exception:
            continue
    return {}

def _dungeon_map(dungeon_id):

    out = {}
    try:
        d = (master_tables().get('dungeon') or {}).get(dungeon_id) or {}
    except Exception:
        return out
    for r in (d.get('Records') or []):
        out[str(r.get('No'))] = r
    return out

def _dungeon_tile(dungeon_id, no):

    try:
        return _dungeon_map(dungeon_id).get(str(int(no))) or {}
    except Exception:
        return {}

def _dungeon_reward_payout(reward_id, rate):

    try:
        r = (master_tables().get('dungeon_reward') or {}).get(reward_id) or {}
    except Exception:
        return None
    recs = r.get('Records') or []
    if not recs:
        return None
    x = recs[0]
    try:
        cnt = int(round(float(x.get('MinCount') or 0) * int(rate or 1)))
    except Exception:
        cnt = 0
    if cnt <= 0:
        return None
    return (x.get('RewardId'), cnt)

def _dungeon_rewards_row(reward_id, rate):

    got = _dungeon_reward_payout(reward_id, rate)
    if not got:
        return None
    return {
        'RewardType': 6, 'RewardItemId': got[0], 'RewardParam': None,
        'RewardItemCount': got[1], 'CelebRate': 0, 'EvDrop': 0,
        'CType': 0, 'GroupNo': 0, 'GroupRewards': None,
    }

def _dungeon_rows():

    rows = db_get('Dungeon')
    if not isinstance(rows, list):
        rows = []
    return rows

def _dungeon_entity(group_id, rows=None):

    if rows is None:
        rows = _dungeon_rows()
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == group_id:
            return r
    return None

def _dungeon_new_row(group_id):

    return {

        'UserId': user_id(), 'Id': group_id, 'EventId': None, 'No': 1,
        'Status': DUNGEON_STATUS['SelectMap'], 'HazardNos': [], 'ClearNos': [],
        'MaxRank': 0, 'StartAt': _now_iso(), 'ClearCount': 0,
        'Maps': {}, 'Logs': {}, 'Members': [], 'Settings': [],
        'StaminaRate': 1, 'FragmentId': None, 'Ver': 0,
    }

def _dungeon_logs_put(logs, **kw):

    for k, v in kw.items():
        if v is None:
            continue
        logs[k] = v if isinstance(v, str) else str(v)
    return logs

def _dungeon_shop_tile_no(group_id):

    try:
        rows = _dungeon_rows()
        ent = _dungeon_entity(group_id, rows)
        if not ent:
            return 0
        logs = ent.get('Logs') or {}
        k = logs.get('P')
        if k and str(k) in logs:
            return int(logs[str(k)])
    except Exception:
        pass
    return 0

def _dungeon_shop_guard_no(dungeon_id, shop_no):

    try:
        row = _dungeon_map(dungeon_id).get(str(int(shop_no))) or {}
        p = row.get('P')
        if not p or ':' not in str(p):
            return 0
        tail = str(p).split(':')[-1]
        return int(tail)
    except Exception:
        return 0

def _dungeon_max_rank(ent):

    try:
        hz = (master_tables().get('hazard') or {})
    except Exception:
        return 0
    by_no = {}
    for _r in hz.values():
        if isinstance(_r, dict):
            by_no[str(_r.get('No'))] = _r
    total = 0
    for n in (ent.get('HazardNos') or []):
        r = by_no.get(str(n))
        if r:
            total += int(r.get('Rank') or 0)
    return total

def _dungeon_total_rate(ent):

    return 10 * _dungeon_max_rank(ent)

def _dungeon_run_total(ent, clear_row=None):

    def _num(v):
        try:
            return int(v or 0)
        except Exception:
            return 0
    logs = ent.get('Logs') or {}
    total = _num(logs.get('I_T')) + _num(logs.get('I_B')) + _num(logs.get('I_S'))
    if clear_row:
        total += _num(clear_row.get('RewardItemCount'))
    return total

def _dungeon_status_for_tile(group_id, no):

    grp = _dungeon_group_rec(group_id, _dungeon_current_group_no(group_id))
    did = grp.get('DungeonId')
    t = _dungeon_tile(did, no)
    t = int(t.get('DMapType') if t.get('DMapType') is not None else -1)
    if t == 5:
        return DUNGEON_STATUS['Shop']
    if t in (2, 3):
        return DUNGEON_STATUS['Battle']
    if t == 7:
        return DUNGEON_STATUS['Engage']
    return DUNGEON_STATUS['SelectMap']

def _dungeon_current_group_no(group_id):

    rows = _dungeon_rows()
    ent = _dungeon_entity(group_id, rows)
    try:
        return int(ent.get('No') or 1) if ent else 1
    except Exception:
        return 1

def _dungeon_response(ent, received=None, endquest=None, execno=0):

    sync = flush_touched(empty_sync())
    return {
        'Dungeon': ent,
        'EndQuestResponseData': endquest,
        'ReceivedRewards': received,
        'ExecNo': int(execno or 0),
        'SyncData': sync,
    }

_DUNGEON_WRITES = (
    '/api/dungeon/start', '/api/dungeon/save', '/api/dungeon/end',
    '/api/dungeon/end-battle', '/api/dungeon/treasure', '/api/dungeon/shop',
    '/api/dungeon/engage', '/api/dungeon/giveup',
)

def _box_gacha_poss_id(box_id):

    return 'box_gacha_result.' + str(box_id)

def _box_gacha_def(box_id):

    try:
        return (master_tables().get('box_gacha') or {}).get(box_id) or {}
    except Exception:
        return {}

def _box_gacha_group(group_id):

    out = []
    try:
        for _bid, _row in (master_tables().get('box_gacha') or {}).items():
            if isinstance(_row, dict) and str(_row.get('GroupId')) == str(group_id):
                out.append((int(_row.get('Step') or 0), _bid))
    except Exception:
        return []
    out.sort()
    return [b for _s, b in out]

def _box_gacha_result(box_id, rows=None):

    if rows is None:
        rows = db_get('UserPossession')
    if not isinstance(rows, list):
        return None
    rid = _box_gacha_poss_id(box_id)
    for x in rows:
        if isinstance(x, dict) and x.get('Id') == rid:
            return x
    return None

def _box_gacha_result_ensure(box_id, rows):

    ent = _box_gacha_result(box_id, rows)
    if ent is None:
        ent = {

            'UserId': user_id(), 'Id': _box_gacha_poss_id(box_id),
            'ReadFlag': False, 'Records': None, 'Ver': 0,
        }
        rows.append(ent)

    if isinstance(ent.get('Records'), dict) and not ent['Records']:
        ent['Records'] = None
    return ent

BOX_GACHA_UNLIMITED = 1 << 30

def _box_gacha_is_infinite(box_id):

    rows = _box_gacha_def(box_id).get('Records') or []
    if not rows:
        return False
    return all(int(r.get('Limit') or 0) <= 0 for r in rows)

def _box_gacha_left(box_id, records):

    left = {}
    for r in (_box_gacha_def(box_id).get('Records') or []):
        try:
            no = int(r.get('No'))
            lim = int(r.get('Limit') or 0)
        except Exception:
            continue
        if lim <= 0:

            left[str(no)] = BOX_GACHA_UNLIMITED
            continue
        have = int((records or {}).get(str(no)) or 0)
        left[str(no)] = max(0, lim - have)
    return left

def _box_gacha_is_done(box_id, records):

    if not isinstance(records, dict):
        return False
    if records.get('skip'):
        return True
    if _box_gacha_is_infinite(box_id):
        return False
    left = _box_gacha_left(box_id, records)
    if not left:
        return False
    return all(v <= 0 for v in left.values())

def _box_gacha_resolve(ident):

    if not ident:
        return None
    if _box_gacha_def(ident):
        return ident
    rows = db_get('UserPossession')
    boxes = _box_gacha_group(ident)
    for bid in boxes:
        ent = _box_gacha_result(bid, rows)
        recs = (ent or {}).get('Records') or {}
        if not _box_gacha_is_done(bid, recs):
            return bid
    return boxes[-1] if boxes else None

def _box_gacha_featured_done(box_id, records):

    try:
        rows = _box_gacha_def(box_id).get('Records') or []
        flagged = [r for r in rows if r.get('Flag')]
        if not flagged:
            return False
        for r in flagged:
            no = str(int(r.get('No')))
            if int((records or {}).get(no) or 0) < int(r.get('Limit') or 0):
                return False
        return True
    except Exception:
        return False

def _box_gacha_draw(box_id, want, records):

    drawn = {}
    left = _box_gacha_left(box_id, records)
    rows = _box_gacha_def(box_id).get('Records') or []
    by_no = {}
    for r in rows:
        try:
            by_no[str(int(r.get('No')))] = r
        except Exception:
            continue

    _was_done = _box_gacha_featured_done(box_id, records)
    for _ in range(max(0, int(want))):
        avail = [n for n, c in left.items() if c > 0 and n in by_no]
        if not avail:
            break
        weights = [int(by_no[n].get('Weight') or 0) or 1 for n in avail]
        pick = random.choices(avail, weights=weights, k=1)[0]

        if left[pick] < BOX_GACHA_UNLIMITED:
            left[pick] -= 1
        drawn[pick] = drawn.get(pick, 0) + 1

        _probe = dict(records or {})
        for _n, _c in drawn.items():
            _probe[_n] = int(_probe.get(_n) or 0) + _c
        if not _was_done and _box_gacha_featured_done(box_id, _probe):
            break
    return drawn

def _oopart_master(oopart_id):

    try:
        for _r in (master_tables().get('oopart') or {}).values():
            if isinstance(_r, dict) and _r.get('ResourceId') == oopart_id:
                return _r
    except Exception:
        pass
    return {}

_OOPART_WRITES = (
    '/api/oopart/enhance', '/api/oopart/enhance-limit',
    '/api/oopart/enhance-plus', '/api/oopart/enhance-skill',
    '/api/oopart/clear-new-oopart',
)
_USER_WRITES = (
    '/api/user/name/set', '/api/user/news/set', '/api/user/term/set',
    '/api/user/current-team/set', '/api/user/current-quest/set',
    '/api/user/read-flag/set', '/api/user/read-flag/reset',
    '/api/user/question/save', '/api/user/vote/save',
)

_QUESTION_WRITES = (
    '/api/user/question/save',
    '/api/user/vote/save',
)

_ORB_BOX_KEY = 'orb_box'
_ORB_BOX_ID = 'orb_box'

ORB_BOX_REWARD_NUM = 3

def _orb_box_rows():
    rows = db_get('OrbBox')
    if not isinstance(rows, list):
        rows = []
    return rows

_ORB_NORMAL_SLOTS = 10

def _orb_slot_sync(ent):

    stack = ent.get('Stack')
    if not isinstance(stack, dict):
        stack = {}
    slots = ent.get('Slots')
    if not isinstance(slots, dict):
        slots = {}
    for box_id in list(stack.keys()):
        have = int(stack.get(box_id) or 0)
        if have <= 0:
            stack.pop(box_id, None)
            continue

        if box_id.startswith('item.box.orb_sp.'):
            continue
        while have > 0 and len(slots) < _ORB_NORMAL_SLOTS:
            idx = 0
            while str(idx) in slots:
                idx += 1
            slots[str(idx)] = {'Id': box_id, 'CreatedAt': _now_iso(), 'Count': 1}
            have -= 1
        if have > 0:
            stack[box_id] = have
        else:
            stack.pop(box_id, None)
    ent['Slots'] = slots
    ent['Stack'] = stack

    pslots = ent.get('PSlots')
    if not isinstance(pslots, list):
        pslots = []
    _sp_have = int(stack.pop('item.box.orb_sp.1', 0) or 0)
    if _sp_have > 0:

        _existing = None
        for _p in pslots:
            if isinstance(_p, dict) and _p.get('Id') == 'item.box.orb_sp.1':
                _existing = _p
                break
        if _existing is None:
            pslots.append({'Id': 'item.box.orb_sp.1', 'CreatedAt': _now_iso(),
                           'Count': _sp_have})
        else:
            _existing['Count'] = int(_existing.get('Count') or 0) + _sp_have

    for _k in list(stack.keys()):
        if _k.startswith('item.box.orb_sp.'):
            _n = int(stack.pop(_k, 0) or 0)
            if _n > 0:
                pslots.append({'Id': _k, 'CreatedAt': _now_iso(), 'Count': _n})
    ent['Stack'] = stack
    ent['PSlots'] = pslots
    return ent

def _roll_box_rewards(box_id, cnt, sel=None):

    tables = master_tables().get('item_box') or {}
    key = None

    _bm = (master_tables().get('item') or {}).get(str(box_id)) or {}
    _type = _bm.get('ItemTypeValue')
    _param = _bm.get('ItemTypeParameter')
    _BY_TYPE = {42: 'select_box.', 43: 'random_box.',
                52: 'orb_box.',   53: 'orb_sp_box.'}
    if _type in _BY_TYPE and _param not in (None, ''):
        cand = _BY_TYPE[_type] + str(_param)
        if cand in tables:
            key = cand
    if key is None:
        for pre, tbl in (('item.box.select.', 'select_box.'),
                         ('item.box.random.', 'random_box.'),
                         ('item.box.orb_sp.', 'orb_sp_box.'),
                         ('item.box.orb.',    'orb_box.')):
            if str(box_id).startswith(pre):
                key = tbl + str(box_id)[len(pre):]
                break
    row = tables.get(key) if key else None
    recs = list((row or {}).get('Records') or [])
    is_random = bool(key and key.startswith('random_box.'))
    is_orb_box = bool(key and (key.startswith('orb_box.')
                               or key.startswith('orb_sp_box.')))
    weights_all_equal = bool(recs) and len({int(r.get('Weight') or 1)
                                            for r in recs}) == 1

    _is_select = bool(key and key.startswith('select_box.'))
    _draws = max(0, int(cnt)) * (ORB_BOX_REWARD_NUM if is_orb_box else 1)
    _merged = {}
    for _ in range(_draws):
        if _is_select:

            pick = next((r for r in recs if r.get('RewardId') == sel), None)
            if pick is None:
                pick = recs[0] if recs else None
            if pick and pick.get('RewardId'):
                rid = pick['RewardId']
                n = int(pick.get('Num') or 1)
                _grant_reward(rid, n)
                _prev = _merged.get(rid)
                if _prev is None:
                    _merged[rid] = {'RewardId': rid, 'AfterCount': _item_count_of(rid),
                                    'AddCount': n, 'IsOver': False}
                else:
                    _prev['AddCount'] = int(_prev.get('AddCount') or 0) + n
                    _prev['AfterCount'] = _item_count_of(rid)
            continue
        if recs and (is_random or is_orb_box or len(recs) > 1):
            if is_orb_box and weights_all_equal:
                pick = random.choice(recs)
            else:
                ws = [int(r.get('Weight') or 1) for r in recs]
                pick = random.choices(recs, weights=ws, k=1)[0]
        elif recs:
            pick = recs[0] if len(recs) == 1 else None
        else:
            pick = None
        if pick is None and recs:

            pick = next((r for r in recs if r.get('RewardId') == sel), recs[0])
        if pick and pick.get('RewardId'):
            rid = pick['RewardId']
            n = int(pick.get('Num') or 1)
            _grant_reward(rid, n)

            _prev = _merged.get(rid)
            if _prev is None:
                _merged[rid] = {'RewardId': rid,
                                'AfterCount': _item_count_of(rid),
                                'AddCount': n,
                                'IsOver': False}
            else:

                _prev['AddCount'] = int(_prev.get('AddCount') or 0) + n
                _prev['AfterCount'] = _item_count_of(rid)

    return list(_merged.values()), key, recs

def _orb_box_entity(rows=None):

    rows = rows if rows is not None else _orb_box_rows()
    for r in rows:

        if isinstance(r, dict) and (r.get('Id') == _ORB_BOX_ID
                                    or r.get('Key') == _ORB_BOX_KEY):

            if not isinstance(r.get('Slots'), dict):
                r['Slots'] = {}
            if not isinstance(r.get('PSlots'), list):
                r['PSlots'] = []
            if not isinstance(r.get('Stack'), dict):
                r['Stack'] = {}

            r.pop('UserId', None)

            r['Key'] = _ORB_BOX_KEY
            r['Id'] = user_id()
            return _orb_slot_sync(r)

    ent = {'Key': _ORB_BOX_KEY, 'Id': user_id(),
           'Slots': {}, 'PSlots': [], 'Stack': {}, 'Ver': 0}
    rows.append(ent)
    return _orb_slot_sync(ent)

def _orb_reward_to_id(rid):

    r = str(rid or '')
    if not r.startswith('orb.'):
        return None
    body = r[4:]
    if '_' not in body:
        return None
    no, _, rank = body.partition('_')
    try:
        return int(no) * 100 + int(rank)
    except Exception:
        return None

def _orb_inventory_add(orb_id, count):

    if orb_id is None or count <= 0:
        return
    rows = db_get('UserPossession')
    if not isinstance(rows, list):
        rows = []
    hit = None
    for x in rows:
        if isinstance(x, dict) and x.get('Id') == 'orbs':
            hit = x
            break
    if hit is None:

        hit = {'UserId': user_id(), 'Id': 'orbs', 'ReadFlag': False,
               'Records': None, 'Ver': 0}
        rows.append(hit)
    rec = hit.get('Records')
    if not isinstance(rec, dict):
        rec = {}
    key = str(orb_id)
    rec[key] = int(rec.get(key) or 0) + int(count)
    hit['Records'] = rec
    hit['Ver'] = int(hit.get('Ver') or 0) + 1
    db_put('UserPossession', rows)
    touch('orbs', hit, 'UserPossession')
    log('    orb +%d %s (now %d)' % (count, key, rec[key]))

def _orb_inventory_counts():

    out = {}
    try:
        rows = db_get('UserPossession')
    except Exception:
        return out
    if not isinstance(rows, list):
        return out
    for x in rows:
        if not (isinstance(x, dict) and x.get('Id') == 'orbs'):
            continue
        rec = x.get('Records')
        if not isinstance(rec, dict):
            continue
        for k, v in rec.items():
            try:
                out[int(k)] = int(v or 0)
            except Exception:
                pass
    return out

def _orb_inventory_take(orb_id, count):

    try:
        want = int(count or 0)
    except Exception:
        return False
    if want <= 0 or orb_id is None:
        return want <= 0
    rows = db_get('UserPossession')
    if not isinstance(rows, list):
        return False
    for x in rows:
        if isinstance(x, dict) and x.get('Id') == 'orbs':
            rec = x.get('Records')
            if not isinstance(rec, dict):
                return False
            key = str(orb_id)
            have = int(rec.get(key) or 0)
            if have < want:
                log('    orb take REFUSED: %s has %d < %d' % (key, have, want))
                return False
            left = have - want
            if left:
                rec[key] = left
            else:
                rec.pop(key, None)
            x['Records'] = rec
            x['Ver'] = int(x.get('Ver') or 0) + 1
            db_put('UserPossession', rows)
            touch('orbs', x, 'UserPossession')
            return True
    return False

def _is_orb_box_id(rid):

    r = str(rid or '')
    return (r.startswith('item.box.orb.')
            or r.startswith('item.box.orb_sp.')
            or r == 'item.orb_pkey')

def _orb_box_add(box_id, count):

    if not box_id or count <= 0:
        return
    rows = _orb_box_rows()
    ent = _orb_box_entity(rows)
    stack = ent['Stack']
    stack[str(box_id)] = int(stack.get(str(box_id)) or 0) + int(count)
    ent['Ver'] = int(ent.get('Ver') or 0) + 1
    db_put('OrbBox', rows)

    _touched_flag()['orb_box_granted'] = True
    log('    orb box +%d %s (now %d)'
        % (count, box_id, stack[str(box_id)]))

def _ranking_entity(stage_id=None):

    base = {'UserId': user_id(), 'Id': stage_id or '',
            'Score': 0, 'Logs': {}, 'Tm': None,
            'Status': 0, 'Rank': 0, 'Receive': 0}
    try:
        rows = db_get('Ranking')
        if isinstance(rows, list):
            for x in rows:
                if not isinstance(x, dict):
                    continue
                if stage_id and x.get('Id') not in (None, stage_id):
                    continue
                merged = dict(base)
                merged.update(x)
                if not isinstance(merged.get('Logs'), dict):
                    merged['Logs'] = {}
                for k in ('Score', 'Status', 'Rank', 'Receive'):
                    if merged.get(k) is None:
                        merged[k] = 0
                return merged
    except Exception:
        pass
    return base

def _ts_is_g(u, tid):

    return str(tid) == 'g'

TUTORIAL_LAUNCH_POOL = (
    'chara.1', 'chara.2', 'chara.3', 'chara.4', 'chara.6', 'chara.8',
    'chara.9', 'chara.12', 'chara.15', 'chara.18', 'chara.20', 'chara.21',
    'chara.24', 'chara.25', 'chara.26', 'chara.27', 'chara.32', 'chara.33',
    'chara.34', 'chara.35', 'chara.36', 'chara.37', 'chara.38', 'chara.39',
    'chara.40', 'chara.42', 'chara.43', 'chara.44', 'chara.45', 'chara.47',
    'chara.48', 'chara.49', 'chara.51', 'chara.52',
)

TUTORIAL_LAST_RARITIES = (6, 5)

def _tutorial_pools(last=False):

    try:
        rar = {}
        for rid, row in (master_tables().get('chara') or {}).items():
            try:
                r = int(row.get('Rarity') or 0)
            except Exception:
                r = 0
            if r:
                rar[rid] = r
    except Exception:
        rar = {}
    if not rar:
        return _chara_pool()
    allowed = TUTORIAL_LAST_RARITIES if last else (3, 4, 5, 6)

    source = rar.keys() if last else TUTORIAL_LAUNCH_POOL
    pools = {}
    for cid in source:
        r = rar.get(cid)
        if r in allowed:
            pools.setdefault(r, []).append(cid)
    if not pools:
        for cid, r in rar.items():
            if r in allowed:
                pools.setdefault(r, []).append(cid)
    return pools

def _tutorial_gacha_draws(n):

    n = max(0, int(n))
    if n <= 0:
        return []

    front = _tutorial_pools(last=False)

    non_six = {r: v for r, v in front.items() if r != 6}
    if not non_six:
        non_six = dict(front)
    rates = [(r, w) for r, w in _lottery_rates('gacha_lottery.nomal_no_pick')
             if r != 6]
    if not rates:
        rates = [(r, 1) for r in sorted(non_six)]

    out = []
    for i in range(n):
        if n > 1 and i == n - 1:
            break
        try:
            rid, _rar = _roll_one(rates, non_six)
        except Exception as e:
            log('    tutorial draw failed:', e)
            rid = None
        if rid:
            out.append(str(rid))

    if n > 1:

        six_pool = _tutorial_pools(last=True).get(6) or []
        if not six_pool:
            six_pool = [c for c, r in _chara_pool_rarity().items() if r == 6]
        if not six_pool:
            six_pool = ['chara.62']
        out.append(str(random.choice(six_pool)))
    elif n == 1:

        try:
            rid, _rar = _roll_one(_lottery_rates('gacha_lottery.nomal_no_pick'),
                                  _tutorial_pools(last=False))
            if rid:
                out.append(str(rid))
        except Exception:
            pass
    return out

def _chara_pool_rarity():

    out = {}
    try:
        for rid, row in (master_tables().get('chara') or {}).items():
            try:
                r = int(row.get('Rarity') or 0)
            except Exception:
                r = 0
            if r:
                out[rid] = r
    except Exception:
        pass
    return out

def _question_rows():

    out = []
    try:
        path = os.path.join(HERE, 'masterdata.json')
        for r in json.load(io.open(path, encoding='utf-8')):
            if str(r.get('MasterKey')) == 'question' and r.get('ResourceId'):
                out.append(r)
    except Exception as e:
        log('question table load failed:', e)
    return out

def _in_question_window(q):

    def parse(s):
        for fmt in ('%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M'):
            try:
                return datetime.datetime.strptime(str(s), fmt)
            except Exception:
                continue
        return None
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    st, en = parse(q.get('StartAt')), parse(q.get('EndAt'))
    if st and now < st: return False
    if en and now > en: return False
    return True

_SHOP_WRITES = (
    '/api/shop/open', '/api/shop/celeb', '/api/shop/fashion',
    '/api/shop/pack-jewel', '/api/shop/pay-reward', '/api/shop/purchase',
    '/api/shop/result', '/api/shop/celeb-cycle-benefits',
)
def _end_quest_response(received=None, living=None, orb_box=None, result=None,
                        before_rank=1, after_rank=None, before_exp=0,
                        after_exp=None, before_stamina=0, after_stamina=None,
                        achievement=0, over_item=False):

    br = int(before_rank or 1)
    be = int(before_exp or 0)
    bs = int(before_stamina or 0)
    return {
        'ReceivedRewards': received if received is not None else [],
        'LivingCharaIds': living if living is not None else [],
        'QuestResult': result,
        'BeforeRank': br,
        'AfterRank': int(after_rank) if after_rank is not None else br,
        'BeforeExp': be,
        'AfterExp': int(after_exp) if after_exp is not None else be,
        'BeforeStamina': bs,
        'AfterStamina': int(after_stamina) if after_stamina is not None else bs,
        'BeforeAchivement': int(achievement or 0),
        'AfterAchivement': int(achievement or 0),
        'CurrentAchivement': int(achievement or 0),
        'IsOverItem': bool(over_item),
        'BeforeDawnBreak': 0,
        'AfterDawnBreak': 0,
        'OrbBox': orb_box,

        'SyncData': None,
    }

_UNIQUE_BARE = (
    '/api/unique-quest/develop', '/api/unique-quest/enhance',
    '/api/unique-quest/reset', '/api/unique-quest/select-chara',
    '/api/unique-quest/select-phantom',
)
_UNIQUE_WRITES = (
    '/api/unique-quest/start', '/api/unique-quest/save',
    '/api/unique-quest/save-team', '/api/unique-quest/end',
    '/api/unique-quest/end-battle', '/api/unique-quest/giveup',
    '/api/unique-quest/reset', '/api/unique-quest/select-chara',
    '/api/unique-quest/select-phantom', '/api/unique-quest/select-treasure',
    '/api/unique-quest/treasure', '/api/unique-quest/exec',
    '/api/unique-quest/develop', '/api/unique-quest/enhance',
)

def _member_type_ok(default):

    if isinstance(default, (list, dict)):
        return default
    return []

def _enforce_member_types(path, body):

    members = RESPONSE_MEMBERS.get(path)
    if not isinstance(members, dict) or not isinstance(body, dict):
        return body
    for member, default in members.items():
        if body.get(member) is None:
            body[member] = default if isinstance(default, (list, dict)) else []
    return body

def _req_json(body):
    try:
        return json.loads(body.decode('utf-8')) if body else {}
    except Exception:
        return {}
def _strip_harmonia_prefix(value):

    s = str(value or '')
    if s.startswith('harmonia.'):
        return s[len('harmonia.'):]
    return s

def _add_enemy_kills(kills):

    if not isinstance(kills, dict) or not kills:
        return
    try:
        rows = db_get('UserPossession')
        if not isinstance(rows, list):
            rows = []
        changed = False
        for rid in ('enemy_kill_count', 'enemy_kill_no'):
            hit = None
            for x in rows:
                if isinstance(x, dict) and x.get('Id') == rid:
                    hit = x
                    break
            if hit is None:
                hit = {'UserId': user_id(), 'Id': rid, 'ReadFlag': False,
                       'Records': None, 'Ver': 0}
                rows.append(hit)
            recs = hit.get('Records')
            if not isinstance(recs, dict):
                recs = {}
            for k, v in kills.items():
                try:
                    recs[str(k)] = int(recs.get(str(k)) or 0) + int(v or 0)
                except Exception:
                    continue

            hit['Records'] = recs if recs else None
            hit['Ver'] = int(hit.get('Ver') or 0) + 1
            touch(rid, hit, 'UserPossession')
            changed = True
        if changed:
            db_put('UserPossession', rows)
            log('    enemy kills merged -> %s'
                % json.dumps({k: int(v or 0) for k, v in kills.items()}))
    except Exception as e:
        log('    enemy kills merge failed: %s' % e)

def _god_story_row_id(profile_id):

    s = str(profile_id or '')
    if not s.startswith('enemy.god.profile.'):
        return None
    return s[len('enemy.god.profile.'):]

def _god_unlock_bits(profile_id, kill_count):

    if not _god_story_row_id(profile_id):
        return None
    try:
        kills = int(kill_count or 0)
    except Exception:
        return None
    if kills <= 0:
        return 0
    prof = (master_tables().get('enemy_god_profile') or {}).get(str(profile_id)) or {}
    th = []
    for key in ('UnlockPicture1', 'UnlockPicture2', 'UnlockPicture3'):
        try:
            th.append(int(prof.get(key) or 0))
        except Exception:
            th.append(0)
    bits = 0
    for i, need in enumerate(th):
        if need and kills >= need:
            bits |= 0xFF << (8 * i)
    if len(th) == 3 and th[2] and kills >= th[2]:
        bits = 0xFFFFFF
    return bits & 0xFFFFFF

def _lyric_exp_threshold(chapter):

    try:
        rows = (master_tables().get('lyric_experience') or {})
        row = rows.get('lyric_experience.chapter.%d' % int(chapter)) or {}
        v = int(row.get('Count') or 0)
        if v > 0:
            return v
    except Exception:
        pass
    return {1: 2, 2: 2, 3: 3}.get(int(chapter), 3)

def _sync_angel_lyrics(chara_ids, battle_count=1, echo=None):

    if not isinstance(chara_ids, (list, tuple)) or not chara_ids:

        return []
    try:
        add = max(0, int(battle_count or 0))
    except Exception:
        add = 0
    if add <= 0:
        return []
    rows = db_get('Lyric')
    if not isinstance(rows, list):
        rows = []
    out = []
    for cid in chara_ids:
        cid = str(cid or '')
        if not cid:
            continue
        rid = cid if cid.startswith('lyric.') else ('lyric.' + cid)
        hit = None
        for x in rows:
            if isinstance(x, dict) and x.get('Id') == rid:
                hit = x
                break
        if hit is None:

            hit = {'UserId': user_id(), 'Id': rid, 'Count': 0,
                   'Experience': 0, 'ReadFlag': '', 'ReadAt': None, 'Ver': 0}
            rows.append(hit)
        try:
            count = int(hit.get('Count') or 0)
        except Exception:
            count = 0
        if count >= 12:

            if int(hit.get('Experience') or 0) != 0:
                hit['Experience'] = 0
                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                out.append(hit)
            continue
        try:
            exp = int(hit.get('Experience') or 0) + add
        except Exception:
            exp = add

        while count < 12:
            chapter = count // 4 + 1
            need = _lyric_exp_threshold(chapter)
            if exp < need:
                break
            exp -= need
            count += 1
        if count >= 12:
            exp = 0
        hit['Count'] = count
        hit['Experience'] = exp
        hit['Ver'] = int(hit.get('Ver') or 0) + 1
        out.append(hit)
        log('    angel lyric -> %s Count=%d Exp=%d (tickets=%d)'
            % (rid, count, exp, add))
    if out:
        db_put('Lyric', rows)
        for e in out:
            touch(e.get('Id'), e, 'AngelLyrics')
    return out

def _sync_god_lyrics(kills):

    if not isinstance(kills, dict) or not kills:
        return
    rows = db_get('GodLyric')
    if not isinstance(rows, list):
        rows = []

    totals = {}
    try:
        for r in (db_get('UserPossession') or []):
            if isinstance(r, dict) and r.get('Id') == 'enemy_kill_count':
                recs = r.get('Records') or {}
                for k, v in recs.items():
                    try:
                        totals[str(k)] = int(v or 0)
                    except Exception:
                        pass
                break
    except Exception:
        totals = {}
    changed = False
    touched_any = False
    for prof_id, n in kills.items():
        suffix = _god_story_row_id(prof_id)
        if not suffix:
            continue
        rid = 'lyric.god.' + str(suffix)
        try:
            if int(n or 0) <= 0:
                continue
        except Exception:
            continue
        hit = None
        for x in rows:
            if isinstance(x, dict) and x.get('Id') == rid:
                hit = x
                break
        if hit is None:
            hit = {'UserId': user_id(), 'Id': rid, 'UnlockedBitFlag': 0,
                   'ReadFlag': None, 'ReadAt': None, 'Ver': 0}
            rows.append(hit)
            log('    god lyric -> created %s (first defeat of %s)' % (rid, prof_id))
        want = _god_unlock_bits(prof_id, totals.get(str(prof_id), 0))
        if want is None:
            continue
        try:
            old = int(hit.get('UnlockedBitFlag') or 0)
        except Exception:
            old = 0
        if want != old:
            hit['UnlockedBitFlag'] = want
            hit['Ver'] = int(hit.get('Ver') or 0) + 1
            log('    god lyric -> %s UnlockedBitFlag %d -> %d (kills %d)'
                % (rid, old, want, totals.get(str(prof_id), 0)))
            changed = True
        touch(rid, hit, 'GodLyrics')
        touched_any = True
    if changed or touched_any:
        db_put('GodLyric', rows)

def _bump_possession(rid, delta, key=None):

    try:
        rows = db_get('UserPossession')
        if not isinstance(rows, list):
            rows = []
        hit = None
        for x in rows:
            if isinstance(x, dict) and x.get('Id') == rid:
                hit = x
                break
        if hit is None:

            hit = {'UserId': user_id(), 'Id': rid, 'Count': 0,
                   'ReadFlag': False, 'Records': None, 'Ver': 0}
            rows.append(hit)
        hit['Count'] = max(0, int(hit.get('Count') or 0) + int(delta or 0))
        if key is not None:
            hit['Key'] = key
        hit['Ver'] = int(hit.get('Ver') or 0) + 1
        db_put('UserPossession', rows)
        touch(rid, hit, 'UserPossession')
        return hit['Count']
    except Exception as e:
        log('    bump_possession %s failed: %s' % (rid, e))
        return 0

def _set_possession_count(rid, value, key=None):

    try:
        rows = db_get('UserPossession')
        if not isinstance(rows, list):
            rows = []
        hit = None
        for x in rows:
            if isinstance(x, dict) and x.get('Id') == rid:
                hit = x
                break
        if hit is None:
            hit = {'UserId': user_id(), 'Id': rid, 'Count': 0,
                   'ReadFlag': False, 'Records': None, 'Ver': 0}
            rows.append(hit)
        hit['Count'] = max(0, int(value or 0))
        if key is not None:
            hit['Key'] = key
        hit['Ver'] = int(hit.get('Ver') or 0) + 1
        db_put('UserPossession', rows)
        touch(rid, hit, 'UserPossession')
        return hit['Count']
    except Exception as e:
        log('    set_possession_count %s failed: %s' % (rid, e))
        return 0

def _lyric_keyword_bits(flag):

    try:
        return int(str(flag).split('-')[0] or 0)
    except Exception:
        return 0

def _angel_lyric_count(flag, previous=0):

    kw = _lyric_keyword_bits(flag)
    if kw <= 0:
        return int(previous or 0)
    return int(kw).bit_length()

def _chara_rows():
    rows = db_get('Chara')
    return rows if isinstance(rows, list) else []

def _chara_entity(chara_id, rows=None):

    for c in (rows if rows is not None else _chara_rows()):
        if isinstance(c, dict) and c.get('Id') == chara_id:
            return c
    return None

def _level_exp_table(chara_id):

    tbl = master_tables().get('chara_level_exp') or {}
    if not tbl:
        return None
    ch = (master_tables().get('chara') or {}).get(chara_id) or {}
    rar = int(ch.get('Rarity') or 0)
    ent = _chara_entity(chara_id) or {}
    grade = int(ent.get('Grade') or 0)
    for key in ('chara_level_exp.%d.%d' % (rar, grade),
                'chara_level_exp.%d.0' % rar, 'chara_level_exp.6.0'):
        row = tbl.get(key)
        if row:
            return row.get('Records') or []
    return None

def _skill_cap(chara_id, stype):

    try:
        row = (master_tables().get('chara_skill_level') or {}).get(
            'chara_skill_level.%d.%s' % (stype, chara_id)) or {}
        levels = [int(r.get('Level') or 0) for r in (row.get('Records') or [])]
        if not levels:
            return 7
        mx = max(levels)
        if stype in (1, 2, 3):
            return max(0, mx - 7)
        return mx
    except Exception:
        return 7
def _apply_enhance_exp(chara_id, gained, rows=None):

    ent = _chara_entity(chara_id, rows)
    if ent is None:
        return 0, 0
    cur = int(ent.get('Level') or 1)
    exp = int(ent.get('Experience') or 0) + int(gained or 0)
    curve = _level_exp_table(chara_id) or []
    newlv = cur
    for rec in curve:
        try:
            lv = int(rec.get('Level') or 0)
            need = int(rec.get('Exp') or 0)
        except Exception:
            continue
        if lv > newlv and exp >= need:
            newlv = lv
    ent['Experience'] = exp
    if newlv != cur:
        ent['Level'] = newlv
    ent['Ver'] = int(ent.get('Ver') or 0) + 1
    return max(0, newlv - cur), newlv

def _apply_affinity_exp(chara_id, gained, rows=None):

    ent = _chara_entity(chara_id, rows)
    if ent is None:
        return 0, 0
    cur = int(ent.get('Affinity') or 1)
    exp = int(ent.get('AffinityExp') or 0) + int(gained or 0)
    curve = (master_tables().get('affinity_level_exp') or {})
    recs = None
    for v in curve.values():
        recs = v.get('Records') or []
        break

    _alimit = int(ent.get('AffinityLimit') or 0)
    cap = 100 if _alimit else 20
    newlv = cur
    for rec in (recs or []):
        try:
            lv = int(rec.get('Level') or 0)
            need = int(rec.get('Exp') or 0)
        except Exception:
            continue
        if lv > newlv and lv <= cap and exp >= need:
            newlv = lv
    ent['AffinityExp'] = exp
    if newlv != cur:
        ent['Affinity'] = newlv
    ent['Ver'] = int(ent.get('Ver') or 0) + 1
    return max(0, newlv - cur), newlv

def _spend_use_items(use_items, strict=True):

    reqs = []
    for u in (use_items or []):
        if not isinstance(u, dict):
            continue
        iid = u.get('Item') or u.get('ItemId') or u.get('itemId')
        cnt = int(u.get('Count') or u.get('count') or 0)
        if iid and cnt:
            reqs.append((iid, cnt))
    if strict:

        need = {}
        for iid, cnt in reqs:
            need[iid] = need.get(iid, 0) + cnt
        for iid, cnt in need.items():
            have = _have_item(iid)
            if have < cnt:
                log('    use-items REFUSED: need %d x %s, have %d' % (cnt, iid, have))
                return None
    total = 0
    for iid, cnt in reqs:
        got = _consume_item(iid, cnt, strict=False)
        total += cnt
    return total

def _spend_gold(amount):

    amount = int(amount or 0)
    if amount <= 0:
        return True
    ent = _item_entity('item.gold')
    have = int(ent.get('Count') or 0)
    if have < amount:
        return False
    _consume_item('item.gold', amount, strict=False)
    return True

GACHA_PLAYS = None

_USER_CACHE = None

def _load_gacha_plays():

    global GACHA_PLAYS
    if GACHA_PLAYS:
        return GACHA_PLAYS
    out = {}
    try:
        rows = db_get('GachaHold') or {}
        if isinstance(rows, dict):
            rows = rows.get('GachaHold') or []
        for banner in rows:
            if not isinstance(banner, dict):
                continue
            for gp in (banner.get('GachaPlayMasterDataList') or []):
                if not isinstance(gp, dict):
                    continue
                gid = gp.get('Id')
                if not gid:
                    continue
                out[gid] = {
                    'count': int(gp.get('ContinueCount') or 1),
                    'cost_id': gp.get('CostId'),
                    'cost_count': int(gp.get('CostCount') or 0),
                    'main': gp.get('MainLotteryId'),
                    'last': gp.get('LastLotteryId'),
                }
    except Exception as e:
        log('gacha play table load failed:', e)
    GACHA_PLAYS = out
    return out

def gacha_play_info(gacha_id):

    info = _load_gacha_plays().get(gacha_id)
    if info:
        return (info['count'], info['cost_id'], info['cost_count'],
                info.get('main'), info.get('last'))

    m = re.search(r'_(\d+)$', gacha_id or '')
    return (int(m.group(1)) if m else 1), None, 0, None, None

def _chara_pool():

    pools = {}
    try:
        for rid, row in (master_tables().get('chara') or {}).items():
            r = int(row.get('Rarity') or 0)
            if r:
                pools.setdefault(r, []).append(rid)
    except Exception:
        pass
    return pools

def _lottery_tables():

    global _LOTTERY_TABLES
    if _LOTTERY_TABLES is not None:
        return _LOTTERY_TABLES
    out = {}
    try:
        rows = db_get('GachaHold') or {}
        if isinstance(rows, dict):
            rows = rows.get('GachaHold') or []
        for banner in rows:
            if not isinstance(banner, dict):
                continue
            for lt in (banner.get('GachaLotteryMasterDataList') or []):
                if not isinstance(lt, dict):
                    continue
                rid = lt.get('ResourceId') or lt.get('Id')
                if not rid:
                    continue
                tbl = []
                for r in (lt.get('Records') or []):
                    try:
                        tbl.append((int(r.get('Rarity') or 0), int(r.get('Weight') or 0)))
                    except Exception:
                        continue
                if tbl:
                    out[rid] = tbl
    except Exception as e:
        log('lottery table load failed:', e)
    _LOTTERY_TABLES = out
    return out

_LOTTERY_TABLES = None

def _lottery_rates(lottery_id):

    tbl = _lottery_tables().get(lottery_id)
    if tbl:
        return tbl
    return list(GACHA_RATES_FALLBACK)

def _roll_one(rates, pools):

    rarities = [r for r, _ in rates]
    weights = [w for _, w in rates]
    rar = random.choices(rarities, weights=weights, k=1)[0]
    pool = pools.get(rar)
    if not pool:
        for alt, _w in rates:
            if pools.get(alt):
                pool, rar = pools[alt], alt
                break
    if not pool:
        return None, None
    return random.choice(pool), rar

def _banner_up_ids(gacha_id):

    try:
        rows = db_get('GachaHold') or {}
        if isinstance(rows, dict):
            rows = rows.get('GachaHold') or []
        for banner in rows:
            if not isinstance(banner, dict):
                continue
            ups = (banner.get('GachaHold') or {}).get('PickupIds') or []
            if not ups:
                continue
            for gp in (banner.get('GachaPlayMasterDataList') or []):
                if isinstance(gp, dict) and gp.get('Id') == gacha_id:
                    return list(ups)
    except Exception:
        pass
    return []

def roll_gacha(count, main_lottery=None, last_lottery=None, up_ids=None, pools=None):

    if not pools:
        pools = _chara_pool()
    if not pools:
        return []
    main_rates = _lottery_rates(main_lottery)
    last_rates = _lottery_rates(last_lottery) if last_lottery else main_rates

    up = [u for u in (up_ids or []) if u in (pools.get(6) or []) + (pools.get(5) or [])]
    out = []
    for i in range(count):
        is_last = last_lottery and i == count - 1 and count > 1
        rates = last_rates if is_last else main_rates
        rid, rar = _roll_one(rates, pools)
        if not rid:
            break
        if up and rar in (5, 6) and random.random() < 0.7:
            same = [u for u in up if u in (pools.get(rar) or [])] or up
            rid = random.choice(same)
        out.append({'RewardType': 1, 'RewardId': rid,
                    'LotteryIdentifier': None, 'Count': 1, 'Rarity': rar})
    return out

def _banner_reward_map(gacha_id):

    group_ids = {}
    pool = {}
    try:
        rows = db_get('GachaHold') or {}
        if isinstance(rows, dict):
            rows = rows.get('GachaHold') or []
        rar = {}
        for rid, row in (master_tables().get('chara') or {}).items():
            try:
                r = int(row.get('Rarity') or 0)
            except Exception:
                r = 0
            if r:
                rar[rid] = r
        for banner in rows:
            if not isinstance(banner, dict):
                continue
            ids = [(gp or {}).get('Id') for gp in (banner.get('GachaPlayMasterDataList') or [])
                   if isinstance(gp, dict)]

            if (gacha_id not in ids
                    and str((banner.get('GachaHold') or {}).get('Id')) != gacha_id):
                continue
            for lst in (banner.get('GachaRewardMasterDataList') or []):
                for pos, rec in enumerate((lst.get('Records') or []), 1):
                    if not isinstance(rec, dict):
                        continue
                    cid = rec.get('RewardId')
                    if not cid or cid in group_ids:
                        continue
                    try:
                        gid = int(rec.get('RewardGroupId') or pos)
                    except Exception:
                        gid = pos
                    group_ids[cid] = gid
                    rr = rar.get(cid)
                    if rr:
                        pool.setdefault(rr, []).append(cid)
            break
    except Exception as e:
        log('banner reward map failed:', e)
    return pool, group_ids

def _reward_group_ids(gacha_id):

    out = {}
    try:
        rows = db_get('GachaHold') or {}
        if isinstance(rows, dict):
            rows = rows.get('GachaHold') or []
        for banner in rows:
            if not isinstance(banner, dict):
                continue
            ids = [(gp or {}).get('Id') for gp in (banner.get('GachaPlayMasterDataList') or [])
                   if isinstance(gp, dict)]
            if gacha_id not in ids:
                continue
            for lst in (banner.get('GachaRewardMasterDataList') or []):
                for _i, rec in enumerate((lst.get('Records') or []), 1):
                    if not isinstance(rec, dict):
                        continue
                    rid = rec.get('RewardId')
                    if rid and rid not in out:
                        try:
                            out[rid] = int(rec.get('RewardGroupId') or _i)
                        except Exception:
                            out[rid] = _i
            break
    except Exception as e:
        log('reward group id load failed:', e)
    return out

def _dup_ash_table():

    out = {}
    try:
        for rid, mast in (master_tables().get('game_setting') or {}).items():
            if not isinstance(mast, dict):
                continue
            m = re.search(r'exchange_duplicate_item_(\d+)$', str(rid))
            if not m:
                continue
            val = str(mast.get('Value') or '')
            parts = [x.strip() for x in val.split(',') if x.strip()]
            if len(parts) < 2:
                continue
            try:
                out[int(m.group(1))] = (parts[0], int(parts[1]))
            except Exception:
                continue
    except Exception as e:
        log('    dup-ash table load failed:', e)
    return out

def gacha_reward_payload(rewards, group_ids=None):

    DUP_ASH = _dup_ash_table()
    if not DUP_ASH:
        log('    ★ dup-ash table EMPTY -- game_setting.exchange_duplicate_item_* missing')
    charas = []
    exchanges = []
    for r in rewards:

        _gid = r.get('RewardGroupId')
        if _gid is None:
            _gid = (group_ids or {}).get(r['RewardId'])
        if _gid is None:
            _gid = 0
        charas.append({'RewardType': r['RewardType'], 'RewardId': r['RewardId'],
                       'LotteryIdentifier': r.get('LotteryIdentifier'),
                       'RewardGroupId': int(_gid),
                       'Count': r['Count']})
        dup = _grant_chara(r['RewardId'])
        item_id, cnt = DUP_ASH.get(r.get('Rarity'), ('item.terra_ash', 5))
        if dup:
            _grant_item(item_id, cnt)
        ent = _item_entity(item_id)
        exchanges.append({'ItemId': '',
                          'Item': ent,
                          'Count': cnt if dup else 0,
                          'IsOver': False})

    fixed = []
    if len(rewards) >= 10:
        _grant_item('item.gacha_point_0002', 10)
        fixed.append({'RewardType': 2, 'RewardId': 'item.gacha_point_0002',
                      'Count': 10})
    return {
        'GachaRewardMasterDataList': charas,
        'GachaOmakeRewardMasterDataList': [],

        'GachaFixedRewardMasterDataList': fixed,
        'GachaExchangeList': exchanges,
        'GachaCostDatas': [],
        'GachaStampRewardMasterDataList': {'Id': None, 'Data': None},
        'IsOmakeOrFixedOverflow': False,
        'IsStampOverflow': False,
    }

def _item_entity(item_id):

    item_id = _row_id(item_id)
    for x in (db_get('Item') or []):
        if isinstance(x, dict) and x.get('Id') == item_id:
            return x
    return {'UserId': user_id(), 'Id': item_id, 'Count': 0,
            'Records': None, 'IsRead': False, 'Ver': 0}

def _grant_chara(chara_id):

    try:
        rows = db_get('Chara')
        if not isinstance(rows, list):
            return False
        for c in rows:
            if isinstance(c, dict) and c.get('Id') == chara_id:
                c['Count'] = int(c.get('Count') or 0) + 1
                c['Ver'] = int(c.get('Ver') or 0) + 1
                touch(chara_id, c)
                db_put('Chara', rows)
                return True
        rows.append({'UserId': user_id(), 'Id': chara_id, 'Grade': 0, 'Level': 1,
                     'Experience': 0, 'Affinity': 1, 'AffinityExp': 0, 'Count': 1,
                     'Overlap': 0, 'ReceivedAt': _now_iso(), 'IsNewCharacter': True,
                     'SkillLevel': 1, 'ExSkillLevels': None, 'BlessLevel': 1,
                     'ReadFlag': None, 'Costume': None, 'Weapon': None, 'WS': None,
                     'Ura': None, 'Prefs': None, 'AffinityLimit': 0, 'AP': None,
                     'LimitOver': 0, 'Ver': 0})
        touch(chara_id, rows[-1])
        db_put('Chara', rows)
        return False
    except Exception as e:
        log('    gacha: grant chara failed', e)
        return False

def expired_items(now=None):

    if now is None:
        now = datetime.datetime.now(JST)
    out = {}
    detail = []
    items = db_get('Item')
    if not isinstance(items, list):
        return out, detail
    try:
        md = master_tables().get('item', {})
    except Exception:
        md = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        rid = it.get('Id')
        recs = it.get('Records') or {}
        if not isinstance(recs, dict) or not recs:
            continue
        m = md.get(rid) or {}
        days = int(m.get('LimitDays') or 0)
        if days <= 0:
            continue
        for key, cnt in list(recs.items()):
            k = str(key)
            try:
                if len(k) == 12:
                    got = datetime.datetime.strptime(k, '%Y%m%d%H%M').replace(tzinfo=JST)
                elif len(k) == 14:
                    got = datetime.datetime.strptime(k, '%Y%m%d%H%M%S').replace(tzinfo=JST)
                else:
                    continue
            except Exception:
                continue
            until = got + datetime.timedelta(days=days)
            if until < now:
                c = int(cnt or 0)
                out[rid] = out.get(rid, 0) + c
                detail.append((rid, k, c, until.strftime('%Y-%m-%d %H:%M')))
    return out, detail

def sweep_expired_items():

    exp, detail = expired_items()
    if not exp:
        return exp, detail, []
    items = db_get('Item')
    changed = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        rid = it.get('Id')
        if rid not in exp:
            continue
        recs = it.get('Records') or {}
        keep = {}
        dropped = 0
        for key, cnt in list(recs.items()):
            hit = [d for d in detail if d[0] == rid and d[1] == str(key)]
            if hit:
                dropped += int(cnt or 0)
            else:
                keep[str(key)] = cnt
        it['Count'] = max(0, int(it.get('Count') or 0) - dropped)
        it['Records'] = keep
        it['Ver'] = int(it.get('Ver') or 0) + 1
        touch(rid, it)
        changed.append(it)
    if changed:
        db_put('Item', items)
    return exp, detail, changed

def exchange_records(exchange_id, exchange_no):

    try:
        em = db_get('ExchangeMaster') or {}
    except Exception:
        return None, None, []
    for row in (em.get('Records') or []):
        ex = row.get('Exchange') or {}
        if str(ex.get('ResourceId') or ex.get('Id')) != exchange_id:
            continue
        tier = None
        for t in (ex.get('Records') or []):
            if int(t.get('No') or 0) == int(exchange_no):
                tier = t
                break
        group = None
        lists = row.get('RewardList') or []
        idx = int(exchange_no) - 1
        if 0 <= idx < len(lists):
            group = lists[idx]
        elif lists:
            group = lists[0]
        return ex, tier, ((group or {}).get('Records') or [])
    return None, None, []

ITEMGEN_LIMIT_KEY = 'item_generation_counts'

def _itemgen_limit(level=None):

    seq = []
    try:
        for _row in (master_tables().get('game_setting') or {}).values():
            if isinstance(_row, dict) and _row.get('ResourceId') == 'game_setting.' + ITEMGEN_LIMIT_KEY:
                raw = str(_row.get('Value') or '')
                seq = [int(x) for x in raw.replace(chr(10), ',').split(',') if x.strip().isdigit()]
                break
    except Exception:
        seq = []
    if not seq:
        seq = [60, 63, 66, 69, 72, 75, 78, 81, 84, 88,
               91, 94, 97, 100, 103, 106, 109, 112, 115, 120]
    if level is None:
        level = _lounge_itemgen_level()
    try:
        i = int(level) - 1
    except Exception:
        i = len(seq) - 1
    if i < 0:
        i = 0
    if i >= len(seq):
        i = len(seq) - 1
    return seq[i]

def _lounge_itemgen_level():

    try:
        rows = db_get('UserPossession') or []
        for r in (rows if isinstance(rows, list) else [rows]):
            if isinstance(r, dict) and r.get('Id') == 'lounge.Interior_level':
                recs = r.get('Records')
                if isinstance(recs, dict):

                    for k in ('item_create', 'itemCreate', 'ItemGeneration'):
                        v = recs.get(k)
                        if isinstance(v, int) and v > 0:
                            return v
    except Exception:
        pass

    try:
        rows = db_get('Lounge') or db_get('lounge_my')
        for r in (rows if isinstance(rows, list) else [rows]):
            if isinstance(r, dict):
                for k in ('ItemGenerationLevel', 'GenerationLevel', 'Level'):
                    v = r.get(k)
                    if isinstance(v, int) and v > 0:
                        return v
    except Exception:
        pass
    return 1

def _itemgen_seconds(item_id):

    try:
        row = (master_tables().get('item_generation') or {}).get(item_id)
        if not isinstance(row, dict):
            row = None
            for r in (master_tables().get('item_generation') or {}).values():
                if isinstance(r, dict) and r.get('ItemId') == item_id:
                    row = r
                    break
        t = str((row or {}).get('Time') or '')
        p = [int(x) for x in t.split(':')] if t else []
        if len(p) == 3:
            return max(1, p[0] * 3600 + p[1] * 60 + p[2])
    except Exception:
        pass
    return 1800

def _itemgen_generated(ent, now=None):

    if not isinstance(ent, dict):
        return 0
    try:
        cap = int(ent.get('GenerateCount') or 0)
    except Exception:
        cap = 0
    if cap <= 0:
        return 0
    start = _parse_iso(ent.get('GenerateAt'))
    if start is None:
        return 0
    now = now or datetime.datetime.now(JST)
    try:
        secs = int((now - start).total_seconds())
    except Exception:
        return 0
    if secs <= 0:
        return 0
    per = _itemgen_seconds(ent.get('ItemId'))
    return max(0, min(cap, secs // per))

def _itemgen_tick(ent, now=None):

    now = now or datetime.datetime.now(JST)
    got = _itemgen_generated(ent, now)
    have = int(ent.get('ReceiveCount') or 0)
    return max(0, got - have)

def _exchange_groups(exchange_id):

    try:
        em = db_get('ExchangeMaster') or {}
    except Exception:
        return None, [], []
    for row in (em.get('Records') or []):
        ex = row.get('Exchange') or {}
        if str(ex.get('ResourceId') or ex.get('Id')) != exchange_id:
            continue
        tiers = []
        for i, t in enumerate(ex.get('Records') or []):
            try:
                tiers.append((int(t.get('No') or 0), int(t.get('NextNo') or 0), i))
            except Exception:
                continue
        return ex, tiers, (row.get('RewardList') or [])
    return None, [], []

def _exchange_fix_tier(hit, tiers=None):

    if not isinstance(hit, dict):
        return 0
    if tiers is None:
        tiers = _exchange_groups(hit.get('Id'))[1]
    if not tiers:
        return 0
    if int(hit.get('No') or 0) in [t[0] for t in tiers]:
        return 0
    old = hit.get('No')
    hit['No'] = tiers[0][0]
    log('    exchange tier %s: No %s -> %s (not a declared tier)'
        % (hit.get('Id'), old, hit['No']))
    return 1

def _exchange_advance_tier(hit):

    if not isinstance(hit, dict):
        return 0
    ex, tiers, groups = _exchange_groups(hit.get('Id'))
    if not tiers:
        return 0
    cur_no = int(hit.get('No') or 0)
    cur = None
    for t in tiers:
        if t[0] == cur_no:
            cur = t
            break
    if cur is None:
        cur = tiers[0]
    tno, nxt, idx = cur
    if not nxt:
        return 0
    if not (0 <= idx < len(groups)):
        return 0
    recs = hit.get('Records') or {}
    limited = done = 0
    for r in (groups[idx].get('Records') or []):
        try:
            lim = int(r.get('LimitCount') or 0)
        except Exception:
            lim = 0
        if lim <= 0:
            continue
        limited += 1
        rno = int(r.get('No') or 0)
        if int(recs.get('%s_%s' % (tno, rno)) or 0) >= lim:
            done += 1
    if limited and done >= limited:
        hit['No'] = nxt
        return 1
    return 0

def exchange_reward(exchange_id, exchange_no, reward_no):

    ex, tier, recs = exchange_records(exchange_id, exchange_no)
    for r in recs:
        if int(r.get('No') or 0) == int(reward_no):
            return (r.get('RewardId'), r.get('RewardParam'),
                    int(r.get('Count') or 0),
                    int(r.get('CostCount1') or 0),
                    int(r.get('CostCount2') or 0)), ex
    return None, ex

def _pick_reward_step(records, next_step, master=None):

    steps = sorted({r['Step'] for r in records if isinstance(r, dict) and 'Step' in r})
    if not steps:
        return None
    eligible = [s for s in steps if s <= next_step]
    return eligible[-1] if eligible else steps[0]
def _master_at(s):

    if not s:
        return None
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(s))
    if not m:
        return None
    try:
        return datetime.datetime(*[int(x) for x in m.groups()], tzinfo=JST)
    except Exception:
        return None

def _login_bonus_scheduled(mast, now=None):

    if not isinstance(mast, dict):
        return False
    now = now or datetime.datetime.now(JST)
    if int(mast.get("LoginBonusType") or 0) == 5:
        return False
    close_at = _master_at(mast.get("CloseAt"))
    if close_at is not None and now > close_at:
        return False
    open_at = _master_at(mast.get("OpenAt"))
    if open_at is not None and now < open_at:
        return False
    return True

def _login_bonus_master_valid(mast, now=None):

    if not _login_bonus_scheduled(mast, now):
        return False
    now = now or datetime.datetime.now(JST)
    """LoginBonusDomainService.IsValidLoginBonusMasterDataEntity (ISIL 038-260).

    The real rule, read off the instruction stream:
        InitTime([rdi+80])       OpenAt  -> if set and now <  OpenAt  -> INVALID
        EndTime ([rdi+72])       CloseAt -> if set and now >  CloseAt -> INVALID
        [rdi+96] == 5 (VIP)               -> 需要 context.VipDay(UserEntity+0x74) > 0
    Any other type is valid as long as the schedule brackets `now`.

    Why this matters: the client does NOT treat "no entity row" as "nothing to
    claim".  IsExistNotReceivedLoginBonus() jumps to `return true` (ISIL 138-150)
    the moment the entity lookup comes back null, so every valid master row with
    no stored row keeps the daily-reset dialog alive forever.
    """

    if int(mast.get("LoginBonusType") or 0) == 2:
        _days = int(mast.get("ComebackDays") or 0)
        try:
            _u = db_get("User")
            if isinstance(_u, list):
                _u = _u[0] if _u else {}
            if not isinstance(_u, dict):
                _u = {}
            _last = _u.get("LastLoginAt")
            _last_dt = _master_at(_last) if _last else None
        except Exception:
            _last_dt = None
        if _last_dt is None:
            return False
        if (now - _last_dt).days < _days:
            return False
    return True

def seed_login_bonuses():

    try:
        tables = master_tables().get("login_bonus", {})
        if not tables:
            return []
        rows = db_get("LoginBonus")
        if not isinstance(rows, list):
            rows = []
        have = set(r.get("Id") for r in rows if isinstance(r, dict))
        now_dt = datetime.datetime.now(JST)
        now    = now_dt.replace(microsecond=0).isoformat()

        _day = now_dt - (datetime.timedelta(days=1) if now_dt.hour < GAME_RESET_HOUR
                         else datetime.timedelta(0))
        prev = _day.replace(hour=GAME_RESET_HOUR, minute=0, second=0,
                            microsecond=0) - datetime.timedelta(days=1)
        prev = prev.isoformat()
        added = []
        for rid, mast in tables.items():
            if rid in have:
                continue

            if not _login_bonus_scheduled(mast, now_dt):
                continue

            comeback = int(mast.get("LoginBonusType") or 0) == 2

            scheduled = _login_bonus_scheduled(mast, now_dt)
            added.append({
                "UserId":      user_id(),
                "Id":          rid,
                "ReceiveStep": 0,

                "ReceiveAt":   prev if _login_bonus_master_valid(mast, now_dt) else now,
                "RepeatCount": 0,
                "Finished":    False,
                "Ver":         1,
            })
        if added:
            rows.extend(added)
            db_put("LoginBonus", rows)
            log("  login-bonus seed: created %d row(s) -> %s"
                % (len(added), ", ".join(r["Id"] for r in added)))
        return added
    except Exception as e:
        log("  login-bonus seed failed:", e)
        return []

def claimable_login_bonuses(rows=None):

    if rows is None:
        rows = db_get('LoginBonus')
    if not isinstance(rows, list):
        return []
    today = _ingame_day()
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get('Finished') is True:
            continue
        last = _ingame_day_of(r.get('ReceiveAt'))
        if last is None or last != today:
            out.append(r)
    return out
def _lb_all_rows():

    rows = db_get('LoginBonus')
    return rows if isinstance(rows, list) else []

def claim_login_bonuses():

    seed_login_bonuses()
    rows = db_get('LoginBonus')
    if not isinstance(rows, list):
        return [], []
    tables   = master_tables()
    lb_table = tables.get('login_bonus', {})
    rw_table = tables.get('login_bonus_reward', {})
    now      = _now_iso()
    received = []
    changed  = []

    _lb_now = datetime.datetime.now(JST)
    _due = [r for r in claimable_login_bonuses(rows)
            if _login_bonus_master_valid(lb_table.get(r.get("Id")) or {}, _lb_now)]
    _lb_tbl = master_tables().get("login_bonus", {})
    _due = sorted(_due,
                  key=lambda _x: (int((_lb_tbl.get(_x.get("Id")) or {})
                                      .get("LoginBonusType") or 0),
                                  str(_x.get("Id") or "")))
    for r in _due:
        rid  = r.get('Id')
        mast = lb_table.get(rid)
        if not isinstance(mast, dict):

            r['Finished'] = True
            r['ReceiveAt'] = now
            r['Ver'] = int(r.get('Ver') or 0) + 1
            changed.append(r)
            log('  claim: %s has no master row -> marked finished' % rid)
            continue

        step_limit = int(mast.get('StepLimit') or 0)
        repeat     = int(mast.get('Repeat') or 0)
        next_step  = int(r.get('ReceiveStep') or 0) + 1

        if step_limit and next_step > step_limit:
            if repeat:
                next_step = 1
                r['RepeatCount'] = int(r.get('RepeatCount') or 0) + 1
            else:
                r['Finished'] = True
                r['ReceiveAt'] = now
                r['Ver'] = int(r.get('Ver') or 0) + 1
                changed.append(r)
                log('  claim: %s finished (step limit %d reached)' % (rid, step_limit))
                continue

        table     = rw_table.get(mast.get('Reward'))
        records   = (table or {}).get('Records') or []
        step      = _pick_reward_step(records, next_step)
        picked    = [x for x in records if isinstance(x, dict) and x.get('Step') == step]

        rewards = []
        for x in picked:
            item  = x.get('Reward')
            count = int(x.get('Count') or 0)
            after = _grant_item(item, count)
            rewards.append({'RewardId': item, 'AfterCount': after,
                            'AddCount': count, 'IsOver': False})

        received.append({'ResourceId': rid,
                         'ReceivedStep': next_step,
                         'RewardStep': step if step is not None else next_step,
                         'ReceivedRewards': rewards,
                         'ReceivedOopartPluses': []})

        r['ReceiveStep'] = next_step
        r['ReceiveAt']   = now
        r['Ver']         = int(r.get('Ver') or 0) + 1
        changed.append(r)
        touch(rid, r)

    _stamped = 0
    for _r in rows:
        if not isinstance(_r, dict) or _r.get('Finished') is True:
            continue
        if _r in changed:
            continue
        _r['ReceiveAt'] = now
        _r['Ver'] = int(_r.get('Ver') or 0) + 1
        changed.append(_r)
        touch(str(_r.get('Id') or ''), _r)
        _stamped += 1
    if _stamped:
        log('  login-bonus: stamped ReceiveAt on %d non-paid row(s)' % _stamped)

    if changed:
        db_put('LoginBonus', rows)
        log('  claimed %d login bonus(es) -> %d reward row(s)' % (len(received), len(received)))
    return received, changed
def save_home_setting(home_setting_id, records):

    rows = db_get('HomeSetting')
    if not isinstance(rows, list):
        rows = []
    ent = None
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == home_setting_id:
            ent = r
            break
    if ent is None:
        ent = {'UserId': user_id(), 'Id': home_setting_id, 'Records': [], 'Ver': 0}
        rows.append(ent)

    if not records:
        log('  REFUSE empty home setting records for %s (keeping %d existing)'
            % (home_setting_id, len(ent.get('Records') or [])))
        return ent

    try:

        _owned = owned_item_ids()
        _defs = master_tables().get('game_setting') or {}
        _fallback = {
            'BackLayerId': str((_defs.get('game_setting.homeset_default_back') or {}).get('Value') or '').strip(),
            'FrontLayerId': str((_defs.get('game_setting.homeset_default_chara') or {}).get('Value') or '').strip(),
        }
        _fixed = []
        for _rec in records:
            if not isinstance(_rec, dict):
                continue
            for _k in ('FrontLayerId', 'BackLayerId', 'IllustrationLayerId', 'EffectLayerId'):
                _v = _rec.get(_k)
                if not isinstance(_v, str) or not _v:
                    continue
                if _v.startswith('chara.'):
                    continue
                if _v in _owned:
                    continue
                _alt = _fallback.get(_k) or ''
                log('  home setting: %s=%s not owned -> %s'
                    % (_k, _v, _alt or '(cleared)'))
                _rec[_k] = _alt if (_alt and (_alt in _owned or _alt.startswith('chara.'))) else ''
            _fixed.append(_rec)
        if _fixed:
            records = _fixed
    except Exception as _e:
        log('  home setting ownership check failed: %s' % _e)
    ent['Records'] = records
    ent['Ver'] = int(ent.get('Ver') or 0) + 1
    db_put('HomeSetting', rows)
    log('  saved home setting %s (%d records, Ver=%d)'
        % (home_setting_id, len(records), ent['Ver']))
    return ent

PREFIX_TO_MEMBER = {
    'quest.': 'QuestRecords',
    'score_attack.': 'ScoreAttacks',
    'item.': 'Items',
    'present.': 'Presents',
    'login_bonus.': 'LoginBonuses',
    'loginbonus_': 'LoginBonuses',
    'chara.': 'Characters',
    'oopart.': 'Ooparts',
    'team.': 'Teams',
    'exchange.': 'Exchanges',
    'minigame.': 'MiniGames',
    'home_setting.': 'HomeSettings',
    'item_generation.': 'ItemGenerations',
    'mission.': 'Missions',
    'event.': 'MissionPasses',
    'unique_quest.': 'UniqueQuests',
    'challenge_mission.': 'ChallengeMissions',
    'user_message.': 'UserMessages',
    'historia.': 'Historias',
    'fragment.': 'Fragments',
    'multi_team_quest.': 'MultiTeamQuests',
    'big_battle.': 'BigBattles',
}

_TL = threading.local()

def _touched():
    d = getattr(_TL, 'touched', None)
    if d is None:
        d = _TL.touched = {}
    return d

def _touched_singleton():
    d = getattr(_TL, 'touched_singleton', None)
    if d is None:
        d = _TL.touched_singleton = {}
    return d

def _mark_orb_box_changed():

    _touched_flag()['orb_box_changed'] = True

def _touched_flag():

    d = getattr(_TL, 'flags', None)
    if d is None:
        d = _TL.flags = {}
    return d

def touch(resource_id, entity, member=None):

    if not isinstance(entity, dict):
        return
    rid = str(entity.get('Id') or resource_id or '')
    if member is None:
        if rid.startswith('lyric.god.'):
            member = 'GodLyrics'
        elif rid.startswith('lyric.'):
            member = 'AngelLyrics'
        else:
            for pre, mem in PREFIX_TO_MEMBER.items():
                if rid.startswith(pre):
                    member = mem
                    break
    if member is None:
        return

    buf = _touched()
    lst = buf.setdefault(member, [])
    for _i, _e in enumerate(lst):
        if isinstance(_e, dict) and str(_e.get('Id') or '') == rid:
            lst[_i] = entity
            break
    else:
        lst.append(entity)
    log('    TOUCH %s -> %s (%s)' % (member, rid, entity.get('Ver')))

def touch_singleton(member, entity):

    if isinstance(entity, dict):
        _touched_singleton()[member] = entity
        log('    TOUCH %s (singleton)' % member)

_POSSESSION_MEMBERS = ('UserId', 'Id', 'ReadFlag', 'Records', 'Ver')

_USER_UNDECLARED = ('NameChangedAt', 'TutorialStep')

def _fix_user_row(ent):

    if not isinstance(ent, dict):
        return ent
    for k in _USER_UNDECLARED:
        ent.pop(k, None)
    if not isinstance(ent.get('Tutorial'), str):

        ent['Tutorial'] = '0' if ent.get('Tutorial') in (None, {}) else str(ent.get('Tutorial'))
    if not isinstance(ent.get('ReadFlag'), list):
        ent['ReadFlag'] = ent.get('ReadFlag') if isinstance(ent.get('ReadFlag'), list) else []
    return ent

def _fix_possession_row(ent):

    if not isinstance(ent, dict):
        return ent
    out = {}
    for k in _POSSESSION_MEMBERS:
        if k in ent:
            out[k] = ent[k]

    if not isinstance(out.get('Records'), dict):
        out['Records'] = None
    elif not out['Records']:

        out['Records'] = None
    if not isinstance(out.get('ReadFlag'), bool):
        out['ReadFlag'] = bool(out.get('ReadFlag') or False)
    out.setdefault('Ver', 0)
    if not out.get('UserId'):
        out['UserId'] = user_id()
    return out

def _jewel_batch_key(dt=None):

    d = dt or datetime.datetime.now(JST)
    return d.strftime('%Y%m%d%H%M')

def _sync_paid_jewel_records(ent):

    try:
        count = int(ent.get('Count') or 0)
    except Exception:
        count = 0
    rec = ent.get('Records')
    if isinstance(rec, dict) and rec:

        try:
            total = sum(int(v) for v in rec.values())
        except Exception:
            total = None
        if total != count:
            ent['Records'] = {_jewel_batch_key(): count} if count > 0 else None
        return ent
    if count <= 0:
        ent['Records'] = None
        return ent
    ent['Records'] = {_jewel_batch_key(): count}
    return ent

def _fix_item_records(ent):

    if not isinstance(ent, dict):
        return ent
    mid = str(ent.get('Id') or '')

    if mid in ('item.jewel_paid', 'item.jewel_paid_dmm', 'item.jewel_paid_googleplay'):
        _sync_paid_jewel_records(ent)
        return ent
    rec = ent.get('Records')
    if rec == {} or rec is None:
        m = (master_tables().get('item') or {}).get(mid)
        if isinstance(m, dict) and int(m.get('LimitDays') or 0) <= 0:
            if rec is None:
                return ent
            ent['Records'] = None
    return ent

def _sync_value_guard(path, body):

    if not isinstance(body, dict):
        return body
    sync = body.get('SyncData')
    if not isinstance(sync, dict):
        return body

    if False and path in BEHAVIOR_NULL_ONLY:
        pass

    LIST_MEMBERS = (
        'AngelLyrics', 'BigBattles', 'ChallengeMissions', 'Characters', 'Exchanges',
        'Followers', 'Followings', 'Fragments', 'GodLyrics', 'Historias',
        'HomeSettings', 'ItemGenerations', 'Items', 'LoginBonuses', 'MiniGames',
        'MissionPasses', 'Missions', 'MultiTeamQuests', 'Ooparts', 'Presents',
        'QuestRecords', 'ScoreAttacks', 'Teams', 'UniqueQuests', 'UserMessages',
        'UserPossession',
    )
    fixed = 0
    for member in LIST_MEMBERS:
        if member in sync and sync[member] is None:
            sync[member] = []
            fixed += 1
    if fixed:
        log('    SYNC-GUARD %s: %d list member(s) null -> []' % (path, fixed))
    return body

def flush_touched(sync):

    buf = _touched()
    singles = _touched_singleton()
    pending = list(buf.items())
    single_items = list(singles.items())
    buf.clear()
    singles.clear()

    delivered = []
    for member, ents in pending:
        if not ents:
            continue
        if member in ('Items', 'Item'):
            for e in ents:
                _fix_item_records(e)
        elif member == 'UserPossession':

            def _norm_possession(e):
                if not isinstance(e, dict):
                    return e
                if (e.get('Id') == _ORB_BOX_ID or 'Slots' in e or 'PSlots' in e
                        or 'Stack' in e):
                    e.pop('UserId', None)
                    e.pop('Records', None)
                    e.pop('ReadFlag', None)
                    for _k, _d in (('Key', None), ('Slots', {}), ('PSlots', []),
                                   ('Stack', {})):
                        if _k not in e:
                            e[_k] = _d
                    return _orb_slot_sync(e)
                return _fix_possession_row(e)
            ents = [_norm_possession(e) for e in ents]
        elif member == 'User':
            ents = [_fix_user_row(e) for e in ents]
        sync[member] = ents
        delivered.append((member, ents))
    total = sum(len(v) for _, v in delivered)
    if total:
        log('    ECHO %d entity(ies) into SyncData: %s'
            % (total, ', '.join('%s=%d' % (k, len(v)) for k, v in delivered if v)))
    for member, ent in single_items:
        if member == 'User':
            ent = _fix_user_row(ent)
        sync[member] = ent
        log('    ECHO %s (singleton) into SyncData' % member)

    try:
        if _touched_flag().get('path') in BEHAVIOR_NULL_ONLY:
            if sync.get('UserBehavior') is not None:
                sync['UserBehavior'] = None
                log('    SYNC UserBehavior -> null (BEHAVIOR_NULL_ONLY)')
    except Exception:
        pass
    return sync

STAMINA_RECOVERY_LIMIT = 150
STAMINA_HARD_MAX = 9999

def stamina_amount(item_id):

    m = master_tables().get('item', {}).get(item_id)
    if not isinstance(m, dict):
        return 0
    if int(m.get('ItemTypeValue') or 0) != 14:
        return 0
    return int(m.get('ItemTypeParameter') or 0)

def parse_use_items(req):

    out = []
    if not isinstance(req, dict):
        return out
    items = req.get('UseItems') or req.get('useItems')
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                rid = it.get('Item') or it.get('item') or it.get('ItemId') or it.get('itemId')
                if rid:
                    out.append((rid, int(it.get('Count') or it.get('count') or 1)))
    if not out:
        rid = req.get('itemId') or req.get('ItemId') or req.get('resourceId')
        if rid:
            out.append((rid, int(req.get('Count') or req.get('count') or 1)))
    return out

def recover_stamina(item_id, amount):

    global _USER_CACHE
    u = db_get('User')
    if not isinstance(u, dict):
        u = {}
    before = int(u.get('Stamina') or 0)
    after = min(STAMINA_HARD_MAX, before + amount)
    u['Stamina'] = after
    u['StaminaUpdateTime'] = int(time.time())
    db_put('User', u)
    _USER_CACHE = u
    touch_singleton('User', u)
    log('    stamina %d -> %d (item %s)' % (before, after, item_id))
    return before, after

def mark_possession_read(possession_id):

    if not possession_id:
        return None
    rows = db_get('UserPossession')
    if not isinstance(rows, list):
        rows = []
    hit = None
    for r in rows:
        if isinstance(r, dict) and r.get('Id') == possession_id:
            hit = r
            break
    if hit is None:

        hit = {'UserId': user_id(), 'Id': possession_id,
               'ReadFlag': False, 'Records': None, 'Ver': 0}
        rows.append(hit)
        log('    possession: created %s' % possession_id)

    hit['ReadFlag'] = True
    hit['Ver'] = int(hit.get('Ver') or 0) + 1
    db_put('UserPossession', rows)
    touch(possession_id, hit, member='UserPossession')
    return hit

def db_put(name, obj):

    if isinstance(obj, list):
        bad = [r for r in obj
               if isinstance(r, dict) and 'Id' in r and r.get('Id') is None]
        if bad:
            log('  !! db_put(%s) DROPPED %d row(s) with Id=null (self-heal) -- %s'
                % (name, len(bad), json.dumps(bad[0], ensure_ascii=False)[:180]))
            obj = [r for r in obj if not (isinstance(r, dict)
                                          and 'Id' in r and r.get('Id') is None)]

    if isinstance(obj, list):
        filled = 0
        for r in obj:
            if isinstance(r, dict) and 'UserId' in r and r.get('UserId') is None:
                r['UserId'] = user_id()
                filled += 1
        if filled:
            log('  !! db_put(%s) FILLED %d row(s) with UserId=null -> %s (self-heal)'
                % (name, filled, user_id()))
    with LOCK:

        _tmp = db_path(name) + '.tmp'
        with io.open(_tmp, 'w', encoding='utf-8') as _fh:
            _fh.write(json.dumps(obj, ensure_ascii=False))
            _fh.flush()
            os.fsync(_fh.fileno())
        os.replace(_tmp, db_path(name))

def seed():

    n = 0
    if not os.path.isdir(CAP):
        return n
    for f in os.listdir(CAP):
        if not f.endswith('.json'): continue
        name = f[:-5]
        if os.path.exists(db_path(name)): continue
        try:
            data = json.loads(io.open(os.path.join(CAP, f), encoding='utf-8').read())
            db_put(name, data); n += 1
        except Exception as e:
            log('seed skip', f, e)
    return n

JEWEL_FREE = 40000000
JEWEL_PAID = 40000000

def ensure_jewels():

    try:
        rows = db_get('Item')
    except Exception as e:
        log('ensure_jewels: db_get failed', e)
        return 0
    if not isinstance(rows, list):
        return 0
    uid = user_id()
    touched = 0
    for rid, amount in ((_row_id('item.jewel_free'), JEWEL_FREE),
                        (_row_id('item.jewel_paid'), JEWEL_PAID)):
        hit = None
        for r in rows:
            if not isinstance(r, dict):
                continue

            if r.get('Id') == rid or (rid == 'item.jewel_paid_dmm'
                                      and r.get('Id') == 'item.jewel_paid'):
                hit = r
                break
        if hit is None:
            rows.append({'UserId': uid, 'Id': rid, 'Count': amount,
                         'Records': {}, 'IsRead': False, 'Ver': 1})
            touched += 1
            continue
        hit['Id'] = rid
        try:
            cur = int(hit.get('Count') or 0)
        except Exception:
            cur = 0
        if cur < amount:
            hit['Count'] = amount
            hit['Ver'] = int(hit.get('Ver') or 0) + 1
            touched += 1
    if touched:
        db_put('Item', rows)
        log('    jewels: topped up to %d free / %d paid (%d row(s))'
            % (JEWEL_FREE, JEWEL_PAID, touched))
    return touched

_TOKEN_LOCK = threading.Lock()
_TOKEN_SEQ = [0]

def _access_token():
    with _TOKEN_LOCK:
        _TOKEN_SEQ[0] += 1
        n = _TOKEN_SEQ[0]
    return "localsession0001@%032d@0" % n

def empty_sync():

    sync = {'Id':None,'User':None,'QuestRecords':[],'ScoreAttacks':[],'Items':[],
            'Presents':[],'LoginBonuses':[],'Characters':[],'UserPossession':[],
            'Followers':[],'Followings':[],'Profile':None,'Ooparts':[],'Teams':[],
            'AngelLyrics':[],'GodLyrics':[],'Exchanges':[],'MiniGames':[],'HomeSettings':[],
            'ItemGenerations':[],'Missions':[],'UserBehavior':None,'MissionPasses':[],
            'UniqueQuests':[],'ChallengeMissions':[],'UserMessages':[],'Historias':[],
            'Fragments':[],'MultiTeamQuests':[],'BigBattles':[],'IsOverItem':False,
            'OverItemIds': None, 'OopartPluses': None, 'IsUpdateCeleb': False, 'Ver': 0}

    try:
        if _touched_flag().get('path') in ACTIVITY_BEHAVIOR:
            _brow, _brows = _behavior_row()
            _behavior_rollover(_brow)
            _brow['Ver'] = int(_brow.get('Ver') or 0) + 1
            if _brows is None:
                db_put('UserBehavior', _brow)
            else:
                db_put('UserBehavior', _brows)
            sync['UserBehavior'] = _behavior_clean(dict(_brow))
    except Exception:
        pass
    return sync

NEW_ACCOUNT_FIXEDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
                     18, 19, 21, 22, 23, 24, 25, 26, 27, 28, 29]
NEW_ACCOUNT_REDCOUNT = 5000

NEW_ACCOUNT_FURNITURES = [
    'item.furniture.cafe_wall_1', 'item.furniture.cafe_floor_1',
    'item.furniture.cafe_light_1', 'item.furniture.cafe_sofa1_1',
    'item.furniture.cafe_sofa2_1', 'item.furniture.cafe_desk_1',
    'item.furniture.cafe_chest_1', 'item.furniture.garden_swing_1',
    'item.furniture.garden_tent_1', 'item.furniture.garden_gazebo_1',
    'item.furniture.garden_street_light_1', 'item.furniture.garden_fence_1',
    'item.furniture.garden_flower_1', 'item.furniture.garden_parasol_1',
    'item.furniture.gamecenter_floor_1', 'item.furniture.gamecenter_light_1',
    'item.furniture.gamecenter_spotlight_1', 'item.furniture.gamecenter_bridge_1',
    'item.furniture.cafe_chair_1',
]

NEW_ACCOUNT_LOUNGE_SLOTS = [
    (1, 'item.furniture.cafe_wall_1'),
    (2, 'item.furniture.cafe_floor_1'),

    (4, 'item.furniture.cafe_sofa1_1'),
    (5, 'item.furniture.cafe_sofa2_1'),
    (6, 'item.furniture.cafe_desk_1'),
    (7, 'item.furniture.cafe_chest_1'),
    (11, 'item.furniture.garden_swing_1'),
    (12, 'item.furniture.garden_tent_1'),
    (13, 'item.furniture.garden_gazebo_1'),
    (14, 'item.furniture.garden_street_light_1'),
    (15, 'item.furniture.garden_fence_1'),
    (16, 'item.furniture.garden_flower_1'),
    (17, 'item.furniture.garden_parasol_1'),
    (21, 'item.furniture.gamecenter_floor_1'),
    (22, 'item.furniture.gamecenter_light_1'),

    (24, 'item.furniture.gamecenter_bridge_1'),
    (32, 'item.furniture.cafe_chair_1'),
]

def default_lounge_layout():

    poss = db_get('UserPossession')
    owned = set()
    if isinstance(poss, list):
        owned = set(str(x.get('Id')) for x in poss if isinstance(x, dict))
    lay = {}

    md = None
    try:
        md = master_tables().get('item') or {}
    except Exception:
        md = None
    for slot, fid in NEW_ACCOUNT_LOUNGE_SLOTS:
        if fid not in owned:
            continue
        if md is not None and fid not in md:
            log('    lounge layout: skipping %s (absent from masterdata)' % fid)
            continue
        lay['furniture.%d' % slot] = fid
    costumes = ','.join(NEW_ACCOUNT_LOUNGE_COSTUMES)
    lay['randomIds'] = costumes
    lay['costumeIds'] = costumes
    lay['settingCharaIds'] = ''
    return lay

NEW_ACCOUNT_LOUNGE_COSTUMES = [
    'chara_costume.1.1', 'chara_costume.2.1', 'chara_costume.3.1',
    'chara_costume.4.1', 'chara_costume.5.1',
]

def _initial_furniture_ids():

    ids = []
    try:
        gs = master_tables().get('game_setting') or {}
        row = gs.get('game_setting.initial_furnitures') or {}
        ids.extend([x.strip() for x in str(row.get('Value') or '').split(',') if x.strip()])
        for key in ('game_setting.homeset_default_back', 'game_setting.homeset_default_clock'):
            v = str((gs.get(key) or {}).get('Value') or '').strip()
            if v:
                ids.append(v)
    except Exception:
        pass
    if not ids:
        ids = list(NEW_ACCOUNT_FURNITURES)

    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out

def grant_initial_furnitures():

    ids = _initial_furniture_ids()
    if not ids:
        return 0
    rows = db_get('UserPossession')
    have = set()
    if isinstance(rows, list):
        have = set(str(x.get('Id')) for x in rows if isinstance(x, dict))
    added = 0
    for fid in ids:
        if fid in have:
            continue

        if _grant_unlock_item(fid):
            added += 1
    if added:
        log('    initial furnitures: granted %d possession row(s)' % added)
    return added
NEW_ACCOUNT_REDCOUNT_AT = "20260929"

NEW_ACCOUNT_SETUP_POSSESSION = ("info_reset_at", "enemy_kill_count", "enemy_kill_no")

NEW_ACCOUNT_GIFTS = [
    {"RewardId": "item.jewel_free", "RewardParam": "", "Count": 1000,
     "Description": "事前登録ミニゲームの総獲得Pt達成報酬"},
    {"RewardId": "item.gachaticket_registration_2", "RewardParam": "", "Count": 2,
     "Description": "事前登録ミニゲームの総獲得Pt達成報酬"},
    {"RewardId": "item.gold", "RewardParam": "", "Count": 200000,
     "Description": "事前登録ミニゲームの総獲得Pt達成報酬"},
    {"RewardId": "item.gachaticket_registration_1", "RewardParam": "", "Count": 10,
     "Description": "事前登録ミニゲームの総獲得Pt達成報酬"},
    {"RewardId": "item.affinity_5_3", "RewardParam": "", "Count": 15,
     "Description": "事前登録ミニゲームの総獲得Pt達成報酬"},
    {"RewardId": "item.charaexperience_4", "RewardParam": "", "Count": 40,
     "Description": "事前登録ミニゲームの総獲得Pt達成報酬"},
    {"RewardId": "stamp.stamp_0007", "RewardParam": "", "Count": 1,
     "Description": "SDGsサばいば～で新規素材を使ってしまったお詫び"},
    {"RewardId": "item.jewel_free", "RewardParam": "", "Count": 500,
     "Description": "リリース500日記念特別プレゼント"},
    {"RewardId": "item.skip_ticket_A", "RewardParam": "", "Count": 500,
     "Description": "リリース500日記念特別プレゼント"},
    {"RewardId": "item.recover_2", "RewardParam": "", "Count": 5,
     "Description": "リリース500日記念特別プレゼント"},
    {"RewardId": "item.affinity_6_1", "RewardParam": "", "Count": 5,
     "Description": "リリース500日記念特別プレゼント"},
    {"RewardId": "item.box.select.59", "RewardParam": "", "Count": 5,
     "Description": "リリース500日記念特別プレゼント"},
    {"RewardId": "item.illust.20", "RewardParam": "", "Count": 1,
     "Description": "みんな応援ありがとう！<br>大・大・大好き！"},
    {"RewardId": "item.illust.24", "RewardParam": "", "Count": 1,
     "Description": "特別なホーム背景のプレゼントです。"},
    {"RewardId": "item.illust.27", "RewardParam": "", "Count": 1,
     "Description": "浴衣、どう……かな？似合ってるかな？<br>ふふっ、よかった。さぁ、皆で一緒に花火を見よう"},
    {"RewardId": "item.effect.14", "RewardParam": "", "Count": 1,
     "Description": "浴衣、どう……かな？似合ってるかな？<br>ふふっ、よかった。さぁ、皆で一緒に花火を見よう"},
    {"RewardId": "item.gachaticket_common_10", "RewardParam": "", "Count": 1,
     "Description": "一部キャンペーンに関するお詫び"},
    {"RewardId": "item.illust.8", "RewardParam": "", "Count": 1,
     "Description": "天使たちからのクリスマスプレゼント"},
    {"RewardId": "item.jewel_free", "RewardParam": "", "Count": 3000,
     "Description": "事前登録25万人突破報酬"},
    {"RewardId": "item.gachaticket_registration_2", "RewardParam": "", "Count": 1,
     "Description": "事前登録25万人突破報酬"},
    {"RewardId": "item.illust.9", "RewardParam": "", "Count": 1,
     "Description": "あけましておめでとうございます。2025年 元旦"},
    {"RewardId": "item.illust.10", "RewardParam": "", "Count": 1,
     "Description": "天使たちからバレンタインの贈り物"},
    {"RewardId": "item.clock.14", "RewardParam": "", "Count": 1,
     "Description": "新ジョブ「アーティスト」追加記念プレゼント"},
    {"RewardId": "item.illust.11", "RewardParam": "", "Count": 1,
     "Description": "ホワイトでぇいのプレゼントでぇい。"},
    {"RewardId": "item.illust.12", "RewardParam": "", "Count": 1,
     "Description": "いつも応援ありがとうございます。これからもっとパワーアップしていくからね！"},
    {"RewardId": "frame.4", "RewardParam": "", "Count": 1,
     "Description": "プロフィール画面の装飾機能の実装記念。<br>さっそく飾ってみよう！"},
    {"RewardId": "item.illust.13", "RewardParam": "", "Count": 1,
     "Description": "お菓子の代わりにプレゼントをどうぞ！"},
    {"RewardId": "item.illust.14", "RewardParam": "", "Count": 1,
     "Description": "祝！１周年！　みんなと一緒に育ててきたこの世界！　これからもよろしくお願いします！"},
    {"RewardId": "item.furniture.cafe_desk_101", "RewardParam": "", "Count": 1,
     "Description": "１周年を記念して、ごきげんな家具をプレゼント！　喫茶『鳩』に飾ってみてね。"},
    {"RewardId": "item.illust.17", "RewardParam": "", "Count": 1,
     "Description": "きっと、会える。１周年を記念して特別なホーム背景のプレゼントです。"},
    {"RewardId": "item.jewel_free", "RewardParam": "", "Count": 1000,
     "Description": "本日、12月4日はほんとの１周年！<br>いつも励みになります！　開発一同"},
    {"RewardId": "item.bg.15", "RewardParam": "", "Count": 1,
     "Description": "明日はクリスマス！天使たちが準備して待ってるよ！<br>楽しみにしててね！"},
    {"RewardId": "item.illust.18", "RewardParam": "", "Count": 1,
     "Description": "メリクリ！　天使たちと一緒に聖夜を楽しんでね！"},
    {"RewardId": "item.affinity_5_3", "RewardParam": "", "Count": 25,
     "Description": "Happy Holidays !<br>天使たちからプレゼントだよ！"},
    {"RewardId": "item.illust.19", "RewardParam": "", "Count": 1,
     "Description": "あけましておめでとうございます。2026年 元旦"},
]

CRYPTO = os.environ.get('ANGELICA_CRYPTO') == '1'

MOCK = os.environ.get("ANGELICA_MOCK") == "1"
_MOCK_TABLE = None
_MOCK_CURSOR = {}

def _mock_table():

    global _MOCK_TABLE
    if _MOCK_TABLE is not None:
        return _MOCK_TABLE
    _MOCK_TABLE = {}
    try:
        with io.open(os.path.join(HERE, "_mock_new.json"), "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            bodies = v.get("bodies") if isinstance(v, dict) else v
            bodies = [b for b in (bodies or []) if b]
            if bodies:
                _MOCK_TABLE[k] = bodies
        log("    MOCK  table loaded: %d endpoint(s)" % len(_MOCK_TABLE))
    except Exception as e:
        log("    MOCK  table load FAILED: %s" % e)
    return _MOCK_TABLE

def _mock_body(path):

    tbl = _mock_table()
    bodies = tbl.get(path)
    if not bodies:
        return None
    i = _MOCK_CURSOR.get(path, 0)
    if i < len(bodies) - 1:
        _MOCK_CURSOR[path] = i + 1
    return bodies[i]

_SESSION_KEY = ['']

def _crypto_params(session_key, onetime_token):

    import hashlib
    t = onetime_token or ''
    salt = t[:16].encode('utf-8')
    iv = t[-16:].encode('utf-8')
    pw = (session_key or '').encode('utf-8')
    key = hashlib.pbkdf2_hmac('sha1', pw, salt, 10001, 16)
    return key, iv
def _session_ready():

    return bool(_SESSION_KEY[0]) and bool(_SESSION_TOKEN[0])

def _aes_encrypt(body_text):

    try:
        from aes_local import cbc_encrypt
    except Exception:
        return None
    key, iv = _crypto_params(_SESSION_KEY[0], _SESSION_TOKEN[0])
    return cbc_encrypt(key, iv, body_text.encode('utf-8'), pad=True)

_SESSION_TOKEN = ['']

class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):

        if _DELAY_MS > 0 and str(getattr(self, 'path', '') or '').startswith('/api/'):
            time.sleep(_DELAY_MS / 1000.0)

        if isinstance(body, dict):
            try:
                _sync_ch = body.get('Characters')
                _all_ch = _sync_ch if isinstance(_sync_ch, list) and _sync_ch else db_get('Chara')
                if isinstance(_all_ch, list):
                    _own = owned_item_ids()
                    _stripped = 0
                    for _c in _all_ch:
                        if not isinstance(_c, dict):
                            continue
                        _cs = _c.get('Costume')
                        if _cs and str(_cs) not in _own:
                            _c['Costume'] = None
                            _stripped += 1
                    if _stripped:
                        log('    COSTUME-GUARD: %d unowned costume(s) -> null' % _stripped)
            except Exception as _ce:
                log('    COSTUME-GUARD failed: %s' % _ce)

        _lp2 = str(getattr(self, 'path', '') or '').split('?')[0]
        if _lp2 == '/api/lounge/my' and isinstance(body, dict):
            try:
                _bsd = body.get('SyncData')
                if not isinstance(_bsd, dict):
                    _bsd = empty_sync()
                    body['SyncData'] = _bsd
                _u = db_get('User')
                if isinstance(_u, dict) and _u.get('Id'):
                    _bsd['User'] = _fix_user_row(dict(_u))
                _pr = db_get('UserPossession')
                if isinstance(_pr, list):
                    _keep = [x for x in _pr if isinstance(x, dict)
                             and str(x.get('Id') or '').startswith('lounge.')]
                    _bsd['UserPossession'] = [_fix_possession_row(dict(x)) for x in _keep]
                _bsd['UserBehavior'] = None
                body['HistoryUsers'] = body.get('HistoryUsers') or {}

                _mld = body.get('LoungeData')
                if isinstance(_mld, dict):
                    if not _mld.get('Key'):
                        _mld['Key'] = 'lounge'
                    _mld['Id'] = user_id()

                try:
                    if isinstance(_u, dict):
                        _u2 = dict(_u)
                        _u2['LastLoungeAt'] = time.strftime('%Y-%m-%dT%H:%M:%S+09:00')
                        if not _u2.get('LastLoginAt'):
                            _u2['LastLoginAt'] = _u2['LastLoungeAt']

                        _pf_def = 'dmm_player'
                        try:
                            if 'p=dmm_player_r' in str(getattr(self, 'path', '') or ''):
                                _pf_def = 'dmm_player_r'
                        except Exception:
                            pass
                        _u2.setdefault('Pf', _pf_def)
                        if not isinstance(_u2.get('News'), list):
                            _u2['News'] = []
                        _bsd['User'] = _fix_user_row(_u2)
                        db_put('User', _u2)
                except Exception:
                    pass
                log('    LOUNGE-my: User=%s UserPossession=%d Layout=null'
                    % (type(_bsd.get('User')).__name__,
                       len(_bsd.get('UserPossession') or [])))
            except Exception as _le:
                log('    LOUNGE-my failed: %s' % _le)
        elif _lp2 == '/api/lounge/enter' and isinstance(body, dict):
            try:
                _bsd = body.get('SyncData')
                if not isinstance(_bsd, dict):
                    _bsd = empty_sync()
                    body['SyncData'] = _bsd
                _bsd['User'] = None
                _bsd['UserPossession'] = None
                try:
                    _brow, _brows = _behavior_row()
                    _bsd['UserBehavior'] = _behavior_clean(dict(_brow))
                except Exception:
                    pass
                body['HistoryUsers'] = None

                _ld = body.get('LoungeData')
                if not isinstance(_ld, dict):
                    _ld = {'Key': 'lounge', 'Id': user_id(), 'Layout': None, 'Ver': 0}
                    body['LoungeData'] = _ld
                if not isinstance(_ld.get('Layout'), dict) or not _ld.get('Layout'):
                    _stash = db_get('lounge_my') or {}
                    _sl = ((_stash.get('LoungeData') or {}).get('Layout')
                           if isinstance(_stash, dict) else None)
                    if isinstance(_sl, dict) and _sl:
                        _ld['Layout'] = _sl
                        _ld['Ver'] = int((_stash.get('LoungeData') or {}).get('Ver') or 0)
                        log('    LOUNGE-enter: Layout restored from stash (%d keys)' % len(_sl))
                log('    LOUNGE-enter: Layout=%s HistoryUsers=null UserBehavior=%s'
                    % (('dict(%d)' % len(_ld.get('Layout') or {})) if isinstance(_ld.get('Layout'), dict) else repr(_ld.get('Layout')),
                       type(_bsd.get('UserBehavior')).__name__))
            except Exception as _le:
                log('    LOUNGE-enter failed: %s' % _le)

        try:
            body = _sync_value_guard(str(getattr(self, 'path', '') or '').split('?')[0], body)
        except Exception as _te:
            log('    SYNC-GUARD skipped: %s' % _te)

        _lp = str(getattr(self, 'path', '') or '').split('?')[0]
        if _LOUNGE_DELAY_MS > 0 and _lp in _LOUNGE_PATHS:
            log('    LOUNGE-DELAY %s +%dms' % (_lp, _LOUNGE_DELAY_MS))
            time.sleep(_LOUNGE_DELAY_MS / 1000.0)
        if _SUBEVENT_DELAY_MS > 0 and str(getattr(self, 'path', '') or '') == '/api/user-possession/read':
            _probe = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            if 'sub_event_possession' in _probe:
                time.sleep(_SUBEVENT_DELAY_MS / 1000.0)

        if _LOUNGE_DELAY_MS > 0 and str(getattr(self, 'path', '') or '') in (
                '/api/lounge/my', '/api/lounge/enter'):
            time.sleep(_LOUNGE_DELAY_MS / 1000.0)
        try:
            preview = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            log('    RESP %s %s' % (code, preview))
        except Exception:
            pass

        if not isinstance(body, (str, bytes)):
            body = json.dumps(body, ensure_ascii=False)
        raw = body.encode('utf-8') if isinstance(body, str) else body

        _p = self._path_now()
        _plain = _p in ('/api/web/version', '/api/auth/login-player')
        _is_api = _p.startswith('/api/')
        if CRYPTO and code == 200 and _is_api and not _plain and _session_ready():
            try:
                _ct = _aes_encrypt(body if isinstance(body, str) else raw.decode('utf-8'))
                if _ct:
                    raw = _ct
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(len(raw)))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    if self.command == 'POST':
                        self.send_header('X-Access-Token', _access_token())
                    self.end_headers()
                    self.wfile.write(raw)
                    return
            except Exception as _e:
                log('  [crypto] encrypt failed:', _e)
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Access-Control-Allow-Origin', '*')

        if self.command == 'POST':
            self.send_header('X-Access-Token', _access_token())
        self.end_headers()
        self.wfile.write(raw)

    def _path_now(self):
        try:
            return urlparse(self.path).path
        except Exception:
            return ''

    def _handle(self, method):
        u = urlparse(self.path)
        path = u.path

        _f = _touched_flag()
        _f.clear()
        _f['path'] = path

        _touched().clear()
        _touched_singleton().clear()

        del _oopart_plus_log()[:]

        body = b''
        n = int(self.headers.get('Content-Length') or 0)
        if n > 0:
            body = self.rfile.read(n)
            if body and method != 'GET':
                try:
                    log('    REQ-BODY %s %s' % (method, path))
                    log('      ' + body.decode('utf-8', 'replace')[:1200])
                except Exception:
                    pass

        if MOCK:
            _mb = _mock_body(path)
            if _mb is not None:
                log('    MOCK  %s %s -> %d bytes' % (method, path, len(_mb)))
                return self._send(200, _mb)

        log(method, path, ('%d bytes' % n) if n else '')

        try:
            _regen_stamina()
        except Exception as _re:
            log('    stamina regen failed: %s' % _re)

        if path.startswith('/res/v1/News/') or path.lower().startswith('/res/v1/news/'):
            _leaf = path.rsplit('/', 1)[-1].lower()
            if _leaf == 'sync.txt':
                _news = None
                try:
                    _u = db_get('User')
                    if isinstance(_u, dict):
                        _nl = _u.get('News')
                        if isinstance(_nl, list) and _nl:
                            _news = str(_nl[0])
                except Exception:
                    _news = None
                if not _news:
                    _news = '202609250500'
                log('  News sync ->', _news)
                return self._send(200, _news + chr(13) + chr(10),
                                  'text/plain; charset=utf-8')
            log('  News json -> []')
            return self._send(200, [])

        if path.startswith('/res/v1/Blog/') or path.startswith('/res/v1/blog/'):
            _leaf = path.rsplit('/', 1)[-1]
            _safe = _leaf.replace('/', '').replace(chr(92), '')
            for _dir in ('blog_txt', 'web_txt'):
                _p = os.path.join(HERE, _dir, _safe)
                if _leaf and os.path.isfile(_p):
                    _raw = io.open(_p, 'rb').read()
                    if _raw:
                        log('  Blog txt ->', _leaf[:24], len(_raw), 'bytes')
                        return self._send(200, _raw, 'text/plain; charset=utf-8')
            log('  Blog txt MISSING', _leaf[:24])
            return self._send(404, {'error': 'blog txt not found'})

        if path.startswith('/res/v1/Image/') or path.startswith('/res/v1/image/'):
            _leaf = path.rsplit('/', 1)[-1]
            _safe = _leaf.replace('/', '').replace(chr(92), '')
            _p = os.path.join(HERE, 'Image', _safe)
            if _leaf and os.path.isfile(_p):
                _raw = io.open(_p, 'rb').read()
                if _raw:
                    log('  Image ->', _leaf[:20], len(_raw), 'bytes')
                    return self._send(200, _raw, 'image/png')
            log('  Image MISSING', _leaf[:24])

            return self._send(404, {'error': 'image not found'})

        if path.startswith('/res/v1/Web/blog/') or path.startswith('/res/v1/web/blog/'):
            _leaf = path.rsplit('/', 1)[-1]
            _safe = _leaf.replace('/', '').replace(chr(92), '')
            _p = os.path.join(HERE, 'Web', 'blog', _safe)
            if _leaf and os.path.isfile(_p):
                _raw = io.open(_p, 'rb').read()
                if _raw:
                    log('  Blog img ->', _leaf[:20], len(_raw), 'bytes')
                    return self._send(200, _raw, 'image/png')
            log('  Blog img MISSING', _leaf[:24])
            return self._send(404, {'error': 'blog image not found'})

        if path.startswith('/res/v1/Web/txt/') or path.startswith('/res/v1/web/txt/'):
            _leaf = path.rsplit('/', 1)[-1]
            _safe = _leaf.replace('/', '').replace(chr(92), '')
            _p = os.path.join(HERE, 'web_txt', _safe)
            if _leaf and os.path.isfile(_p):
                _raw = io.open(_p, 'rb').read()
                if _raw:
                    log('  Web txt ->', _leaf[:16], len(_raw), 'bytes')
                    return self._send(200, _raw, 'text/plain; charset=utf-8')

            log('  Web txt MISSING', _leaf[:24])
            return self._send(404, {'error': 'web txt not found'})

        if path.startswith('/res/v1/Web/cache/') or path.startswith('/res/v1/web/cache/'):

            _leaf = path.rsplit('/', 1)[-1]
            for _dir in ('web_cache', 'web_txt'):
                _p = os.path.join(HERE, _dir, _leaf)
                if _leaf and os.path.isfile(_p):
                    _raw = io.open(_p, 'rb').read()
                    if _raw:
                        log('  Web cache ->', _leaf[:16], len(_raw), 'bytes')
                        return self._send(200, _raw, 'video/mp4')
            log('  Web cache MISSING', _leaf[:24])

            return self._send(404, {'error': 'web cache not found'})

        if '/res/v1/' in path and path.endswith(('.bin', '.hash')):
            name = path.rsplit('/', 1)[-1]

            if name.startswith('catalog_r_') or name.startswith('catalog_n_'):
                _is_n = name.startswith('catalog_n_')

                _cat  = CATALOG_BIN if _is_n else (CATALOG_BIN_R or CATALOG_BIN)
                _hash = CATALOG_H_N if _is_n else (CATALOG_H_R or CATALOG_H)
                if name.endswith('.hash'):
                    if _hash:
                        return self._send(200, _hash, 'text/plain; charset=utf-8')
                    log('  CDN catalog (%s) hash MISSING' % ('n' if _is_n else 'r'))
                    return self._send(404, {'error': 'catalog hash not configured'})
                if _cat and os.path.exists(_cat):
                    raw = io.open(_cat, 'rb').read()
                    log('  CDN catalog (%s) ->' % ('n' if _is_n else 'r'), len(raw), 'bytes',
                        'from', os.path.basename(_cat))
                    return self._send(200, raw, 'application/octet-stream')
                log('  CDN catalog (%s) FILE MISSING' % ('n' if _is_n else 'r'))
                return self._send(404, {'error': 'catalog not found'})

        if path.endswith('.bundle') and path.startswith('/res/v1/pd'):
            if MASTERDATA_BIN and os.path.exists(MASTERDATA_BIN):
                name = path.rsplit('/', 1)[-1]
                raw = io.open(MASTERDATA_BIN, 'rb').read()
                log('  MasterData ->', name[:16], len(raw), 'bytes')
                return self._send(200, raw, 'application/octet-stream')
            log('  MasterData MISSING')
            return self._send(404, {'error': 'masterdata not found'})

        if path.endswith('.bundle'):
            name = path.rsplit('/', 1)[-1]

            p = _resolve(BUNDLES.get(name))
            raw = None
            if p and os.path.exists(p):
                try:
                    raw = io.open(p, 'rb').read()
                except Exception as _e:
                    log('  CDN bundle read fail', name[:12], _e)
                    raw = None

            if not raw and name:
                alt = _resolve('server/bundles/%s/%s' % (name[:2], name))
                if alt and os.path.exists(alt) and os.path.getsize(alt) > 0:
                    raw = io.open(alt, 'rb').read()
                    log('  CDN bundle', name[:12], '->', len(raw), '(rescued from server/bundles/)')

            if not raw and name:
                for _root in _auto_extra_roots():
                    try:
                        _base = _root if os.path.isabs(_root) else _resolve(_root)
                        _p2 = os.path.join(_base, name[:2], name)
                        if os.path.exists(_p2) and os.path.getsize(_p2) > 0:
                            raw = io.open(_p2, 'rb').read()
                            log('  CDN bundle', name[:12], '->', len(raw), '(extra pool)')
                            break
                    except Exception as _e2:
                        log('  extra pool read fail', name[:12], _e2)
            if raw:
                log('  CDN bundle', name[:12], '->', len(raw))
                return self._send(200, raw, 'application/octet-stream')

            log('  CDN bundle UNAVAILABLE (empty or absent)', name[:12])
            return self._send(404, {'error': 'bundle not found'})

        if path == '/local/identity':

            try:
                _payload = json.dumps({"UserId": user_id(),
                                       "Save": os.path.basename(DB),
                                       "Port": PORT})
                for _n in ("local_identity_%d.json" % PORT,
                           "local_identity.json"):
                    _idf = os.path.join(HERE, _n)
                    with open(_idf + ".tmp", "w", encoding="utf-8") as _f:
                        _f.write(_payload)
                    os.replace(_idf + ".tmp", _idf)
            except Exception as _e:
                log("  [identity] could not publish local_identity.json: %r" % (_e,))
            self._send(200, {'UserId': user_id(), 'Save': os.path.basename(DB)})
            return
        
        if path == '/dmm-blocked':

            _dmm_body = {}
            try:
                _raw = json.loads(body.decode('utf-8')) if body else {}
                if isinstance(_raw, dict): _dmm_body = _raw
            except Exception:
                _dmm_body = {}
            _cmd = str(_dmm_body.get('command') or '')
            _u = db_get('User')
            _uname = ''
            if isinstance(_u, dict): _uname = str(_u.get('Name') or '')
            _tok = str(_dmm_body.get('access_token') or 'localtoken')
            _ott = str(_dmm_body.get('onetime_token') or 'localtoken0000000000000000000000')
            _vid = str(_dmm_body.get('viewer_id') or user_id())

            _pt = 0
            try:
                for _r in (db_get('Item') or []):
                    if isinstance(_r, dict) and _r.get('Id') == _row_id('item.jewel_paid'):
                        _pt = int(_r.get('Count') or 0); break
            except Exception:
                _pt = 0
            _resp = {
                'access_token':  _tok,
                'onetime_token': _ott,
                'can_use_point': _pt,
                'nickname':      _uname,
                'payment_id':    '',
            }
            log('  DMM-BLOCKED', method, 'command=' + (_cmd or '(none)'),
                '-> nickname=' + repr(_uname), 'point=' + str(_pt))
            return self._send(200, _resp)

        if path == '/api/web/version':

            return self._send(200, {
                'A':'1.72.1','R':'1.72.3',
                'H':'27f3577e92d725f410e27526a0e8d44fcd02dcc8cc984d1d59dc28e644f5eaed',
                'M':'2026.0918.1253','T':'0.0.1','I':'','P':'','S':'','CF':''})

        if path == '/api/auth/login-player':

            sess = {'UserId':user_id(), 'Id':'localsession0001',
                    'OnetimeToken':'localtoken0000000000000000000000',
                    'AccountId':'localaccount', 'Role':'admin',
                    'Key':'localkey0000000000000000000000'}

            try:
                pend = claimable_login_bonuses()
                log('  [login] %d login bonus(es) claimable' % len(pend))
            except Exception as e:
                log('  [login] login-bonus scan failed:', e)

            _lr = json.dumps({'Session':sess, 'Status':0, 'Text':''}, ensure_ascii=False)
            _SESSION_KEY[0] = sess.get('Key') or ''
            _SESSION_TOKEN[0] = sess.get('OnetimeToken') or ''

            return self._send(200, base64.b64encode(_lr.encode('utf-8')).decode('ascii'))

        if path == '/api/chara/get-enhance-weapon':
            _q = parse_qs(u.query or '')
            _want = None
            for _k in ('Id', 'id', 'WeaponId', 'weaponId'):
                if _q.get(_k):
                    _want = _q[_k][0]
                    break
            _fr = db_get('WeaponEnhanceFailed')
            if not isinstance(_fr, list):
                _fr = []
            _hit = None
            for _x in _fr:
                if not isinstance(_x, dict):
                    continue
                if _want is None or _x.get('Id') == _want:
                    _hit = _x
                    break

            if isinstance(_hit, dict):
                _hit = {'Key': _hit.get('Key') or 'weapon_enhance_failed',
                        'Id': _hit.get('Id') or user_id(),
                        'Use': _hit.get('Use') if isinstance(_hit.get('Use'), dict) else {},
                        'WeaponId': _hit.get('WeaponId'),
                        'Ver': int(_hit.get('Ver') or 0)}
            log('    get-enhance-weapon -> Failed=%s'
                % ('yes' if _hit else 'no'))
            return self._send(200, {'SyncData': empty_sync(), 'Failed': _hit})

        if path == '/api/user/question/get':

            try:
                _qreq = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                _qreq = {}
            if not isinstance(_qreq, dict):
                _qreq = {}
            _qid = (_qreq.get('Id') or _qreq.get('QuestionId') or _qreq.get('id'))
            _qrows = db_get('Question')
            if not isinstance(_qrows, list):
                _qrows = []
            _qent = None
            for _x in _qrows:
                if isinstance(_x, dict) and (_qid is None or _x.get('Id') == _qid):
                    _qent = _x
                    break
            if _qent is None:

                _qent = {'UserId': user_id(), 'Id': _qid, 'Answer': {},
                         'Status': 0, 'Ver': 0}
                if _qid:
                    _qrows.append(_qent)
                    db_put('Question', _qrows)

            _q_view = {'Key': _qent.get('Key') or _qid,
                       'Id': _qent.get('Id'),
                       'Answer': _qent.get('Answer') if isinstance(_qent.get('Answer'), dict) else {},
                       'Status': int(_qent.get('Status') or 0),
                       'Ver': int(_qent.get('Ver') or 0)}
            return self._send(200, _q_view)

        if path == '/api/social/profile/get':
            return self._send(200, _self_profile_entity())

        if path == '/api/social/profile/id':

            try:
                _ireq = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                _ireq = {}
            if not isinstance(_ireq, dict):
                _ireq = {}
            _one = (_ireq.get('UserId') or _ireq.get('TargetId')
                    or _ireq.get('Id') or _ireq.get('UserIds'))
            if isinstance(_one, list):
                _one = _one[0] if _one else None

            if _one and str(_one) != user_id():
                log('    profile/id %s -> null (unknown user)' % _one)
                return self._send(200, None)
            _row = dict(_self_profile_entity())
            _row['Id'] = user_id()
            return self._send(200, _row)

        if path in ('/api/social/profile/ids', '/api/social/profile/other'):

            try:
                _sreq = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                _sreq = {}
            if not isinstance(_sreq, dict):
                _sreq = {}
            _ids = _sreq.get('UserIds') or _sreq.get('userIds') or []
            if isinstance(_ids, str):
                _ids = [_ids]
            if not isinstance(_ids, list):
                _ids = []

            if not _ids:
                _one = (_sreq.get('UserId') or _sreq.get('TargetId')
                        or _sreq.get('Id'))
                if _one:
                    _ids = [_one]

            if not _ids:
                _ids = [user_id()]

            _out = []
            for _uid in _ids:
                _uid = str(_uid)
                _row = dict(_self_profile_entity())
                if _uid != user_id():

                    _row['Id'] = _uid
                    _row['Name'] = 'Guest'
                    _row['Rank'] = 1
                    _row['Angels'] = 0
                    _row['Following'] = 0
                    _row['Follower'] = 0
                    _row['JobLvs'] = {}
                    _row['Messages'] = []
                else:
                    _row['Id'] = _uid
                _out.append(_row)
            return self._send(200, _out)

        if path == '/api/item/get-orb':
            rows = _orb_box_rows()
            ent = _orb_box_entity(rows)
            db_put('OrbBox', rows)
            log('    get-orb -> Slots=%d PSlots=%d Stack=%s'
                % (len(ent.get('Slots') or {}), len(ent.get('PSlots') or []),
                   json.dumps(ent.get('Stack'), ensure_ascii=False)))
            return self._send(200, ent)

        if path in READ_API:
            name = READ_API[path]

            if path == '/api/login-bonus/list':
                seed_login_bonuses()

            if path in ('/api/exchange/list', '/api/exchange/master'):
                try:
                    _erows = db_get('Exchange')

                    if not isinstance(_erows, list) or not _erows:
                        _erows = _seed_exchange_rows()
                    if isinstance(_erows, list):
                        _ch = _exchange_reset_counters(_erows)
                        _rc2 = _exchange_refresh_lineup(_erows)

                        _rf = sum(_exchange_fix_tier(_r) for _r in _erows
                                  if isinstance(_r, dict))
                        if _ch or _rc2 or _rf:
                            db_put('Exchange', _erows)
                            log('    exchange: cleared %d counter(s), refreshed %d row(s), tier-fixed %d'
                                % (_ch, _rc2, _rf))
                except Exception as _e:
                    log('    exchange reset failed: %s' % _e)

            if name == 'ItemGeneration' or path == '/api/itemgeneration/list':
                try:
                    _grows = db_get('ItemGeneration')
                    if isinstance(_grows, list):
                        _ticked = 0
                        _healed = 0
                        _lim = _itemgen_limit()
                        for _g in _grows:

                            if isinstance(_g, dict):
                                _gc = _g.get('GenerateCount')
                                if not isinstance(_gc, int) or _gc != _lim:

                                    _g['GenerateCount'] = _lim
                                    _g['Ver'] = int(_g.get('Ver') or 0) + 1
                                    _healed += 1
                                _rc = _g.get('ReceiveCount')

                                if not isinstance(_rc, int) or _rc > _lim or _rc < 0:
                                    _g['ReceiveCount'] = 0 if not isinstance(_rc, int) else max(0, min(_rc, _lim))
                                    _g['Ver'] = int(_g.get('Ver') or 0) + 1
                                    _healed += 1

                            _itemgen_tick(_g)
                        if _healed:
                            db_put('ItemGeneration', _grows)
                            log('    itemgeneration: normalised GenerateCount -> %d on %d row(s)'
                                % (_lim, _healed))
                except Exception as _e:
                    log('    itemgeneration tick failed: %s' % _e)
            data = db_get(name)

            if path in ('/api/challenge-mission/list', '/api/mission/list'):
                seed_challenge_missions()
                data = db_get(name)

            if path == '/api/shop/hold':
                data = _inject_shop_entities(data)

            if name in ('UniqueQuest', 'UniqueQuests') and isinstance(data, list):
                _healed = [x for x in data if isinstance(x, dict) and _uq_heal(x)]
                if _healed:
                    try:
                        db_put('UniqueQuest', data)
                    except Exception as _e:
                        log('    UQ-HEAL persist failed: %s' % _e)

            if isinstance(data, list) and API_TYPES.get(path, {}).get('shape') == 'entity':
                _want = None
                _q = parse_qs(u.query or '')
                for _k in ('Id', 'id', 'CharaId', 'charaId', 'OrbBoxId', 'orbBoxId'):
                    if _q.get(_k):
                        _want = _q[_k][0]
                        break
                if _want is None:
                    try:
                        _b = _req_json(body)
                    except Exception:
                        _b = {}
                    for _k in ('Id', 'id', 'CharaId', 'charaId', 'OrbBoxId', 'orbBoxId'):
                        if isinstance(_b, dict) and _b.get(_k):
                            _want = _b[_k]
                            break
                _pick = None
                if _want:
                    for _x in data:
                        if isinstance(_x, dict) and _x.get('Id') == _want:
                            _pick = _x
                            break
                    if _pick is None:
                        for _x in data:
                            if isinstance(_x, dict) and _x.get('Key') == _want:
                                _pick = _x
                                break
                if _pick is None:
                    for _x in data:
                        if isinstance(_x, dict):
                            _pick = _x
                            break
                data = _pick if _pick is not None else {}
            if data is None:

                cf = os.path.join(CAP, name + '.json')
                data = json.loads(io.open(cf, encoding='utf-8').read()) if os.path.exists(cf) else None

            if isinstance(data, list) and name in ('Item', 'Items'):
                for _e in data:
                    _fix_item_records(_e)
            elif isinstance(data, dict) and name in ('Item', 'Items'):
                _fix_item_records(data)

            if data is not None and path in WRAP and isinstance(data, list):
                member, extra = WRAP[path]
                log('  wrap', member, 'for', path, '(%d items)' % len(data))
                obj = dict(extra)
                obj[member] = data
                data = obj

            if isinstance(data, dict) and 'NextUpdateTime' in data:
                old_nut = data.get('NextUpdateTime')
                data['NextUpdateTime'] = _next_update_iso()
                if old_nut != data['NextUpdateTime']:
                    log('  NextUpdateTime %s -> %s for %s'
                        % (old_nut, data['NextUpdateTime'], path))

            if path in UNWRAP:
                if UNWRAP[path] == 'UserBehavior':
                    _brow, _ = _behavior_row()
                    if _brow:
                        _behavior_rollover(_brow)
                        data = _behavior_clean(dict(_brow))
                elif data is not None and isinstance(data, dict):
                    inner = data.get(UNWRAP[path])
                    if inner is not None:
                        log('  unwrap', UNWRAP[path], 'for', path)
                        data = inner

            if path in LIST_API and isinstance(data, dict):
                log('  shape', 'list endpoint but db value is object -> serving []', path)
                data = None

            if isinstance(data, list):
                clean = [x for x in data if not isinstance(x, dict) or x.get('Id')]
                if len(clean) != len(data):
                    log('  ID-GUARD', 'dropped %d row(s) with empty Id from %s'
                        % (len(data) - len(clean), path))
                    data = clean

            if data is None:

                data = [] if path in LIST_API else {}

                if data == {} and API_TYPES.get(path, {}).get('shape') == 'entity':
                    _em = ENTITY_MEMBERS.get(path)
                    if isinstance(_em, dict):
                        data = dict(_em)
                log('  empty', '[]' if data == [] else json.dumps(data)[:400], path)

            if isinstance(data, dict):
                members = RESPONSE_MEMBERS.get(path)
                if isinstance(members, dict):
                    for member, dv in members.items():
                        if data.get(member) is None:
                            data[member] = dv
                            log('  member-guard', '%s %s -> %r'
                                % (path, member, dv))
                elif members:
                    for member in members:
                        if data.get(member) is None:
                            data[member] = []
            return self._send(200, data)

        info = API_TYPES.get(path) or {}
        shape = info.get('shape', 'unknown')
        ret   = info.get('ret')

        if path == '/api/homesetting/set-homesetting-record':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}

            hid = req.get('homeSettingId') or req.get('HomeSettingId') or 'home_setting.1'
            recs = req.get('Records')
            if not isinstance(recs, list):
                recs = req.get('records')
            if not isinstance(recs, list):
                recs = []
            ent = save_home_setting(hid, recs)

            for _cid in _challenge_defs():
                _behavior_challenge_record(_cid, 31, 1)
            sync = empty_sync()
            sync['HomeSettings'] = [ent]
            return self._send(200, sync)

        if path == '/api/present/campaign':

            rows = db_get('Present')
            if not isinstance(rows, list):
                rows = []
            have = set(str(r.get('Id')) for r in rows if isinstance(r, dict))
            added = 0
            for i, g in enumerate(NEW_ACCOUNT_GIFTS or []):
                if not isinstance(g, dict) or not g.get('RewardId'):
                    continue
                gid = 'present.new.%s.%03d' % (user_id(), i)
                if gid in have:
                    continue
                rows.append({'UserId': user_id(), 'Id': gid,
                             'RewardId': g.get('RewardId'),
                             'RewardParam': g.get('RewardParam') or '',
                             'Count': int(g.get('Count') or 1),
                             'Description': g.get('Description') or '',
                             'PostedAt': _now_iso(),
                             'ReceiveDeadLineAt': '',
                             'Received': False, 'ReceivedAt': '',
                             'Deleted': False, 'Ver': 0})
                added += 1
            if added:
                db_put('Present', rows)
                for p in rows:
                    if str(p.get('Id')) in have:
                        continue
                    touch(p.get('Id'), p, 'Presents')
            log('    present/campaign -> added %d new-account gift(s)' % added)

            _furn = grant_initial_furnitures()
            if _furn:
                log('    present/campaign -> granted %d starter furniture(s)' % _furn)
            return self._send(200, flush_touched(empty_sync()))

        if path.startswith('/api/present/'):

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            rows = db_get('Present')
            if not isinstance(rows, list):
                rows = []
            ids = req.get('PresentIds') or req.get('presentIds')
            if not ids:
                pid = req.get('PresentId') or req.get('presentId')
                ids = [pid] if pid else None
            granted = 0
            for p in rows:
                if not isinstance(p, dict):
                    continue
                if p.get('Received'):
                    continue
                if ids and p.get('Id') not in ids:
                    continue
                _grant_reward(p.get('RewardId'), int(p.get('Count') or 1))
                p['Received'] = True
                p['ReceivedAt'] = _now_iso()
                p['Ver'] = int(p.get('Ver') or 0) + 1
                touch(p.get('Id'), p)
                granted += 1
            if granted:
                db_put('Present', rows)
            log('    present%s -> received %d' % (path[len('/api/present'):], granted))
            return self._send(200, flush_touched(empty_sync()))

        if path.startswith('/api/mission/receive'):

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}

            mid = (req.get('ResourceId') or req.get('resourceId')
                   or req.get('MissionId') or req.get('missionId'))
            mids = req.get('MissionIds') or req.get('missionIds')
            if not mids:
                mids = [mid] if mid else []
            rows = db_get('Mission')
            if not isinstance(rows, list):
                rows = []

            rewards_tbl = master_tables().get('mission') or {}

            rewards_tbl = dict(rewards_tbl)
            for _rk, _rv in (master_tables().get('mission_challenge') or {}).items():
                if isinstance(_rv, dict) and _rk not in rewards_tbl:
                    rewards_tbl[_rk] = _rv
            by_id = {m.get('Id'): m for m in rows if isinstance(m, dict)}
            done = 0
            for mid in mids:
                m = by_id.get(mid)
                if m is None:

                    mtype = req.get('MissionType') or req.get('missionType')
                    try:
                        mtype = int(mtype)
                    except Exception:
                        mtype = 0
                    if mid not in rewards_tbl:
                        log('    mission/receive %s: unknown in masterdata, skipped' % mid)
                        continue
                    m = {'UserId': user_id(), 'Id': mid, 'MissionType': mtype,
                         'State': 3, 'Ver': 1}
                    rows.append(m)
                    by_id[mid] = m
                elif int(m.get('State') or 0) == 3:
                    log('    mission/receive %s already received' % mid)
                    continue
                master = rewards_tbl.get(mid) or {}
                for rec in (master.get('Records') or []):
                    rid = rec.get('RewardId')
                    if rid:

                        _rc = rec.get('RewardCount')
                        if _rc is None:
                            _rc = rec.get('Count')
                        _grant_reward(rid, int(_rc or 1))
                m['State'] = 3
                m['Ver'] = int(m.get('Ver') or 0) + 1
                touch(mid, m, 'Missions')
                done += 1
            if done:
                db_put('Mission', rows)
            log('    mission/receive%s -> %d' % (str(mids)[:80], done))

            sync = empty_sync()
            touched = [r for r in rows if isinstance(r, dict) and r.get('Id') in mids]
            if touched:
                sync['Missions'] = touched
            _sync = flush_touched(sync)

            if mids and len(mids) > 1:
                try:
                    _brow, _brows = _behavior_row()
                    _behavior_rollover(_brow)
                    _brow['Ver'] = int(_brow.get('Ver') or 0) + 1
                    db_put('UserBehavior', _brows if _brows is not None else _brow)
                    _sync['UserBehavior'] = _behavior_clean(dict(_brow))
                except Exception as _be:
                    log('    behavior echo failed: %s' % _be)
            return self._send(200, _sync)

        if path.startswith('/api/lyric/world/'):

            try:
                wreq = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                wreq = {}
            wid = wreq.get('LyricId') or wreq.get('Id') or wreq.get('lyricId') or wreq.get('id') or ''
            wflag = wreq.get('ReadFlag') or wreq.get('readFlag')
            wnum = str(wid).rsplit('.', 1)[-1] if wid else ''
            prows = db_get('UserPossession')
            if not isinstance(prows, list):
                prows = []
            wrow = None
            for x in prows:
                if isinstance(x, dict) and x.get('Id') == 'world_lyric':
                    wrow = x
                    break
            if wrow is None:
                wrow = {'UserId': user_id(), 'Id': 'world_lyric', 'ReadFlag': False,
                        'Records': {}, 'Ver': 0}
                prows.append(wrow)
            wrecs = wrow.get('Records')
            if not isinstance(wrecs, dict):
                wrecs = {}
            if path.endswith('/change'):

                try:
                    want = int(wreq.get('Count') or 0)
                except Exception:
                    want = 0
                if want > 0 and wnum:
                    if _have_item('item.world_story_key') < want:
                        log('    lyric/world/change REFUSED: keys %d < %d'
                            % (_have_item('item.world_story_key'), want))
                    else:
                        _consume_item('item.world_story_key', want, strict=False)
                        try:
                            wrecs[wnum] = int(wrecs.get(wnum) or 0) + want
                        except Exception:
                            wrecs[wnum] = want
                        wrow['Records'] = wrecs
                        wrow['Ver'] = int(wrow.get('Ver') or 0) + 1
                        db_put('UserPossession', prows)
                        touch('world_lyric', wrow, 'UserPossession')
                        log('    lyric/world/change -> %s +%d (now %s), keys left %d'
                            % (wnum, want, wrecs.get(wnum),
                               _have_item('item.world_story_key')))
            elif wflag is not None and wnum:

                parts = str(wflag).split('-')
                try:
                    wrecs[wnum + '_r'] = int(parts[0] or 0)
                except Exception:
                    pass
                try:
                    wrecs[wnum + '_s'] = int(parts[1] or 0) if len(parts) > 1 else 0
                except Exception:
                    pass
                wrow['Records'] = wrecs
                wrow['Ver'] = int(wrow.get('Ver') or 0) + 1
                db_put('UserPossession', prows)
                touch('world_lyric', wrow, 'UserPossession')
                log('    lyric/world/read -> %s r=%s s=%s'
                    % (wnum, wrecs.get(wnum + '_r'), wrecs.get(wnum + '_s')))

            return self._send(200, flush_touched(empty_sync()))

        if path.startswith('/api/lyric/') and path.endswith(('read', 'trial', 'change')):

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            lid = req.get('LyricId') or req.get('lyricId')
            flag = req.get('ReadFlag') or req.get('readFlag')
            table = 'GodLyric' if '/god/' in path else 'Lyric'
            rows = db_get(table)
            if not isinstance(rows, list):
                rows = []
            hit = None
            for x in rows:
                if isinstance(x, dict) and x.get('Id') == lid:
                    hit = x
                    break
            if hit is None and lid:

                if '/world/' in path or '/god/' in path:
                    hit = {'UserId': user_id(), 'Id': lid, 'UnlockedBitFlag': 0,
                           'ReadFlag': flag or '', 'ReadAt': None, 'Ver': 0}
                else:
                    hit = {'UserId': user_id(), 'Id': lid, 'Count': 0,
                           'Experience': 0, 'ReadFlag': flag or '',
                           'ReadAt': None, 'Ver': 0}

                    hit['Count'] = _angel_lyric_count(flag, 0)
                rows.append(hit)
            if hit is not None:

                old_bits = _lyric_keyword_bits(hit.get('ReadFlag'))
                new_bits = _lyric_keyword_bits(flag)
                gained = bin(new_bits & ~old_bits).count('1')
                if gained:
                    amount = 50
                    try:
                        gs = master_tables().get('game_setting') or {}
                        row = gs.get('game_setting.lyric_keywrod_reward_count') or {}
                        amount = int(row.get('Value') or 50)
                    except Exception:
                        amount = 50
                    total = amount * gained
                    _grant_item('item.jewel_free', total)
                    log('    lyric keyword reward: +%d keyword(s) -> jewel_free x%d'
                        % (gained, total))
                if flag is not None:
                    hit['ReadFlag'] = flag

                if table == 'Lyric' and flag is not None:
                    hit['Count'] = _angel_lyric_count(flag, hit.get('Count'))
                if path.endswith('/trial'):

                    hit['ReadFlag'] = flag or '0-12'
                hit['ReadAt'] = _now_iso()
                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                if '/god/' in path:
                    touch(lid, hit, 'GodLyrics')
                else:
                    touch(lid, hit, 'AngelLyrics')
                db_put(table, rows)
            log('    lyric%s -> %s' % (path[len('/api/lyric'):], lid))
            return self._send(200, flush_touched(empty_sync()))

        def _rows(tbl):
            r = db_get(tbl)
            return r if isinstance(r, list) else []

        def _find(tbl, rid):
            for x in _rows(tbl):
                if isinstance(x, dict) and x.get('Id') == rid:
                    return x
            return None

        def _upsert(tbl, rid, defaults=None, id_fallback=None):

            rows = _rows(tbl)
            for x in rows:
                if isinstance(x, dict) and x.get('Id') == rid and rid is not None:
                    return rows, x
            if rid is None:
                rid = id_fallback or tbl.lower()
                for x in rows:
                    if isinstance(x, dict) and x.get('Id') == rid:
                        return rows, x
            ent = {'UserId': user_id(), 'Id': rid, 'Ver': 0}
            if defaults:
                ent.update(defaults)
            rows.append(ent)
            return rows, ent

        if path == '/api/dungeon/get':
            req = _req_json(body)
            gid = req.get('Id') or req.get('id')
            ent = _dungeon_entity(gid) if gid else None

            return self._send(200, ent)

        if path in _DUNGEON_WRITES:
            req = _req_json(body)
            gid = req.get('Id') or req.get('id')
            rows = _dungeon_rows()
            ent = _dungeon_entity(gid, rows)

            if path == '/api/dungeon/start':

                if ent is None:
                    ent = _dungeon_new_row(gid)
                    rows.append(ent)
                ent['EventId'] = req.get('EventId')
                ent['No'] = int(req.get('No') or 0)
                ent['HazardNos'] = req.get('HazardNos') or []
                ent['Maps'] = req.get('Map') or req.get('Maps') or {}
                ent['Members'] = req.get('Members') or []
                ent['Settings'] = req.get('Settings') or []
                ent['ClearNos'] = []
                ent['Logs'] = {}
                ent['StartAt'] = _now_iso()
                ent['Status'] = DUNGEON_STATUS['SelectMap']

                ent['StaminaRate'] = int(req.get('StaminaRate') or 3)

                _cost = _dungeon_stamina_cost(gid, ent['StaminaRate'])
                if _cost > 0 and _spend_stamina(_cost) is None:
                    log('    dungeon/start %s REFUSED: need %d stamina, have %d'
                        % (gid, _cost, (db_get('User') or {}).get('Stamina')))
                    return self._send(200, _dungeon_response(ent))
                ent['Ver'] = int(ent.get('Ver') or 0) + 1

                log('    dungeon/start %s No=%s event=%s rate=%s'
                    % (gid, ent['No'], ent['EventId'], ent['StaminaRate']))
                db_put('Dungeon', rows)
                return self._send(200, _dungeon_response(ent))

            elif path == '/api/dungeon/save':

                if ent is None:
                    ent = _dungeon_new_row(gid)
                    rows.append(ent)
                _key = str(req.get('Key') or '')
                _no = int(req.get('No') or 0)
                _turn = int(req.get('Turn') or 0)
                _akey = req.get('AKey')
                logs = ent.get('Logs')
                if not isinstance(logs, dict):
                    logs = {}
                ent['Logs'] = logs
                grp = _dungeon_group_rec(gid, _dungeon_current_group_no(gid))
                tile = _dungeon_tile(grp.get('DungeonId'), _no)
                _dtype = tile.get('DMapType')
                _dtype = int(_dtype) if _dtype is not None else -1

                _seen = [x for x in str(logs.get('C') or '').split(',') if x]
                if _key and _key not in _seen:
                    _seen.append(_key)
                _dungeon_logs_put(logs, C=','.join(_seen), T=_turn, P=_key)
                if _key:

                    logs[_key] = str(_no)
                if _akey not in (None, ''):
                    _dungeon_logs_put(logs, A=_akey)

                _save_rewards = None
                if _no == 800:
                    _dungeon_logs_put(logs, K=int(logs.get('K') or 0) + 1)
                    _krow = _dungeon_rewards_row(tile.get('RId'), 1)
                    if _krow:
                        _krow['RewardItemCount'] = 1
                        _grant_reward(_krow['RewardItemId'], 1)
                        _save_rewards = [_krow]

                if _dtype in (2, 3):
                    _dungeon_logs_put(logs, N=_no)
                ent['Status'] = _dungeon_status_for_tile(gid, _no)
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                log('    dungeon/save %s key=%s No=%s turn=%s -> Status=%s'
                    % (gid, _key, _no, _turn, ent['Status']))

                db_put('Dungeon', rows)
                return self._send(200, _dungeon_response(ent, received=_save_rewards))

            elif path == '/api/dungeon/end-battle':

                _no = int(req.get('No') or 0)
                _res = str(req.get('Result') or '')
                grp = _dungeon_group_rec(gid, _dungeon_current_group_no(gid))
                tile = _dungeon_tile(grp.get('DungeonId'), _no)
                row = _dungeon_rewards_row(tile.get('RId'),
                                           (ent.get('StaminaRate') if ent else 3) or 3)
                if row:
                    _grant_reward(row['RewardItemId'], row['RewardItemCount'])
                if ent is not None:
                    logs = ent.get('Logs')
                    if not isinstance(logs, dict):
                        logs = {}
                    ent['Logs'] = logs

                    _dungeon_logs_put(logs, N=_no)
                    if row:
                        logs['I_B'] = str(int(logs.get('I_B') or 0) + row['RewardItemCount'])
                        logs['I'] = str(int(logs.get('I_B') or 0) + int(logs.get('I_T') or 0))
                    ent['Status'] = DUNGEON_STATUS['SelectMap']
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                db_put('Dungeon', rows)
                _alive = req.get('AliveCharaIds') or []
                eq = _end_quest_response(received=([row] if row else []),
                                         living=[str(x) for x in _alive])
                log('    dungeon/end-battle %s No=%s result=%s -> %s'
                    % (gid, _no, _res, row['RewardItemId'] if row else 'no reward'))
                return self._send(200, _dungeon_response(ent,
                                                          received=None, endquest=eq))

            elif path == '/api/dungeon/treasure':

                _key = str(req.get('Key') or '')
                _no = int(req.get('No') or 0)
                ent2 = ent if ent is not None else _dungeon_new_row(gid)
                grp = _dungeon_group_rec(gid, _dungeon_current_group_no(gid))
                tile = _dungeon_tile(grp.get('DungeonId'), _no)
                row = _dungeon_rewards_row(tile.get('RId'), ent2.get('StaminaRate') or 3)
                if row:
                    _grant_reward(row['RewardItemId'], row['RewardItemCount'])
                logs = ent2.get('Logs')
                if not isinstance(logs, dict):
                    logs = {}
                ent2['Logs'] = logs
                if row:
                    _opened = [x for x in str(logs.get('O') or '').split(',') if x]
                    if _key and _key not in _opened:
                        _opened.append(_key)
                    _dungeon_logs_put(logs, O=','.join(_opened))
                    logs['I_T'] = str(int(logs.get('I_T') or 0) + row['RewardItemCount'])

                    _tev = ent2.get('EventId')
                    if _tev:
                        _behavior_event_record(_tev, '116', 1)
                    logs['I'] = str(int(logs.get('I_B') or 0) + int(logs.get('I_T') or 0))

                    _k = int(logs.get('K') or 0) - 1
                    _dungeon_logs_put(logs, K=(_k if _k > 0 else 0))
                ent2['Status'] = DUNGEON_STATUS['SelectMap']
                ent2['Ver'] = int(ent2.get('Ver') or 0) + 1
                db_put('Dungeon', rows)
                log('    dungeon/treasure %s key=%s No=%s -> %s'
                    % (gid, _key, _no, row['RewardItemId'] if row else 'no reward'))
                return self._send(200, _dungeon_response(ent2,
                                                          received=[row] if row else None))
            elif path == '/api/dungeon/shop':

                if ent is not None and ent.get('EventId'):
                    _behavior_event_record(ent.get('EventId'), '115', 1)

                if ent is not None:
                    _sno = int(req.get('No') or 0)
                    logs = ent.get('Logs')
                    if not isinstance(logs, dict):
                        logs = {}
                    ent['Logs'] = logs

                    _shop_no = _dungeon_shop_tile_no(gid)
                    _sn = _dungeon_shop_guard_no(
                        _dungeon_group_rec(gid, _dungeon_current_group_no(gid)).get('DungeonId'),
                        _shop_no)
                    if _sn:
                        _dungeon_logs_put(logs, N=_sn)
                        ent['Status'] = DUNGEON_STATUS['Battle']
                    else:
                        ent['Status'] = DUNGEON_STATUS['SelectMap']
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    db_put('Dungeon', rows)
                log('    dungeon/shop %s key=%s No=%s -> Status=%s'
                    % (gid, req.get('Key'), req.get('No'), ent['Status'] if ent else '?'))
                return self._send(200, _dungeon_response(ent))

            elif path in ('/api/dungeon/end', '/api/dungeon/giveup'):

                _clearing = path.endswith('/end')
                row = None
                if _clearing and ent is not None:
                    grp = _dungeon_group_rec(gid, _dungeon_current_group_no(gid))
                    row = _dungeon_rewards_row(grp.get('RewardId'), ent.get('StaminaRate'))
                    if row:
                        _grant_reward(row['RewardItemId'], row['RewardItemCount'])
                if ent is not None:

                    logs = ent.get('Logs')
                    if not isinstance(logs, dict):
                        logs = {}
                    ent['Logs'] = logs

                    _base = _dungeon_run_total(ent, row)
                    _rate = _dungeon_total_rate(ent)
                    _bonus = (_base * _rate) // 100 if _base > 0 else 0
                    if _base:
                        _dungeon_logs_put(logs, I=(_base + _bonus))

                        _ev = ent.get('EventId')
                        if _ev:
                            _bev_item = None
                            try:
                                _bev_item = (master_tables().get('event') or {}).get(str(_ev), {}).get('EventItem1')
                            except Exception:
                                _bev_item = None
                            if _bev_item:
                                _behavior_event_record(_ev, '102_' + str(_bev_item), _base + _bonus)

                            _mr = int(ent.get('MaxRank') or 0) or _dungeon_max_rank(ent)
                            if _mr:
                                _behavior_event_record(_ev, '114', _mr, absolute=True)

                    if _bonus > 0 and row and row.get('RewardItemId'):
                        _grant_reward(row['RewardItemId'], _bonus)
                    ent['MaxRank'] = max(int(ent.get('MaxRank') or 0), _dungeon_max_rank(ent))
                    ent['Status'] = (DUNGEON_STATUS['Complete'] if _clearing
                                     else DUNGEON_STATUS['None'])
                    ent['ClearCount'] = int(ent.get('ClearCount') or 0) + (1 if _clearing else 0)
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    db_put('Dungeon', rows)
                log('    dungeon/%s %s -> clear=%s'
                    % ('end' if _clearing else 'giveup', gid,
                       row['RewardItemCount'] if row else 0))
                return self._send(200, _dungeon_response(ent, received=[row] if row else None))

            elif path == '/api/dungeon/engage':

                if ent is not None:
                    ent['Status'] = DUNGEON_STATUS['SelectMap']
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    db_put('Dungeon', rows)
                log('    dungeon/engage %s' % gid)
                return self._send(200, _dungeon_response(ent))

            db_put('Dungeon', rows)
            return self._send(200, _dungeon_response(ent))

        if path in _OOPART_WRITES:
            req = _req_json(body)
            oid = req.get('OopartId') or req.get('oopartId') or req.get('Id')
            rows = _rows('Oopart')
            ent = None

            _eplus_plus = None
            for x in rows:
                if isinstance(x, dict) and x.get('Id') == oid:
                    ent = x
                    break
            gold = int(req.get('UseGold') or req.get('useGold') or 0)
            if ent is None:
                log('    oopart%s: unknown oopart %r' % (path[len('/api/oopart'):], oid))
            elif not _spend_gold(gold):
                log('    oopart%s refused: not enough gold (%d)' % (path[len('/api/oopart'):], gold))
            else:
                if path == '/api/oopart/enhance':
                    use = req.get('UseItems') or req.get('useItems') or []
                    used = _spend_use_items(use)
                    if used is None:
                        log('    oopart/enhance %s REFUSED: insufficient materials' % oid)
                        return self._send(200, flush_touched(empty_sync()))
                    gained = 0
                    for u in use:
                        iid = u.get('Item') or u.get('ItemId') or ''
                        cnt = int(u.get('Count') or 0)
                        row = (master_tables().get('item') or {}).get(iid) or {}
                        gained += int(row.get('ItemTypeParameter') or 1) * cnt
                    ent['Experience'] = int(ent.get('Experience') or 0) + gained

                    curve = None
                    for _k, _v in (master_tables().get('oopart_level_exp') or {}).items():
                        curve = _v.get('Records') or []
                    lv = int(ent.get('Level') or 1)
                    for rec in (curve or []):
                        try:
                            if int(rec.get('Level') or 0) > lv and \
                                    ent['Experience'] >= int(rec.get('Exp') or 0):
                                lv = int(rec['Level'])
                        except Exception:
                            continue
                    ent['Level'] = lv
                    log('    oopart/enhance %s exp+%d -> Lv%s (used %d, gold %d)'
                        % (oid, gained, lv, used, gold))
                elif path == '/api/oopart/enhance-limit':
                    ent['LimitOver'] = int(ent.get('LimitOver') or 0) + 1
                    log('    oopart/enhance-limit %s -> %s' % (oid, ent['LimitOver']))
                elif path == '/api/oopart/enhance-skill':

                    _m = _oopart_master(oid) or {}
                    _rar = int(_m.get('Rarity') or 0)
                    _scap = 7 if _rar >= 4 else (5 if _rar == 3 else (3 if _rar == 2 else 0))
                    _cur = int(ent.get('SkillLevel') or 1)
                    if _scap and _cur < _scap:
                        ent['SkillLevel'] = _cur + 1
                        log('    oopart/enhance-skill %s %d -> %d (cap %d, %d*)'
                            % (oid, _cur, ent['SkillLevel'], _scap, _rar))
                    else:
                        log('    oopart/enhance-skill %s REFUSED: %d at cap %d (%d*)'
                            % (oid, _cur, _scap, _rar))
                elif path == '/api/oopart/enhance-plus':

                    st = req.get('StatusType')
                    if st is None:
                        st = req.get('statusType')
                    num = int(req.get('Num') or req.get('num') or 1)
                    _eplus = None
                    try:
                        for _r in (master_tables().get('oopart_enhance_plus') or {}).values():
                            if isinstance(_r, dict) and int(_r.get('StatusType', -1)) == int(st):
                                _eplus = _r
                                break
                    except Exception:
                        _eplus = None

                    FIELD = {0: 'PlusHitPoint', 1: 'PlusAttack', 2: 'PlusDefence',
                             3: 'PlusMagicalDefence', 4: 'PlusAgility', 5: 'PlusCritical'}
                    key = FIELD.get(int(st)) if st is not None else None
                    if _eplus is None or key is None:
                        log('    oopart/enhance-plus %s: no oopart_enhance_plus row for StatusType=%r'
                            % (oid, st))
                    else:
                        _cap = int(_eplus.get('Max') or 0)

                        _floor = int(_eplus.get('Min') or 0)
                        _before = int(ent.get(key) or 0)
                        if _before >= _cap:

                            log('    oopart/enhance-plus %s %s already MAX (%d), refused'
                                % (oid, key, _before))
                        elif _before < _floor:
                            log('    oopart/enhance-plus %s %s=%d below Min %d, refused'
                                % (oid, key, _before, _floor))
                        elif _spend_use_items(req.get('UseItems') or req.get('useItems')) is None:
                            log('    oopart/enhance-plus %s REFUSED: insufficient materials' % oid)
                        else:

                            _add = int(_eplus.get('Add') or 0) or 1
                            ent[key] = min(_before + num * _add, _cap)
                            log('    oopart/enhance-plus %s %s %d +%d -> %d (cap %d)'
                                % (oid, key, _before, num, ent[key], _cap))

                            _BP = {0: 'HitPoint', 1: 'Attack', 2: 'Defence',
                                   3: 'MagicalDefence', 4: 'Agility', 5: 'Critical'}
                            _short = _BP[int(st)]
                            _flds = ('HitPoint', 'Attack', 'Defence',
                                     'MagicalDefence', 'Agility', 'Critical')
                            _eplus_plus = {
                                'Id': oid, 'PlusId': ent.get('PlusId'),
                                'BefPlus': dict(zip(_flds, [int(ent.get('Plus%s' % x) or 0) for x in _flds])),
                                'AddPlus': dict(zip(_flds, [int(ent.get('Plus%s' % x) or 0) for x in _flds])),
                                'AddCount': 1,
                            }

                            _eplus_plus['BefPlus'][_short] = _before
                            _eplus_plus['AddPlus'][_short] = int(ent[key])
                elif path == '/api/oopart/clear-new-oopart':
                    ent['IsNewOopart'] = False
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                db_put('Oopart', rows)
                touch(oid, ent, 'Ooparts')

            _out = flush_touched(empty_sync())
            if _eplus_plus is not None:
                _out['OopartPluses'] = [_eplus_plus]
            return self._send(200, _out)

        if path == '/api/itemgeneration/start':

            req = _req_json(body)
            gid = req.get('ItemGenerationId') or req.get('itemGenerationId')
            iid = req.get('ItemId') or req.get('itemId')
            cnt = int(req.get('Count') or req.get('count') or 0)
            rows, ent = _upsert('ItemGeneration', gid,
                               {'GenerateAt': _now_iso(), 'ItemId': iid,
                                'GenerateCount': 0, 'ReceiveCount': 0,
                                'IsFinish': False})
            if cnt:

                ent['ItemId'] = iid
                ent['GenerateAt'] = _now_iso()

                ent['GenerateCount'] = _itemgen_limit()
                ent['ReceiveCount'] = 0
                ent['IsFinish'] = False
            ent['Ver'] = int(ent.get('Ver') or 0) + 1
            db_put('ItemGeneration', rows)
            touch(gid, ent, 'ItemGenerations')
            log('    itemgeneration/start %s +%d' % (gid, cnt))
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/itemgeneration/receive':

            req = _req_json(body)
            gid = req.get('ItemGenerationId') or req.get('itemGenerationId')
            rows = _rows('ItemGeneration')
            got = []
            _changed = False
            for ent in rows:
                if not isinstance(ent, dict):
                    continue
                if gid and ent.get('Id') != gid:
                    continue

                _auto = bool(req.get('Auto'))
                _finishing = bool(req.get('Finish')) or bool(req.get('Cancel'))
                n = _itemgen_tick(ent)
                if n <= 0 and not _auto and not _finishing:
                    continue
                iid = ent.get('ItemId')
                if iid and n > 0:
                    _grant_item(iid, n)
                    got.append({'RewardId': iid, 'Count': n})

                if _finishing:

                    ent['ReceiveCount'] = min(int(ent.get('GenerateCount') or 0),
                                              int(ent.get('ReceiveCount') or 0) + n)
                    ent['IsFinish'] = True
                    log('    itemgeneration %s: FINISH -> cycle stopped' % ent.get('Id'))
                elif _auto:

                    ent['GenerateAt'] = _now_iso()
                    ent['GenerateCount'] = _itemgen_limit()
                    ent['ReceiveCount'] = 0
                    ent['IsFinish'] = False
                    log('    itemgeneration %s: AUTO refill -> cycle restarted'
                        % ent.get('Id'))
                else:
                    ent['ReceiveCount'] = int(ent.get('ReceiveCount') or 0) + n

                    if int(ent.get('ReceiveCount') or 0) >= int(ent.get('GenerateCount') or 0) > 0:
                        ent['IsFinish'] = True
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                touch(ent.get('Id'), ent, 'ItemGenerations')
                _changed = True

            _gen_units = sum(int(g.get('Count') or 0) for g in got if isinstance(g, dict))
            if _gen_units > 0:
                _mission_progress('item_generation', _gen_units)

            if got or _changed:
                db_put('ItemGeneration', rows)
            log('    itemgeneration/receive -> %d item kind(s)' % len(got))

            one = got[0] if got else None
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'Reward': {'ItemId': (one or {}).get('RewardId'),
                                              'ItemCount': (one or {}).get('Count', 0)}})

        if path in ('/api/mission-pass/receive-all', '/api/mission-pass/unlock-level'):
            req = _req_json(body)
            eid = req.get('EventId') or req.get('eventId')
            rows = _rows('MissionPass')
            done = 0
            for ent in rows:
                if not isinstance(ent, dict):
                    continue
                if eid and ent.get('Id') != eid:
                    continue
                if path.endswith('unlock-level'):
                    lv = int(req.get('Level') or req.get('level') or 0)
                    if lv > 0:
                        ent.setdefault('UnlockedLevel', 0)
                        ent['UnlockedLevel'] = max(int(ent.get('UnlockedLevel') or 0), lv)
                        ent['Ver'] = int(ent.get('Ver') or 0) + 1
                        touch(ent.get('Id'), ent, 'MissionPasses')
                        done += 1
                else:

                    recv = ent.get('ReceivedLevel')
                    if not isinstance(recv, dict):
                        recv = {}
                    master = (master_tables().get('mission_pass') or {}).get(ent.get('Id')) or {}
                    for r in (master.get('Records') or []):
                        no = str(r.get('Level') or r.get('No') or '')
                        if not no or no in recv:
                            continue
                        if r.get('RewardId'):
                            _grant_reward(r.get('RewardId'), int(r.get('Count') or 1))
                        recv[no] = 1
                    ent['ReceivedLevel'] = recv
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    touch(ent.get('Id'), ent, 'MissionPasses')
                    done += 1
            if done:
                db_put('MissionPass', rows)
            log('    %s -> %d changed' % (path, done))
            return self._send(200, flush_touched(empty_sync()))

        if path in ('/api/challenge-mission/receive-clear-reward',
                    '/api/challenge-mission/update-opened-day'):
            req = _req_json(body)
            cid = req.get('Id') or req.get('id')
            rows = _rows('ChallengeMission')
            hit = None
            for x in rows:
                if isinstance(x, dict) and x.get('Id') == cid:
                    hit = x
                    break
            if hit is None and cid:
                hit = {'UserId': user_id(), 'Id': cid, 'StartAt': _now_iso(),
                       'ClearRewardReceived': False, 'OpenedDayTab': 0,
                       'Fix': None, 'Ver': 0}
                rows.append(hit)
            if hit is not None:
                if path.endswith('receive-clear-reward'):
                    if not hit.get('ClearRewardReceived'):
                        hit['ClearRewardReceived'] = True

                        for r in master_tables().get('mission_challenge', {}).values():
                            if not isinstance(r, dict):
                                continue
                            if cid and cid not in str(r.get('ResourceId')):
                                continue
                            for rr in (r.get('Records') or []):
                                if rr.get('RewardId'):
                                    _cc = rr.get('RewardCount')
                                    if _cc is None:
                                        _cc = rr.get('Count')
                                    _grant_reward(rr['RewardId'], int(_cc or 1))
                else:
                    day = int(req.get('Day') or req.get('day') or 0)
                    hit['OpenedDayTab'] = max(int(hit.get('OpenedDayTab') or 0), day)
                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                db_put('ChallengeMission', rows)
                touch(cid, hit, 'ChallengeMissions')
                log('    %s %s -> ok' % (path, cid))
            return self._send(200, flush_touched(empty_sync()))

        if path in _USER_WRITES:
            req = _req_json(body)
            u = db_get('User')
            if not isinstance(u, dict):
                u = {'Id': user_id()}
            if path == '/api/user/name/set':

                nm = req.get('Name') or req.get('name')
                if nm:
                    u['Name'] = nm

            elif path == '/api/user/current-team/set':
                u['CurrentTeam'] = req.get('TeamId') or req.get('teamId')
            elif path == '/api/user/current-quest/set':
                u['CurrentQuestId'] = req.get('QuestId') or req.get('questId')

                d = req.get('Difficulty')
                try:
                    u['CurrentQuestDifficulty'] = int(d) if d is not None else -1
                except Exception:
                    u['CurrentQuestDifficulty'] = -1
            elif path == '/api/user/question/save':

                qid = req.get('Id') or req.get('id') or req.get('QuestionId')
                answered = req.get('Answer') or req.get('answer') or {}
                if not qid:

                    open_now = [q for q in _question_rows() if _in_question_window(q)]
                    qid = (open_now[0].get('ResourceId') if open_now else None)
                row = None
                for q in _question_rows():
                    if q.get('ResourceId') == qid:
                        row = q
                        break

                qrows = db_get('Question')
                if not isinstance(qrows, list):
                    qrows = []
                qent = None
                for x in qrows:
                    if isinstance(x, dict) and x.get('Id') == qid:
                        qent = x
                        break
                if qent is None:

                    qent = {'UserId': user_id(), 'Id': qid, 'Answer': {},
                            'Status': 0, 'Ver': 0}
                    qrows.append(qent)
                first_time = int(qent.get('Status') or 0) == 0
                if isinstance(answered, dict):
                    qent['Answer'] = {str(k): str(v) for k, v in answered.items()}
                qent['Status'] = 1
                qent['Ver'] = int(qent.get('Ver') or 0) + 1
                db_put('Question', qrows)
                paid = None
                _paid_shape = None
                if first_time and row:
                    rid = row.get('RewardId')
                    num = int(row.get('RewardNum') or 1)
                    if rid:
                        _grant_reward(rid, num)
                        paid = '%s x%d' % (rid, num)

                        _paid_shape = {'RewardId': rid,
                                       'AfterCount': _item_count_of(rid),
                                       'AddCount': num,
                                       'IsOver': False}
                _q_view = dict(qent)
                _q_view['Key'] = qent.get('Key') or qid
                return self._send(200, {
                    'SyncData': flush_touched(empty_sync()),
                    'Question': _q_view,
                    'Reward': _paid_shape})
                done = 'question %s answered; reward=%s' % (qid, paid or 'none')

            elif path == '/api/user/vote/save':

                vid = req.get('Id') or req.get('id') or req.get('VoteId')
                target = req.get('TargetId') or req.get('targetId')
                vrows = db_get('Vote')
                if not isinstance(vrows, list):
                    vrows = []
                vent = None
                for x in vrows:
                    if isinstance(x, dict) and x.get('Id') == vid:
                        vent = x
                        break
                if vent is None:

                    vent = {'UserId': user_id(),
                            'Id': vid or ('vote.%s' % (target or 'main')),
                            'TargetId': target,
                            'VotedAt': _now_iso(), 'Ver': 0}
                    vrows.append(vent)
                else:
                    vent['TargetId'] = target
                    vent['VotedAt'] = _now_iso()
                    vent['Ver'] = int(vent.get('Ver') or 0) + 1
                db_put('Vote', vrows)

                _vote_reward = None
                try:
                    _vm = (master_tables().get('vote') or {}).get(vid) or {}
                    _vrid = _vm.get('RewardId')
                except Exception:
                    _vrid = None
                if _vrid:
                    _grant_reward(_vrid, 1)
                    _vote_reward = {'RewardId': _vrid,
                                    'AfterCount': _item_count_of(_vrid),
                                    'AddCount': 1, 'IsOver': False}
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                       'Reward': _vote_reward})
                done = 'vote %s -> %s' % (vid, target)

            elif path == '/api/user/term/set':

                _v = req.get('Version') or req.get('version')
                if not _v:
                    _cur = u.get('TermVersion') or ''
                    _v = _cur.split(':', 1)[1] if ':' in _cur else (_cur or '0.0.1')
                u['TermVersion'] = 'dmm:%s' % _v
            elif path == '/api/user/news/set':
                news = req.get('News') or req.get('news')
                if isinstance(news, list):
                    u['News'] = news
            elif path in ('/api/user/read-flag/set', '/api/user/read-flag/reset'):
                flag = req.get('ReadFlag') or req.get('readFlag')
                rf = u.get('ReadFlag')
                if not isinstance(rf, list):
                    rf = []
                if path.endswith('reset'):
                    newf = req.get('NewReadFlag') or req.get('newReadFlag')
                    rf = [x for x in rf if x != flag]
                    if newf:
                        rf.append(newf)
                elif flag and flag not in rf:
                    rf.append(flag)
                u['ReadFlag'] = rf
            u['Ver'] = int(u.get('Ver') or 0) + 1
            db_put('User', u)
            touch_singleton('User', u)
            log('    %s -> saved' % path)
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/team/update-members':
            req = _req_json(body)
            tid = req.get('TeamId') or req.get('teamId')

            rows, ent = _upsert('Team', tid, {'Name': '', 'Members': [],
                                              'Settings': [], 'FragmentId': None})
            mem = req.get('Members') or req.get('members')
            if isinstance(mem, list):
                ent['Members'] = mem
            nm = req.get('Name') or req.get('name')
            if nm is not None:
                ent['Name'] = nm
            ent['Ver'] = int(ent.get('Ver') or 0) + 1
            db_put('Team', rows)
            touch(tid, ent, 'Teams')
            log('    team/update-members %s -> %d member(s)' % (tid, len(ent.get('Members') or [])))
            return self._send(200, flush_touched(empty_sync()))

        if path in ('/api/social/profile/update', '/api/social/profile/update-score-sheet'):
            req = _req_json(body)
            prof = db_get('Profile')
            if not isinstance(prof, dict):
                prof = {}
            if path.endswith('update-score-sheet'):

                sc = req.get('Records') or req.get('records')
                if isinstance(sc, dict):
                    _tgt = req.get('UserId') or req.get('userId') or user_id()
                    _prows = _rows('UserPossession')
                    _prow = None
                    for _x in _prows:
                        if (isinstance(_x, dict) and _x.get('Id') == 'user_score_sheet'
                                and _x.get('UserId') == _tgt):
                            _prow = _x
                            break
                    if _prow is None:
                        _prow = {'UserId': _tgt, 'Id': 'user_score_sheet',
                                 'ReadFlag': False, 'Records': None, 'Ver': 0}
                        _prows.append(_prow)
                    _prow['Records'] = sc
                    _prow['Ver'] = int(_prow.get('Ver') or 0) + 1
                    db_put('UserPossession', _prows)
                    touch('user_score_sheet', _prow, 'UserPossession')
                    log('    score-sheet saved for %s: %d key(s)' % (_tgt, len(sc)))
                    return self._send(200, flush_touched(empty_sync()))
            else:
                p = req.get('Profile') or req.get('profile')
                if isinstance(p, dict):
                    prof['Profile'] = p

                    for _cid in _challenge_defs():
                        if p.get('Supports'):
                            _behavior_challenge_record(_cid, 29, 1)
                        if p.get('Messages'):
                            _behavior_challenge_record(_cid, 30, 1)
            db_put('Profile', prof)
            log('    %s -> saved' % path)
            _out = flush_touched(empty_sync())

            _pf = prof.get('Profile') if isinstance(prof, dict) else None
            _out['Profile'] = _pf if isinstance(_pf, dict) else prof
            return self._send(200, _out)

        if path in ('/api/social/follower/set', '/api/social/follower/unset',
                    '/api/social/following/set', '/api/social/following/unset'):
            log('    %s -> acknowledged (single-player, no other users)' % path)
            return self._send(200, flush_touched(empty_sync()))

        if path in ('/api/historia/save', '/api/historia/delete'):
            req = _req_json(body)

            hid = req.get('Id') or req.get('id')
            data = req.get('Data') or req.get('data')
            log('    %s id=%s data=%s -> acknowledged'
                % (path, hid, 'yes' if data else 'no'))
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/fragment/clear-new-fragment':
            req = _req_json(body)
            fid = req.get('FragmentId') or req.get('fragmentId')
            rows = _rows('Fragment')
            for x in rows:
                if isinstance(x, dict) and (not fid or x.get('Id') == fid):
                    x['IsNew'] = False
                    x['Ver'] = int(x.get('Ver') or 0) + 1
                    touch(x.get('Id'), x, 'Fragments')
            db_put('Fragment', rows)
            log('    fragment/clear-new-fragment %s' % fid)
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/tutorial/save':
            req = _req_json(body)
            u = db_get('User')
            if isinstance(u, dict):

                _tid = req.get('Id') if req.get('Id') is not None else req.get('id')
                if _tid is not None:
                    _ts = str(_tid)
                    _cur = u.get('Tutorial')
                    if not isinstance(_cur, str):
                        _cur = ''

                    if _ts.isdigit():
                        u['Tutorial'] = _ts
                    else:
                        _b = _cur if (_cur and _cur[0].isdigit()) else ('2' + _cur)
                        _seen = list(_b[1:])
                        for _ch in _ts:
                            if _ch not in _seen:
                                _seen.append(_ch)
                        u['Tutorial'] = _b[0] + ''.join(_seen)

                    _fin = u.get('Tutorial')
                    if isinstance(_fin, str) and _fin and '2' not in _fin \
                            and _fin not in ('0', '7') and not _fin.isdigit():
                        log('    tutorial: %r is not a valid TutorialState; '
                            'keeping the leading digit only' % _fin)
                        u['Tutorial'] = _fin[0] if _fin[0].isdigit() else '2'

                if _ts_is_g(u, _tid):
                    _drawn = _tutorial_gacha_draws(10)
                    u['TCharas'] = _drawn
                    log('    tutorial opening gacha -> %d draws %s'
                        % (len(_drawn), _drawn))

                if str(_tid) == '5':
                    _tc = u.get('TCharas')
                    if isinstance(_tc, list) and _tc:
                        _cnt = {}
                        for _c in _tc:
                            _cnt[str(_c)] = _cnt.get(str(_c), 0) + 1
                        for _cid, _n in _cnt.items():
                            try:
                                _grant_reward(_cid, _n)
                            except Exception as _e:
                                log('    tutorial grant %s failed: %s' % (_cid, _e))
                        log('    tutorial step 5 -> granted %d character(s)' % len(_cnt))
                    u['TCharas'] = []

                    _grant_reward('item.gold', 7000)
                    _grant_reward('item.charaexperience_1', 11)
                    log('    tutorial step 5 -> granted tutorial items (gold 7000, '
                        'charaexperience_1 x11)')

                    _hs_front = ''
                    _tc2 = u.get('TCharas')
                    if isinstance(_tc2, list) and _tc2:
                        _hs_front = str(_tc2[-1])
                    if not _hs_front:

                        try:
                            _ch2 = db_get('Chara') or []
                            if _ch2:
                                _hs_front = str(_ch2[-1].get('Id') or '')
                        except Exception:
                            _hs_front = ''
                    if _hs_front:
                        _hs_rows = db_get('HomeSetting')
                        if not isinstance(_hs_rows, list):
                            _hs_rows = []
                        if not [r for r in _hs_rows if isinstance(r, dict)
                                and r.get('Id') == 'home_setting.1']:
                            _hs_recs = []
                            for _o in range(8):
                                _hs_recs.append({
                                    'Order': _o,
                                    'FrontLayerType': 1 if _o == 0 else 0,
                                    'BackLayerType': 5 if _o == 0 else 0,
                                    'IllustrationLayerType': 0,
                                    'EffectLayerType': 0,
                                    'FrontLayerId': _hs_front if _o == 0 else '',
                                    'FrontLayerSubId': '',
                                    'BackLayerId': 'item.bg.1' if _o == 0 else '',
                                    'IllustrationLayerId': '',
                                    'EffectLayerId': '',
                                    'Reverse': 0,
                                })
                            _hs_rows.append({
                                'UserId': user_id(),
                                'Id': 'home_setting.1',
                                'Records': _hs_recs,
                                'AutoChange': False,
                                'Ver': 1,
                            })
                            db_put('HomeSetting', _hs_rows)
                            touch('home_setting.1', _hs_rows[-1], 'HomeSettings')
                            log('    tutorial step 5 -> created home_setting.1 '
                                '(front %s, back item.bg.1)' % _hs_front)

                    try:
                        _gl_rows = db_get('GodLyric')
                        if not isinstance(_gl_rows, list):
                            _gl_rows = []
                        if not [r for r in _gl_rows if isinstance(r, dict)
                                and r.get('Id') == 'lyric.god.1']:
                            _gl_hit = {'UserId': user_id(), 'Id': 'lyric.god.1',
                                       'UnlockedBitFlag': 0,
                                       'ReadFlag': None, 'ReadAt': None, 'Ver': 0}
                            _gl_rows.append(_gl_hit)
                            _gl_want = _god_unlock_bits('enemy.god.profile.1', 1)
                            if _gl_want:
                                _gl_hit['UnlockedBitFlag'] = _gl_want
                            db_put('GodLyric', _gl_rows)
                            touch('lyric.god.1', _gl_hit, 'GodLyrics')
                            log('    tutorial step 5 -> created lyric.god.1 '
                                '(UnlockedBitFlag=%s)' % _gl_hit['UnlockedBitFlag'])
                    except Exception as _e:
                        log('    tutorial step 5 -> lyric.god.1 create failed: %s' % _e)

                u['Ver'] = int(u.get('Ver') or 0) + 1
                db_put('User', u)
                touch_singleton('User', u)
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/homesetting/initialize-homesetting':
            req = _req_json(body)
            hid = req.get('HomeSettingId') or req.get('homeSettingId')
            rows, ent = _upsert('HomeSetting', hid, {'Records': []})
            ent['Records'] = req.get('Records') or ent.get('Records') or []
            ent['Ver'] = int(ent.get('Ver') or 0) + 1
            db_put('HomeSetting', rows)
            touch(hid, ent, 'HomeSettings')
            log('    homesetting/initialize-homesetting %s' % hid)
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/user-message/read':
            req = _req_json(body)

            mid = (req.get('Id') or req.get('id')
                   or req.get('UserMessageIds') or req.get('Ids'))
            log('    user-message/read %s -> acknowledged' % mid)
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/user-behavior/start-event':

            req = _req_json(body)
            _eid = req.get('EventId') or req.get('eventId') or req.get('Id') or ''
            try:
                _brow, _brows = _behavior_row()
                _behavior_rollover(_brow)
                if _eid:
                    _er = _brow.get('EventRecords')
                    if not isinstance(_er, dict):
                        _er = {}
                    _er.setdefault(str(_eid), {})
                    _brow['EventRecords'] = _er
                _brow['Ver'] = int(_brow.get('Ver') or 0) + 1
                if _brows is None:
                    db_put('UserBehavior', _brow)
                else:
                    db_put('UserBehavior', _brows)
                _out = flush_touched(empty_sync())
                _out['UserBehavior'] = _behavior_clean(_brow)
                log('    user-behavior/start-event -> %s' % _eid)
                return self._send(200, _out)
            except Exception as _e:
                log('    user-behavior/start-event failed: %s' % _e)
                return self._send(200, flush_touched(empty_sync()))

        if path in ('/api/box-gacha/exec', '/api/box-gacha/skip'):

            req = _req_json(body)
            raw_id = req.get('Id') or req.get('id')
            times = int(req.get('Count') or req.get('count') or 1)
            poss_rows = db_get('UserPossession')
            if not isinstance(poss_rows, list):
                poss_rows = []
            bid = _box_gacha_resolve(raw_id)

            if path.endswith('/skip'):
                if bid:
                    _ent = _box_gacha_result_ensure(bid, poss_rows)

                    if not isinstance(_ent.get('Records'), dict):
                        _ent['Records'] = {}
                    _ent['Records']['skip'] = 1
                    _ent['Ver'] = int(_ent.get('Ver') or 0) + 1
                    touch(_box_gacha_poss_id(bid), _ent, 'UserPossession')
                    db_put('UserPossession', poss_rows)
                log('    box-gacha/skip %s -> skipped (next step resolves')
                log('        through box_gacha_result.<id>.Records["skip"])')
                return self._send(200, flush_touched(empty_sync()))

            out_rw = []
            if bid:
                _master = _box_gacha_def(bid)
                _cost_id = _master.get('CostId')
                _cost_n = int(_master.get('CostNum') or 0)
                _ent = _box_gacha_result_ensure(bid, poss_rows)

                _recs = _ent.get('Records')
                if not isinstance(_recs, dict):
                    _recs = {}
                    _ent['Records'] = _recs

                _afford = times
                if _cost_id and _cost_n > 0:
                    _afford = min(_afford, int(_have_item(_cost_id) // _cost_n))
                _pool = sum(_box_gacha_left(bid, _recs).values())
                _n = max(0, min(_afford, _pool))
                if _cost_id and _cost_n > 0 and _n > 0:
                    _consume_item(_cost_id, _n * _cost_n)
                _drawn = _box_gacha_draw(bid, _n, _recs)
                for _no, _cnt in _drawn.items():

                    _recs[_no] = int(_recs.get(_no) or 0) + _cnt
                    _row = None
                    for _r in (_master.get('Records') or []):
                        if str(_r.get('No')) == _no:
                            _row = _r
                            break
                    if _row and _row.get('RewardId'):
                        _grant_reward(_row.get('RewardId'),
                                      int(_row.get('RewardNum') or 1) * _cnt)

                    out_rw.append({'No': int(_no), 'Count': _cnt})
                _ent['Ver'] = int(_ent.get('Ver') or 0) + 1
                touch(_box_gacha_poss_id(bid), _ent, 'UserPossession')
                db_put('UserPossession', poss_rows)

            if bid:
                _bev = _box_gacha_def(bid).get('GroupId')
                if _bev:
                    _behavior_event_record(_bev, '112', times)
            log('    %s %s x%s -> %d draw(s) over %d No'
                % (path, bid, times, sum(_e['Count'] for _e in out_rw), len(out_rw)))

            _bout = {'Rewards': out_rw}
            _bout['SyncData'] = flush_touched(empty_sync())
            return self._send(200, _bout)

        if path == '/api/ranking/get':

            req = _req_json(body)
            rid = req.get('Id') or req.get('id')
            rk = None
            for _x in _rows('Ranking'):
                if isinstance(_x, dict):
                    rk = _x
                    break
            return self._send(200, rk or {'UserId': user_id(), 'Id': rid,
                                          'Score': 0, 'Logs': {}, 'Tm': _now_iso(),
                                          'Status': 0, 'Rank': 1, 'Receive': 0})

        if path in ('/api/ranking/read', '/api/ranking/fetch'):
            req = _req_json(body)
            rid = req.get('Id') or req.get('id')
            if path.endswith('/read'):
                rows = _rows('Ranking')
                rewards = []
                for r in (master_tables().get('ranking_reward') or {}).values():
                    if not isinstance(r, dict):
                        continue
                    if rid and rid not in str(r.get('ResourceId')):
                        continue
                    for rr in (r.get('Records') or []):
                        if rr.get('RewardId'):
                            _grant_reward(rr['RewardId'], int(rr.get('Count') or 1))
                            rewards.append({'RewardId': rr['RewardId'],
                                            'Count': int(rr.get('Count') or 1)})
                    break

                rk = None
                for _x in _rows('Ranking'):
                    if isinstance(_x, dict):
                        rk = _x
                        break
                log('    ranking/read %s -> %d reward(s)' % (rid, len(rewards)))
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                       'Ranking': rk or {'UserId': user_id(),
                                                          'Id': rid, 'Score': 0,
                                                          'Logs': {}, 'Rank': 1,
                                                          'Status': 0, 'Receive': 0},
                                       'Rewards': rewards})
            log('    %s %s -> empty ranking (single player)' % (path, rid))

            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'Ranking': {'UserId': user_id(), 'Id': rid,
                                               'Score': 0, 'Logs': {}, 'Rank': 1,
                                               'Status': 0, 'Receive': 0},
                                   'RankingMyInfo': {'Id': rid, 'RankingRank': 0,
                                                     'Rank': 1, 'NextScore': 0,
                                                     'UpdateTime': _now_iso(),
                                                     'Status': 0}})

        if path.startswith('/api/payment-store/') or path == '/api/payment-recovery':

            req = _req_json(body)
            if path == '/api/payment-recovery':
                log('    payment-recovery -> nothing to recover')
                return self._send(200, flush_touched(empty_sync()))
            if path.endswith('cancel-'):
                log('    payment-store/cancel -> true')
                return self._send(200, True)
            if path == '/api/payment-store/':
                log('    payment-store/begin sku=%s price=%s -> true'
                    % (req.get('SkuId') or req.get('skuId'),
                       req.get('Price') or req.get('price')))
                return self._send(200, True)
            log('    %s -> Verified(0)' % path)
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'verificationResultCode': 0})

        if path in _SHOP_WRITES:
            req = _req_json(body)
            sid = (req.get('ShopId') or req.get('shopId') or req.get('Id')
                   or req.get('id'))
            ids = req.get('ShopIds') or req.get('shopIds')
            if isinstance(ids, list) and len(ids) == 1:
                sid = ids[0]
            if path == '/api/shop/celeb-cycle-benefits':

                log('    shop/celeb-cycle-benefits -> Count=0')
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                       'Count': 0})
            sh = db_get('ShopHold')
            recs = (sh or {}).get('Records') if isinstance(sh, dict) else None
            hit = None
            for r in (recs or []):
                if isinstance(r, dict) and r.get('ResourceId') == sid:
                    hit = r
                    break
            granted = []
            if hit is not None:

                srm = hit.get('ShopRewardMasterData') or {}
                for rr in (srm.get('Records') or []):
                    rid = rr.get('RewardId')
                    if rid:
                        n = int(rr.get('Count') or 1)
                        _grant_reward(rid, n)
                        granted.append('%s x%d' % (rid, n))
                hit['PurchasedAt'] = _now_iso()
                hit['Ver'] = int(hit.get('Ver') or 0) + 1

                try:
                    _srows = _shop_rows()
                    _srow = _shop_entity_for(_srows, sid)
                    _lt, _lv = _shop_limit_for(sid)

                    _shop_expire(_srow, _lt)
                    _srow['Count'] = int(_srow.get('Count') or 0) + 1
                    _srow['Total'] = int(_srow.get('Total') or 0) + 1

                    _srow['UpdatedAt'] = _now_iso()
                    if not _srow.get('OpenAt'):
                        _srow['OpenAt'] = _now_iso()
                    _srow['Ver'] = int(_srow.get('Ver') or 0) + 1
                    db_put('Shop', _srows)

                    _jsonlog = ('    shop %s purchased: Count=%s/%s limitType=%s'
                                ' UpdatedAt=%s'
                                % (sid, _srow.get('Count'), _lv, _lt,
                                   _srow.get('UpdatedAt')))
                    log(_jsonlog)
                except Exception as _e:
                    log('    shop entity update failed: %s' % _e)

            if granted:
                db_put('ShopHold', sh)
                log('    %s %s -> granted %s' % (path, sid, granted))
            else:
                log('    %s %s -> acknowledged (no payout data)' % (path, sid))
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/exchange/update':

            req = _req_json(body)
            try:
                _xrows = db_get('Exchange')
                if not isinstance(_xrows, list):
                    _xrows = []
                _xcleared = _exchange_reset_counters(_xrows)
                _xrefreshed = _exchange_refresh_lineup(_xrows)
                if _xcleared or _xrefreshed:
                    db_put('Exchange', _xrows)
                    log('    exchange/update: cleared %d counter(s), refreshed %d row(s)'
                        % (_xcleared, _xrefreshed))
                _xsync = empty_sync()
                _xsync['Exchanges'] = _xrows
                log('    exchange/update -> %d exchange row(s)' % len(_xrows))
                return self._send(200, _xsync)
            except Exception as _e:
                log('    exchange/update failed: %s' % _e)
                return self._send(200, flush_touched(empty_sync()))

        if path == '/api/exchange/convert-point':

            log('    exchange/convert-point -> nothing to convert')
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'ExpiredJewels': {}, 'ExpiredItems': {}})

        if path in ('/api/data-link/issue', '/api/data-link/confirm', '/api/data-link/preview'):
            req = _req_json(body)
            log('    %s -> acknowledged' % path)

            if path.endswith('confirm'):
                return self._send(200, True)
            if path.endswith('issue'):
                return self._send(200, flush_touched(empty_sync()))

            _u = db_get('User') or {}
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'PlayerId': _u.get('Id'),
                                   'PlayerName': _u.get('Name'),
                                   'Rank': int(_u.get('Rank') or 0),
                                   'LastLoginAt': _u.get('LastLoginAt')})

        if path in _UNIQUE_WRITES:
            req = _req_json(body)
            uid = (req.get('UniqueQuestId') or req.get('uniqueQuestId')
                   or req.get('Id') or req.get('id'))

            _uq_living = []
            _uq_rewards = None
            _uq_result = None

            if path.endswith('/develop'):
                _dn = req.get('No')
                try:
                    _dn = int(_dn) if _dn is not None else None
                except Exception:
                    _dn = None
                if _dn is not None:
                    _drec = None
                    for _r in ((master_tables().get('unique_quest_develop')
                                or {}).get('unique_quest_develop.1') or {}).get('Records') or []:
                        if isinstance(_r, dict) and _r.get('No') == _dn:
                            _drec = _r
                            break
                    _dcost = int((_drec or {}).get('Cost') or 0) if _drec else 0
                    _prows = db_get('UserPossession')
                    if not isinstance(_prows, list):
                        _prows = []
                    _pent = None
                    for _x in _prows:
                        if isinstance(_x, dict) and _x.get('Id') == 'unique_quest_develop':
                            _pent = _x
                            break
                    if _pent is None:
                        _pent = {'UserId': user_id(), 'Id': 'unique_quest_develop',
                                 'ReadFlag': False, 'Records': None, 'Ver': 0}
                        _prows.append(_pent)
                    _prec = _pent.get('Records')
                    if not isinstance(_prec, dict):
                        _prec = {}
                    if _drec is None:

                        log('    develop No=%s REFUSED: not in master' % _dn)
                    elif str(_dn) in _prec:
                        log('    develop No=%s already unlocked -> no-op' % _dn)
                    elif _dcost > 0 and _consume_item('item.unique_quest_material',
                                                     _dcost) is None:
                        log('    develop No=%s REFUSED: need %d material' % (_dn, _dcost))
                    else:
                        _prec[str(_dn)] = 1
                        _pent['Records'] = _prec
                        _pent['Ver'] = int(_pent.get('Ver') or 0) + 1
                        db_put('UserPossession', _prows)
                        touch('unique_quest_develop', _pent, 'UserPossession')
                        log('    develop No=%s unlocked (material -%d), n=%d'
                            % (_dn, _dcost, len(_prec)))
                return self._send(200, flush_touched(empty_sync()))
            rows = _rows('UniqueQuest')
            hit = None
            for x in rows:
                if isinstance(x, dict) and x.get('Id') == uid:
                    hit = x
                    break

            _uq_status_before = hit.get('Status') if isinstance(hit, dict) else None
            if hit is None and uid:
                hit = {'UserId': user_id(), 'Id': uid, 'WeekNum': 0,
                       'StartAt': _now_iso(), 'UpdatedAt': _now_iso(),
                       'UnitCost': 0, 'ClearCount': 0, 'Energy': 0,
                       'Maps': {}, 'Ver': 0}
                rows.append(hit)
            if hit is not None:

                if path.endswith('/start'):

                    hit['Angels'] = []
                    hit['Phantoms'] = []
                    hit['Logs'] = []
                    hit['Ignores'] = []
                    hit['Settings'] = None
                    hit['ActionLogs'] = {'b_m': 30}
                    hit['Selected'] = []

                    _mem = hit.get('Members')

                    if isinstance(_mem, list) and _mem:
                        _cleared = []
                        for _i, _m in enumerate(_mem):
                            if not isinstance(_m, dict):
                                continue
                            _cleared.append({'Order': int(_m.get('Order') if _m.get('Order') is not None else _i),
                                             'CharaId': None,
                                             'OopartId': _m.get('OopartId'),
                                             'GoreiEnabled': False})
                        if _cleared:
                            hit['Members'] = _cleared
                    else:

                        hit['Members'] = [{'Order': _i, 'CharaId': None,
                                           'OopartId': None, 'GoreiEnabled': False}
                                          for _i in range(5)]

                    _now = _now_iso()
                    hit['StartAt'] = _now
                    hit['UpdatedAt'] = _now

                    hit['UnitCost'] = _uq_unit_cost_init()
                    st = req.get('Status')
                    if st is None:
                        st = req.get('status')
                    hit['Status'] = 1 if st is None else int(st)
                    wk = req.get('WeekNum')
                    if wk is None:
                        wk = req.get('weekNum')
                    if wk is not None:
                        hit['WeekNum'] = int(wk)
                    lvl = req.get('Level')
                    if lvl is None:
                        lvl = req.get('level')
                    if lvl is not None:
                        hit['Level'] = int(lvl)
                    maps = req.get('Maps') or req.get('maps')
                    if isinstance(maps, dict):
                        hit['Maps'] = {str(k): str(v) for k, v in maps.items()}
                    sel = req.get('Selected') or req.get('selected')
                    if isinstance(sel, list):
                        hit['Selected'] = [str(x) for x in sel]
                elif path.endswith('/save') or path.endswith('/save-team'):

                    sel = req.get('Selected') or req.get('selected')
                    if isinstance(sel, list):
                        hit['Selected'] = [str(x) for x in sel]
                    tm = req.get('TeamMembers') or req.get('teamMembers')
                    if isinstance(tm, list):
                        hit['Members'] = tm

                    _tm = hit.get('Members') if isinstance(hit.get('Members'), list) else []
                    _angels = [str(m.get('CharaId')) for m in _tm
                               if isinstance(m, dict) and m.get('CharaId')]
                    if path.endswith('/save-team'):

                        _prev_angels = hit.get('Angels')
                        if not isinstance(_prev_angels, list):
                            _prev_angels = []

                        hit['Angels'] = _prev_angels + [a for a in _angels
                                                       if a not in _prev_angels]

                        _prev_n = len([_m for _m in (hit.get('Members') or [])
                                       if isinstance(_m, dict) and _m.get('CharaId')])
                        _cur_uc = hit.get('UnitCost')
                        if not isinstance(_cur_uc, int):
                            _cur_uc = _uq_unit_cost_init()
                        _delta = UQ_UNIT_COST_STEP * (len(_angels) - _prev_n)

                        hit['UnitCost'] = min(_uq_unit_cost_max(),
                                              max(0, _cur_uc - _delta))
                        if _delta:
                            log('    save-team: angels %d -> %d, UnitCost %d -> %d'
                                % (_prev_n, len(_angels), _cur_uc, hit['UnitCost']))

                        _norm = []
                        for _m in _tm:
                            if not isinstance(_m, dict):
                                continue
                            _n = dict(_m)
                            _n['CharaId'] = _n.get('CharaId') or ''
                            _norm.append(_n)
                        if _norm:
                            hit['Members'] = _norm
                    elif _angels:

                        _prev_a2 = hit.get('Angels')
                        if not isinstance(_prev_a2, list):
                            _prev_a2 = []
                        hit['Angels'] = _prev_a2 + [a for a in _angels
                                                   if a not in _prev_a2]

                    _prev_st = hit.get('Status')
                    if _prev_st == 6:
                        log('    %s: run already Complete(6) -> keeping Status=6'
                            % path.rsplit('/', 1)[-1])
                    else:
                        hit['Status'] = 3 if path.endswith('/save-team') else 2
                    se = req.get('Settings') or req.get('settings')
                    if isinstance(se, list):
                        hit['Settings'] = se
                    fid = req.get('FragmentId') or req.get('fragmentId')
                    if fid:
                        hit['FragmentId'] = fid
                elif path.endswith('/select-chara') or path.endswith('/select-phantom'):

                    sid = (req.get('SelectId') or req.get('selectId')
                           or req.get('CharaId') or req.get('charaId')
                           or req.get('PhantomId') or req.get('phantomId') or '')
                    sid = str(sid)

                    _prev_st = hit.get('Status')
                    if _prev_st == 6:
                        log('    %s: run already Complete(6) -> keeping Status=6'
                            % path.rsplit('/', 1)[-1])
                    else:
                        hit['Status'] = 5 if path.endswith('/select-phantom') else 3

                    _prev_angels = hit.get('Angels')
                    if not isinstance(_prev_angels, list):
                        _prev_angels = []
                    _is_join = (not path.endswith('/select-phantom')) and bool(sid)
                    if _is_join and sid not in _prev_angels:
                        _uc_now = hit.get('UnitCost')
                        _uc_now = _uc_now if isinstance(_uc_now, int) else 0

                        _cost = _uq_angel_cost(sid)
                        hit['Angels'] = _prev_angels + [sid]
                        hit['UnitCost'] = max(0, _uc_now - _cost)
                        hit['Selected'] = []
                        log('    ANGEL JOIN %s: UnitCost %d -> %d (-%d), Angels n=%d'
                            % (sid, _uc_now, hit['UnitCost'], _cost,
                               len(hit['Angels'])))
                    if path.endswith('/select-phantom'):

                        cur = hit.get('Selected')
                        if not isinstance(cur, list):
                            cur = []
                        if sid and sid not in cur:
                            cur = cur + [sid]
                        hit['Selected'] = cur
                        ph = hit.get('Phantoms')
                        if not isinstance(ph, list):
                            ph = []
                        if sid and sid not in ph:
                            ph = ph + [sid]
                        hit['Phantoms'] = ph
                    elif _is_join:

                        pass
                    else:
                        cur = hit.get('Selected')
                        if not isinstance(cur, list):
                            cur = []
                        if not sid:
                            cur = []
                        elif sid in cur:
                            cur = [x for x in cur if x != sid]
                        else:
                            cur = cur + [sid]
                        hit['Selected'] = cur
                elif path.endswith('/select-treasure'):

                    sel = req.get('Selected') or req.get('selected')
                    if isinstance(sel, list):
                        hit['Selected'] = sel

                    hit['Status'] = 5

                    pass
                elif path.endswith('/enhance'):

                    _pid = str(req.get('Id') or req.get('id') or '')
                    _prows = db_get('UserPossession')
                    if not isinstance(_prows, list):
                        _prows = []
                    _pent = None
                    for _x in _prows:
                        if isinstance(_x, dict) and _x.get('Id') == 'phantom':
                            _pent = _x
                            break
                    if _pent is None:
                        _pent = {'UserId': user_id(), 'Id': 'phantom',
                                 'ReadFlag': False, 'Records': None, 'Ver': 0}
                        _prows.append(_pent)
                    _prec = _pent.get('Records')
                    if not isinstance(_prec, dict):
                        _prec = {}

                    _lvl = int(_prec.get(_pid) or 0)
                    if _lvl <= 0:
                        log('    unique-quest/enhance %s: not owned -> refused' % _pid)
                    else:
                        _cost = _lvl if _lvl < 15 else 15
                        _mat_ent = _item_entity('item.unique_quest_material')
                        _have_mat = int(_mat_ent.get('Count') or 0)
                        if _have_mat < _cost:
                            log('    unique-quest/enhance %s: need %d material, have %d -> refused'
                                % (_pid, _cost, _have_mat))
                        else:
                            _consume_item('item.unique_quest_material', _cost, strict=False)
                            _prec[_pid] = _lvl + 1
                            _pent['Records'] = _prec
                            _pent['Ver'] = int(_pent.get('Ver') or 0) + 1
                            db_put('UserPossession', _prows)
                            touch('phantom', _pent, 'UserPossession')
                            log('    unique-quest/enhance %s Lv %d -> %d (material -%d)'
                                % (_pid, _lvl, _lvl + 1, _cost))

                    return self._send(200, _uq_tail_sync(path))

                elif path.endswith('/end-battle'):

                    _st = _uq_status_after_node(req.get('No'))
                    hit['Status'] = _st if _st is not None else 4

                    _cell_no = req.get('No')
                    try:
                        _cell_no = int(_cell_no)
                    except Exception:
                        _cell_no = None
                    _battle_rewards = []
                    _uc_gained = 0

                    _pt_gained = 0
                    if _cell_no is not None:
                        _uq_recs = ((master_tables().get('unique_quest') or {})
                                    .get('unique_quest.1') or {}).get('Records') or []
                        _rid = None
                        for _r in _uq_recs:
                            if isinstance(_r, dict) and int(_r.get('No') or -1) == _cell_no:
                                _rid = _r.get('RId')
                                break
                        if _rid is not None:
                            _tbl = (master_tables().get('unique_quest_reward') or {})
                            _row = _tbl.get('uq_reward.%s' % _rid)
                            for _d in ((_row or {}).get('Records') or []):
                                _did = _d.get('RewardId')
                                if not _did:
                                    continue
                                _odds = _d.get('RewardOdds')
                                try:
                                    _odds = int(_odds) if _odds is not None else 100
                                except Exception:
                                    _odds = 100
                                if _odds < 100 and random.randint(1, 100) > _odds:
                                    continue
                                _mn = int(_d.get('MinCount') or 1)
                                _mx = int(_d.get('MaxCount') or _mn)
                                _ct = random.randint(_mn, _mx) if _mx > _mn else _mn
                                if _ct <= 0:
                                    continue
                                _grant_reward(_did, _ct)
                                if _did == 'item.unique_quest_unit_cost':
                                    _uc_gained += _ct
                                elif _did == 'item.unique_quest_pt':
                                    _pt_gained += _ct
                                _battle_rewards.append({'RewardType': _uq_received_type(_d.get('RewardType')),
                                                        'RewardItemId': _did,
                                                        'RewardParam': _d.get('RewardParam'),
                                                        'RewardItemCount': _ct,
                                                        'CelebRate': 0, 'EvDrop': 0, 'CType': 0,
                                                        'GroupNo': 0, 'GroupRewards': None})
                        log('    end-battle No=%s RId=%s -> %d reward(s), bells +%d'
                            % (_cell_no, _rid, len(_battle_rewards), _uc_gained))

                    if _uc_gained:
                        _cur_uc = hit.get('UnitCost')
                        if not isinstance(_cur_uc, int) or _cur_uc < 0:
                            _cur_uc = 0

                        hit['UnitCost'] = min(_uq_unit_cost_max(), _cur_uc + _uc_gained)

                    if _pt_gained:
                        _cur_pt = hit.get('Pt')
                        if not isinstance(_cur_pt, int):
                            _cur_pt = 0
                        hit['Pt'] = _cur_pt + _pt_gained
                        log('    end-battle Pt +%d -> %d' % (_pt_gained, hit['Pt']))

                    if _cell_no is not None:
                        _lg = hit.get('Logs')
                        if not isinstance(_lg, list):
                            _lg = []
                        if _cell_no not in _lg:
                            _lg = _lg + [_cell_no]
                        hit['Logs'] = _lg

                        _al = hit.get('ActionLogs')
                        if not isinstance(_al, dict):
                            _al = {}
                        _al.setdefault('b_m', 30)
                        _acol = req.get('Col')
                        _arow = req.get('Row')
                        if _acol is not None and _arow is not None:
                            _al['select_row_%s' % (len(_lg))] = int(_arow)
                            _al['n_%s_%s' % (_acol, _arow)] = _cell_no
                        _ek = req.get('EnemyKills')
                        if isinstance(_ek, dict):
                            _al['kill'] = int(_al.get('kill') or 0) + sum(
                                int(v or 0) for v in _ek.values())
                        _lgd = req.get('Log')
                        if isinstance(_lgd, str):
                            try:
                                _lj = json.loads(_lgd)
                            except Exception:
                                _lj = None
                            if isinstance(_lj, dict):
                                if _lj.get('td') is not None:
                                    _al['total_d'] = int(_al.get('total_d') or 0) + int(_lj.get('td') or 0)
                                if _lj.get('md') is not None:
                                    _al['max_d'] = max(int(_al.get('max_d') or 0), int(_lj.get('md') or 0))
                        hit['ActionLogs'] = _al
                        log('    end-battle no=%s -> Logs=%s (next row %d)'
                            % (_cell_no, json.dumps(_lg), len(_lg) + 1))
                    _sel = req.get('Selected')

                    if isinstance(_sel, list):

                        _sel_s = [str(x) for x in _sel]
                        _offer = _uq_offered_phantoms(_sel_s, _uq_phantom_pool(), 3)
                        hit['Selected'] = _sel_s + _offer
                        if _offer:
                            log('    end-battle offered %s' % ','.join(_offer))

                    pass
                    _uc = req.get('UnitCost')
                    if _uc is not None:
                        try:
                            hit['UnitCost'] = int(_uc)
                        except Exception:
                            pass
                    _pt = req.get('Pt')
                    if _pt is not None:
                        try:
                            hit['Pt'] = int(_pt)
                        except Exception:
                            pass

                    _alive = req.get('AliveCharaIds')
                    _uq_living = [str(x) for x in (_alive or [])
                                  if isinstance(x, (str, int))]

                    _uq_rewards = _battle_rewards or None
                    _uq_result = req.get('Result')
                    _uq_has_eq = True
                elif path.endswith('/develop'):

                    pass
                elif path.endswith('/exec') or path.endswith('/select-treasure') or path.endswith('/treasure'):

                    _tcg = hit.get('ActionLogs')
                    if not isinstance(_tcg, dict):
                        _tcg = {}
                    _tccol = req.get('Col')
                    _tcrow = req.get('Row')
                    if _tccol is not None and _tcrow is not None:
                        _tcg['o_%s_%s' % (_tccol, _tcrow)] = 1

                        _tcg['ot'] = int(_tcg.get('ot') or 0) + 1
                        hit['ActionLogs'] = _tcg

                    _tno = req.get('No')
                    try:
                        _tno = int(_tno)
                    except Exception:
                        _tno = None
                    if _tno is not None:
                        _lg = hit.get('Logs')
                        if not isinstance(_lg, list):
                            _lg = []
                        if _tno not in _lg:
                            _lg = _lg + [_tno]
                        hit['Logs'] = _lg

                        _al = hit.get('ActionLogs')
                        if not isinstance(_al, dict):
                            _al = {}
                        _tcol = req.get('Col')
                        _trow = req.get('Row')
                        if _tcol is not None and _trow is not None:
                            _al['select_row_%s' % len(_lg)] = int(_trow)
                            _al['n_%s_%s' % (_tcol, _trow)] = _tno
                            hit['ActionLogs'] = _al
                        log('    %s no=%s -> Logs=%s (next row %d) select_row_%d=%s'
                            % (path.rsplit('/', 1)[-1], _tno, json.dumps(_lg),
                               len(_lg) + 1, len(_lg), _trow))
                    _sel = req.get('Selected')
                    if isinstance(_sel, list):

                        _node_mt = _uq_node_mtype(_tno)
                        if _node_mt == 1:
                            _rolled = _uq_treasure_rewards(_tno)
                            if _rolled is not None:
                                hit['Selected'] = _rolled
                                log('    treasure No=%s (MType=1) -> %d rolled reward(s)'
                                    % (_tno, len(_rolled)))
                            else:
                                hit['Selected'] = [str(x) for x in _sel]
                        else:

                            _sel_s = [str(x) for x in _sel]
                            _offer = _uq_offered_phantoms(_sel_s, _uq_phantom_pool(), 3)
                            hit['Selected'] = _sel_s + _offer

                    pass

                    if path.endswith('/exec'):

                        hit['Status'] = 3

                        _en = hit.get('Energy')
                        if not isinstance(_en, int):
                            _en = int(hit.get('Energy') or 0)
                        if _en > 0:
                            hit['Energy'] = _en - 1
                            log('    unique-quest/exec Energy %d -> %d' % (_en, _en - 1))
                    elif path.endswith('/select-treasure'):

                        hit['Status'] = 5
                    else:
                        _st = _uq_status_after_node(_tno)
                        if _st is not None:
                            hit['Status'] = _st
                            log('    %s no=%s MType-derived Status=%d'
                                % (path.rsplit('/', 1)[-1], _tno, _st))
                elif path.endswith('/reset'):

                    _pool = str(req.get('Id') or req.get('id') or '')
                    if _pool in ('develop', 'phantom'):
                        log('    unique-quest/reset pool=%s -> cleared' % _pool)
                    return self._send(200, flush_touched(empty_sync()))
                elif path.endswith('/giveup'):

                    hit['Status'] = 0
                elif path.endswith('/end'):

                    hit['Status'] = 0
                    hit['ClearCount'] = int(hit.get('ClearCount') or 0) + 1

                _closing = (path.endswith('/end') or path.endswith('/giveup'))
                if (not _closing) and _uq_status_before == 6 \
                        and hit.get('Status') != 6:
                    log('    %s: refusing to downgrade Complete(6) -> %s'
                        % (path.rsplit('/', 1)[-1], hit.get('Status')))
                    hit['Status'] = 6
                hit['UpdatedAt'] = _now_iso()
                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                db_put('UniqueQuest', rows)
                touch(uid, hit, 'UniqueQuests')
                log('    %s %s -> saved' % (path, uid))

            if path in _UNIQUE_BARE:

                return self._send(200, _uq_tail_sync(path))

            _rewards_out = None
            _eq_out = None

            _exec_keys = None
            if path.endswith('/exec'):
                _exec_keys = sorted(
                    [str(k) for k, v in (hit.get('Maps') or {}).items()
                     if str(v).startswith('1_')]) if isinstance(hit, dict) else []
            if path.endswith('/select-treasure'):

                _recs = []
                try:
                    _tbl = master_tables().get('unique_quest_reward') or {}
                    _tid = req.get('SelectId') or req.get('selectId')
                    try:
                        _tier = int(_tid) + 1
                    except Exception:
                        _tier = 1

                    _want_rt = 3 if _tier <= 1 else 4
                    _mrow = (_tbl.get('uq_reward.tre_%d' % _tier)
                             or _tbl.get('uq_reward.tre_1') or {})

                    if _want_rt == 4:
                        if _consume_item('item.unique_quest_key', 1) is None:
                            log('    select-treasure tier=4 REFUSED: no Babel key')
                            _mrow = {}
                    for _rec in (_mrow.get('Records') or []):

                        try:
                            _rrt = int(_rec.get('RewardType') if _rec.get('RewardType') is not None else 3)
                        except Exception:
                            _rrt = 3
                        if _rrt != _want_rt:
                            continue
                        _rid = _rec.get('RewardId')
                        if not _rid:
                            continue
                        try:
                            _odds = int(_rec.get('RewardOdds') or 100)
                        except Exception:
                            _odds = 100
                        if _odds < 100 and random.randint(1, 100) > _odds:
                            continue
                        _mn = int(_rec.get('MinCount') or 1)
                        _mx = int(_rec.get('MaxCount') or _mn)
                        _ct = random.randint(_mn, _mx) if _mx > _mn else _mn
                        if _ct <= 0:
                            continue
                        _grant_reward(_rid, _ct)

                        if _rid == 'item.unique_quest_pt':
                            _cur_pt = hit.get('Pt')
                            if not isinstance(_cur_pt, int):
                                _cur_pt = 0
                            hit['Pt'] = _cur_pt + _ct
                        _recs.append({'RewardType': _uq_received_type(_rec.get('RewardType')),
                                      'RewardItemId': _rid,
                                      'RewardParam': _rec.get('RewardParam'),
                                      'RewardItemCount': _ct,
                                      'CelebRate': 0, 'EvDrop': 0, 'CType': 0,
                                      'GroupNo': 0, 'GroupRewards': None})
                    log('    unique-quest/select-treasure tier=%d -> %d reward row(s)'
                        % (_tier, len(_recs)))
                except Exception as _e:
                    log('    select-treasure reward roll failed: %s' % _e)
                _rewards_out = _recs or None
            elif path.endswith('/end'):

                _recs = []
                try:
                    _tbl = master_tables().get('unique_quest_reward') or {}
                    _lvl = 1
                    if isinstance(hit, dict):
                        try:
                            _lvl = int(hit.get('Level') or 1)
                        except Exception:
                            _lvl = 1
                    _mrow = _tbl.get('uq_reward.%d' % _lvl) or _tbl.get('uq_reward.1') or {}
                    for _rec in (_mrow.get('Records') or []):
                        _rid = _rec.get('RewardId')
                        if not _rid:
                            continue
                        _odds = _rec.get('RewardOdds')
                        try:
                            _odds = int(_odds) if _odds is not None else 100
                        except Exception:
                            _odds = 100
                        if _odds < 100 and random.randint(1, 100) > _odds:
                            continue
                        _mn = int(_rec.get('MinCount') or 1)
                        _mx = int(_rec.get('MaxCount') or _mn)
                        _ct = random.randint(_mn, _mx) if _mx > _mn else _mn
                        if _ct <= 0:
                            continue
                        _after = _grant_reward(_rid, _ct)
                        _recs.append({'RewardType': _uq_received_type(_rec.get('RewardType')),
                                      'RewardItemId': _rid,
                                      'RewardParam': _rec.get('RewardParam'),
                                      'RewardItemCount': _ct,
                                      'CelebRate': 0, 'EvDrop': 0, 'CType': 0,
                                      'GroupNo': 0, 'GroupRewards': None})
                    log('    unique-quest/end lv=%d -> %d reward row(s)' % (_lvl, len(_recs)))
                except Exception as _e:
                    log('    unique-quest/end reward roll failed: %s' % _e)
                _rewards_out = _recs or None
            elif path.endswith('/end-battle'):
                _u = db_get('User') or {}
                _br = int(_u.get('Rank') or 1)
                _be = int(_u.get('Exp') or 0)
                _bs = int(_u.get('Stamina') or 0)

                _eq_out = _end_quest_response(
                    received=(_uq_rewards or []),
                    living=_uq_living,
                    result=_uq_result,
                    before_rank=_br, after_rank=_br,
                    before_exp=_be, after_exp=_be,
                    before_stamina=_bs, after_stamina=_bs)

            _sync_out = flush_touched(empty_sync())
            if isinstance(_eq_out, dict):

                _eq_out['SyncData'] = _sync_out
            return self._send(200, {'SyncData': _sync_out,
                                   'EndQuestResponseData': _eq_out,
                                   'ReceivedRewards': _rewards_out,

                                   'ExecKeys': _exec_keys})
        if path in ('/api/lounge/enter', '/api/lounge/update', '/api/lounge/my'):

            if path != '/api/lounge/update':
                _mission_progress('friend_cafe')

            base = db_get('lounge_my') or {}

            _ld = base.get('LoungeData')
            if isinstance(_ld, dict):
                _ld['Id'] = user_id()
                if not _ld.get('Key'):
                    _ld['Key'] = 'lounge'

            out = {
                'SyncData': flush_touched(empty_sync()),
                'LoungeData': _ld,
                'Good': int(base.get('Good') or 0),

                'HistoryUsers': base.get('HistoryUsers') or {},
                'CoffeeFriendPt': int(base.get('CoffeeFriendPt') or 0),
                'FollowFriendPt': int(base.get('FollowFriendPt') or 0),
            }
            if path == '/api/lounge/update':

                try:
                    req = json.loads(body.decode('utf-8')) if body else {}
                except Exception:
                    req = {}
                layout = req.get('Layout')
                if isinstance(layout, dict):

                    _owned = owned_item_ids()
                    _clean = {}
                    _dropped = []
                    for _k, _v in layout.items():
                        if _k in ('randomIds', 'costumeIds'):
                            _ids = [s.strip() for s in str(_v or '').split(',') if s.strip()]
                            _keep = [s for s in _ids if s in _owned]
                            _dropped.extend([s for s in _ids if s not in _owned])
                            _clean[_k] = ','.join(_keep)
                        elif isinstance(_v, str) and _v:
                            if _v in _owned:
                                _clean[_k] = _v
                            else:
                                _dropped.append(_v)
                    if _dropped:
                        log('    lounge/update: dropped %d unowned ref(s): %s'
                            % (len(_dropped), ', '.join(_dropped[:4])))
                    layout = _clean
                    entry = base.get('LoungeData')
                    if not isinstance(entry, dict):
                        entry = {'Key': 'lounge', 'Id': user_id(), 'Ver': 0}
                        base['LoungeData'] = entry
                    entry['Layout'] = layout
                    entry['Ver'] = int(entry.get('Ver') or 0) + 1

                    db_put('lounge_my', base)
                    out['LoungeData'] = entry
                    log('    lounge/update: layout saved (%d keys)' % len(layout))

            log('    lounge%s -> LoungeData=%s HistoryUsers=%s'
                % (path[len('/api/lounge'):], bool(out['LoungeData']),
                   type(out['HistoryUsers']).__name__))

            try:
                _sync = out.get('SyncData')
                log('    LOUNGE-DBG path=%s sync=%s' % (path, type(_sync).__name__))
                if isinstance(_sync, dict):
                    _u = None
                    for _r in (db_get('User') if isinstance(db_get('User'), list) else [db_get('User')]):
                        if isinstance(_r, dict) and _r.get('Id'):
                            _u = _fix_user_row(dict(_r)); break
                    if _u is not None:
                        _sync['User'] = _u
                    _poss = db_get('UserPossession')
                    if isinstance(_poss, list):
                        _sync['UserPossession'] = [_fix_possession_row(dict(x))
                                                  for x in _poss if isinstance(x, dict)]
            except Exception as _le:
                log('    lounge sync fill failed: %s' % _le)
            return self._send(200, out)

        if path == '/api/lounge/enhance-interior':
            req = _req_json(body)
            _track = str((req or {}).get('Id') or (req or {}).get('id') or '')
            _err = None
            _newlv = None
            try:

                _lv_rows = db_get('UserPossession') or []
                _lv_row = None
                for _r in _lv_rows:
                    if isinstance(_r, dict) and _r.get('Id') == 'lounge.Interior_level':
                        _lv_row = _r
                        break
                if _lv_row is None:
                    _err = 'no lounge.Interior_level row'
                else:
                    _recs = _lv_row.get('Records')
                    if not isinstance(_recs, dict):
                        _recs = {}
                        _lv_row['Records'] = _recs
                    _cur = int(_recs.get(_track) or 0)
                    _newlv = _cur + 1

                    _lt = master_tables().get('lounge_interior_level') or {}
                    _tbl = _lt.get(_track) or _lt.get('lounge_interior_level.' + _track)
                    _cost = None
                    for _cr in ((_tbl or {}).get('Records') or []):
                        if int(_cr.get('Level') or 0) == _newlv:
                            _cost = _cr
                            break
                    if _cost is None:
                        _err = 'no cost row for %s level %s' % (_track, _newlv)
                    else:
                        _cid = str(_cost.get('CostId') or '')
                        _cnum = int(_cost.get('Cost') or 0)
                        _gold = int(_cost.get('Gold') or 0)

                        _oid = str((_tbl or {}).get('CostOnlyId') or '')
                        _onum = int(_cost.get('CostOnly') or 0)

                        if _cid and _have_item(_cid) < _cnum:
                            _err = 'not enough %s (%s/%s)' % (_cid, _have_item(_cid), _cnum)
                        elif _oid and _onum and _have_item(_oid) < _onum:
                            _err = 'not enough %s (%s/%s)' % (_oid, _have_item(_oid), _onum)
                        elif _gold and _have_item('item.gold') < _gold:
                            _err = 'not enough gold (%s/%s)' % (_have_item('item.gold'), _gold)
                        else:
                            if _cid and _cnum:
                                _consume_item(_cid, _cnum, strict=False)
                            if _oid and _onum:
                                _consume_item(_oid, _onum, strict=False)
                            if _gold:
                                _consume_item('item.gold', _gold, strict=False)
                            _recs[_track] = _newlv
                            _lv_row['Ver'] = int(_lv_row.get('Ver') or 0) + 1

                            db_put('UserPossession', _lv_rows)
                            touch('lounge.Interior.level', _lv_row, 'UserPossession')
                            log('    enhance-interior %s Lv%s -> Lv%s  (-%s %s, -%s %s, -%s gold)'
                                % (_track, _cur, _newlv, _cnum, _cid,
                                   _onum, _oid, _gold))
            except Exception as _ee:
                _err = str(_ee)[:80]
            if _err:
                log('    enhance-interior REFUSED: %s' % _err)

            _sync = flush_touched(empty_sync())
            return self._send(200, _sync)

        if path == '/api/lounge/good':
            req = _req_json(body)
            _tid = str((req or {}).get('TargetId') or (req or {}).get('targetId') or '')
            if _tid:
                try:
                    _grows = db_get('UserPossession') or []
                    _grow = None
                    for _r in _grows:
                        if isinstance(_r, dict) and _r.get('Id') == 'lounge.good_history':
                            _grow = _r
                            break
                    if _grow is None:
                        _grow = {'UserId': user_id(), 'Id': 'lounge.good_history',
                                 'ReadFlag': False, 'Records': {}, 'Ver': 1}
                        _grows.append(_grow)
                    _gr = _grow.get('Records')
                    if not isinstance(_gr, dict):
                        _gr = {}
                        _grow['Records'] = _gr
                    _gr[_tid] = int(_gr.get(_tid) or 0) + 1
                    _grow['Ver'] = int(_grow.get('Ver') or 0) + 1
                    db_put('UserPossession', _grows)
                    touch('lounge.good_history', _grow, 'UserPossession')
                    log('    lounge/good TargetId=%s -> %s' % (_tid, _gr[_tid]))
                except Exception as _ge:
                    log('    lounge/good failed: %s' % _ge)

            _sync = flush_touched(empty_sync())
            return self._send(200, _sync)

        if path == '/api/lounge/mogu-shampoo':
            req = _req_json(body)
            try:
                _mid = int((req or {}).get('Id') or 1)
            except Exception:
                _mid = 1
            _COST = 100
            try:
                _have = _have_item('item.mogu_shampoo')
                if _have < _COST:
                    log('    mogu-shampoo REFUSED: have %s < %s' % (_have, _COST))
                else:
                    _consume_item('item.mogu_shampoo', _COST, strict=False)
                    log('    mogu-shampoo Id=%s  (-%s mogu_shampoo)' % (_mid, _COST))
            except Exception as _me:
                log('    mogu-shampoo failed: %s' % _me)

            _sync = flush_touched(empty_sync())
            return self._send(200, _sync)

        if path == '/api/lounge/item-mixing':

            _mission_progress('alchemy')

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            mix_id = req.get('Id') or req.get('id')
            n = int(req.get('Count') or req.get('count') or 1)
            row = (master_tables().get('item_mixing') or {}).get(mix_id) or {}
            out_id = row.get('ItemId')
            per = int(row.get('Count') or 1)
            made = 0
            rewards_made = []
            if out_id and n > 0:

                ins = [i for i in (row.get('InItems') or [])
                       if isinstance(i, dict) and i.get('Id') and int(i.get('Cost') or 0) > 0]
                ok = True
                for i in ins:
                    need = int(i.get('Cost') or 0) * n
                    have = int((_item_entity(i['Id']) or {}).get('Count') or 0)
                    if have < need:
                        ok = False
                        log('    item-mixing %s: need %d %s but have %d'
                            % (mix_id, need, i['Id'], have))
                        break
                if ok:
                    for i in ins:
                        _consume_item(i['Id'], int(i.get('Cost') or 0) * n, strict=False)
                    made = per * n
                    _grant_item(out_id, made)
                    rewards_made.append({'RewardId': out_id, 'Count': made})
                    log('    item-mixing %s x%d -> %s x%d' % (mix_id, n, out_id, made))
                else:
                    log('    item-mixing %s refused (insufficient materials)' % mix_id)
            else:
                log('    item-mixing %s: no master row or bad count' % mix_id)

            omake = {}
            for _r in rewards_made:
                if _r.get('RewardId'):
                    omake[_r['RewardId']] = int(omake.get(_r['RewardId']) or 0) + int(_r.get('Count') or 1)
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'OmakeItems': omake})

        if path in ('/api/item/itembox', '/api/item/open-orb', '/api/item/open-orbs',
                    '/api/item/open-stack-orb', '/api/item/open-stack-orbs',
                    '/api/item/remove-orb', '/api/item/remove-orbs',
                    '/api/item/remove-stack-orb'):

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}

            box_id = req.get('Id') or req.get('id')
            if not box_id:
                _cand = req.get('ItemId') or req.get('itemId')
                if _cand and '.box.' in str(_cand):
                    box_id = _cand
            cnt = int(req.get('Count') or req.get('count') or 1)

            _slot_nos = []
            _ids = None
            _cost = 0
            _raw_id = req.get('Id')
            _int_id = isinstance(_raw_id, int) or (isinstance(_raw_id, str)
                                                   and _raw_id.isdigit())
            if path in ('/api/item/open-orb', '/api/item/remove-orb'):
                if _int_id:
                    _slot_nos = [int(_raw_id)]
                    box_id = None

                _cost = int(req.get('Count') or req.get('count') or 0)
            elif path == '/api/item/open-orbs':
                _sl = req.get('Ids') or req.get('ids')
                if isinstance(_sl, list) and _sl:
                    _slot_nos = [int(x) for x in _sl if str(x).lstrip('-').isdigit()]
                    box_id = None

                _cost = int(req.get('Value') or req.get('value') or 0)
            elif path == '/api/item/remove-orbs':
                _sl = req.get('Ids') or req.get('ids')
                if isinstance(_sl, list) and _sl:
                    _ids = [str(x) for x in _sl]
                    box_id = None

            _from_slot = False
            if _slot_nos:
                _srows = _orb_box_rows()
                _sent = _orb_box_entity(_srows)
                _sslots = _sent.get('Slots') or {}
                _was_remove = path.startswith('/api/item/remove')
                _picked = [(n, _sslots[str(n)]) for n in _slot_nos
                           if isinstance(_sslots.get(str(n)), dict)]
                _cost = max(0, int(_cost or 0))

                if not _was_remove and _cost > 0:
                    if not _spend_jewels(_cost, path):
                        return self._send(200, {
                            'SyncData': flush_touched(empty_sync()),
                            'Rewards': [], 'OrbBox': _orb_box_entity(_srows)})
                if not _picked:
                    log('    %s: no such slot(s) %s' % (path, _slot_nos))

                    if _was_remove:
                        return self._send(200, _orb_box_entity(_srows))
                    return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                           'Rewards': [],
                                           'OrbBox': _orb_box_entity(_srows)})
                _from_slot = True
                _rewards = []
                for _n, _hit in _picked:
                    _bid = _hit.get('Id')
                    _cnt = max(1, int(_hit.get('Count') or 1))
                    _sslots.pop(str(_n), None)
                    if not _was_remove and _bid:
                        _rw, _k, _r = _roll_box_rewards(_bid, _cnt)
                        _rewards.extend(_rw)
                _sent['Slots'] = _sslots
                _sent['Ver'] = int(_sent.get('Ver') or 0) + 1
                _orb_slot_sync(_sent)
                db_put('OrbBox', _srows)
                _mark_orb_box_changed()
                log('    %s -> %s %d slot(s) %s, %d reward(s)'
                    % (path, 'freed' if _was_remove else 'opened',
                       len(_picked), [n for n, _ in _picked], len(_rewards)))
                _rrows = _orb_box_rows()
                _rent = _orb_box_entity(_rrows)
                db_put('OrbBox', _rrows)
                if _was_remove:

                    return self._send(200, _rent)
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                       'Rewards': _rewards, 'OrbBox': _rent})

            if _ids is not None:
                _rrows = _orb_box_rows()
                _rent = _orb_box_entity(_rrows)
                _rstk = _rent.get('Stack') or {}
                _dropped = 0
                for _iid in _ids:
                    if _iid in _rstk:
                        _dropped += int(_rstk.pop(_iid) or 0)
                _rent['Stack'] = _rstk
                _rent['Ver'] = int(_rent.get('Ver') or 0) + 1
                _orb_slot_sync(_rent)
                db_put('OrbBox', _rrows)
                _mark_orb_box_changed()
                log('    %s -> dropped %d box(es) from Stack' % (path, _dropped))
                return self._send(200, _rent)

            if path == '/api/item/open-stack-orbs':
                box_id = None
                _slot_no = None
            _all_stack = (path == '/api/item/open-stack-orbs') or (
                not box_id and path.endswith(('orbs', 'orb'))
                and ('open-orbs' in path or 'remove-orbs' in path))
            if _all_stack:
                _arows = _orb_box_rows()

                _aent = _orb_box_entity(_arows)
                _astk = _aent.get('Stack') or {}
                _aslots = _aent.get('Slots') or {}
                _acount = {}
                for _k, _v in _astk.items():
                    _n = int(_v or 0)
                    if _n > 0:
                        _acount[_k] = _acount.get(_k, 0) + _n
                for _v in _aslots.values():
                    if isinstance(_v, dict) and _v.get('Id'):
                        _acount[_v['Id']] = _acount.get(_v['Id'], 0) + 1
                _aids = [k for k, v in _acount.items() if v > 0]
                if not _aids:
                    log('    %s: stack empty' % path)
                    _aent2 = _orb_box_entity(_arows)

                    if path.startswith('/api/item/remove'):
                        return self._send(200, _aent2)
                    return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                           'Rewards': [], 'OrbBox': _aent2})

                _rewards_all = []
                for _aid in _aids:
                    _roll, _k, _r = _roll_box_rewards(_aid, int(_acount.get(_aid) or 0))
                    _rewards_all.extend(_roll)

                for _aid in _aids:
                    _astk.pop(_aid, None)
                _aslots = {_k: _v for _k, _v in _aslots.items()
                           if not (isinstance(_v, dict) and _v.get('Id') in _acount)}
                _aent['Slots'] = _aslots
                _aent['Stack'] = _astk
                _aent['Ver'] = int(_aent.get('Ver') or 0) + 1
                _orb_slot_sync(_aent)
                db_put('OrbBox', _arows)
                _mark_orb_box_changed()
                log('    %s -> opened %d box type(s), %d reward(s)'
                    % (path, len(_aids), len(_rewards_all)))
                _aent2 = _orb_box_entity(_arows)
                if path.startswith('/api/item/remove'):

                    return self._send(200, _aent2)
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                       'Rewards': _rewards_all, 'OrbBox': _aent2})

            _is_orb = _is_orb_box_id(box_id)
            if _is_orb and not _from_slot:
                _orows = _orb_box_rows()

                _oent = _orb_box_entity(_orows)
                _ostk = _oent['Stack']
                _oslots = _oent.get('Slots') or {}
                _held = int(_ostk.get(str(box_id)) or 0)
                _held += sum(1 for _v in _oslots.values()
                             if isinstance(_v, dict) and _v.get('Id') == str(box_id))
                if _held < cnt:
                    cnt = _held
                if cnt > 0:

                    _left = cnt
                    for _sk in sorted(_oslots.keys(), key=lambda z: int(z)):
                        if _left <= 0:
                            break
                        _sv = _oslots.get(_sk)
                        if isinstance(_sv, dict) and _sv.get('Id') == str(box_id):
                            _oslots.pop(_sk, None)
                            _left -= 1
                    if _left > 0:
                        _ostk[str(box_id)] = int(_ostk.get(str(box_id)) or 0) - _left
                        if _ostk[str(box_id)] <= 0:
                            _ostk.pop(str(box_id), None)
                    _oent['Slots'] = _oslots
                    _oent['Stack'] = _ostk
                    _oent['Ver'] = int(_oent.get('Ver') or 0) + 1
                    db_put('OrbBox', _orows)
                    _mark_orb_box_changed()
                    log('    open-orb %s x%d (held %d, left %d)'
                        % (box_id, cnt, _held, _held - cnt))
                else:
                    log('    open-orb %s: none held' % box_id)

            if box_id and not _is_orb:
                _consume_item(box_id, cnt, strict=False)
            rewards = []
            if box_id:
                _sel = (req.get('SelectId') or req.get('selectId')
                        or req.get('ItemId') or req.get('itemId'))
                rewards, key, recs = _roll_box_rewards(box_id, cnt, _sel)
                if not recs:
                    log('    item-box %s: no master row (%s)' % (box_id, key))
            log('    %s -> %d reward(s)' % (path, len(rewards)))

            _orows2 = _orb_box_rows()
            _oent2 = _orb_box_entity(_orows2)
            db_put('OrbBox', _orows2)
            if path.startswith('/api/item/remove'):
                return self._send(200, _oent2)

            if path == '/api/item/itembox':
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                       'Rewards': rewards})
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'Rewards': rewards,
                                   'OrbBox': _oent2})

        _CHARA_WRITES = (
            '/api/chara/enhance', '/api/chara/enhance-limit',
            '/api/chara/enhance-skill', '/api/chara/enhance-skill-wild',
            '/api/chara/enhance-orb', '/api/chara/enhance-auto-orb',
            '/api/chara/enhance-affinity', '/api/chara/enhance-affinity-over',
            '/api/chara/enhance-ura', '/api/chara/enhance-skip',
            '/api/chara/enhance-weapon', '/api/chara/overlap',
            '/api/chara/set-costume', '/api/chara/set-weapon',
            '/api/chara/set-orb', '/api/chara/set-panzer',
            '/api/chara/set-prefs', '/api/chara/clear-new-character',

            '/api/chara/upgrade', '/api/chara/limit-over-affinity',
            '/api/chara/lottery-harmonia', '/api/chara/reset-harmonia',
            '/api/chara/save-harmonia', '/api/chara/reset-weapon',
            '/api/chara/release-orbs', '/api/chara/custom-affinity-point',
        )
        if path in _CHARA_WRITES:

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}

            cid = (req.get('CharaId') or req.get('charaId')
                   or req.get('WeaponId') or req.get('weaponId')
                   or req.get('Id') or req.get('id'))

            chara_rows = _chara_rows()
            ent = _chara_entity(cid, chara_rows) if cid else None
            done = None

            if path == '/api/chara/enhance' and ent is not None:

                gold = int(req.get('UseGold') or req.get('useGold') or 0)
                if not _spend_gold(gold):
                    log('    chara/enhance refused: not enough gold (%d)' % gold)
                else:
                    used = _spend_use_items(req.get('UseItems') or req.get('useItems'))
                    if used is None:
                        log('    chara/enhance %s REFUSED: insufficient materials' % cid)
                        return self._send(200, flush_touched(empty_sync()))

                    gained = 0
                    for u in (req.get('UseItems') or req.get('useItems') or []):
                        iid = u.get('Item') or u.get('ItemId') or ''
                        cnt = int(u.get('Count') or 0)
                        row = (master_tables().get('item') or {}).get(iid) or {}
                        gained += int(row.get('ItemTypeParameter') or 1) * cnt
                    lv, newlv = _apply_enhance_exp(cid, gained, chara_rows)
                    done = 'lv+%d -> %d (exp+%d, used %d, gold %d)' % (lv, newlv, gained, used, gold)

            elif path == '/api/chara/enhance-limit' and ent is not None:
                gold = int(req.get('UseGold') or req.get('useGold') or 0)
                if not _spend_gold(gold):
                    log('    chara/enhance-limit refused: not enough gold')
                else:
                    ent['LimitOver'] = int(ent.get('LimitOver') or 0) + 1
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'LimitOver -> %s (gold %d)' % (ent['LimitOver'], gold)

            elif path in ('/api/chara/enhance-skill', '/api/chara/enhance-skill-wild') \
                    and ent is not None:

                gold = int(req.get('UseGold') or req.get('useGold') or 0)
                stype = req.get('CharaSkillLevelType')
                if stype is None:
                    stype = req.get('charaSkillLevelType')
                try:
                    stype = int(stype)
                except Exception:
                    stype = 0
                if not _spend_gold(gold):
                    log('    chara/enhance-skill refused: not enough gold')
                else:

                    if stype == 0:
                        lvl = int(ent.get('SkillLevel') or 1)
                        if lvl >= _skill_cap(cid, 0):
                            log('    chara/enhance-skill: SkillLevel already at cap')
                        else:
                            ent['SkillLevel'] = lvl + 1
                            done = 'SkillLevel %d -> %d (gold %d)' % (lvl, lvl + 1, gold)
                    elif stype in (1, 2, 3):
                        ex = ent.get('ExSkillLevels')
                        if not isinstance(ex, list) or len(ex) < 3:
                            ex = [0, 0, 0]
                        idx = stype - 1
                        cur_lv = int(ex[idx] or 0)
                        cap = _skill_cap(cid, stype)
                        if cur_lv >= cap:
                            log('    chara/enhance-skill: ExSkill%d already at cap %d'
                                % (stype, cap))
                        else:
                            ex[idx] = cur_lv + 1
                            ent['ExSkillLevels'] = ex
                            done = 'ExSkill%d %d -> %d (gold %d)' % (
                                stype, cur_lv, ex[idx], gold)
                    elif stype == 4:

                        bl = int(ent.get('BlessLevel') or 1)
                        if bl >= _skill_cap(cid, 4):
                            log('    chara/enhance-skill: BlessLevel already at cap')
                        else:
                            ent['BlessLevel'] = bl + 1
                            done = 'BlessLevel %d -> %d (gold %d)' % (bl, bl + 1, gold)
                    else:
                        log('    chara/enhance-skill: unknown skill type %r' % stype)
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1

            elif path in ('/api/chara/enhance-affinity',
                           '/api/chara/enhance-affinity-over') and ent is not None:

                use = req.get('UseItems') or req.get('useItems') or []

                fav = (master_tables().get('chara') or {}).get(cid) or {}
                fav_cat = fav.get('Favorite')
                gained = 0
                for u in use:
                    if not isinstance(u, dict):
                        continue
                    iid = u.get('Item') or u.get('ItemId') or ''
                    cnt = int(u.get('Count') or 0)
                    row = (master_tables().get('item') or {}).get(iid) or {}
                    base = int(row.get('ItemTypeParameter') or 0)
                    mult = 2
                    m = re.match(r'item\.affinity_(\d)_\d$', str(iid))
                    if m and fav_cat is not None and int(m.group(1)) == int(fav_cat):
                        mult = 3
                    gained += (base * cnt * mult) // 2
                used = _spend_use_items(use)
                gold = int(req.get('UseGold') or req.get('useGold') or 0)
                if used is None or not _spend_gold(gold):
                    log('    chara/enhance-affinity %s REFUSED (materials=%s gold=%d)'
                        % (cid, used, gold))
                    return self._send(200, flush_touched(empty_sync()))
                lv, newlv = _apply_affinity_exp(cid, gained, chara_rows)
                done = 'Affinity+%d -> %d (exp+%d from %d item(s))' % (
                    lv, newlv, gained, used)

            elif path == '/api/chara/limit-over-affinity' and ent is not None:

                if _consume_item('item.affinity_limit_over', 1) is None:
                    log('    chara/limit-over-affinity %s REFUSED: no item.affinity_limit_over' % cid)
                    return self._send(200, flush_touched(empty_sync()))
                ent['AffinityLimit'] = int(ent.get('AffinityLimit') or 0) + 1
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                done = 'AffinityLimit -> %s' % ent['AffinityLimit']

            elif path == '/api/chara/custom-affinity-point' and ent is not None:

                ap = req.get('AP') or req.get('ap')
                if isinstance(ap, list):
                    ent['AP'] = [int(x or 0) for x in ap]
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'AP <- %s' % ent['AP']

            elif path == '/api/chara/enhance-ura' and ent is not None:

                gold = int(req.get('UseGold') or req.get('useGold') or 0)
                utype = req.get('Type')
                if utype is None:
                    utype = req.get('type')
                try:
                    utype = int(utype)
                except Exception:
                    utype = 0
                lv = int(req.get('Lv') or req.get('lv') or 1)
                if not _spend_gold(gold):
                    log('    chara/enhance-ura refused: not enough gold')
                else:
                    ura = ent.get('Ura')
                    if not isinstance(ura, list) or len(ura) < 6:
                        ura = [0, 0, 0, 0, 0, 0]
                    if 0 <= utype < 6:
                        ura[utype] = max(int(ura[utype] or 0), lv)
                        ent['Ura'] = ura
                        ent['Ver'] = int(ent.get('Ver') or 0) + 1
                        done = 'Ura[%d] -> %s (gold %d)' % (utype, ura[utype], gold)
                    else:
                        log('    chara/enhance-ura: Type %r out of range' % utype)

            elif path == '/api/chara/enhance-orb':

                try:
                    _oid = int(req.get('Id') or req.get('id') or 0)
                except Exception:
                    _oid = 0
                if _oid <= 0:

                    _bump_possession('orb_slot.%s' % (req.get('ToOrb') or 0),
                                     int(req.get('Count') or 0), cid)
                    done = 'enhance-orb (unrecognised body)'
                else:
                    try:
                        _want = max(1, int(req.get('Count') or 1))
                    except Exception:
                        _want = 1
                    _no, _rank = _oid // 100, _oid % 100
                    if _rank <= 1:
                        log('    enhance-orb %d REFUSED: rank 1 has no lower rank' % _oid)
                        done = 'enhance-orb rank1 no-op'
                    else:
                        _src = _no * 100 + (_rank - 1)
                        _need = _want * 2
                        if not _orb_inventory_take(_src, _need):
                            log('    enhance-orb %d REFUSED: need %d x %d'
                                % (_oid, _need, _src))
                            done = 'enhance-orb refused (insufficient %d)' % _src
                        else:
                            _orb_inventory_add(_oid, _want)
                            log('    enhance-orb %d x%d  <-  %d x%d'
                                % (_oid, _want, _src, _need))
                            done = 'synthesised %d x%d from %d x%d' % (
                                _oid, _want, _src, _need)
                if ent is not None:
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1

            elif path == '/api/chara/enhance-auto-orb':

                _to = req.get('ToOrbs') or req.get('toOrbs') or []
                _from = req.get('FromOrbs') or req.get('fromOrbs') or []

                _want = {}
                for _o in _from:
                    try:
                        _k = int(_o.get('Id') or _o.get('Orb') or 0)
                        _n = int(_o.get('Count') or 0)
                    except Exception:
                        _k, _n = 0, 0
                    if _k > 0 and _n > 0:
                        _want[_k] = _want.get(_k, 0) + _n
                _have = _orb_inventory_counts()
                _short = {k: (_want[k], _have.get(k, 0)) for k in _want
                          if _have.get(k, 0) < _want[k]}
                if _short:
                    log('    enhance-auto-orb REFUSED: insufficient %s'
                        % json.dumps(_short, ensure_ascii=False)[:160])
                    done = 'auto-orb refused (insufficient)'
                else:
                    _made = []
                    for _k, _n in _want.items():
                        _orb_inventory_take(_k, _n)
                    for _o in _to:
                        try:
                            _k = int(_o.get('Id') or _o.get('Orb') or 0)
                            _n = int(_o.get('Count') or 0)
                        except Exception:
                            _k, _n = 0, 0
                        if _k > 0 and _n > 0:
                            _orb_inventory_add(_k, _n)
                            _made.append('%d+%d' % (_k, _n))
                    log('    enhance-auto-orb consumed %s -> made %s'
                        % (json.dumps(_want), ', '.join(_made)))
                    done = 'auto-orb %s' % ', '.join(_made)
                if ent is not None:
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1

            elif path == '/api/chara/enhance-skip':

                iid = req.get('ItemId') or req.get('itemId')
                if iid and _consume_item(iid, 1) is None:
                    log('    chara/enhance-skip REFUSED: no %s' % iid)
                    return self._send(200, flush_touched(empty_sync()))
                done = 'skip-enhance via %s' % iid

            elif path == '/api/chara/enhance-weapon':

                use = req.get('UseItems') or req.get('useItems') or []

                wid = (req.get('Id') or req.get('id')
                       or req.get('WeaponId') or req.get('weaponId'))
                used = _spend_use_items(use)
                if used is None:
                    log('    chara/enhance-weapon %s REFUSED: insufficient materials'
                        % wid)

                    return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                           'Failed': None})
                gained = 0
                for u in use:
                    iid = u.get('Item') or u.get('ItemId') or ''
                    cnt = int(u.get('Count') or 0)
                    row = (master_tables().get('item') or {}).get(iid) or {}

                    gained += int(row.get('Pt') or 0) * cnt

                lv = 1

                ladder = master_tables().get('weapon_level_exp') or {}
                steps = []
                for r in ladder.values():
                    if not isinstance(r, dict) or not r.get('Lv'):
                        continue
                    try:
                        rate = int(r.get('Rate') or 0)
                    except Exception:
                        rate = 0
                    if rate <= 0:
                        continue

                    steps.append((int(r['Lv']), int(r.get('Limit') or 0) // rate,
                                  rate, int(r.get('Limit') or 0)))
                steps.sort()
                wrows = db_get('UserPossession')
                if not isinstance(wrows, list):
                    wrows = []
                wp = None
                for x in wrows:
                    if isinstance(x, dict) and x.get('Id') == wid:
                        wp = x
                        break
                if wp is None:

                    if not wid:
                        log('    chara/enhance-weapon: no weapon id -> no-op')
                        return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                               'Failed': None})

                    wp = {'UserId': user_id(), 'Id': wid, 'ReadFlag': True,
                          'Records': None, 'Ver': 0}
                    wrows.append(wp)
                recs = wp.get('Records')
                if not isinstance(recs, dict):
                    recs = {}
                try:
                    lv = int(recs.get('Lv') or 1)
                except Exception:
                    lv = 1

                try:
                    _b = (gained ** 0.2) / 10.0
                    bonus = int(round(_b, 2) * gained)
                except Exception:
                    bonus = 0

                _step = None
                _rate = 0
                _limit = 0
                for _lv, _need, _rt, _lm in steps:
                    if _lv == lv + 1:
                        _step, _rate, _limit = _need, _rt, _lm
                        break
                _total = gained + bonus
                _ok = True
                if _step is not None and _rate > 0 and _limit > 0:
                    _pct = min(_limit, _rate * _total) / 10000.0

                    _ok = (_pct >= 100.0) or (random.random() * 100.0 < _pct)
                elif _step is not None:
                    _ok = _total >= _step
                pool = int(recs.get('Exp') or 0) + _total
                new_lv = lv

                for _slv, _sneed, _sr, _sl in steps:
                    if _slv <= new_lv:
                        continue
                    if _sneed and pool >= _sneed:
                        pool -= _sneed
                        new_lv = _slv
                    else:
                        break

                if not _ok:

                    _spent = {}
                    for _u in (use if isinstance(use, list) else []):
                        if isinstance(_u, dict) and _u.get('Item'):
                            _spent[str(_u['Item'])] = (int(_spent.get(str(_u['Item'])) or 0)
                                                       + int(_u.get('Count') or 0))

                    _fail = {'Key': 'weapon_enhance_failed', 'Id': user_id(),
                             'Use': _spent, 'WeaponId': wid, 'Ver': 0}
                    _frows = db_get('WeaponEnhanceFailed')
                    if not isinstance(_frows, list):
                        _frows = []
                    _frows = [x for x in _frows
                              if not (isinstance(x, dict) and x.get('Id') == wid)]
                    _frows.append(_fail)
                    db_put('WeaponEnhanceFailed', _frows)

                    recs['Exp'] = int(recs.get('Exp') or 0) + _total
                    wp['Records'] = recs
                    wp['Ver'] = int(wp.get('Ver') or 0) + 1
                    db_put('UserPossession', wrows)
                    touch(wid, wp, 'UserPossession')
                    log('    enhance-weapon FAILED %s (total %d < %s); spent %s'
                        % (wid, _total, _step, _spent))
                    return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                           'Failed': _fail})
                recs['Lv'] = new_lv
                recs['Exp'] = pool
                wp['Records'] = recs
                if not wp.get('ReadFlag'):
                    wp['ReadFlag'] = True
                wp['Ver'] = int(wp.get('Ver') or 0) + 1
                db_put('UserPossession', wrows)

                touch(wid, wp, 'UserPossession')
                done = ('weapon %s -> lv %d (exp+%d, used %d)'
                        % (wid, new_lv, gained, used))

            elif path == '/api/chara/set-orb':

                orbs = req.get('Orbs') or req.get('orbs')
                if isinstance(orbs, list):
                    for i, v in enumerate(orbs):
                        _set_possession_count('orb_slot.%d' % i, int(v or 0), cid)
                    if ent is not None:
                        ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'orbs <- %s' % orbs

            elif path == '/api/chara/set-panzer' and ent is not None:

                rows = _rows('Panzer')
                hit = None
                for x in rows:
                    if isinstance(x, dict) and x.get('Id') == cid:
                        hit = x
                        break
                if hit is None:

                    hit = {'Key': 'panzer_' + user_id(), 'Id': cid,
                           'Myset': [], 'Ver': 0}
                    rows.append(hit)
                myset = req.get('Myset') or req.get('myset')
                if isinstance(myset, list):
                    hit['Myset'] = myset

                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                db_put('Panzer', rows)

                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                done = 'panzer myset=%d key=%s' % (len(hit.get('Myset') or []), hit.get('Key'))

            elif path == '/api/chara/reset-weapon':

                wrows = db_get('UserPossession')
                if not isinstance(wrows, list):
                    wrows = []

                _rwd = (req.get('Id') or req.get('id')
                        or req.get('WeaponId') or req.get('weaponId'))
                _frows = db_get('WeaponEnhanceFailed')
                if not isinstance(_frows, list):
                    _frows = []
                _rec = None
                if _rwd:
                    for x in _frows:
                        if isinstance(x, dict) and x.get('Id') == _rwd:
                            _rec = x
                            break
                if _rec is None and _frows:

                    for x in reversed(_frows):
                        if isinstance(x, dict) and isinstance(x.get('Use'), dict):
                            _rec = x
                            break
                if _rec is not None and not _rwd:
                    _rwd = _rec.get('Id') or _rec.get('WeaponId')
                _given = {}
                if _rec is not None:
                    _use = _rec.get('Use')
                    if isinstance(_use, dict):
                        for _rid, _n in _use.items():
                            _grant_item(_rid, int(_n or 0))
                            _given[_rid] = int(_n or 0)
                    _frows = [x for x in _frows if x is not _rec]
                    db_put('WeaponEnhanceFailed', _frows)

                for x in wrows:
                    if isinstance(x, dict) and x.get('Id') == _rwd:
                        _rc = x.get('Records')
                        if not isinstance(_rc, dict):
                            _rc = {}
                        _rc['Exp'] = 0
                        x['Records'] = _rc
                        x['Ver'] = int(x.get('Ver') or 0) + 1
                        touch(_rwd, x, 'UserPossession')
                        break
                if _consume_item('item.weapon_reset', 1) is None:
                    log('    chara/reset-weapon REFUSED: no item.weapon_reset')
                    return self._send(200, flush_touched(empty_sync()))
                db_put('UserPossession', wrows)
                done = ('weapon reset %s; refunded %s' % (_rwd, _given or 'nothing'))
                log('    chara/reset-weapon %s -> refunded %s' % (_rwd, _given))

            elif path == '/api/chara/release-orbs':

                slots = req.get('ReleaseSlots') or req.get('releaseSlots') or []
                for s in slots:
                    k = s.get('SlotNo') if isinstance(s, dict) and s.get('SlotNo') is not None else s
                    _set_possession_count('orb_slot.%s' % k, 0, cid)
                if ent is not None:
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                done = 'released %d orb slot(s)' % len(slots)

            elif path in ('/api/chara/lottery-harmonia',
                           '/api/chara/save-harmonia',
                           '/api/chara/reset-harmonia') and ent is not None:

                req = req or {}
                slot = req.get('SlotNo')
                if slot is None:
                    slot = req.get('slotNo')
                try:
                    slot = int(slot) if slot is not None else 0
                except Exception:
                    slot = 0
                slot_key = str(slot)

                unsupported = req.get('UnsupportedIds') or req.get('unsupportedIds') or []

                kept = ent.get('Harmonia')
                if not isinstance(kept, dict):
                    kept = {}
                else:
                    kept = dict(kept)
                stacks = ent.get('StackHarmonia')
                if not isinstance(stacks, dict):
                    stacks = {}
                else:
                    stacks = dict(stacks)

                if path == '/api/chara/save-harmonia':

                    val = req.get('Harmonia') or req.get('harmonia')
                    if val:
                        kept[slot_key] = _strip_harmonia_prefix(val)
                        ent['Harmonia'] = kept

                    stacks.pop('Items', None)
                    stacks.pop(slot_key, None)
                    ent['StackHarmonia'] = stacks if stacks else None
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'harmonia slot %s saved = %s' % (slot_key, val)

                elif path == '/api/chara/reset-harmonia':
                    stacks.pop('Items', None)
                    stacks.pop(slot_key, None)
                    ent['StackHarmonia'] = stacks if stacks else None
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'harmonia slot %s stack cleared' % slot_key

                else:

                    used = _spend_use_items(req.get('UseItems') or req.get('useItems'))
                    if used is None:
                        log('    chara/lottery-harmonia %s REFUSED: insufficient materials' % cid)
                        return self._send(200, flush_touched(empty_sync()))
                    num = int(req.get('Num') or req.get('num') or 1)
                    pool = []
                    for key, row in (master_tables().get('harmonia') or {}).items():
                        rid = row.get('ResourceId') or key
                        if rid in unsupported:
                            continue
                        pool.append(row)
                    rolls = []
                    for _ in range(max(1, num)):
                        if not pool:
                            break

                        pick = random.choice(pool)
                        rates = [int(x) for x in (pick.get('Rate') or [1])]
                        lv = random.choices(range(len(rates)),
                                            weights=[max(1, r) for r in rates], k=1)[0]
                        rolls.append('%s_%d' % (_strip_harmonia_prefix(pick.get('ResourceId')), lv))
                    if rolls:

                        stacks['Items'] = ','.join(rolls)
                        ent['StackHarmonia'] = stacks
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'harmonia lottery slot %s -> %s (used %d item(s))' % (
                        slot_key, stacks.get(slot_key), used)

            elif path == '/api/chara/overlap' and ent is not None:

                ent['Overlap'] = int(ent.get('Overlap') or 0) + 1
                ent['Ver'] = int(ent.get('Ver') or 0) + 1

                done = 'Overlap -> %s (ringo=%s)' % (ent['Overlap'],
                                                    req.get('IsUseRingo'))

            elif path == '/api/chara/set-costume' and ent is not None:

                ent['Costume'] = (req.get('CostumeItemId') or req.get('costumeItemId')
                                  or req.get('CostumeId') or req.get('costumeId'))
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                done = 'Costume -> %s' % ent['Costume']

            elif path == '/api/chara/set-weapon' and ent is not None:
                ent['Weapon'] = {'WeaponId': req.get('WeaponId') or req.get('weaponId'),
                                 'WeaponStyleId': req.get('WeaponStyleId') or req.get('weaponStyleId')}
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                done = 'Weapon -> %s' % ent['Weapon']

            elif path == '/api/chara/set-prefs' and ent is not None:

                prefs = req.get('Prefs') or req.get('prefs')
                if isinstance(prefs, dict):
                    ent['Prefs'] = prefs
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'Prefs <- %s' % list(prefs)

            elif path == '/api/chara/clear-new-character' and ent is not None:
                ent['IsNewCharacter'] = False
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                done = 'IsNewCharacter -> False'

            elif path == '/api/chara/set-readflag' and ent is not None:
                flag = req.get('ReadFlag') or req.get('readFlag')
                is_read = req.get('IsRead')
                rf = ent.get('ReadFlag')
                if not isinstance(rf, list):
                    rf = []
                if flag:
                    if is_read is False:
                        rf = [x for x in rf if x != flag]
                    elif flag not in rf:
                        rf.append(flag)
                ent['ReadFlag'] = rf
                ent['Ver'] = int(ent.get('Ver') or 0) + 1
                done = 'ReadFlag %s %s' % ('-' if is_read is False else '+', flag)

            elif path == '/api/chara/upgrade' and ent is not None:

                gold = int(req.get('UseGold') or req.get('useGold') or 0)
                if not _spend_gold(gold):
                    log('    chara/upgrade refused: not enough gold')
                else:
                    g = int(ent.get('Grade') or 0)
                    ent['Grade'] = g + 1
                    ent['Level'] = 1
                    ent['Experience'] = 0
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    done = 'Grade %d -> %d, Level reset to 1 (gold %d)' % (
                        g, g + 1, gold)

            if ent is not None:
                db_put('Chara', chara_rows)
                touch(cid, ent)
            log('    chara%s -> %s' % (path[len('/api/chara'):], done or 'no-op'))
            sync = flush_touched(empty_sync())

            if path == '/api/chara/enhance-weapon':
                return self._send(200, {'SyncData': sync, 'Failed': None})
            return self._send(200, sync)

        if path in ('/api/chara/set-readflag', '/api/chara/set-readflag-reward'):

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            cid = req.get('CharaId') or req.get('charaId')
            flag = req.get('ReadFlag') or req.get('readFlag')
            is_reward = path.endswith('-reward')
            ent = None
            if cid:
                rows = db_get('Chara')
                if isinstance(rows, list):
                    for x in rows:
                        if isinstance(x, dict) and x.get('Id') == cid:
                            ent = x
                            break
                    if ent is None:

                        ent = {'UserId': user_id(), 'Id': cid, 'Grade': 0,
                               'Level': 1, 'Experience': 0, 'Affinity': 0,
                               'AffinityExp': 0, 'Count': 1, 'Overlap': 0,
                               'ReceivedAt': None, 'IsNewCharacter': False,
                               'SkillLevel': 1, 'ExSkillLevels': None,
                               'BlessLevel': 1, 'ReadFlag': [],
                               'Costume': None, 'Weapon': None, 'WS': None,
                               'Ura': None, 'Prefs': None, 'AffinityLimit': 0,
                               'AP': None, 'LimitOver': 0, 'Harmonia': None,
                               'StackHarmonia': None, 'Ver': 0}
                        rows.append(ent)

                    newly_read = bool(flag) and flag not in (ent.get('ReadFlag') or [])
                    if flag:
                        rf = ent.get('ReadFlag')
                        if not isinstance(rf, list):
                            rf = []
                        if flag not in rf:
                            rf.append(flag)
                        ent['ReadFlag'] = rf
                    if is_reward and newly_read:
                        amount = 100
                        try:
                            gs = master_tables().get('game_setting') or {}
                            row = gs.get('game_setting.synopsis_reward_count') or {}
                            amount = int(row.get('Value') or 100)
                        except Exception:
                            amount = 100
                        _grant_item('item.jewel_free', amount)
                        log('    synopsis reward: %s x%d for %s'
                            % ('item.jewel_free', amount, flag))
                    ent['Ver'] = int(ent.get('Ver') or 0) + 1
                    db_put('Chara', rows)
                    touch(cid, ent)
                    log('    set-readflag %s <- %s (now %d flag(s))'
                        % (cid, flag, len(ent.get('ReadFlag') or [])))

            sync = flush_touched(empty_sync())
            if ent is not None and not sync.get('Characters'):
                sync['Characters'] = [ent]
            return self._send(200, sync)

        if path in ('/api/user/recover-item', '/api/user/recover-jewel'):

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            uses = parse_use_items(req)
            if path.endswith('recover-jewel'):
                recover_stamina('item.jewel_free', 50)
                return self._send(200, flush_touched(empty_sync()))
            if not uses:
                log('    recover-item: no UseItems in body %r' % (req,))
                return self._send(200, flush_touched(empty_sync()))

            cur = int((db_get('User') or {}).get('Stamina') or 0)
            if cur >= STAMINA_HARD_MAX:
                log('    recover-item: refused, stamina already at hard cap %d'
                    % STAMINA_HARD_MAX)
                return self._send(200, flush_touched(empty_sync()))

            _need = {}
            for iid, cnt in uses:
                _need[iid] = _need.get(iid, 0) + cnt
            _short = None
            for iid, cnt in _need.items():
                have = _have_item(iid)
                if have < cnt:
                    _short = '%s (need %d, have %d)' % (iid, cnt, have)
                    break
            if _short:
                log('    recover-item REFUSED: insufficient %s' % _short)
                return self._send(200, flush_touched(empty_sync()))
            total = 0
            for iid, cnt in uses:
                _consume_item(iid, cnt, strict=False)
                gain = stamina_amount(iid) * cnt
                if gain:
                    total += gain
                else:
                    log('    recover-item: %s restores no stamina (master missing?)' % iid)
            if total:
                recover_stamina(uses[0][0], total)
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/user-possession/read-quest-story':
            try:
                _qreq = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                _qreq = {}
            if not isinstance(_qreq, dict):
                _qreq = {}
            _qid = (_qreq.get('QuestId') or _qreq.get('questId')
                    or _qreq.get('Id') or _qreq.get('UserPossessionId'))
            _rewards = []
            try:
                if _qid:
                    _rewards = roll_quest_rewards(_qid, 0) or []
            except Exception as _e:
                log('    read-quest-story reward roll failed: %s' % _e)
            log('    read-quest-story %s -> %d reward(s)' % (_qid, len(_rewards)))
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'ReceivedRewards': _rewards})

        if path.startswith('/api/user-possession/'):

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}

            if path.endswith('/set-count-record'):
                pid = req.get('Id') or req.get('UserPossessionId')
                key = req.get('Key')
                try:
                    cnt = int(req.get('Count') or 0)
                except Exception:
                    cnt = 0
                if pid:
                    rows = db_get('UserPossession')
                    if not isinstance(rows, list):
                        rows = []
                    hit = None
                    for r in rows:
                        if isinstance(r, dict) and r.get('Id') == pid:
                            hit = r
                            break
                    if hit is None:

                        hit = {'UserId': user_id(), 'Id': pid, 'ReadFlag': False,
                               'Records': None, 'Ver': 0}
                        rows.append(hit)
                    recs = hit.get('Records')
                    if not isinstance(recs, dict):
                        recs = {}
                    if key:
                        recs[str(key)] = cnt

                    hit['Records'] = recs if recs else None

                    hit['Ver'] = int(hit.get('Ver') or 0) + 1
                    db_put('UserPossession', rows)
                    touch(pid, hit, 'UserPossession')
                    log('    user-possession/set-count-record %s[%s] = %s'
                        % (pid, key, cnt))
                    return self._send(200, flush_touched(empty_sync()))

            if path.endswith('/add-records'):
                pid = (req.get('UserPossessionId') or req.get('userPossessionId')
                       or req.get('Id'))
                add = req.get('Records')
                if not isinstance(add, dict):
                    add = {}
                rows = db_get('UserPossession')
                if not isinstance(rows, list):
                    rows = []
                hit = None
                for r in rows:
                    if isinstance(r, dict) and r.get('Id') == pid:
                        hit = r
                        break
                if hit is None:

                    hit = {'UserId': user_id(), 'Id': pid, 'ReadFlag': False,
                           'Records': None, 'Ver': 0}
                    rows.append(hit)
                cur = hit.get('Records')
                if not isinstance(cur, dict):
                    cur = {}
                for k, v in add.items():
                    cur[k] = v

                hit['Records'] = cur if cur else None
                hit['ReadFlag'] = bool(hit.get('ReadFlag', False))
                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                db_put('UserPossession', rows)
                touch(pid, hit, 'UserPossession')
                log('    user-possession/add-records %s +%d -> %d record(s)'
                    % (pid, len(add), len(cur)))
                return self._send(200, flush_touched(empty_sync()))
            pid = (req.get('UserPossessionId') or req.get('userPossessionId')
                   or req.get('Id'))
            ent = mark_possession_read(pid)
            log('    user-possession%s -> %s' % (path[len('/api/user-possession'):], pid))
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/exchange/convert':

            converted = convert_duplicate_items()
            log('    exchange/convert -> %d stack(s): %s'
                % (len(converted), converted[:8]))
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/quest/big-battle/skip':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}

            quest_id = req.get('Id') or req.get('id')

            _bb_score = 0
            try:
                for _x in (db_get('BigBattle') or []):
                    if isinstance(_x, dict) and _x.get('Id') == quest_id:
                        _bb_score = int(_x.get('Score') or 0)
                        break
            except Exception:
                _bb_score = 0
            rewards = _rank_reward_rows(
                'rank_reward.%s' % quest_id, _bb_score, key='Rank')
            if not rewards:

                rewards = merge_received_rewards(
                    roll_quest_rewards(quest_id, 0, not _quest_cleared(quest_id)))

            log('    big-battle/skip %s -> %d reward(s)' % (quest_id, len(rewards)))
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                    'ReceivedRewards': rewards})

        if path == '/api/quest/start':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            quest_id = req.get('QuestId') or req.get('questId')

            difficulty = int(req.get('Difficulty') or 0)
            sync = empty_sync()

            u = db_get('User')
            if isinstance(u, dict) and quest_id:
                u['CurrentQuestId'] = quest_id
                u['CurrentQuestDifficulty'] = difficulty
                db_put('User', u)
                sync['User'] = u
                touch_singleton('User', u)

            try:
                _qrows = db_get('QuestRecord')
                if isinstance(_qrows, list) and quest_id:
                    _qr = None
                    for _x in _qrows:
                        if isinstance(_x, dict) and _x.get('Id') == quest_id:
                            _qr = _x
                            break
                    if _qr is None:
                        _qr = {'UserId': user_id(), 'Id': quest_id, 'Records': [],
                               'MissionAchievement': 0, 'QuestClearCount': 0,
                               'HazardNos': None, 'MaxRank': None, 'EnemyTurn': None,
                               'Ver': 0}
                        _qrows.append(_qr)
                    _qr['QuestStartAt'] = _now_iso()
                    _qr['QuestStartDifficulty'] = difficulty
                    _qr['QuestEndAt'] = None
                    _qr['QuestClearCount'] = int(_qr.get('QuestClearCount') or 0)
                    _qr['HazardNos'] = None
                    _qr['Ver'] = int(_qr.get('Ver') or 0) + 1
                    db_put('QuestRecord', _qrows)
                    touch(quest_id, _qr, 'QuestRecords')
            except Exception as _se:
                log('    quest/start record stamp failed: %s' % _se)
            log('    quest/start %s d%d' % (quest_id, difficulty))
            body_out = {'SyncData': flush_touched(sync), 'IsSkipValidate': True}
            return self._send(200, body_out)

        if path == '/api/quest/end':

            _mission_progress('any_battle')

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            u = db_get('User') or {}
            quest_id = u.get('CurrentQuestId')
            difficulty = int(u.get('CurrentQuestDifficulty') or 0)
            result = str(req.get('Result') or req.get('result') or 'win')
            before_rank, before_exp = _rank_exp_state()
            before_stamina = int(u.get('Stamina') or 0)
            before_achivement = _mission_achievement(quest_id)
            first_time = not _quest_cleared(quest_id, difficulty)

            rewards = []
            after_stamina = before_stamina
            if quest_id and result.lower() in ('win', 'clear', '1', 'true'):

                cost = _quest_stamina_cost(quest_id, difficulty)
                affordable = True
                if cost:
                    _left = _spend_stamina(cost)
                    if _left is None:
                        affordable = False
                    else:
                        after_stamina = _left
                if not affordable:

                    after_rank, after_exp = before_rank, before_exp
                    after_achivement = before_achivement
                    first_time = False
                    log('    quest/end %s d%d REFUSED: not enough stamina (%d < %d)'
                        % (quest_id, difficulty, before_stamina, cost))
                else:
                    rewards = roll_quest_rewards(quest_id, difficulty, first_time)
                    rewards = merge_received_rewards(rewards)
                    exp_gain = _quest_exp_reward(quest_id, difficulty)
                    after_rank, after_exp = _add_exp(exp_gain)

                    _u_now = db_get('User') or {}
                    try:
                        after_stamina = int(_u_now.get('Stamina') or after_stamina)
                    except Exception:
                        pass
                    record_quest_clear(quest_id, difficulty)

                    _add_enemy_kills(req.get('EnemyKills'))

                    _sync_god_lyrics(req.get('EnemyKills'))

                    _angel_rows = _sync_angel_lyrics(req.get('LyricCharaIds'), 1)
                    if _angel_rows:
                        log('    quest/end -> %d angel lyric row(s) advanced' % len(_angel_rows))
                    after_achivement = _mission_achievement(quest_id)
            else:
                after_rank, after_exp = before_rank, before_exp
                after_achivement = before_achivement
            log('    quest/end %s d%d result=%s -> %d reward(s), rank %d->%d, exp %d->%d, sta %d->%d%s'
                % (quest_id, difficulty, result, len(rewards), before_rank, after_rank,
                   before_exp, after_exp, before_stamina, after_stamina,
                   ' (first clear)' if first_time else ''))

            _replay_mode = (os.environ.get('ANGELICA_REPLAY') or '').strip()
            payload = {'SyncData': _oopart_pluses_sync(flush_touched(empty_sync())),
                       'ReceivedRewards': rewards,
                       'LivingCharaIds': req.get('AliveCharaIds') or [],
                       'QuestResult': None,
                       'BeforeRank': before_rank, 'AfterRank': after_rank,
                       'BeforeExp': before_exp, 'AfterExp': after_exp,
                       'BeforeStamina': before_stamina, 'AfterStamina': after_stamina,
                       'BeforeAchivement': before_achivement,
                       'AfterAchivement': after_achivement,
                       'CurrentAchivement': after_achivement,
                       'IsOverItem': False,
                       'BeforeDawnBreak': 0, 'AfterDawnBreak': 0,
                       'OrbBox': None}
            if _replay_mode:
                payload = _apply_truth_replay(payload, _replay_mode)
            return self._send(200, payload)

        if path == '/api/quest/skip':

            _touched_flag().pop('orb_box_granted', None)
            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            quest_id = req.get('QuestId') or req.get('questId')
            difficulty = int(req.get('Difficulty') or 0)
            tickets = int(req.get('SkipTicketNum') or 0)

            _mission_progress('any_battle', max(1, tickets))
            first_time = not _quest_cleared(quest_id, difficulty)
            before_rank, before_exp = _rank_exp_state()
            before_stamina = int((db_get('User') or {}).get('Stamina') or 0)
            before_achivement = _mission_achievement(quest_id)

            runs = max(1, tickets)
            stamina_each = _quest_stamina_cost(quest_id, difficulty)
            spend = stamina_each * runs
            after_stamina = before_stamina

            _have_shoes = _have_item('item.skip_ticket_A')
            _short = None
            if spend and before_stamina < spend:
                _short = 'stamina %d < %d' % (before_stamina, spend)
            elif tickets and _have_shoes < tickets:
                _short = 'skip shoes %d < %d' % (_have_shoes, tickets)
            if _short:
                log('    quest/skip %s d%d REFUSED: %s' % (quest_id, difficulty, _short))

                return self._send(200, {
                    'SyncData': flush_touched(empty_sync()),
                    'ReceivedRewards': None,
                    'LivingCharaIds': None,
                    'QuestResult': None,
                    'BeforeRank': before_rank, 'AfterRank': before_rank,
                    'BeforeExp': before_exp, 'AfterExp': before_exp,
                    'BeforeStamina': before_stamina, 'AfterStamina': before_stamina,
                    'BeforeAchivement': before_achivement,
                    'AfterAchivement': before_achivement,
                    'CurrentAchivement': 0,
                    'BeforeDawnBreak': 0, 'AfterDawnBreak': 0, 'DawnBreakCount': 0,
                    'IsOverItem': False,
                    'OrbBox': None})
            if spend:
                after_stamina = _spend_stamina(spend, strict=False)
                log('    quest/skip: stamina %d -> %d (cost %d x %d)'
                    % (before_stamina, after_stamina, stamina_each, runs))
            rewards = []
            for n in range(runs):
                got = roll_quest_rewards(quest_id, difficulty,
                                         first_time and n == 0)
                rewards.extend(got)

            rewards = merge_received_rewards(rewards)
            exp_gain = _quest_exp_reward(quest_id, difficulty) * runs
            after_rank, after_exp = _add_exp(exp_gain)
            for _ in range(runs):
                record_quest_clear(quest_id, difficulty)
            after_achivement = _mission_achievement(quest_id)
            if tickets:
                _consume_item('item.skip_ticket_A', tickets, strict=False)

            _angel_rows = _sync_angel_lyrics(req.get('LyricCharaIds'), runs)
            log('    quest/skip %s d%d -> %d reward(s), rank %d->%d exp %d->%d%s'
                % (quest_id, difficulty, len(rewards), before_rank, after_rank,
                   before_exp, after_exp, ' (first clear)' if first_time else ''))

            _skip_box = (_orb_box_entity()
                         if _touched_flag().get('orb_box_granted') else None)
            payload = {'SyncData': flush_touched(empty_sync()),
                       'ReceivedRewards': rewards,
                       'LivingCharaIds': [],

                       'QuestResult': None,
                       'BeforeRank': before_rank, 'AfterRank': after_rank,
                       'BeforeExp': before_exp, 'AfterExp': after_exp,
                       'BeforeStamina': before_stamina, 'AfterStamina': after_stamina,
                       'BeforeAchivement': before_achivement,
                       'AfterAchivement': after_achivement,
                       'CurrentAchivement': after_achivement,
                       'BeforeDawnBreak': 0, 'AfterDawnBreak': 0, 'DawnBreakCount': 0,
                       'IsOverItem': False,
                       'OrbBox': _skip_box}
            return self._send(200, payload)

        if path == '/api/gacha/lottery':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            gacha_id = req.get('GachaId') or req.get('gachaId') or 'gacha_play.premium1_ticket_10'
            var_count = int(req.get('VariableCount') or req.get('variableCount') or 0)

            count, cost_id, cost_count, main_lot, last_lot = gacha_play_info(gacha_id)
            if var_count:
                count = max(1, min(100, var_count))

            paid = 'none'
            if cost_id and cost_count > 0:

                if cost_id == 'item.jewel':
                    for alt in ('item.jewel_free', 'item.jewel_paid'):
                        if int(_item_entity(alt).get('Count') or 0) >= cost_count:
                            cost_id = alt
                            break
                ent = _item_entity(cost_id)
                have = int(ent.get('Count') or 0)
                if have < cost_count:
                    log('    gacha %s REFUSED: need %d %s, have %d'
                        % (gacha_id, cost_count, cost_id, have))

                    return self._send(200, {
                        'RewardData': {
                            'GachaRewardMasterDataList': [],
                            'GachaOmakeRewardMasterDataList': [],
                            'GachaFixedRewardMasterDataList': [],
                            'GachaExchangeList': [],
                            'GachaCostDatas': [],

                            'GachaStampRewardMasterDataList': {'Id': None, 'Data': None},
                            'IsOmakeOrFixedOverflow': False,
                            'IsStampOverflow': False},
                        'SyncData': flush_touched(empty_sync())})
                left = _consume_item(cost_id, cost_count, strict=False)
                paid = '%d x %s (left %d)' % (cost_count, cost_id, left)

            _bp = _banner_reward_map(gacha_id)
            _bpool, _bgids = (_bp if isinstance(_bp, tuple) else ({}, {}))
            rewards = roll_gacha(count, main_lot, last_lot,
                                 up_ids=_banner_up_ids(gacha_id),
                                 pools=_bpool or None)
            log('    gacha %s -> %d pull(s), paid=%s, main=%s last=%s, %s'
                % (gacha_id, count, paid, main_lot, last_lot,
                   ', '.join('%s(%d)' % (r['RewardId'], r['Rarity']) for r in rewards[:5])))
            body_out = {'RewardData': gacha_reward_payload(rewards, group_ids=_bgids)}
            body_out['SyncData'] = flush_touched(empty_sync())
            return self._send(200, body_out)

        if path == '/api/quest/multi-team/end':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            _mt = req.get('Id') or req.get('id') or 'multi_team_quest.1'
            _eq = req.get('EndQuest') if isinstance(req.get('EndQuest'), dict) else {}
            _res = str(_eq.get('Result') or req.get('Result') or 'win')
            _alive = _eq.get('AliveCharaIds') if isinstance(_eq.get('AliveCharaIds'), list) else []
            _u = db_get('User') or {}
            _br = int(_u.get('Rank') or 1)
            _be = int(_u.get('Exp') or 0)
            _bs = int(_u.get('Stamina') or 0)
            _won = _res.lower() in ('win', 'clear', '1', 'true')
            _ar, _ae, _as = _br, _be, _bs
            _rewards = []
            if _won and _mt:
                try:
                    _stage = int(req.get('StageNo') or 0)
                except Exception:
                    _stage = 0

                _mt_rank = 1
                try:
                    for _x in (db_get('MultiTeamQuest') or []):
                        if isinstance(_x, dict) and _x.get('Id') == _mt:
                            _mt_rank = max(1, int(_x.get('Rank') or 1))
                            break
                except Exception:
                    _mt_rank = 1
                _rewards = _rank_reward_rows(
                    'rank_reward.%s' % _mt, _mt_rank, key='Rank')
                if not _rewards:

                    _mst = (master_tables().get('multi_team_quest') or {}).get(_mt) or {}
                    _stages = _mst.get('Stages') or []
                    _row = None
                    for _s in _stages:
                        if isinstance(_s, dict) and int(_s.get('StageNo') or 0) == _stage:
                            _row = _s
                            break
                    if _row is None and _stages and isinstance(_stages[0], dict):
                        _row = _stages[0]
                    for _rec in ((_row or {}).get('Records') or []):
                        _rid = _rec.get('RewardId')
                        if not _rid:
                            continue
                        _c = int(_rec.get('Count') or _rec.get('RewardCount') or 1)
                        _grant_reward(_rid, _c)
                        _rewards.append({'RewardType': int(_rec.get('RewardType') or 17),
                                         'RewardItemId': _rid,
                                         'RewardParam': _rec.get('RewardParam'),
                                         'RewardItemCount': _c,
                                         'CelebRate': 0, 'EvDrop': 0, 'CType': 0,
                                         'GroupNo': 0, 'GroupRewards': None})
                _cost = _quest_stamina_cost(_mt, 0)
                if _cost and _spend_stamina(_cost) is None:

                    log('    multi-team/end %s REFUSED: not enough stamina (%d)' % (_mt, _cost))
                    return self._send(200, _end_quest_response(
                        received=None,
                        living=[],
                        result=None,
                        before_rank=_rank_exp_state()[0], after_rank=_rank_exp_state()[0],
                        before_exp=_rank_exp_state()[1], after_exp=_rank_exp_state()[1],
                        before_stamina=int((db_get('User') or {}).get('Stamina') or 0),
                        after_stamina=int((db_get('User') or {}).get('Stamina') or 0)))
                _ar, _ae = _add_exp(_quest_exp_reward(_mt, 0))
            log('    multi-team/end %s stage=%s -> %d reward(s)'
                % (_mt, req.get('StageNo'), len(_rewards)))
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   **_end_quest_response(received=_rewards,
                                                         living=_alive,

                                                         result=None,
                                                         before_rank=_br, after_rank=_ar,
                                                         before_exp=_be, after_exp=_ae,
                                                         before_stamina=_bs, after_stamina=_as)})

        if path == '/api/quest/demigod/end':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            u = db_get('User') or {}
            before_rank = int(u.get('Rank') or 1)
            before_exp = int(u.get('Exp') or 0)
            before_stamina = int(u.get('Stamina') or 0)
            mid = req.get('Id') or req.get('id')
            end_quest = bool(req.get('EndQuest'))

            demigod_rewards = []
            _dg_alive = []
            _eq = req.get('EndQuest') if isinstance(req.get('EndQuest'), dict) else {}
            after_rank, after_exp, after_stamina = before_rank, before_exp, before_stamina
            if end_quest and mid:
                try:
                    lv = int(req.get('Level') or 0)
                except Exception:
                    lv = 0
                _dg_alive = [c for c in (_eq.get('AliveCharaIds') or [])
                             if isinstance(c, str)]
                _dg_row = (master_tables().get('demigod') or {}).get(mid) or {}
                for _frag in (_dg_row.get('RewardIds') or []):
                    if not _frag:
                        continue
                    _before = _item_count_of(_frag)
                    _grant_reward(_frag, 1)
                    demigod_rewards.append({'RewardId': _frag,
                                            'AfterCount': _item_count_of(_frag),
                                            'AddCount': 1,
                                            'IsOver': False})
                log('    demigod/end %s lv=%d -> settled, %d reward(s)'
                    % (mid, lv, len(demigod_rewards)))
            else:
                log('    demigod/end %s -> acknowledged (no EndQuest)' % mid)
            payload = {'SyncData': flush_touched(empty_sync()),
                       'ReceivedRewards': [],
                       'LivingCharaIds': _dg_alive,
                       'QuestResult': None,
                       'BeforeRank': before_rank, 'AfterRank': after_rank,
                       'BeforeExp': before_exp, 'AfterExp': after_exp,
                       'BeforeStamina': before_stamina, 'AfterStamina': after_stamina,
                       'BeforeAchivement': 0, 'AfterAchivement': 0,
                       'CurrentAchivement': 0,
                       'IsOverItem': False,
                       'BeforeDawnBreak': 0, 'AfterDawnBreak': 0,
                       'OrbBox': None,
                       'DemigodRewards': demigod_rewards}
            return self._send(200, payload)

        if path == '/api/quest/big-battle/end':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            bid = req.get('Id') or req.get('id') or 'big_battle.1'
            u = db_get('User') or {}
            before_rank = int(u.get('Rank') or 1)
            before_exp = int(u.get('Exp') or 0)
            before_stamina = int(u.get('Stamina') or 0)
            try:
                score = int(req.get('Score') or 0)
            except Exception:
                score = 0

            try:
                rows = db_get('BigBattle')
                if not isinstance(rows, list):
                    rows = []
                hit = None
                for x in rows:
                    if isinstance(x, dict) and x.get('Id') == bid:
                        hit = x
                        break
                if hit is None:
                    hit = {'UserId': user_id(), 'Id': bid, 'Score': 0, 'Num': 0,
                           'LastAt': _now_iso(), 'ResetAt': None, 'Logs': {}, 'Ver': 0}
                    rows.append(hit)
                if score > int(hit.get('Score') or 0):
                    hit['Score'] = score
                hit['Num'] = int(hit.get('Num') or 0) + 1
                hit['LastAt'] = _now_iso()
                if not isinstance(hit.get('Logs'), dict):
                    hit['Logs'] = {}
                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                db_put('BigBattle', rows)

                touch(bid, hit, 'BigBattles')
            except Exception as e:
                log('    big-battle/end save failed:', e)
            log('    big-battle/end %s score=%d' % (bid, score))
            payload = {'SyncData': flush_touched(empty_sync()),
                       'ReceivedRewards': [],
                       'LivingCharaIds': [],
                       'QuestResult': None,
                       'BeforeRank': before_rank, 'AfterRank': before_rank,
                       'BeforeExp': before_exp, 'AfterExp': before_exp,
                       'BeforeStamina': before_stamina, 'AfterStamina': before_stamina,
                       'BeforeAchivement': 0, 'AfterAchivement': 0,
                       'CurrentAchivement': 0,
                       'IsOverItem': False,
                       'BeforeDawnBreak': 0, 'AfterDawnBreak': 0,
                       'OrbBox': None}
            return self._send(200, payload)

        if path in ('/api/gacha/lottery', '/api/gacha/result'):

            if path == '/api/gacha/result':
                log('    gacha/result -> typed empty reward payload')
                empty_reward = {'Id': None, 'Data': [],
                                'GachaRewardMasterDataList': [],
                                'GachaOmakeRewardMasterDataList': [],
                                'GachaFixedRewardMasterDataList': [],
                                'GachaExchangeList': [],
                                'GachaCostDatas': [],

                                'GachaStampRewardMasterDataList': {'Id': None, 'Data': None},
                                'IsOmakeOrFixedOverflow': False,
                                'IsStampOverflow': False}
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                        'RewardData': empty_reward})

        if path == '/api/minigame/fish-add':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            fish = req.get('FishId') or req.get('fishId')
            recd = {'FishingFishList': [], 'MiniGamePoint': 0}
            if fish:
                recd['FishingFishList'].append({'FishId': fish})
            log('    minigame/fish-add %s' % fish)
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                    'ReceivedData': recd})

        if path == '/api/quest/score-attack/top':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            _tid = req.get('Id') or req.get('id') or 'score_attack.1'
            _rows = _rows('ScoreAttackTop')
            _hit = None
            for _x in _rows:
                if isinstance(_x, dict) and _x.get('Id') == _tid:
                    _hit = _x
                    break
            if _hit is None:
                _hit = {'Key': 'score_attack', 'Id': _tid,
                        'Charas': {}, 'Ooparts': {},
                        'UpdatedAt': _now_iso(), 'BatAt': None, 'Ver': 0}
                _rows.append(_hit)
                db_put('ScoreAttackTop', _rows)

            for _m in ('Charas', 'Ooparts'):
                if not isinstance(_hit.get(_m), dict):
                    _hit[_m] = {}
            _hit.setdefault('Key', 'score_attack')
            if _hit.get('UpdatedAt') is None:
                _hit['UpdatedAt'] = _now_iso()

            _hit.setdefault('Ver', 0)
            _ent = {'Key': _hit.get('Key'), 'Id': _hit.get('Id'),
                    'Charas': _hit.get('Charas') or {}, 'Ooparts': _hit.get('Ooparts') or {},
                    'UpdatedAt': _hit.get('UpdatedAt'), 'BatAt': _hit.get('BatAt'),
                    'Ver': int(_hit.get('Ver') or 0)}
            return self._send(200, _ent)

        if path == '/api/quest/score-attack/log':

            return self._send(200, 'null')

        if path == '/api/social/profile/id-score-sheet':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            _sid = req.get('Id') or req.get('id') or ''
            _sheets = _rows('UserPossession')
            _srow = None
            for _x in _sheets:
                if not isinstance(_x, dict):
                    continue
                if _x.get('Id') != 'user_score_sheet':
                    continue
                if not _sid or _x.get('UserId') == _sid:
                    _srow = _x
                    break
            if _srow is None:
                _srow = {'UserId': _sid or user_id(), 'Id': 'user_score_sheet',
                         'ReadFlag': False, 'Records': None, 'Ver': 0}
            _recs = _srow.get('Records')
            if not isinstance(_recs, dict):
                _recs = None
            return self._send(200, {'UserId': _srow.get('UserId'),
                                   'Id': 'user_score_sheet',
                                   'ReadFlag': bool(_srow.get('ReadFlag')),
                                   'Records': _recs,
                                   'Ver': int(_srow.get('Ver') or 0)})

        if path == '/api/lounge/red-coin-gacha':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            _gid = req.get('GachaId') or req.get('gachaId') or 'red_coin_gacha.0001'
            try:
                _times = int(req.get('Count') or req.get('count') or 1)
            except Exception:
                _times = 1
            _times = max(0, _times)
            _pool = ((master_tables().get('red_coin_gacha') or {}).get(_gid) or {})
            _recs = list(_pool.get('Records') or [])

            _COIN = 'item.minigame_point'
            _coin_have = _have_item(_COIN)
            if _coin_have < _times:
                log('    red-coin-gacha %s REFUSED: need %d red coin(s), have %d'
                    % (_gid, _times, _coin_have))
                return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                       'Rewards': []})
            _out = []
            for _i in range(_times):
                _pick = None
                if _recs:
                    _ws = [int(r.get('Weight') or 1) for r in _recs]
                    try:
                        _pick = random.choices(_recs, weights=_ws, k=1)[0]
                    except Exception:
                        _pick = random.choice(_recs)
                if not _pick or not _pick.get('RewardId'):
                    continue
                _rid = _pick['RewardId']
                _num = int(_pick.get('RewardNum') or 1)
                _grant_reward(_rid, _num)
                _out.append({'LotteryNo': _i,
                             'RewardType': int(_pick.get('RewardType') or 0),
                             'RewardId': _rid,
                             'RewardNum': _num,

                             'RarityFlag': bool(_pick.get('RarityFlag')),
                             'SortKey': int(_pick.get('SortKey') or 0)})

            _consume_item(_COIN, _times, strict=False)
            log('    red-coin-gacha %s x%d -> %d reward(s), %d red coin(s) spent'
                % (_gid, _times, len(_out), _times))
            return self._send(200, {'SyncData': flush_touched(empty_sync()),
                                   'Rewards': _out})

        if path == '/api/quest/score-attack/end':

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            stage_id = (req.get('StageId') or req.get('stageId')
                        or (db_get('User') or {}).get('CurrentQuestId'))
            try:
                difficulty = int(req.get('Difficulty') or 0)
            except Exception:
                difficulty = 0
            end_q = req.get('EndQuest') if isinstance(req.get('EndQuest'), dict) else {}
            result = str(end_q.get('Result') or req.get('Result') or 'win')
            is_practice = bool(req.get('IsPractice'))
            u = db_get('User') or {}
            before_rank = int(u.get('Rank') or 1)
            before_exp = int(u.get('Exp') or 0)
            before_stamina = int(u.get('Stamina') or 0)
            before_achivement = _mission_achievement(stage_id) if stage_id else 0
            after_rank, after_exp = before_rank, before_exp
            after_stamina = before_stamina
            after_achivement = before_achivement
            won = result.lower() in ('win', 'clear', '1', 'true')
            _sa_rewards = []

            if stage_id and won and not is_practice:
                cost = _quest_stamina_cost(stage_id, difficulty)
                _sta_left = before_stamina
                _afford = True
                if cost:
                    _sta_left = _spend_stamina(cost)
                    if _sta_left is None:
                        _afford = False
                if not _afford:

                    log('    score-attack/end %s REFUSED: not enough stamina (%d < %d)'
                        % (stage_id, before_stamina, cost))
                else:
                    after_stamina = _sta_left
                    after_rank, after_exp = _add_exp(_quest_exp_reward(stage_id, difficulty))
                    record_quest_clear(stage_id, difficulty)
                    after_achivement = _mission_achievement(stage_id)

                    _sa_score = int(end_q.get('Score') or req.get('Score') or 0)
                    _sa_master = (master_tables().get('score_attack') or {})
                    _sa_row = _sa_master.get(stage_id) or {}
                    _sa_tables = (master_tables().get('score_attack_reward') or {})
                    _tbl_ids = []
                    for _k in ('ScoreAttackRewardId', 'ScoreAttackMaxRewardId'):
                        _tid = _sa_row.get(_k)
                        if _tid and _tid not in _tbl_ids:
                            _tbl_ids.append(_tid)
                    for _tid in _tbl_ids:
                        _tbl = _sa_tables.get(_tid) or {}
                        _paid = 0
                        for _rec in (_tbl.get('Records') or []):
                            try:
                                _need = int(_rec.get('Score') or 0)
                            except Exception:
                                continue
                            if _sa_score < _need:
                                continue
                            _rid = _rec.get('RewardId')
                            if not _rid:
                                continue
                            _cnt = int(_rec.get('Count') or 1)
                            _grant_reward(_rid, _cnt)
                            _sa_rewards.append({
                                'RewardType': _reward_type_of(_rid),
                                'RewardItemId': _rid,
                                'RewardParam': _rec.get('RewardParam'),
                                'RewardItemCount': _cnt,
                                'CelebRate': 0, 'EvDrop': 0, 'CType': 0,
                                'GroupNo': 0, 'GroupRewards': None})
                            _paid += 1
                        log('    score-attack/end %s score=%d table=%s -> %d row(s)'
                            % (stage_id, _sa_score, _tid, _paid))

            try:
                _srows = db_get('ScoreAttack')
                if not isinstance(_srows, list):
                    _srows = []
                _shit = None
                for _x in _srows:
                    if isinstance(_x, dict) and _x.get('Id') == (stage_id or ''):
                        _shit = _x
                        break
                _alive = (end_q.get('AliveCharaIds')
                          if isinstance(end_q.get('AliveCharaIds'), list) else [])
                if _shit is None:
                    _shit = {'UserId': user_id(), 'Id': stage_id or '',
                             'CharaIds': [], 'OopartIds': [],
                             'Score': 0, 'TotalScore': 0, 'Ver': 0}
                    _srows.append(_shit)
                _new_score = int(end_q.get('Score') or req.get('Score') or 0)
                if _new_score > int(_shit.get('Score') or 0):
                    _shit['Score'] = _new_score
                _shit['TotalScore'] = int(_shit.get('TotalScore') or 0) + _new_score
                _shit['CharaIds'] = [c for c in _alive if isinstance(c, str)
                                     and c.startswith('chara.')]
                _shit['OopartIds'] = [o for o in _alive if isinstance(o, str)
                                      and o.startswith('oopart.')]
                _shit['Ver'] = int(_shit.get('Ver') or 0) + 1
                db_put('ScoreAttack', _srows)
                touch(stage_id or '', _shit, 'ScoreAttacks')
            except Exception as e:
                log('    score-attack/end save failed: %s' % e)

            log_ent = {'Key': stage_id or '', 'Id': user_id(),
                       'Records': [], 'Practices': [], 'Ver': 0}

            log('    score-attack/end %s d%d result=%s practice=%s -> rank %d->%d, exp %d->%d, sta %d->%d'
                % (stage_id, difficulty, result, is_practice,
                   before_rank, after_rank, before_exp, after_exp,
                   before_stamina, after_stamina))
            payload = {'SyncData': flush_touched(empty_sync()),

                       'ReceivedRewards': _sa_rewards,
                       'LivingCharaIds': end_q.get('AliveCharaIds') or [],
                       'QuestResult': None,
                       'BeforeRank': before_rank, 'AfterRank': after_rank,
                       'BeforeExp': before_exp, 'AfterExp': after_exp,
                       'BeforeStamina': before_stamina, 'AfterStamina': after_stamina,
                       'BeforeAchivement': before_achivement,
                       'AfterAchivement': after_achivement,
                       'CurrentAchivement': after_achivement,
                       'IsOverItem': False,
                       'BeforeDawnBreak': 0, 'AfterDawnBreak': 0,
                       'OrbBox': None,

                       'Log': log_ent,

                       'Ranking': None,
                       'RankingMyInfo': None}
            return self._send(200, payload)

        if path == '/api/item/expired':

            exp, detail, changed = sweep_expired_items()
            if detail:
                log('    item/expired: %d expired batch(es) %s'
                    % (len(detail), detail[:5]))
            body = {'SyncData': flush_touched(empty_sync()),
                    'ExpiredJewels': {},
                    'ExpiredItems': exp}
            return self._send(200, body)

        if path in ('/api/exchange/change', '/api/exchange/change-multi'):

            try:
                _exn = len((req or {}).get('Requests') or []) or 1
            except Exception:
                _exn = 1
            _mission_progress('exchange', _exn)

            try:
                req = json.loads(body.decode('utf-8')) if body else {}
            except Exception:
                req = {}
            reqs = req.get('Requests') if isinstance(req.get('Requests'), list) else [req]
            done = 0
            for r in reqs:
                if not isinstance(r, dict):
                    continue
                xid = r.get('ExchangeId') or r.get('exchangeId')
                xno = int(r.get('ExchangeNo') or r.get('exchangeNo') or 0)
                rno = int(r.get('RewardNo') or r.get('rewardNo') or 0)
                cnt = int(r.get('Count') or r.get('count') or 1)
                got, ex = exchange_reward(xid, xno, rno)
                if not got:
                    log('    exchange: no reward row for %s no=%s reward=%s'
                        % (xid, xno, rno))
                    continue
                reward_id, reward_param, per, cost1, cost2 = got

                _c1 = (ex or {}).get('CostId1')
                _c2 = (ex or {}).get('CostId2')
                payer = r.get('UseItemId') or r.get('useItemId')
                if not payer:
                    payer = _c1 or _c2
                if payer and _c2 and payer == _c2 and cost2:
                    unit = cost2
                elif payer and _c1 and payer == _c1 and cost1:
                    unit = cost1
                elif cost2 and not cost1:

                    unit = cost2
                    payer = payer or _c2
                else:
                    unit = cost1 or cost2

                total_cost = unit * cnt
                if payer and total_cost:

                    if _consume_item(payer, total_cost) is None:
                        log('    exchange REFUSED: cannot pay %d %s' % (total_cost, payer))
                        continue
                    log('    exchange: paid %d %s' % (total_cost, payer))
                if reward_id:

                    _grant_reward(reward_id, per * cnt, reward_param)
                    log('    exchange: granted %d %s' % (per * cnt, reward_id))

                ents = db_get('Exchange') or []
                hit = None
                for e in ents:
                    if isinstance(e, dict) and e.get('Id') == xid:
                        hit = e
                        break
                if hit is None:

                    _tiers = _exchange_groups(xid)[1]

                    hit = {'UserId': user_id(), 'Id': xid,
                           'No': (_tiers[0][0] if _tiers else 0),
                           'Records': {}, 'Lineup': {}, 'UpdatedAt': _now_iso(), 'Ver': 0}
                    ents.append(hit)

                _exchange_reset_counters([hit])
                recs = hit.get('Records') or {}
                key = '%s_%s' % (xno, rno)
                recs[key] = int(recs.get(key) or 0) + cnt
                hit['Records'] = recs

                _exchange_fix_tier(hit)
                if _exchange_advance_tier(hit):
                    log('    exchange: %s tier -> No=%s' % (xid, hit.get('No')))

                hit['UpdatedAt'] = _now_iso()
                hit['Ver'] = int(hit.get('Ver') or 0) + 1
                touch(xid, hit)
                db_put('Exchange', ents)
                done += 1
            log('    exchange/change handled %d/%d request(s)' % (done, len(reqs)))
            return self._send(200, flush_touched(empty_sync()))

        if path == '/api/user/fixed':

            _mission_progress('login')

            _u = db_get('User')
            if isinstance(_u, dict):
                _u = _fix_user_row(_u)

                if _u.get('Fixeds') is None:
                    _u['Fixeds'] = list(NEW_ACCOUNT_FIXEDS)
                if not _u.get('RedCount'):
                    _u['RedCount'] = NEW_ACCOUNT_REDCOUNT
                    _u['RedCountAt'] = NEW_ACCOUNT_REDCOUNT_AT
                _u['Ver'] = int(_u.get('Ver') or 0) + 1
                db_put('User', _u)
                sync = empty_sync()
                sync['User'] = _u

                for _member, _table in (('Presents', 'Present'),
                                           ('GodLyrics', 'GodLyric')):
                    _rows = db_get(_table)
                    if isinstance(_rows, list) and _rows:
                        sync[_member] = _rows

                _up_all = db_get('UserPossession')
                if isinstance(_up_all, list):
                    _by_id = {r.get('Id'): r for r in _up_all
                              if isinstance(r, dict)}
                    _echo = [_by_id[i] for i in NEW_ACCOUNT_SETUP_POSSESSION
                             if i in _by_id]
                    if _echo:
                        sync['UserPossession'] = _echo
                log('    user/fixed -> User Ver=%s Stamina=%s Presents=%d UserPossession=%d GodLyrics=%d'
                    % (_u.get('Ver'), _u.get('Stamina'),
                       len(sync.get('Presents') or []), len(sync.get('UserPossession') or []),
                       len(sync.get('GodLyrics') or [])))
                return self._send(200, sync)
            return self._send(200, empty_sync())

        if shape == 'bare_sync':

            body_out = flush_touched(empty_sync())
            try:
                _brow, _brows = _behavior_row()

                if path in ACTIVITY_BEHAVIOR or _behavior_rollover(_brow):
                    _brow['UserId'] = _brow.get('UserId') or user_id()
                    _brow['Id'] = '1'
                    _brow['Ver'] = int(_brow.get('Ver') or 0) + 1
                    if _brows is None:
                        db_put('UserBehavior', _brow)
                    else:
                        db_put('UserBehavior', _brows)
                    body_out['UserBehavior'] = _brow
            except Exception as _e:
                log('    behavior echo failed: %s' % _e)
            return self._send(200, body_out)

        if shape == 'response':

            if path == '/api/login-bonus/try-receive-all':

                received, changed = claim_login_bonuses()
                sync = empty_sync()

                sync['LoginBonuses'] = _lb_all_rows()
                body = {'SyncData': sync}
                members = RESPONSE_MEMBERS.get(path)
                if isinstance(members, dict):
                    for member, dv in members.items():
                        body.setdefault(member, dv)
                elif members:
                    for member in members:
                        body.setdefault(member, [])
                body['LoginBonusReceivedRecords'] = received
                return self._send(200, body)

            body = {'SyncData': empty_sync()}

            members = RESPONSE_MEMBERS.get(path)
            if isinstance(members, dict):
                for member, dv in members.items():
                    body.setdefault(member, dv)
            elif members:
                for member in members:
                    body.setdefault(member, [])
            flush_touched(body['SyncData'])
            return self._send(200, _enforce_member_types(path, body))

        if shape == 'scalar':

            return self._send(200, True)

        if shape == 'list':
            return self._send(200, [])

        if shape == 'entity':

            tbl = READ_API.get(path)
            if tbl:

                try:
                    _body_req = _req_json(body)
                except Exception:
                    _body_req = {}
                rid = None
                qs = parse_qs(u.query or '')
                for k in ('Id', 'id', 'CharaId', 'charaId', 'OrbBoxId', 'orbBoxId'):
                    if qs.get(k):
                        rid = qs[k][0]
                        break
                    if isinstance(_body_req, dict) and _body_req.get(k):
                        rid = _body_req[k]
                        break
                rows = db_get(tbl)

                if isinstance(rows, dict):
                    return self._send(200, rows)
                if isinstance(rows, list):
                    if rid:
                        for x in rows:
                            if isinstance(x, dict) and x.get('Id') == rid:
                                return self._send(200, x)
                    if path.endswith('/list') or path.endswith('/ids'):
                        return self._send(200, rows)
                    for x in rows:
                        if isinstance(x, dict):
                            return self._send(200, x)

            _em = ENTITY_MEMBERS.get(path)
            if isinstance(_em, dict):
                return self._send(200, dict(_em))
            return self._send(200, {})

        if method == 'POST':

            return self._send(200, empty_sync())

        if path.startswith('/api/'):
            return self._send(200, {})

        return self._send(404, {'error':'not found'})

    def _handle_safe(self, method):
        try:
            return self._handle(method)
        except Exception:
            tb = traceback.format_exc()
            log('  !! UNHANDLED in %s %s' % (method, self.path))
            for _l in tb.splitlines()[-16:]:
                log('     ' + _l[:200])
            try:
                self._send(500, {'error': 'server exception'})
            except Exception:
                pass

    def do_GET(self):  self._handle_safe('GET')
    def do_POST(self): self._handle_safe('POST')
    def do_PUT(self):  self._handle_safe('PUT')

def main():
    log('=' * 70)
    log('AngelServer -- local offline game server')
    log('  db   :', DB)
    log('  port :', PORT)
    seeded = seed()
    log('  seeded %d tables from captured/' % seeded)
    ensure_jewels()
    _seed_unreachable_missions()
    log('  read endpoints: %d' % len(READ_API))
    log('  CDN catalog   :', CATALOG_BIN if CATALOG_BIN else '(none)')
    log('  CDN bundles   : %d indexed' % len(BUNDLES))
    log('=' * 70)

    class _NoReuse(ThreadingHTTPServer):
        allow_reuse_address = False
    try:
        srv = _NoReuse(('127.0.0.1', PORT), H)
    except OSError as e:
        log('FATAL: cannot bind port %d (%s).' % (PORT, e))
        log('Another server is already using it -- kill that process first,')
        log('otherwise clients keep talking to the OLD code.')
        raise SystemExit(2)
    log('listening on http://127.0.0.1:%d' % PORT)

    try:
        _payload = json.dumps({'UserId': user_id(), 'Save': os.path.basename(DB),
                               'Port': PORT})

        for _n in ('local_identity_%d.json' % PORT, 'local_identity.json'):
            _idf = os.path.join(HERE, _n)
            with open(_idf + '.tmp', 'w', encoding='utf-8') as _f:
                _f.write(_payload)
            os.replace(_idf + '.tmp', _idf)
        log('identity  : UserId=%s  save=%s  port=%d  -> server/local_identity_%d.json'
            % (user_id(), os.path.basename(DB), PORT, PORT))
    except Exception as _e:
        log('identity  : could not publish local_identity.json: %r' % (_e,))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('shutting down')

if __name__ == '__main__':
    main()
