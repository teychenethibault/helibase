"""Import de tous les registres publics téléchargeables (hors États-Unis, voir import_faa.py)
et de la base mondiale OpenSky pour les pays dont le registre n'est pas ouvert (France,
Allemagne, Royaume-Uni, Italie, Espagne…).

Chaque source est indépendante : si l'une échoue, les autres continuent, et le journal
indique pourquoi (colonnes reconnues, exemples de lignes) pour pouvoir corriger.

Utilisation :
  python scripts/import_registres.py                     # toutes les sources
  python scripts/import_registres.py --only casa,nz      # certaines sources
  python scripts/import_registres.py --dry-run           # lit tout, n'écrit rien
"""
import argparse
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))
from helibase import tables as T  # noqa: E402
from helibase.countries import country_from_registration  # noqa: E402
from helibase.models import map_any, map_model  # noqa: E402

# Pays couverts par un registre officiel importé : la base OpenSky ne les traite pas
OFFICIAL_DONE = {'US'}


# ---------------------------------------------------------------------------
# Registres officiels
# ---------------------------------------------------------------------------
def table_source(data, country, prefix, member=None):
    header, rows = T.read_any(data, member)
    cols = T.detect_columns(header)
    print(T.describe(header, rows, cols))
    if not {'manufacturer', 'model', 'serial'} <= set(cols) and not {'model', 'serial'} <= set(cols):
        raise RuntimeError('colonnes indispensables introuvables (voir en-tête ci-dessus)')
    return T.records(header, rows, cols, country, prefix)


def src_australie():
    data = T.http_get('https://services.casa.gov.au/CSV/acrftreg.zip')
    return ('Registre CASA (Australie)', 'https://www.casa.gov.au/search-centre/aircraft-register',
            table_source(data, 'AU', 'VH-', r'\.(csv|txt)$'))


def src_nouvelle_zelande():
    page = 'https://www.aviation.govt.nz/aircraft/aircraft-registration/aircraft-register-search/aircraft-register-amendments/'
    url = T.find_link(page, r'\.csv') or T.find_link(page, r'\.xlsx')
    if not url:
        raise RuntimeError('lien de téléchargement introuvable sur ' + page)
    print('  fichier :', url)
    return 'Registre CAA (Nouvelle-Zélande)', page, table_source(T.http_get(url), 'NZ', 'ZK-')


def src_norvege():
    url = 'https://data.caa.no/nlr/norgesluftfartoyregister.json'
    return 'Registre Luftfartstilsynet (Norvège)', 'https://www.luftfartstilsynet.no', \
        table_source(T.http_get(url), 'NO', 'LN-')


def src_finlande():
    import json
    api = 'https://www.avoindata.fi/data/api/3/action/package_show?id=ilma-alusrekisterin-avoin-data'
    pkg = json.loads(T.http_get(api))['result']
    res = sorted(pkg['resources'], key=lambda r: r.get('last_modified') or r.get('created') or '', reverse=True)
    url = next((r['url'] for r in res if re.search(r'csv|zip', (r.get('format') or '') + r['url'], re.I)), res[0]['url'])
    print('  fichier :', url)
    return 'Registre Traficom (Finlande)', 'https://www.avoindata.fi/data/fi/dataset/ilma-alusrekisterin-avoin-data', \
        table_source(T.http_get(url), 'FI', 'OH-')


def _french_city(address):
    m = re.search(r'\b(\d{5})\s+(.+?)\s+(FRANCE|CEDEX.*)?$', (address or '').strip())
    if not m:
        return ''
    return re.sub(r'\s+CEDEX.*$', '', m.group(2)).strip()


def src_france():
    url = 'https://immat.aviation-civile.gouv.fr/immat/servlet/static/upload/export.csv'
    header, rows = T.read_csv(T.http_get(url))
    idx = {h.strip().upper(): i for i, h in enumerate(header)}
    need = ['IMMATRICULATION', 'CONSTRUCTEUR', 'MODELE', 'NUMERO_SERIE', 'PROPRIETAIRE', 'LOCATAIRE']
    missing = [n for n in need if n not in idx]
    if missing:
        print('  en-tête :', ' | '.join(header))
        raise RuntimeError('colonnes absentes : ' + ', '.join(missing))
    g = lambda r, k: (r[idx[k]].strip() if k in idx and idx[k] < len(r) else '')
    recs, seen = [], set()
    for r in rows:
        reg = g(r, 'IMMATRICULATION').upper()
        if reg in seen:          # copropriété : une ligne par copropriétaire, on garde la première
            continue
        seen.add(reg)
        lessee = g(r, 'LOCATAIRE')
        owner = lessee or g(r, 'PROPRIETAIRE')
        address = g(r, 'ADRESSE_LOCATAIRE') if lessee else g(r, 'ADRESSE_PROPRIETAIRE')
        recs.append({
            'registration': reg, 'msn': g(r, 'NUMERO_SERIE'), 'manufacturer': g(r, 'CONSTRUCTEUR'),
            'registry_model': g(r, 'MODELE'), 'year_built': None, 'owner': owner,
            'city': _french_city(address).title(), 'region': '', 'country': 'FR', 'address': address.title() or None,
            'registered_on': None, 'government': T.is_gov(owner),
        })
    print('  exemple :', recs[:1])
    return 'Registre DGAC (France)', 'https://immat.aviation-civile.gouv.fr/immat/servlet/aeronef_liste.html', recs


def src_suisse():
    """Registre suisse : interface publique du site de l'OFAC (BAZL), lue page par page."""
    import json
    api = 'https://app02.bazl.admin.ch/web/bazl-backend/lfr'
    recs, page = [], 1
    while True:
        body = json.dumps({'page_result_limit': 1000, 'current_page_number': page, 'sort_list': 'registration',
                           'language': 'fr', 'queryProperties': {'marketing': '', 'aircraftStatus': ['Registered']}}).encode()
        req = T.urllib.request.Request(api, data=body, method='POST', headers={
            'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': T.BROWSER_UA})
        with T.urllib.request.urlopen(req, timeout=120) as resp:
            items = json.loads(resp.read())
        for x in items:
            d = x.get('details') or {}
            holders = x.get('ownerOperators') or []
            pick = next((h for h in holders if 'Operator' in ((h.get('holderCategory') or {}).get('categoryNames') or {}).get('en', '')), None) \
                or (holders[0] if holders else {})
            a = pick.get('address') or {}
            na = lambda v: '' if v in (None, 'N/A') else str(v)
            rd = d.get('regDate')
            recs.append({
                'registration': x.get('registration'), 'msn': na(d.get('serialNumber')), 'manufacturer': x.get('manufacturer') or '',
                'registry_model': x.get('aircraftModelType') or '', 'typecode': x.get('icaoCode'),
                'year_built': na(d.get('yearOfManufacture')) or None, 'owner': pick.get('ownerOperator') or '',
                'city': na(a.get('city')), 'region': '', 'country': 'CH',
                'address': ', '.join(p for p in [' '.join(p for p in [na(a.get('street')), na(a.get('streetNo'))] if p),
                                                 ' '.join(p for p in [na(a.get('zipCode')), na(a.get('city'))] if p)] if p) or None,
                'registered_on': f'{rd[0]:04d}-{rd[1]:02d}-{rd[2]:02d}' if isinstance(rd, list) and len(rd) == 3 else None,
                'government': T.is_gov(pick.get('ownerOperator')),
            })
        if len(items) < 1000:
            break
        page += 1
    return 'Registre OFAC (Suisse)', 'https://app02.bazl.admin.ch/web/bazl/fr/#/lfr/search', recs


def src_bresil():
    import datetime as _dt
    base = 'https://sistemas.anac.gov.br/dadosabertos/Aeronaves/RAB/'
    candidates = [base + 'dados_aeronaves.csv']
    d = _dt.date.today().replace(day=1)
    for _ in range(4):
        candidates.append(base + f'Historico_RAB/{d:%Y-%m}.csv')
        d = (d - _dt.timedelta(days=1)).replace(day=1)
    last = None
    for url in candidates:
        try:
            data = T.http_get(url)
            print('  fichier :', url)
            recs = table_source(data, 'BR', '')
            for r in recs:
                reg = (r['registration'] or '').upper()
                if reg and '-' not in reg and len(reg) == 5:
                    r['registration'] = reg[:2] + '-' + reg[2:]
            return 'Registre RAB (Brésil)', 'https://sistemas.anac.gov.br/dadosabertos/Aeronaves/RAB/', recs
        except Exception as e:
            last = e
            print(f'  {url} : {e}')
    raise last


def src_pays_bas():
    import json
    api = 'https://data.overheid.nl/data/api/3/action/package_show?id=luchtvaartuigregister'
    res = json.loads(T.http_get(api))['result']['resources']
    url = next((r['url'] for r in res if re.search(r'csv', (r.get('format') or '') + r['url'], re.I)), res[0]['url'])
    print('  fichier :', url)
    return 'Registre ILT (Pays-Bas)', 'https://data.overheid.nl/dataset/luchtvaartuigregister', \
        table_source(T.http_get(url), 'NL', 'PH-')


def src_canada():
    """Fichiers sans en-tête. Colonnes observées (nov. 2026) :
    carscurr : 0 immatriculation, 3 constructeur (nom courant), 4 modèle, 5 n° de série, 7 constructeur complet,
               22 date d'immatriculation ; carsownr : 0 immatriculation, 1 nom, 5 ville, 6 province."""
    data = T.http_get('https://wwwapps.tc.gc.ca/Saf-Sec-Sur/2/CCARCS-RIACC/download/ccarcsdb.zip')
    _, cur = T.read_csv(T.zip_member(data, r'carscurr'), header=False)
    _, own = T.read_csv(T.zip_member(data, r'carsownr'), header=False)
    c = lambda r, i: r[i].strip() if i < len(r) else ''
    owners = {}
    for r in own:
        owners.setdefault(c(r, 0), r)
    recs = []
    for r in cur:
        mark = c(r, 0)
        o = owners.get(mark)
        name = c(o, 1) if o else ''
        recs.append({
            'registration': 'C-' + mark, 'msn': c(r, 5), 'manufacturer': c(r, 3) + ' ' + c(r, 7),
            'registry_model': c(r, 4), 'year_built': None, 'owner': name,
            'city': c(o, 5).title() if o else '', 'region': c(o, 6) if o else '', 'country': 'CA',
            'registered_on': T.parse_date(c(r, 22)), 'government': T.is_gov(name),
        })
    sample = [x for x in recs if map_model(x['manufacturer'], x['registry_model'])][:2]
    print('  exemple :', sample)
    return 'Registre CCARCS (Canada)', 'https://wwwapps.tc.gc.ca/saf-sec-sur/2/ccarcs-riacc/', recs


# ---------------------------------------------------------------------------
# Base mondiale OpenSky (communautaire, alimentée par les transpondeurs)
# ---------------------------------------------------------------------------
def opensky_url():
    listing = T.http_get('https://s3.opensky-network.org/data-samples?list-type=2&prefix=metadata/').decode()
    files = re.findall(r'<Key>(metadata/aircraft-database-complete-\d{4}-\d{2}\.csv)</Key>', listing)
    if not files:
        raise RuntimeError('aucun fichier aircraft-database-complete dans la liste OpenSky')
    return 'https://s3.opensky-network.org/data-samples/' + sorted(files)[-1]


def src_opensky():
    url = opensky_url()
    print('  fichier :', url)
    header, rows = T.read_csv(T.http_get(url, timeout=900))
    idx = {T.norm(h): i for i, h in enumerate(header)}
    need = ['registration', 'manufacturername', 'model', 'serialnumber', 'typecode', 'owner', 'operator', 'built', 'registered']
    missing = [n for n in need if n not in idx]
    if missing:
        print('  en-tête :', ' | '.join(header))
        raise RuntimeError('colonnes absentes : ' + ', '.join(missing))

    def get(r, k):
        i = idx[k]
        return r[i].strip().strip("'") if i < len(r) else ''

    recs, skipped = [], 0
    for r in rows:
        mfr = get(r, 'manufacturername') or (get(r, 'manufacturericao') if 'manufacturericao' in idx else '')
        if not map_any(mfr, get(r, 'model'), get(r, 'built'), get(r, 'typecode')):
            continue
        reg = get(r, 'registration').upper()
        country = country_from_registration(reg)
        if not country or country in OFFICIAL_DONE:
            skipped += 1
            continue
        owner = get(r, 'operator') or get(r, 'owner')
        recs.append({
            'registration': reg, 'msn': get(r, 'serialnumber'), 'manufacturer': mfr or 'AIRBUS',
            'registry_model': get(r, 'model'), 'typecode': get(r, 'typecode'),
            'year_built': (re.search(r'(19|20)\d\d', get(r, 'built')) or [None])[0],
            'owner': owner, 'city': '', 'region': '', 'country': country,
            'registered_on': T.parse_date(get(r, 'registered')) or
                             (T.parse_date(get(r, 'firstflightdate')) if 'firstflightdate' in idx else None),
            'government': T.is_gov(owner),
        })
    print(f'  {len(recs)} hélicoptères Airbus dans la base OpenSky hors pays déjà couverts ({skipped} ignorés)')
    by_country = {}
    for r in recs:
        by_country[r['country']] = by_country.get(r['country'], 0) + 1
    years = {}
    for r in recs:
        y = (r['year_built'] or (r['registered_on'] or '')[:4] or 'inconnue')
        years[y] = years.get(y, 0) + 1
    print('  années (construction ou immatriculation) :', ', '.join(f'{y} {n}' for y, n in sorted(years.items())[-12:]))
    print('  sans numéro de série :', sum(1 for r in recs if not r['msn']))
    print('  par pays :', ', '.join(f'{c} {n}' for c, n in sorted(by_country.items(), key=lambda x: -x[1])[:40]))
    return 'Base OpenSky Network (mondiale, communautaire)', 'https://opensky-network.org/aircraft-database', recs


OFFICIAL = {
    'australie': ('AU', src_australie),
    'nz': ('NZ', src_nouvelle_zelande),
    'norvege': ('NO', src_norvege),
    'finlande': ('FI', src_finlande),
    'canada': ('CA', src_canada),
    'france': ('FR', src_france),
    'suisse': ('CH', src_suisse),
    'bresil': ('BR', src_bresil),
}
# Désactivés : Pays-Bas (le fichier officiel n'est plus publié), OpenSky (aucune donnée après 2019)
# Registres sans date d'immatriculation : toute la flotte actuelle est intégrée
ALL_YEARS = {'france'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', help='sources à traiter, séparées par des virgules (' + ', '.join([*OFFICIAL, 'opensky']) + ')')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--since', default='2024-01-01')
    args = parser.parse_args()
    only = set(args.only.split(',')) if args.only else None

    store = None
    if not args.dry_run:
        from helibase.store import Store
        store = Store()
    from helibase.ingest import ingest

    failures = []

    def run(name, fn, confidence):
        print(f'\n=== {name} ===')
        try:
            label, url, recs = fn()
            airbus = sum(1 for r in recs if map_any(r['manufacturer'], r['registry_model'], r['year_built'], r.get('typecode')))
            print(f'  {len(recs)} lignes lues, {airbus} hélicoptères Airbus')
            if args.dry_run:
                recent = [r for r in recs if map_any(r['manufacturer'], r['registry_model'], r['year_built'], r.get('typecode'))
                          and ((r['registered_on'] or '') >= args.since or (r['year_built'] or '0') >= args.since[:4])]
                for r in recent[:8]:
                    print(f"    {r['registration']:<9} {r['registry_model'] or r.get('typecode'):<18} n° {r['msn']:<6} "
                          f"{r['year_built']} {r['registered_on']} {r['owner']} ({r['city']})")
                print(f'  {len(recent)} dans la période (mode test : rien n\'a été écrit)')
                return True
            ingest(store, recs, label, url, since=args.since, confidence=confidence, all_years=name in ALL_YEARS)
            return True
        except Exception as e:
            traceback.print_exc(limit=3)
            print(f'  ÉCHEC de la source {name} : {e}')
            failures.append(name)
            return False

    for name, (country, fn) in OFFICIAL.items():
        if only and name not in only:
            continue
        if run(name, fn, 4):
            OFFICIAL_DONE.add(country)
    if only and 'opensky' in only:   # seulement à la demande : base figée depuis 2019 environ
        run('opensky', src_opensky, 2)

    print('\nSources en échec : ' + (', '.join(failures) if failures else 'aucune'))


if __name__ == '__main__':
    main()
