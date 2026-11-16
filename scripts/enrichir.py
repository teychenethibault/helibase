"""Recherche automatique d'informations sur les exploitants : présentation, site web,
téléphone, e-mail, adresse et photo.

Sources gratuites, interrogées dans cet ordre :
  1. Wikidata / Wikipédia : présentation (en français si possible), site web, image ;
  2. OpenStreetMap : adresse, téléphone, site web des sociétés référencées ;
  3. le site web de l'exploitant : photo de présentation, description, téléphone, e-mail.

Seuls les champs vides sont remplis : une information saisie par un utilisateur n'est
jamais écrasée. Chaque exploitant est revu au plus tous les 90 jours.

Utilisation :
  python scripts/enrichir.py                  # exploitants jamais enrichis ou à revoir
  python scripts/enrichir.py --limit 50       # nombre maximal d'exploitants
  python scripts/enrichir.py --name "DRF" --dry-run
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from helibase.names import name_key  # noqa: E402

UA = 'Helibase/1.0 (https://helibase.pages.dev; outil interne de prospection)'
BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 '
              '(KHTML, like Gecko) Version/17.5 Safari/605.1.15')
REVIEW_DAYS = 90
FIELDS = ('summary', 'summary_source', 'website', 'phone', 'email', 'address', 'photo_url', 'photo_credit')
NOT_COMPANY = re.compile(r'\b(film|song|album|single|novel|book|episode|human|person|village|town|city|commune|'
                         r'river|mountain|genus|species|surname|given name|family name|painting|ship|footballer|'
                         r'politician|musician|actor|actress|band)\b', re.I)


def get(url, ua=UA, timeout=20, accept='application/json'):
    req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept': accept,
                                               'Accept-Language': 'fr,en;q=0.8'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(3_000_000)
        charset = resp.headers.get_content_charset() or 'utf-8'
        return raw.decode(charset, errors='replace'), resp.geturl()


def get_json(url):
    return json.loads(get(url)[0])


# ---------------------------------------------------------------------------
# 1. Wikidata et Wikipédia
# ---------------------------------------------------------------------------
def _claim(entity, prop):
    for c in entity.get('claims', {}).get(prop, []):
        v = c.get('mainsnak', {}).get('datavalue', {}).get('value')
        if v is not None:
            return v
    return None


def commons_file(filename, width=1200):
    return ('https://commons.wikimedia.org/wiki/Special:FilePath/' +
            urllib.parse.quote(filename.replace(' ', '_')) + f'?width={width}')


def from_wikidata(name):
    key = name_key(name)
    if len(key) < 4:
        return {}
    ids = []
    for lang in ('en', 'fr'):
        q = urllib.parse.urlencode({'action': 'wbsearchentities', 'search': name, 'language': lang,
                                    'type': 'item', 'limit': 5, 'format': 'json'})
        for r in get_json('https://www.wikidata.org/w/api.php?' + q).get('search', []):
            if r['id'] not in ids:
                ids.append(r['id'])
    if not ids:
        return {}
    q = urllib.parse.urlencode({'action': 'wbgetentities', 'ids': '|'.join(ids[:10]), 'format': 'json',
                                'props': 'labels|aliases|descriptions|claims|sitelinks'})
    entities = get_json('https://www.wikidata.org/w/api.php?' + q).get('entities', {})
    for qid in ids:
        e = entities.get(qid) or {}
        names = [v['value'] for v in e.get('labels', {}).values()]
        names += [a['value'] for al in e.get('aliases', {}).values() for a in al]
        if key not in {name_key(n) for n in names}:
            continue
        desc = ' '.join(d['value'] for d in e.get('descriptions', {}).values())
        if NOT_COMPANY.search(desc) and not _claim(e, 'P856'):
            continue
        info = {'wikidata_id': qid}
        site = _claim(e, 'P856')
        if site:
            info['website'] = site
        image = _claim(e, 'P18')
        logo = _claim(e, 'P154')
        if image or logo:
            info['photo_url'] = commons_file(image or logo)
            info['photo_credit'] = 'Wikimedia Commons'
        coord = _claim(e, 'P625')
        if isinstance(coord, dict):
            info['_lat'], info['_lon'] = coord.get('latitude'), coord.get('longitude')
        links = e.get('sitelinks', {})
        for lang in ('fr', 'en', 'de', 'es', 'it', 'nb', 'fi'):
            title = links.get(f'{lang}wiki', {}).get('title')
            if not title:
                continue
            s = get_json(f'https://{lang}.wikipedia.org/api/rest_v1/page/summary/' +
                         urllib.parse.quote(title.replace(' ', '_')))
            if s.get('extract'):
                info['summary'] = s['extract']
                info['summary_source'] = s.get('content_urls', {}).get('desktop', {}).get('page') or \
                    f'https://{lang}.wikipedia.org/wiki/' + urllib.parse.quote(title)
            if 'photo_url' not in info and (s.get('originalimage') or s.get('thumbnail')):
                info['photo_url'] = (s.get('originalimage') or s.get('thumbnail'))['source']
                info['photo_credit'] = 'Wikipédia'
            break
        return info
    return {}


# ---------------------------------------------------------------------------
# 2. OpenStreetMap
# ---------------------------------------------------------------------------
def from_osm(name, country):
    q = urllib.parse.urlencode({'q': name, 'countrycodes': (country or '').lower(), 'format': 'jsonv2',
                                'limit': 5, 'extratags': 1, 'namedetails': 1, 'addressdetails': 1})
    time.sleep(1.1)   # 1 requête par seconde maximum
    key = name_key(name)
    for r in get_json('https://nominatim.openstreetmap.org/search?' + q):
        names = [r.get('name') or ''] + list((r.get('namedetails') or {}).values())
        if key not in {name_key(n) for n in names if n}:
            continue
        tags = r.get('extratags') or {}
        a = r.get('address') or {}
        street = ' '.join(x for x in [a.get('house_number'), a.get('road')] if x)
        town = a.get('city') or a.get('town') or a.get('village') or a.get('municipality') or ''
        address = ', '.join(x for x in [street, ' '.join(x for x in [a.get('postcode'), town] if x), a.get('country')] if x)
        return {k: v for k, v in {
            'website': tags.get('website') or tags.get('contact:website') or tags.get('url'),
            'phone': tags.get('phone') or tags.get('contact:phone'),
            'email': tags.get('email') or tags.get('contact:email'),
            'address': address or r.get('display_name'),
            '_lat': float(r['lat']), '_lon': float(r['lon']),
        }.items() if v}
    return {}


# ---------------------------------------------------------------------------
# 3. Site web de l'exploitant
# ---------------------------------------------------------------------------
def _meta(page, *names):
    for n in names:
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(n)}["\']'):
            m = re.search(pat, page, re.I)
            if m:
                return html.unescape(m.group(1)).strip()
    return None


def from_website(url):
    if not re.match(r'^https?://', url or ''):
        url = 'https://' + (url or '').lstrip('/')
    page, final = get(url, ua=BROWSER_UA, accept='text/html')
    info = {}
    img = _meta(page, 'og:image', 'og:image:url', 'twitter:image')
    if img:
        info['photo_url'] = urllib.parse.urljoin(final, img)
        info['photo_credit'] = 'Site de l\'exploitant'
    desc = _meta(page, 'og:description', 'description', 'twitter:description')
    if desc and len(desc) >= 60:
        info['summary'] = desc
        info['summary_source'] = final
    tel = re.search(r'href=["\']tel:([^"\']+)', page, re.I) or re.search(r'"telephone"\s*:\s*"([^"]+)"', page)
    if tel:
        info['phone'] = urllib.parse.unquote(tel.group(1)).strip()
    mail = re.search(r'href=["\']mailto:([^"\'?]+)', page, re.I) or re.search(r'"email"\s*:\s*"([^"]+@[^"]+)"', page)
    if mail:
        info['email'] = urllib.parse.unquote(mail.group(1)).strip()
    street = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', page)
    if street:
        town = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', page)
        pc = re.search(r'"postalCode"\s*:\s*"([^"]+)"', page)
        info['address'] = ', '.join(x for x in [street.group(1), ' '.join(
            x for x in [pc and pc.group(1), town and town.group(1)] if x)] if x)
    return info


# ---------------------------------------------------------------------------
def enrich(op, log=print):
    """Renvoie les champs à compléter pour cet exploitant (sans écraser l'existant)."""
    found = {}

    def merge(info, label, prefer=()):
        added = [k for k, v in info.items() if v and not op.get(k) and (k not in found or k in prefer)]
        for k in added:
            found[k] = info[k]
        if added:
            log(f'    {label} : {", ".join(a for a in added if not a.startswith("_"))}')

    for label, fn in (('Wikidata', lambda: from_wikidata(op['name'])),
                      ('OpenStreetMap', lambda: from_osm(op['name'], op.get('country')))):
        try:
            merge(fn(), label)
        except Exception as e:
            log(f'    {label} indisponible : {e}')
    site = op.get('website') or found.get('website')
    if site:
        try:
            merge(from_website(site), 'site web', prefer=('photo_url', 'photo_credit'))
        except Exception as e:
            log(f'    site web inaccessible ({site}) : {e}')
    if 'summary' in found:
        found['summary'] = re.sub(r'\s+', ' ', found['summary']).strip()[:1200]
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=3000)
    parser.add_argument('--minutes', type=int, default=150, help='durée maximale')
    parser.add_argument('--name', help='seulement les exploitants dont le nom contient ce texte')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    from helibase.store import Store
    store = Store()
    cols = 'id,name,country,lat,lon,wikidata_id,enriched_at,' + ','.join(FIELDS)
    ops = store.select('operators', cols)
    limit_date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=REVIEW_DAYS)).isoformat()
    todo = [o for o in ops if not o.get('enriched_at') or o['enriched_at'] < limit_date]
    if args.name:
        todo = [o for o in ops if args.name.lower() in o['name'].lower()]
    # Les exploitants avec le plus d'appareils d'abord
    counts = {}
    for a in store.select('assignments', 'operator_id', end_date='is.null'):
        counts[a['operator_id']] = counts.get(a['operator_id'], 0) + 1
    todo.sort(key=lambda o: -counts.get(o['id'], 0))
    todo = todo[:args.limit]
    print(f'{len(todo)} exploitants à enrichir (sur {len(ops)})')

    start, stats = time.time(), {'enrichis': 0, 'photos': 0, 'presentations': 0, 'telephones': 0, 'sites': 0}
    for i, op in enumerate(todo, 1):
        if time.time() - start > args.minutes * 60:
            print(f'Durée maximale atteinte : la suite sera traitée au prochain passage.')
            break
        print(f'[{i}/{len(todo)}] {op["name"]} ({op.get("country") or "?"})')
        found = enrich(op)
        values = {k: v for k, v in found.items() if not k.startswith('_')}
        if op.get('lat') is None and found.get('_lat') is not None:
            values['lat'], values['lon'] = found['_lat'], found['_lon']
        values['enriched_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        stats['enrichis'] += len(values) > 1
        stats['photos'] += 'photo_url' in values
        stats['presentations'] += 'summary' in values
        stats['telephones'] += 'phone' in values
        stats['sites'] += 'website' in values
        if not args.dry_run:
            store.update('operators', values, id=f'eq.{op["id"]}')
    print('Résultat : ' + ', '.join(f'{k} {v}' for k, v in stats.items()))


if __name__ == '__main__':
    main()
