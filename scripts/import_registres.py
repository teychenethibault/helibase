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


def src_canada():
    data = T.http_get('https://wwwapps.tc.gc.ca/Saf-Sec-Sur/2/CCARCS-RIACC/download/ccarcsdb.zip')
    _, cur = T.read_csv(T.zip_member(data, r'carscurr'), header=False)
    cols = T.guess_columns(cur, map_model)
    print(T.describe([f'col{i}' for i in range(max(map(len, cur[:500])))], cur, cols))
    if not {'manufacturer', 'model', 'serial', 'registration'} <= set(cols):
        raise RuntimeError('structure du fichier non reconnue')
    recs = T.records(None, cur, cols, 'CA', 'C-')
    # Propriétaires : fichier séparé, relié par l'immatriculation
    _, own = T.read_csv(T.zip_member(data, r'carsownr'), header=False)
    marks = {r['registration'].replace('C-', '') for r in recs if r['registration']}
    ocols = T.guess_owner_columns(own, marks)
    print('  propriétaires :', T.describe([f'col{i}' for i in range(max(map(len, own[:500])))], own, ocols, n=1))
    owners = {}
    for r in T.records(None, own, ocols, 'CA', 'C-'):
        owners.setdefault(r['registration'], r)
    for r in recs:
        o = owners.get(r['registration'])
        if o:
            r.update(owner=o['owner'], city=o['city'], region=o['region'], government=o['government'])
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
            'registered_on': T.parse_date(get(r, 'registered')),
            'government': T.is_gov(owner),
        })
    print(f'  {len(recs)} hélicoptères Airbus dans la base OpenSky hors pays déjà couverts ({skipped} ignorés)')
    by_country = {}
    for r in recs:
        by_country[r['country']] = by_country.get(r['country'], 0) + 1
    print('  par pays :', ', '.join(f'{c} {n}' for c, n in sorted(by_country.items(), key=lambda x: -x[1])[:40]))
    return 'Base OpenSky Network (mondiale, communautaire)', 'https://opensky-network.org/aircraft-database', recs


OFFICIAL = {
    'australie': ('AU', src_australie),
    'nz': ('NZ', src_nouvelle_zelande),
    'norvege': ('NO', src_norvege),
    'finlande': ('FI', src_finlande),
    'canada': ('CA', src_canada),
}


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
            ingest(store, recs, label, url, since=args.since, confidence=confidence)
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
    if not only or 'opensky' in only:
        run('opensky', src_opensky, 2)

    print('\nSources en échec : ' + (', '.join(failures) if failures else 'aucune'))


if __name__ == '__main__':
    main()
