"""Recherche automatique d'informations sur les exploitants : position, présentation, site web,
téléphone, e-mail, adresse, photo.

Règle d'or : une information n'est retenue que si elle concerne sans ambiguïté le bon
exploitant. Dans le doute, le champ reste vide (il peut être complété avec « Modifier »).

Étapes pour chaque exploitant :
  1. position : la ville du registre est géolocalisée (OpenStreetMap) ; c'est la référence ;
  2. Wikidata / Wikipédia : retenu seulement si le nom est identique, si l'entité est une
     organisation (pas une œuvre, une personne ni un lieu), du même pays, et si sa
     description ou son article parlent d'aviation (voir helibase/verify.py) ;
  3. OpenStreetMap : fiche de la société retenue seulement si le nom est identique, le pays
     identique et la position à moins de 60 km de la ville du registre ;
  4. site web : lu seulement s'il vient d'une source validée ci-dessus ; sa photo et sa
     description ne sont reprises que si la page nomme l'exploitant ou parle d'aviation.

Seuls les champs vides sont remplis ; une information saisie à la main n'est jamais écrasée.

Utilisation :
  python scripts/enrichir.py                  # exploitants jamais traités ou à revoir (90 jours)
  python scripts/enrichir.py --name "DRF" --dry-run
"""
import argparse
import datetime as dt
import html
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from helibase import verify as V  # noqa: E402
from helibase.names import name_key  # noqa: E402

BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 '
              '(KHTML, like Gecko) Version/17.5 Safari/605.1.15')
REVIEW_DAYS = 90
MAX_KM = 60
FIELDS = ('summary', 'summary_source', 'website', 'phone', 'email', 'address', 'photo_url', 'photo_credit')


# ---------------------------------------------------------------------------
def from_osm(name, country, ref):
    """Fiche OpenStreetMap de la société, si elle est sans ambiguïté et proche de la ville du registre."""
    q = urllib.parse.urlencode({'q': name, 'countrycodes': (country or '').lower(), 'format': 'jsonv2',
                                'limit': 8, 'extratags': 1, 'namedetails': 1, 'addressdetails': 1})
    time.sleep(1.1)
    hits = []
    for r in V.get_json('https://nominatim.openstreetmap.org/search?' + q):
        names = [r.get('name') or ''] + list((r.get('namedetails') or {}).values())
        if not any(V.same_name(n, name) for n in names if n):
            continue
        if country and (r.get('address') or {}).get('country_code', '').upper() != country.upper():
            continue
        lat, lon = float(r['lat']), float(r['lon'])
        if ref and V.km(lat, lon, *ref) > MAX_KM:
            continue
        if r.get('category') in ('boundary', 'place', 'natural', 'waterway', 'highway', 'railway', 'landuse'):
            continue
        hits.append(r)
    if len(hits) != 1:
        return {}
    r = hits[0]
    tags = r.get('extratags') or {}
    a = r.get('address') or {}
    street = ' '.join(x for x in [a.get('house_number'), a.get('road')] if x)
    town = a.get('city') or a.get('town') or a.get('village') or a.get('municipality') or ''
    address = ', '.join(x for x in [street, ' '.join(x for x in [a.get('postcode'), town] if x), a.get('country')] if x)
    return {k: v for k, v in {
        'website': tags.get('website') or tags.get('contact:website'),
        'phone': tags.get('phone') or tags.get('contact:phone'),
        'email': tags.get('email') or tags.get('contact:email'),
        'address': address if street else None,
        '_lat': float(r['lat']), '_lon': float(r['lon']),
    }.items() if v}


def _meta(page, *names):
    for n in names:
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(n)}["\']'):
            m = re.search(pat, page, re.I)
            if m:
                return html.unescape(m.group(1)).strip()
    return None


def from_website(url, name):
    if not re.match(r'^https?://', url or ''):
        url = 'https://' + (url or '').lstrip('/')
    req = urllib.request.Request(url, headers={'User-Agent': BROWSER_UA, 'Accept': 'text/html',
                                               'Accept-Language': 'fr,en;q=0.8'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        page = resp.read(3_000_000).decode(resp.headers.get_content_charset() or 'utf-8', errors='replace')
        final = resp.geturl()
    text = re.sub(r'<[^>]+>', ' ', page)
    if not (V.mentions(text, name) and V.AVIATION.search(text)):
        return {}          # la page ne parle ni de cet exploitant ni d'aviation : on n'en tire rien
    info = {}
    img = _meta(page, 'og:image', 'og:image:url', 'twitter:image')
    if img:
        info['photo_url'] = urllib.parse.urljoin(final, img)
        info['photo_credit'] = "Site de l'exploitant"
    desc = _meta(page, 'og:description', 'description', 'twitter:description')
    if desc and len(desc) >= 60 and (V.AVIATION.search(desc) or V.mentions(desc, name)):
        info['summary'] = desc
        info['summary_source'] = final
    tel = re.search(r'href=["\']tel:([^"\']+)', page, re.I) or re.search(r'"telephone"\s*:\s*"([^"]+)"', page)
    if tel:
        info['phone'] = urllib.parse.unquote(tel.group(1)).strip()
    mail = re.search(r'href=["\']mailto:([^"\'?]+)', page, re.I) or re.search(r'"email"\s*:\s*"([^"]+@[^"]+)"', page)
    if mail:
        info['email'] = urllib.parse.unquote(mail.group(1)).strip()
    return info


# ---------------------------------------------------------------------------
def enrich(op, log=print):
    found, ref = {}, None
    if op.get('lat') is not None:
        ref = (op['lat'], op['lon'])
    elif op.get('city'):
        ref = V.geocode_city(op.get('city'), None, op.get('country'))
        if ref:
            found['_lat'], found['_lon'] = ref
            log(f'    position : {op["city"]}')

    def merge(info, label, prefer=()):
        added = [k for k, v in info.items() if v and not k.startswith('_') and not op.get(k) and (k not in found or k in prefer)]
        for k in added:
            found[k] = info[k]
        if added:
            log(f'    {label} : {", ".join(added)}')

    try:
        wd = V.wikidata_org(op['name'], op.get('country'))
        if wd:
            merge(wd, 'Wikidata/Wikipédia')
            if ref is None and wd.get('_lat') is not None and op.get('country') in (wd.get('_countries') or [op.get('country')]):
                found['_lat'], found['_lon'] = wd['_lat'], wd['_lon']
                ref = (wd['_lat'], wd['_lon'])
    except Exception as e:
        log(f'    Wikidata indisponible : {e}')
    try:
        osm = from_osm(op['name'], op.get('country'), ref)
        merge(osm, 'OpenStreetMap')
        if ref is None and osm.get('_lat') is not None:
            found['_lat'], found['_lon'] = osm['_lat'], osm['_lon']
    except Exception as e:
        log(f'    OpenStreetMap indisponible : {e}')
    site = op.get('website') or found.get('website')
    if site:
        try:
            merge(from_website(site, op['name']), 'site web', prefer=('photo_url', 'photo_credit'))
        except Exception as e:
            log(f'    site web inaccessible ({site}) : {e}')
    if 'summary' in found:
        found['summary'] = re.sub(r'\s+', ' ', found['summary']).strip()[:1200]
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=5000)
    parser.add_argument('--minutes', type=int, default=150, help='durée maximale')
    parser.add_argument('--name', help='seulement les exploitants dont le nom contient ce texte')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    from helibase.store import Store
    store = Store()
    cols = 'id,name,country,city,lat,lon,wikidata_id,enriched_at,info_edited_at,is_placeholder,sector,' + ','.join(FIELDS)
    # Les regroupements « à identifier » et les forces armées n'ont pas de fiche société à chercher
    ops = [o for o in store.select('operators', cols)
           if not o.get('is_placeholder') and not o['name'].startswith('Forces armées')]
    limit_date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=REVIEW_DAYS)).isoformat()
    todo = [o for o in ops if not o.get('enriched_at') or o['enriched_at'] < limit_date]
    if args.name:
        todo = [o for o in ops if args.name.lower() in o['name'].lower()]
    counts = {}
    for a in store.select('assignments', 'operator_id', end_date='is.null'):
        counts[a['operator_id']] = counts.get(a['operator_id'], 0) + 1
    # D'abord ceux qui ne sont pas encore sur la carte, puis les plus grosses flottes
    todo.sort(key=lambda o: (o.get('lat') is not None, -counts.get(o['id'], 0)))
    todo = todo[:args.limit]
    print(f'{len(todo)} exploitants à traiter (sur {len(ops)})')

    start = time.time()
    stats = {'traites': 0, 'positions': 0, 'presentations': 0, 'photos': 0, 'sites': 0, 'telephones': 0}
    for i, op in enumerate(todo, 1):
        if time.time() - start > args.minutes * 60:
            print('Durée maximale atteinte : la suite sera traitée au prochain passage.')
            break
        print(f'[{i}/{len(todo)}] {op["name"]} ({op.get("country") or "?"}, {op.get("city") or "ville inconnue"})')
        found = enrich(op)
        values = {k: v for k, v in found.items() if not k.startswith('_')}
        if op.get('lat') is None and found.get('_lat') is not None:
            values['lat'], values['lon'] = found['_lat'], found['_lon']
            stats['positions'] += 1
        values['enriched_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        stats['traites'] += 1
        stats['presentations'] += 'summary' in values
        stats['photos'] += 'photo_url' in values
        stats['sites'] += 'website' in values
        stats['telephones'] += 'phone' in values
        if not args.dry_run:
            store.update('operators', values, id=f'eq.{op["id"]}')
    print('Résultat : ' + ', '.join(f'{k} {v}' for k, v in stats.items()))


if __name__ == '__main__':
    main()
