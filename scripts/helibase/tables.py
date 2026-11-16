"""Lecture des registres publiés sous forme de fichiers (CSV, ZIP, XLSX, JSON) et
reconnaissance automatique des colonnes, quelle que soit la langue du registre."""
import csv
import datetime as dt
import io
import json
import re
import unicodedata
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 '
              '(KHTML, like Gecko) Version/17.5 Safari/605.1.15')

csv.field_size_limit(10_000_000)


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------
def http_get(url, timeout=300, accept='*/*'):
    last = None
    for ua in (BROWSER_UA, 'Helibase/1.0 (+https://helibase.pages.dev)', 'curl/8.5.0'):
        req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept': accept})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (401, 403, 406, 429):
                raise
    raise last


def find_link(page_url, pattern):
    """Premier lien de la page dont l'adresse correspond au motif (ex. r'\\.csv')."""
    html = http_get(page_url, accept='text/html').decode('utf-8', errors='replace')
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I):
        if re.search(pattern, href, flags=re.I):
            return urllib.request.urljoin(page_url, href.replace('&amp;', '&'))
    return None


# ---------------------------------------------------------------------------
# Lecture des fichiers
# ---------------------------------------------------------------------------
def _decode(data):
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1', errors='replace')


def read_csv(data, header=True):
    text = _decode(data) if isinstance(data, bytes) else data
    sample = text[:20000]
    lines = sample.splitlines()[:6]
    first = max(lines, key=lambda l: max(l.count(d) for d in ',;\t|')) if lines else ''
    delim = max([',', ';', '\t', '|'], key=first.count)
    quote = "'" if first.count("'") > first.count('"') and first.startswith("'") else '"'
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim, quotechar=quote) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    if not header:
        return None, rows
    start = max(range(min(5, len(rows))), key=lambda i: len(rows[i]))
    return rows[start], rows[start + 1:]


def read_xlsx(data):
    """Lecteur XLSX minimal (première feuille), sans dépendance."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    shared = []
    if 'xl/sharedStrings.xml' in zf.namelist():
        for si in ET.fromstring(zf.read('xl/sharedStrings.xml')).findall('m:si', ns):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % ns['m'])))
    sheet = sorted(n for n in zf.namelist() if n.startswith('xl/worksheets/sheet'))[0]
    rows = []
    for row in ET.fromstring(zf.read(sheet)).iter('{%s}row' % ns['m']):
        cells = {}
        for c in row.findall('m:c', ns):
            col = _col_index(re.sub(r'\d', '', c.get('r', 'A')))
            t, v = c.get('t'), c.find('m:v', ns)
            if t == 's' and v is not None:
                val = shared[int(v.text)]
            elif t == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter('{%s}t' % ns['m']))
            else:
                val = v.text if v is not None else ''
            cells[col] = val or ''
        if cells:
            rows.append([cells.get(i, '') for i in range(max(cells) + 1)])
    # La ligne d'en-tête est la première qui contient au moins 4 cellules texte
    for i, r in enumerate(rows[:20]):
        if sum(1 for c in r if re.search(r'[A-Za-z]', c)) >= 4:
            return r, rows[i + 1:]
    return (rows[0], rows[1:]) if rows else ([], [])


def _col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_json(data):
    obj = json.loads(_decode(data))
    if isinstance(obj, dict):   # liste principale dans une clé
        obj = max((v for v in obj.values() if isinstance(v, list)), key=len, default=[])
    rows = [_flatten(o) for o in obj if isinstance(o, dict)]
    header = []
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    return header, [[str(r.get(h, '') if r.get(h) is not None else '') for h in header] for r in rows]


def _flatten(d, prefix=''):
    out = {}
    for k, v in d.items():
        key = f'{prefix}{k}'
        if isinstance(v, dict):
            out.update(_flatten(v, key + '.'))
        elif isinstance(v, list):
            out[key] = ', '.join(str(x.get('navn') or x.get('name') or x) if isinstance(x, dict) else str(x) for x in v)
        else:
            out[key] = v
    return out


def read_any(data, member_hint=None, header=True):
    """Ouvre un fichier téléchargé et renvoie (en-tête, lignes)."""
    if data[:2] == b'PK':
        zf = zipfile.ZipFile(io.BytesIO(data))
        if 'xl/workbook.xml' in zf.namelist():
            return read_xlsx(data)
        names = [n for n in zf.namelist() if not n.endswith('/')]
        if member_hint:
            names = [n for n in names if re.search(member_hint, n, re.I)] or names
        name = max(names, key=lambda n: zf.getinfo(n).file_size)
        return read_any(zf.read(name), header=header)
    head = data.lstrip()[:1]
    if head in (b'[', b'{'):
        return read_json(data)
    return read_csv(data, header=header)


def zip_member(data, pattern):
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = next(n for n in zf.namelist() if re.search(pattern, n, re.I))
    return zf.read(name)


# ---------------------------------------------------------------------------
# Reconnaissance des colonnes
# ---------------------------------------------------------------------------
def norm(text):
    text = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', text.lower())


# Pour chaque information : mots exacts, puis mots contenus dans l'en-tête (toutes langues)
FIELDS = {
    'registration': (['mark', 'registration', 'registrationmark', 'regmark', 'reg', 'immatriculation',
                      'kjennemerke', 'registreringsmerke', 'tunnus', 'rekisteritunnus', 'registreringsnummer',
                      'nationalitymark', 'marca', 'registratie', 'inschrijvingskenmerk'],
                     ['registrationmark', 'inschrijvingskenmerk', 'kjennemerke', 'rekisteritunnus', 'immatric', 'registration']),
    'manufacturer': (['manu', 'manufacturer', 'make', 'mfr', 'produsent', 'fabrikant', 'valmistaja',
                      'manufacturername', 'constructeur', 'tilvirker', 'nmfabricante', 'fabricante'],
                     ['manufacturer', 'produsent', 'valmistaja', 'fabrik', 'tilvirker', 'hersteller']),
    'model': (['model', 'modell', 'malli', 'modelname', 'aircraftmodel', 'type', 'typ', 'tyyppi'],
              ['dsmodelo', 'model', 'modell', 'malli', 'typebetegnelse', 'tyyppi', 'modelo']),
    'serial': (['serial', 'serialno', 'serialnumber', 'msn', 'cn', 'serienummer', 'sarjanumero',
                'constructionnumber', 'produksjonsnummer', 'serienr', 'nrserie'],
               ['serial', 'serienum', 'serienr', 'sarjanum', 'produksjonsnum', 'construction', 'nrserie']),
    'year': (['mfgyr', 'year', 'built', 'byggear', 'valmistusvuosi', 'yearofmanufacture', 'yearbuilt'],
             ['mfgyr', 'yearofmanu', 'manufactureyear', 'byggear', 'produksjonsar', 'valmistusvuosi', 'yearbuilt', 'anofabricacao', 'bouwjaar']),
    'operator': ([], ['regopname', 'operatorname', 'nmoperador', 'operator', 'bruker', 'kayttaja', 'haltija', 'opname', 'houder', 'operador']),
    'owner': ([], ['regholdname', 'ownername', 'owner', 'eier', 'omistaja', 'registrant', 'holdername', 'proprietario', 'eigenaar',
                   'holder', 'name', 'navn', 'nimi']),
    'city': ([], ['regopsuburb', 'opsuburb', 'regholdsuburb', 'suburb', 'city', 'town', 'poststed',
                  'postitoimipaikka', 'kotipaikka', 'locality', 'sted', 'woonplaats', 'vestigingsplaats', 'plaats', 'municipio', 'cidade']),
    'region': ([], ['regopstate', 'regholdstate', 'state', 'province', 'region', 'fylke', 'ufoperador', 'sguf']),
    'owner_city': ([], ['regholdsuburb', 'ownercity']),
    'address': ([], ['owneraddress', 'operatoraddress', 'address', 'adresse', 'osoite', 'adresse']),
    'owner_region': ([], ['regholdstate', 'ownerstate']),
    'date': ([], ['regholdcommdate', 'regopcommdate', 'registrationdate', 'dateofregistration',
                  'registreringsdato', 'registrertdato', 'registrert', 'rekisterointipaiva', 'rekisteroity',
                  'regdate', 'dateregistered', 'registered', 'issuedate', 'certissue', 'dateofissue', 'dtmatricula', 'datuminschrijving', 'inschrijfdatum', 'registratiedatum']),
}
EXCLUDE = re.compile(r'eng|motor|propel|helice|moteur|engine|addr|adresse|osoite|email|phone|post(nr|nummer|code)|zip')


def detect_columns(header):
    cols = {}
    nh = [norm(h) for h in header]
    used = set()
    for field, (exact, contains) in FIELDS.items():
        found = None
        for kw in exact:
            found = next((i for i, h in enumerate(nh) if h == kw and i not in used), None)
            if found is not None:
                break
        if found is None:
            for kw in contains:
                found = next((i for i, h in enumerate(nh) if kw in h and i not in used and not (
                    field in ('model', 'manufacturer', 'serial', 'owner', 'operator', 'city') and EXCLUDE.search(h))), None)
                if found is not None:
                    break
        if found is not None:
            cols[field] = found
            if field not in ('owner',):
                used.add(found)
    return cols


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def parse_date(value):
    v = str(value or '').strip()
    if not v:
        return None
    m = re.match(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', v)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.match(r'^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})', v)
        if m:
            d, mo, y = m.groups()
        elif re.match(r'^\d{8}$', v):
            y, mo, d = v[:4], v[4:6], v[6:8]
        elif re.match(r'^\d{5}(\.\d+)?$', v):    # date Excel
            return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(v)))).isoformat()
        else:
            m = re.match(r'^(\d{1,2})[- ]([A-Za-z]{3})[A-Za-z]*[- ](\d{2,4})', v)
            if not m:
                return None
            months = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()
            try:
                mo = months.index(m.group(2).lower()) + 1
            except ValueError:
                return None
            d, y = m.group(1), m.group(3)
            y = ('20' + y) if len(y) == 2 else y
    try:
        return dt.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


GOV = re.compile(r'\b(polizei|politi|poliisi|polis\b|gendarm|rajavartio|bundespolizei|police|polizei|politi|poliisi|polis|sheriff|state of|government|ministr|ministe|departement|'
                 r'department|county|city of|gendarmerie|securite civile|army|air force|navy|forsvar|coast ?guard|'
                 r'kystvakt|rajavartio|border|federal|commonwealth|crown|national guard|fire (and|&) rescue|'
                 r'brandweer|bundes|landes|guardia|carabinieri|staat|etat|stato|estado)', re.I)


def is_gov(name):
    plain = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
    return bool(GOV.search(plain))


def records(header, rows, cols, country, reg_prefix='', fixed=None):
    """Transforme les lignes d'un registre en enregistrements pour ingest()."""
    out = []

    def get(row, field):
        i = cols.get(field)
        return row[i].strip() if i is not None and i < len(row) and row[i] is not None else ''

    for row in rows:
        reg = get(row, 'registration').upper()
        if reg and reg_prefix and not reg.startswith(reg_prefix):
            reg = reg_prefix + reg
        operator = get(row, 'operator')
        owner = operator or get(row, 'owner')
        city, region = get(row, 'city'), get(row, 'region')
        if not operator and 'owner_city' in cols:
            city, region = get(row, 'owner_city') or city, get(row, 'owner_region') or region
        address = get(row, 'address')
        if not city and address:    # « PO Box 2240, TAUPO 3351, New Zealand » → Taupo
            parts = [p.strip() for p in address.split(',') if p.strip()]
            if len(parts) >= 2:
                city = re.sub(r'\d+', '', parts[-2]).strip().title()
        out.append({
            'registration': reg or None,
            'address': address or None,
            'msn': get(row, 'serial'),
            'manufacturer': get(row, 'manufacturer'),
            'registry_model': get(row, 'model'),
            'year_built': (re.search(r'(19|20)\d\d', get(row, 'year')) or [None])[0],
            'owner': owner,
            'city': city,
            'region': region,
            'country': country,
            'registered_on': parse_date(get(row, 'date')),
            'government': is_gov(owner),
            **(fixed or {}),
        })
    return out


def describe(header, rows, cols, n=2):
    """Résumé lisible pour le journal : colonnes reconnues et exemples."""
    lines = ['  colonnes reconnues : ' + ', '.join(f'{k}={header[i]!r}' for k, i in cols.items())]
    missing = [f for f in ('registration', 'manufacturer', 'model', 'serial', 'owner') if f not in cols]
    if missing:
        lines.append('  colonnes NON reconnues : ' + ', '.join(missing))
        lines.append('  en-tête du fichier : ' + ' | '.join(str(h) for h in header[:60]))
    for r in rows[:n]:
        lines.append('  exemple : ' + ' | '.join(str(c) for c in r[:30]))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Fichiers sans en-tête (ex. Canada) : reconnaissance des colonnes par leur contenu
# ---------------------------------------------------------------------------
AIRBUS_RE = re.compile(r'AIRBUS|EUROCOPTER|AEROSPATIALE|MBB|MESSERSCHMITT', re.I)
MAKERS_RE = re.compile(r'AIRBUS|EUROCOPTER|CESSNA|PIPER|BEECH|ROBINSON|BELL|HAVILLAND|BOEING|MOONEY', re.I)
PROVINCES = {'ON', 'QC', 'BC', 'AB', 'MB', 'SK', 'NS', 'NB', 'NL', 'PE', 'YT', 'NT', 'NU', 'ONTARIO', 'QUEBEC',
             'QUÉBEC', 'BRITISH COLUMBIA', 'ALBERTA', 'MANITOBA', 'SASKATCHEWAN', 'NOVA SCOTIA', 'NEW BRUNSWICK',
             'NEWFOUNDLAND AND LABRADOR', 'PRINCE EDWARD ISLAND', 'YUKON', 'NORTHWEST TERRITORIES', 'NUNAVUT'}


def _best(ncol, score, exclude=()):
    best, best_score = None, 0
    for c in range(ncol):
        if c in exclude:
            continue
        s = score(c)
        if s > best_score:
            best, best_score = c, s
    return best


def guess_columns(rows, map_model):
    """Devine les colonnes d'un registre sans en-tête à partir des valeurs."""
    rows = rows[:20000]
    ncol = max((len(r) for r in rows), default=0)
    val = lambda r, c: r[c].strip() if c < len(r) else ''
    cols = {}
    cols['manufacturer'] = _best(ncol, lambda c: sum(1 for r in rows if MAKERS_RE.search(val(r, c))))
    if cols['manufacturer'] is None:
        return {}
    mc = cols['manufacturer']
    airbus = [r for r in rows if AIRBUS_RE.search(val(r, mc))]
    cols['model'] = _best(ncol, lambda c: sum(1 for r in airbus if map_model(val(r, mc), val(r, c))), {mc})
    taken = {mc, cols['model']}
    cols['registration'] = _best(ncol, lambda c: sum(1 for r in rows if re.fullmatch(r'(C-?)?[A-Z]{3,4}', val(r, c))), taken)
    taken.add(cols['registration'])

    def serial_score(c):
        vals = [val(r, c) for r in airbus if re.fullmatch(r'\d{3,5}', val(r, c))]
        return len(vals) * (len(set(vals)) / len(vals)) if vals else 0
    cols['serial'] = _best(ncol, serial_score, taken)
    taken.add(cols['serial'])
    cols['year'] = _best(ncol, lambda c: sum(1 for r in rows if re.fullmatch(r'(19|20)\d\d', val(r, c))), taken)
    taken.add(cols['year'])
    cols['date'] = _best(ncol, lambda c: sum(1 for r in rows if parse_date(val(r, c)) and len(val(r, c)) >= 8), taken)
    return {k: v for k, v in cols.items() if v is not None}


def guess_owner_columns(rows, marks):
    """Dans un fichier de propriétaires sans en-tête : colonnes immatriculation, nom, ville, province."""
    rows = rows[:20000]
    ncol = max((len(r) for r in rows), default=0)
    val = lambda r, c: r[c].strip() if c < len(r) else ''
    mark = _best(ncol, lambda c: sum(1 for r in rows if val(r, c).upper().replace('C-', '') in marks))
    if mark is None:
        return {}
    name = _best(ncol, lambda c: sum(1 for r in rows if len(re.findall(r'[A-Za-z]{2,}', val(r, c))) >= 2), {mark})
    prov = _best(ncol, lambda c: sum(1 for r in rows if val(r, c).upper() in PROVINCES), {mark, name})
    cols = {'registration': mark, 'owner': name}
    if prov is not None:
        cols['region'] = prov
        if prov - 1 not in (mark, name):
            cols['city'] = prov - 1
    return cols
