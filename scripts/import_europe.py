"""Helibase Europe : hélicoptères Airbus NEUFS livrés en Europe depuis 2024.

Sources :
  1. rotorspot.nl (listes de production, mises à jour le 1er de chaque mois) : chaque numéro de
     série et son historique d'immatriculations. Livraison neuve = première immatriculation
     après les immatriculations d'essai usine. Seules les livraisons européennes sont gardées.
  2. Registre FAA (États-Unis) : uniquement pour CALIBRER l'année de fabrication en fonction du
     numéro de série (Rotorspot ne donne pas de dates). Aucun appareil américain n'est importé.
  3. Registres officiels français (DGAC) et suisse (OFAC) : exploitant réel et adresse.
  Ailleurs, l'exploitant reste « à identifier » (jamais deviné) ; les matricules militaires et
  les aéronefs d'État sont rattachés aux forces armées / à l'État du pays concerné.

Dates :
  - appareils déjà présents au premier passage : année estimée d'après le numéro de série ;
  - appareils apparus ensuite : mois de leur première apparition (précision « mois ») ;
  - immatriculation réservée, pas encore utilisée : statut « prévu ».
  Les familles sans calibrage possible (NH90, Tigre, H175, H215/H225, Dauphin) ne sont reprises
  au premier passage que si leur livraison est en cours (« prévu ») ; ensuite, toute nouvelle
  livraison européenne est ajoutée au mois où elle apparaît.

Utilisation :
  python scripts/import_europe.py              # passage normal (mensuel)
  python scripts/import_europe.py --dry-run    # analyse sans écrire
  python scripts/import_europe.py --force      # relit Rotorspot même si les pages n'ont pas changé
"""
import argparse
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helibase import rotorspot as R  # noqa: E402
from helibase.europe import EUROPE, country_name, is_europe  # noqa: E402
from helibase.names import infer_activities, is_financial, is_stock, name_key, pretty_name  # noqa: E402

SINCE_YEAR = 2024
CALIBRATED = {'Ecureuil', 'H135', 'BK117', 'H160', 'H140'}
SOURCE_LABEL = 'Rotorspot (listes de production)'
SOURCE_URL = 'https://www.rotorspot.nl/product.php'


# ---------------------------------------------------------------------------
# 1. Calibrage numéro de série → année (registre FAA)
# ---------------------------------------------------------------------------
def calibrate(log=print):
    import import_faa
    from helibase.models import map_any
    log('Calibrage des années à partir du registre FAA…')
    recs = import_faa.parse(import_faa.download())
    by = {}
    for r in recs:
        m = map_any(r['manufacturer'], r['registry_model'], r['year_built'])
        n = R.msn_number(r['msn'])
        try:
            y = int(str(r['year_built'])[:4])
        except (TypeError, ValueError):
            continue
        if m and n and 1990 <= y <= dt.date.today().year:
            by.setdefault(m[0], {}).setdefault(y, []).append(n)
    cal = {}
    for fam, years in by.items():
        t = {}
        for y in range(SINCE_YEAR, dt.date.today().year + 1):
            ns = sorted(years.get(y, []))
            if len(ns) >= 3:
                t[y] = ns[max(0, int(len(ns) * 0.1) - 0)]     # 10e centile : écarte les rares retardataires
        if t:
            cal[fam] = t
            log(f'  {fam} : ' + ', '.join(f'{y} ≥ n° {n}' for y, n in sorted(t.items())))
    cal['H140'] = {SINCE_YEAR: 0}     # modèle entièrement nouveau : tous ses exemplaires sont récents
    return cal


def est_year(cal, family, msn):
    t = cal.get(family)
    if not t or msn is None:
        return None
    years = [y for y, n in sorted(t.items()) if msn >= n]
    return years[-1] if years else None


# ---------------------------------------------------------------------------
# 2. Registres officiels pour l'exploitant
# ---------------------------------------------------------------------------
SWEDEN_URL = 'https://github.com/civictechsweden/oppna-luftfartygsregistret/raw/refs/heads/master/register_light.json'


def src_suede():
    """Registre suédois (Transportstyrelsen), republié en données libres CC0 par Civic Tech Sweden.
    Le format exact n'étant pas documenté, la lecture repère les champs par leur contenu."""
    import json as _json
    from helibase.tables import http_get
    data = _json.loads(http_get(SWEDEN_URL))
    rows = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
    out = []
    for a in rows:
        if not isinstance(a, dict):
            continue
        reg = None
        for k, v in a.items():
            if isinstance(v, str) and re.fullmatch(r'(SE-)?[A-Z]{3}', v.strip()) and ('reg' in k.lower() or 'beteck' in k.lower() or k.lower() in ('id', 'mark')):
                reg = v.strip()
                break
        if not reg:
            continue
        reg = reg if reg.startswith('SE-') else 'SE-' + reg
        owners = [o for o in (a.get('owners') or []) if isinstance(o, dict) and o.get('name')]
        # « Brukare / innehavare » (utilisateur) avant « Ägare » (propriétaire, souvent une société de leasing)
        owners.sort(key=lambda o: 0 if re.search(r'bruk|innehav|operat', str(o.get('type', '')), re.I) else 1)
        if not owners:
            continue
        o = owners[0]
        city = next((str(v) for k, v in o.items() if re.search(r'city|ort|postort', k, re.I) and isinstance(v, str)), None)
        out.append({'registration': reg, 'owner': o['name'], 'city': city, 'address': None, 'government': False})
    return None, None, out


def load_registers(log=print):
    import import_registres as IR
    regs = {}
    for name, fn in (('France', IR.src_france), ('Suisse', IR.src_suisse), ('Suède', src_suede)):
        try:
            _, _, recs = fn()
            for r in recs:
                if r.get('registration'):
                    regs[r['registration'].upper()] = r
            log(f'  registre {name} : {len(recs)} aéronefs')
        except Exception as e:
            log(f'  registre {name} indisponible : {e}')
    return regs


# ---------------------------------------------------------------------------
# 3. Analyse des listes de production
# ---------------------------------------------------------------------------
def analyse(pages_html, cal, known_before, first_run, log=print):
    """Renvoie (livraisons européennes retenues, clés de tous les appareils déjà livrés quelque part)."""
    today = dt.date.today()
    picked, delivered_keys = {}, set()
    stats = {'lignes': 0, 'hors_periode': 0, 'non_calibre': 0}
    for page, family in R.PAGES:
        if page not in pages_html:
            continue
        for type_text, cn, history, fate in R.parse_rows(pages_html[page][0]):
            stats['lignes'] += 1
            n = R.msn_number(cn)
            if n is None:
                continue
            key = f'{family}:{n}'
            doubtful = '?' in cn
            d, tests = R.delivery(history)
            if not d:
                continue
            delivered_keys.add(key)
            if not is_europe(d['country']):
                continue
            prev = picked.get(key)
            if prev and not prev['doubtful'] and doubtful:
                continue        # même numéro listé deux fois : la ligne sûre l'emporte
            status = 'prevu' if d['reserved'] else 'livre'
            year = est_year(cal, family, n)
            if family in cal and (year is None or year < SINCE_YEAR) and (first_run or key in known_before):
                stats['hors_periode'] += 1
                continue
            start, precision = None, None
            if status == 'livre':
                if not first_run and key not in known_before:
                    start, precision = today.replace(day=1).isoformat(), 'mois'     # repéré ce mois-ci
                elif family in cal and year is not None:
                    start, precision = f'{year}-01-01', 'annee_estimee'
                else:
                    stats['non_calibre'] += 1
                    continue
            picked[key] = {'key': key, 'family': family, 'msn': str(n), 'variant': type_text, 'doubtful': doubtful,
                           'model': R.commercial_name(type_text, family), 'tests': ', '.join(tests),
                           'delivery': d, 'status': status, 'start': start, 'precision': precision,
                           'est_year': year, 'fate': fate}
    log(f"  {stats['lignes']} lignes lues, {len(picked)} livraisons européennes retenues "
        f"({stats['hors_periode']} antérieures à {SINCE_YEAR}, {stats['non_calibre']} non datables au premier passage)")
    return list(picked.values()), delivered_keys


def resolve_operator(rec, registers):
    """Exploitant d'une livraison : (nom, pays, secteur, est_à_identifier, infos registre)."""
    d = rec['delivery']
    c = d['country']
    if d['kind'] == 'military':
        return f'Forces armées — {country_name(c)}', c, 'militaire', False, None
    if d['kind'] == 'military_fr':
        return 'Aéronefs militaires — France (F-M…)', 'FR', 'militaire', True, None
    if d['kind'] == 'state':
        return "Aéronefs d'État — France (F-Z…)", 'FR', 'etatique', True, None
    reg = registers.get((d['reg'] or '').upper())
    if reg:
        owner = (reg.get('owner') or '').strip()
        if owner and not is_stock(owner) and not is_financial(owner):
            return pretty_name(owner), c, ('etatique' if reg.get('government') else 'civil'), False, reg
    return f'Exploitant à identifier — {country_name(c)}', c, 'civil', True, None


# ---------------------------------------------------------------------------
# 4. Écriture dans la base
# ---------------------------------------------------------------------------
def sync(store, recs, registers, log=print):
    from helibase.verify import geocode_city
    today = dt.date.today()
    source = store.insert('sources', [{'kind': 'registre', 'label': SOURCE_LABEL, 'url': SOURCE_URL,
                                       'retrieved_on': today.isoformat(), 'confidence': 4}])[0]

    # Exploitants
    ops = store.select('operators', 'id,name,aliases,country,city,lat,is_placeholder')
    by_key = {(name_key(o['name']), o.get('country') or ''): o for o in ops}
    placeholder_ids = {o['id'] for o in ops if o.get('is_placeholder')}
    created = []

    def operator_for(rec):
        name, c, sector, placeholder, reg = resolve_operator(rec, registers)
        k = (name_key(name), c or '')
        o = by_key.get(k)
        if o:
            return o
        row = {'name': name, 'country': c, 'sector': sector, 'is_placeholder': placeholder,
               'activities': [] if placeholder else infer_activities(name)}
        if placeholder or sector == 'militaire':     # pas d'adresse : placé au centre du pays
            row['lat'], row['lon'] = EUROPE[c][1], EUROPE[c][2]
        elif reg:
            row['city'] = (reg.get('city') or '').title() or None
            if reg.get('address'):
                row['address'] = reg['address']
        o = store.insert('operators', [row])[0]
        o['_region'] = reg.get('region') if reg else None
        if placeholder:
            placeholder_ids.add(o['id'])
        by_key[k] = o
        created.append(o)
        return o

    # Hélicoptères (nouveaux : avec date de première apparition)
    existing = {(h['family'], h['msn']): h for h in store.select('helicopters', 'id,family,msn,first_seen')}
    new_rows = [{'family': r['family'], 'msn': r['msn'], 'model': r['model'], 'variant': r['variant'],
                 'test_registrations': r['tests'] or None, 'est_year': r['est_year'],
                 'first_seen': today.replace(day=1).isoformat()}
                for r in recs if (r['family'], r['msn']) not in existing]
    upd_rows = [{'family': r['family'], 'msn': r['msn'], 'model': r['model'], 'variant': r['variant'],
                 'test_registrations': r['tests'] or None, 'est_year': r['est_year']}
                for r in recs if (r['family'], r['msn']) in existing]
    heli = {}
    for rows in (new_rows, upd_rows):
        if rows:
            for h in store.insert('helicopters', rows, on_conflict='family,msn'):
                heli[(h['family'], h['msn'])] = h['id']

    # Affectations (une par appareil : la livraison neuve)
    current = {a['helicopter_id']: a for a in store.select(
        'assignments', 'id,helicopter_id,operator_id,registration,status,start_date,date_precision', end_date='is.null')}
    inserts, updates = [], 0
    for r in recs:
        hid = heli.get((r['family'], r['msn']))
        if not hid:
            continue
        op = operator_for(r)
        row = {'helicopter_id': hid, 'operator_id': op['id'], 'registration': r['delivery']['reg'] or None,
               'status': r['status'], 'start_date': r['start'], 'date_precision': r['precision'],
               'is_new': True, 'source_id': source['id'],
               'operator_method': None if op['id'] in placeholder_ids else 'registre'}
        a = current.get(hid)
        if not a:
            inserts.append(row)
            continue
        change = {}
        # Un exploitant trouvé (registre, presse, site, recherche vérifiée) n'est jamais remplacé par « à identifier »
        keep = op['id'] in placeholder_ids and a.get('operator_id') and a['operator_id'] not in placeholder_ids
        if a['operator_id'] != op['id'] and not keep:
            change['operator_id'] = op['id']
            change['operator_method'] = None if op['id'] in placeholder_ids else 'registre'
        if (a.get('registration') or None) != row['registration']:
            change['registration'] = row['registration']
        if a.get('status') != r['status']:
            change['status'] = r['status']
            if r['status'] == 'livre' and a.get('status') == 'prevu':   # livraison constatée ce mois-ci
                change['start_date'], change['date_precision'] = today.replace(day=1).isoformat(), 'mois'
        elif a.get('date_precision') in (None, 'annee_estimee') and r['precision'] == 'annee_estimee' \
                and a.get('start_date') != r['start']:
            change['start_date'] = r['start']
        if change:
            store.update('assignments', change, id=f'eq.{a["id"]}')
            updates += 1
    if inserts:
        store.insert('assignments', inserts)

    # Position des nouveaux exploitants identifiés (ville du registre)
    geo = 0
    for o in created:
        if o.get('lat') is None and o.get('city'):
            pos = geocode_city(o['city'], o.get('_region'), o.get('country'))
            if pos:
                store.update('operators', {'lat': pos[0], 'lon': pos[1]}, id=f'eq.{o["id"]}')
                geo += 1
    store.insert('update_log', [{'source': SOURCE_LABEL, 'added': len(inserts), 'changed': updates, 'queued': 0}])
    log(f'Résultat : {len(inserts)} livraisons ajoutées, {updates} mises à jour, '
        f'{len(created)} exploitants créés ({sum(1 for o in created if o.get("is_placeholder"))} « à identifier »), '
        f'{geo} placés sur la carte')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--force', action='store_true')
    args = p.parse_args()

    store = None
    if not args.dry_run:
        from helibase.store import Store
        store = Store()

    def cache_get(key):
        if not store:
            return None
        r = store.select('source_cache', 'key,hash,payload,fetched_at', key=f'eq.{key}')
        return r[0] if r else None

    def cache_put(key, h=None, payload=None):
        if store:
            store.insert('source_cache', [{'key': key, 'hash': h, 'payload': payload,
                                           'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}], on_conflict='key')

    print('Lecture de Rotorspot…')
    pages = R.fetch_all()
    changed = [pg for pg, (_, h) in pages.items() if args.force or (cache_get('rotorspot:' + pg) or {}).get('hash') != h]
    if not changed:
        print('Aucune page n\'a changé depuis le dernier passage : rien à faire.')
        return
    print(f'{len(changed)} page(s) modifiée(s) depuis le dernier passage')

    cached_cal = cache_get('calibration')
    if cached_cal and cached_cal.get('payload') and not args.force and \
            cached_cal['fetched_at'][:7] == dt.date.today().isoformat()[:7]:
        cal = {f: {int(y): n for y, n in t.items()} for f, t in cached_cal['payload'].items()}
    else:
        try:
            cal = calibrate()
            cache_put('calibration', payload=cal)
        except Exception as e:      # registre FAA indisponible : on garde le dernier calibrage connu
            print(f'Calibrage impossible ({e}) : dernier calibrage connu utilisé')
            cal = {f: {int(y): n for y, n in t.items()} for f, t in ((cached_cal or {}).get('payload') or {'H140': {'2024': 0}}).items()}

    known = cache_get('rotorspot:known')
    first_run = not (known and known.get('payload'))
    known_before = set((known or {}).get('payload') or [])
    recs, delivered_keys = analyse(pages, cal, known_before, first_run)
    by_country = {}
    for r in recs:
        by_country[r['delivery']['country']] = by_country.get(r['delivery']['country'], 0) + 1
    print('  par pays : ' + ', '.join(f'{country_name(c)} {n}' for c, n in sorted(by_country.items(), key=lambda x: -x[1])))
    print('  statut : ' + ', '.join(f'{s} {sum(1 for r in recs if r["status"] == s)}' for s in ('livre', 'prevu')))

    print('Registres officiels (exploitants)…')
    registers = load_registers()
    if args.dry_run:
        for r in recs[:40]:
            name = resolve_operator(r, registers)[0]
            print(f"  {r['model']:<10} n° {r['msn']:<6} {r['delivery']['raw']:<18} {r['status']:<6} "
                  f"{r['start'] or '':<10} {name}")
        return
    sync(store, recs, registers)
    # Mémoire pour le prochain passage : appareils connus, empreinte des pages
    all_keys = sorted(known_before | delivered_keys)
    cache_put('rotorspot:known', payload=all_keys)
    for pg, (_, h) in pages.items():
        cache_put('rotorspot:' + pg, h=h)


if __name__ == '__main__':
    main()
