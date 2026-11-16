"""Lecture des listes de production de rotorspot.nl (pages HTML statiques).

Chaque ligne d'une liste : type | numéro de série | historique des immatriculations | sort.
Exemples réels (nov. 2026) :
    Airbus Helicopters H160B | 1061 | F-WJXM, F-HRTY, PH-OSZ
    MBB BK117 D-3m ('H145M') | 21555 | D-HMBZ[6], (75+16(D))
    NH Industries NH90 | 1567 | 98+84(D), HT.29-43/10349(EC)
    Eurocopter EC665 Tiger HAD | 6018 | F-ZKBI, 6018/F-MBJR(F)
Notations : entre parenthèses = immatriculation réservée (pas encore livrée) ; [n] = n-ième
utilisation d'une immatriculation ; « (D) », « (EC) », « (F) » = pays d'un matricule militaire ;
« ..??.. » = appareil pas encore identifié.
"""
import hashlib
import html as htmllib
import re
import time
import urllib.request

from .countries import PREFIXES, country_from_registration

BASE = 'https://www.rotorspot.nl/product/'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Version/17.5 Safari/605.1.15')
PAUSE = 4.0          # secondes entre deux pages : site bénévole, jamais de requêtes en parallèle

# Page → famille de numérotation (les numéros de série sont uniques dans une famille)
PAGES = [
    ('as350-3.php', 'Ecureuil'), ('ec135.php', 'H135'), ('bk117.php', 'BK117'), ('h140.php', 'H140'),
    ('h160.php', 'H160'), ('ec175.php', 'H175'), ('as332.php', 'Super Puma'), ('as365n.php', 'Dauphin'),
    ('ec665.php', 'Tigre'), ('nh90.php', 'NH90'),
]


def fetch(page):
    req = urllib.request.Request(BASE + page, headers={'User-Agent': UA, 'Accept': 'text/html',
                                                       'Accept-Language': 'en,fr;q=0.8'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    return raw.decode('utf-8', errors='replace'), hashlib.sha256(raw).hexdigest()


def fetch_all(pages=PAGES, log=print):
    out = {}
    for i, (page, family) in enumerate(pages):
        if i:
            time.sleep(PAUSE)
        try:
            out[page] = fetch(page)
            log(f'  {page} : {len(out[page][0]) // 1024} Ko')
        except Exception as e:
            log(f'  {page} : lecture impossible ({e})')
    return out


def _text(cell):
    cell = re.sub(r'<br\s*/?>', ' ', cell, flags=re.I)
    return re.sub(r'\s+', ' ', htmllib.unescape(re.sub(r'<[^>]+>', '', cell))).strip()


def parse_rows(page_html):
    """[(type, numéro de série, historique, sort)] ; les lignes d'en-tête sont ignorées."""
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', page_html, flags=re.S | re.I):
        cells = [_text(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S | re.I)]
        if len(cells) >= 3 and cells[1].lower() != 'c/n':
            rows.append((cells[0], cells[1], cells[2], cells[3] if len(cells) > 3 else ''))
    return rows


# ---------------------------------------------------------------------------
# Analyse de l'historique
# ---------------------------------------------------------------------------
MIL = re.compile(r'^(?P<serial>.*?)\((?P<code>[A-Z0-9]{1,3})\)(?:\(\?\))?$')
REG = re.compile(r'^(?P<reg>[A-Z0-9]{1,4}-[A-Z0-9.]+|N[0-9][A-Z0-9.]*|JA[0-9A-Z.]+|HL[0-9.]+|B-[0-9A-Z.]+)'
                 r'(?:\[(?P<n>\d+)\])?(?:\(\?\))?$')


def _split_tokens(history):
    out, depth, cur = [], 0, ''
    for ch in history:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def classify(token):
    """Décrit une immatriculation : {'raw', 'reserved', 'kind', 'country', 'reg'} ou None si illisible.
    kind : 'test' (essais usine), 'military', 'state' (aéronef d'État français F-Z…),
           'military_fr' (F-M…), 'civil', 'unknown'."""
    t = token.strip()
    if not t or '??' in t:
        return None
    reserved = t.startswith('(') and t.endswith(')')
    inner = t[1:-1].strip() if reserved else t
    # Variantes séparées par « / » : on garde celle qui porte un pays
    parts = [p.strip() for p in inner.split('/') if p.strip()]
    best = None
    for p in parts:
        m = MIL.match(p)
        if m and m.group('code') in PREFIXES:
            best = {'kind': 'military', 'country': PREFIXES[m.group('code')], 'reg': m.group('serial').strip(' .')}
            break
        m = REG.match(p)
        if m:
            reg, n = m.group('reg'), int(m.group('n') or 1)
            if reg.startswith('F-W') or reg.startswith(('F-ZK', 'F-ZW', 'F-ZV')) or (reg.startswith('D-H') and n >= 2):
                kind = 'test'
            elif reg.startswith('F-Z'):
                kind = 'state'
            elif reg.startswith('F-M'):
                kind = 'military_fr'
            else:
                kind = 'civil'
            best = best or {'kind': kind, 'country': country_from_registration(reg), 'reg': reg}
    if not best:
        return None
    if best['reg'] and set(best['reg']) <= set('.'):
        best['reg'] = ''
    if '.' in (best['reg'] or '') and best['kind'] in ('civil', 'state', 'military_fr'):
        best['reg'] = ''          # « OE-... » : pays connu, immatriculation pas encore attribuée
    best.update(raw=t, reserved=reserved)
    return best


def delivery(history):
    """Première immatriculation après les essais usine = livraison neuve.
    Renvoie (livraison, essais) ; livraison = None si l'appareil n'a pas encore quitté l'usine."""
    tests, first = [], None
    for tok in _split_tokens(history):
        c = classify(tok)
        if not c:
            continue
        if c['kind'] == 'test':
            if not c['reserved']:
                tests.append(c['reg'])
            continue
        first = c
        break
    return first, tests


def commercial_name(type_text, family):
    """« MBB BK117 D-3m ('H145M') » → « H145M » ; repli sur la famille."""
    m = re.search(r"\('([^']+)'", type_text)
    if m:
        return m.group(1).replace('ACH ', 'ACH').strip()
    t = type_text.upper()
    if family == 'NH90':
        return 'NH90'
    if family == 'Tigre':
        return 'Tigre'
    if family == 'H140':
        return 'H140'
    if family == 'H160':
        return 'H160M' if 'H160M' in t else 'H160'
    if 'EC130' in t:
        return 'EC130'
    if 'AS350' in t:
        return 'AS350 B3' if 'B3' in t else 'AS350'
    if 'EC135' in t:
        return 'EC135'
    if 'EC175' in t:
        return 'H175'
    if 'EC725' in t or 'CARACAL' in t:
        return 'H225M'
    if 'EC225' in t:
        return 'H225'
    if 'AS332' in t:
        return 'H215' if 'L1E' in t or 'C1E' in t else 'AS332 Super Puma'
    if 'EC155' in t:
        return 'EC155'
    if 'AS365' in t or 'AS565' in t:
        return 'AS365 Dauphin'
    if 'BK117 D-3' in t:
        return 'H145'
    if 'BK117 D-2' in t:
        return 'H145'
    return family


def msn_number(text):
    m = re.match(r'^\s*(\d+)', text or '')
    return int(m.group(1)) if m else None
