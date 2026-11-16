"""Intégration des immatriculations dans la base Helibase.

Chaque registre fournit une liste d'enregistrements (dictionnaires) :
    registration, msn, manufacturer, registry_model, year_built,
    owner, city, region, country (code ISO à 2 lettres),
    registered_on (date ISO de l'immatriculation), government (bool)

L'intégration :
  - ne garde que les hélicoptères Airbus reconnus par models.map_model ;
  - crée les hélicoptères (clé : famille + numéro de série) ;
  - rattache chaque hélicoptère à son exploitant, en créant l'exploitant si besoin ;
  - garde l'historique : un changement d'exploitant ferme l'ancienne affectation (revente) ;
  - met dans la file « à vérifier » les appareils détenus par un propriétaire financier ;
  - ignore les appareils encore au nom du constructeur ou d'un centre d'aménagement.
"""
import datetime as dt
import json
import time
import urllib.parse
import urllib.request

from .models import map_any
from .store import SupabaseError
from .names import infer_activities, is_financial, is_stock, name_key, pretty_name

GEOCODER = 'https://nominatim.openstreetmap.org/search'
USER_AGENT = 'Helibase/1.0 (https://helibase.pages.dev)'


def geocode(city, region, country):
    """Coordonnées approximatives d'une ville (service gratuit OpenStreetMap, 1 requête/seconde)."""
    if not city and not region:
        return None
    query = ', '.join(p for p in [city, region] if p)
    params = urllib.parse.urlencode({'q': query, 'countrycodes': (country or '').lower(), 'format': 'json', 'limit': 1})
    req = urllib.request.Request(GEOCODER + '?' + params, headers={'User-Agent': USER_AGENT})
    time.sleep(1.1)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:  # le géocodage n'est jamais bloquant
        print(f'  géocodage impossible pour « {query} » : {e}')
    return None


def ingest(store, records, source_label, source_url, source_kind='registre', since='2024-01-01',
           do_geocode=True, log=print, confidence=4):
    stats = {'lus': 0, 'airbus': 0, 'hors_periode': 0, 'stock': 0, 'sans_exploitant': 0,
             'nouvelles_affectations': 0, 'reventes': 0, 'inchanges': 0, 'nouveaux_exploitants': 0,
             'a_verifier': 0, 'geocodes': 0}

    # 1. Garder les Airbus de la période
    keep = []
    for r in records:
        stats['lus'] += 1
        mapped = map_any(r.get('manufacturer'), r.get('registry_model'), r.get('year_built'), r.get('typecode'))
        if not mapped or not str(r.get('msn') or '').strip():
            continue
        stats['airbus'] += 1
        year = _int(r.get('year_built'))
        if (r.get('registered_on') or '') < since and (year or 0) < int(since[:4]):
            stats['hors_periode'] += 1
            continue
        if not (r.get('owner') or '').strip():
            stats['sans_exploitant'] += 1
            continue
        if is_stock(r.get('owner')):
            stats['stock'] += 1
            continue
        r['family'], r['model'] = mapped
        r['msn'] = str(r['msn']).strip().lstrip('0') or str(r['msn']).strip()
        keep.append(r)
    keep.sort(key=lambda r: r.get('registered_on') or '')
    log(f'{stats["airbus"]} hélicoptères Airbus dans le registre, {len(keep)} à intégrer '
        f'({stats["hors_periode"]} hors période, {stats["stock"]} encore chez le constructeur ou un aménageur, '
        f'{stats["sans_exploitant"]} sans exploitant connu)')
    if not keep:
        return stats

    # 2. Source de cette mise à jour
    source = store.insert('sources', [{'kind': source_kind, 'label': source_label, 'url': source_url,
                                       'retrieved_on': dt.date.today().isoformat(), 'confidence': confidence}])[0]

    # 3. Hélicoptères : création ou mise à jour (clé famille + numéro de série)
    helis = store.insert('helicopters', [
        {'family': r['family'], 'msn': r['msn'], 'model': r['model'],
         'registry_model': (r.get('registry_model') or '').strip() or None, 'year_built': _int(r.get('year_built'))}
        for r in _unique(keep, lambda r: (r['family'], r['msn']))], on_conflict='family,msn')
    heli_id = {(h['family'], h['msn']): h['id'] for h in helis}

    # 4. Exploitants existants
    operators = store.select('operators', 'id,name,aliases,country,lat,lon')
    op_by_key = {}
    for o in operators:
        for n in [o['name'], *(o.get('aliases') or [])]:
            op_by_key[(name_key(n), o.get('country') or '')] = o

    # 5. Affectations en cours
    open_assign = {}
    for a in store.select('assignments', 'id,helicopter_id,operator_id,owner_name,registration,start_date',
                          end_date='is.null'):
        open_assign[a['helicopter_id']] = a
    known_helis = {a['helicopter_id'] for a in store.select('assignments', 'helicopter_id')}

    new_assignments, queue, touched = [], [], set()
    for r in keep:
        hid = heli_id[(r['family'], r['msn'])]
        owner = (r.get('owner') or '').strip()
        financial = is_financial(owner)
        op = None
        if not financial:
            key = (name_key(owner), r.get('country') or '')
            op = op_by_key.get(key)
            if not op:
                try:
                    op = store.insert('operators', [{
                        'name': pretty_name(owner), 'country': r.get('country'),
                        'city': pretty_name(r.get('city') or '') or None,
                        'activities': infer_activities(owner),
                        'sector': 'etatique' if r.get('government') else 'civil',
                    }])[0]
                except SupabaseError:
                    # Même nom déjà présent sous une autre écriture : on le réutilise
                    found = store.select('operators', 'id,name,aliases,country,lat,lon',
                                         name=f'ilike.{pretty_name(owner)}', country=f'eq.{r.get("country")}')
                    if not found:
                        raise
                    op = found[0]
                op['_region'] = r.get('region')
                op['_new'] = True
                op_by_key[key] = op
                operators.append(op)
                stats['nouveaux_exploitants'] += 1

        if op:
            touched.add(op['id'])
        current = open_assign.get(hid)
        same = current and (
            (op and current['operator_id'] == op['id']) or
            (not op and (current.get('owner_name') or '').upper() == owner.upper()))
        if same:
            if r.get('registration') and current.get('registration') != r['registration']:
                store.update('assignments', {'registration': r['registration']}, id=f'eq.{current["id"]}')
            stats['inchanges'] += 1
            continue
        if current:   # changement d'exploitant ou de propriétaire : revente
            store.update('assignments', {'end_date': r.get('registered_on') or dt.date.today().isoformat()},
                         id=f'eq.{current["id"]}')
            stats['reventes'] += 1
        year = _int(r.get('year_built')) or 0
        reg_year = int((r.get('registered_on') or '0000')[:4] or 0)
        is_new = hid not in known_helis and not current and year >= reg_year - 1
        row = {'helicopter_id': hid, 'operator_id': op['id'] if op else None,
               'owner_name': pretty_name(owner) if financial else None,
               'registration': r.get('registration'), 'start_date': r.get('registered_on'),
               'is_new': is_new, 'source_id': source['id']}
        new_assignments.append(row)
        known_helis.add(hid)
        open_assign[hid] = {**row, 'id': None}
        if financial:
            queue.append({'kind': 'livraison', 'source_id': source['id'],
                          'summary': f'{r["model"]} n° {r["msn"]} ({r.get("registration")}) au nom de '
                                     f'{pretty_name(owner)} : exploitant à identifier',
                          'payload': {'msn': r['msn'], 'family': r['family'], 'registration': r.get('registration'),
                                      'owner': owner, 'city': r.get('city'), 'region': r.get('region'),
                                      'country': r.get('country')}})

    if new_assignments:
        store.insert('assignments', new_assignments)
    if queue:
        store.insert('review_queue', queue)
    stats['nouvelles_affectations'] = len(new_assignments)
    stats['a_verifier'] = len(queue)

    # 6. Géolocalisation des exploitants sans position
    if do_geocode:
        for o in operators:
            if o.get('lat') is not None or o['id'] not in touched:
                continue
            pos = None
            if o.get('city') or o.get('_region'):
                pos = geocode(o.get('city'), o.get('_region'), o.get('country'))
            elif o.get('_new'):   # pas de ville dans la source : on cherche l'exploitant par son nom
                pos = geocode(o.get('name'), None, o.get('country'))
            if pos:
                store.update('operators', {'lat': pos[0], 'lon': pos[1]}, id=f'eq.{o["id"]}')
                stats['geocodes'] += 1

    store.insert('update_log', [{'source': source_label, 'added': stats['nouvelles_affectations'],
                                 'changed': stats['reventes'], 'queued': stats['a_verifier']}])
    log('Résultat : ' + ', '.join(f'{k.replace("_", " ")} {v}' for k, v in stats.items()))
    return stats


def _int(v):
    try:
        return int(str(v).strip()[:4])
    except (TypeError, ValueError):
        return None


def _unique(rows, key):
    seen, out = set(), []
    for r in rows:
        k = key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out
