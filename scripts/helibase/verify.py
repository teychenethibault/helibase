"""Contrôles de fiabilité partagés : une information extérieure n'est acceptée que si elle
concerne sans ambiguïté le bon exploitant (même nom, même pays, activité aéronautique,
position cohérente avec la ville du registre)."""
import json
import math
import re
import time
import urllib.parse
import urllib.request

from .names import name_key, strip_accents

UA = 'Helibase/1.0 (https://helibase.pages.dev; outil interne de prospection)'

# Vocabulaire aéronautique (toutes langues) : une page ou une description doit en contenir
AVIATION = re.compile(
    r'helicop|hélicop|helikop|hubschrauber|elicott|helicópter|helicopter|rotorcraft|'
    r'aviation|aérien|aerien|aerea|aéreo|aereo|airline|air ambulance|air medical|air rescue|aeromedical|'
    r'luftrettung|luftambulanse|rettungshubschrauber|ambulance aérienne|samu|hems\b|medevac|'
    r'search and rescue|sauvetage|salvamento|rescue|police|polizei|politi|gendarmerie|carabinieri|guardia civil|'
    r'coast guard|garde-côtes|army|armée|navy|marine nationale|air force|armée de l.air|luftwaffe|bundeswehr|'
    r'military|militaire|militar|defen[cs]e|défense|offshore|oil and gas|aircraft|aéronef|aeronave|flugzeug|'
    r'airport|aéroport|aeroporto|aeropuerto|flughafen|heliport|héliport|airfield|aérodrome|flight|vol |flug|'
    r'pilot|pilote|charter|aerial work|travail aérien|firefighting|sécurité civile|civil protection|protezione civile',
    re.I)

# Descriptions qui ne désignent jamais une société
NOT_ORG = re.compile(
    r'\b(album|song|single|chanson|film|movie|série|series|episode|novel|roman|book|livre|painting|tableau|'
    r'human|person|personne|singer|chanteu|actor|actress|acteur|actrice|footballer|politician|musician|band|groupe de musique|'
    r'village|town|city|commune|ville|municipality|river|rivière|fleuve|mountain|montagne|lake|lac|island|île|'
    r'genus|species|espèce|surname|given name|prénom|family name|nom de famille|ship|navire|asteroid|planet|'
    r'video game|jeu vidéo|software|logiciel|disambiguation|homonymie|wikimedia)\b', re.I)


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Géolocalisation de la ville du registre
# ---------------------------------------------------------------------------
def geocode_city(city, region, country):
    if not city and not region:
        return None
    params = {'format': 'jsonv2', 'limit': 1, 'countrycodes': (country or '').lower()}
    if city:
        params['city'] = city
        if region:
            params['state'] = region
    else:
        params['q'] = region
    time.sleep(1.1)
    try:
        r = get_json('https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(params))
        if not r and city and region:     # « state » parfois mal reconnu : nouvel essai avec la ville seule
            time.sleep(1.1)
            r = get_json('https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(
                {'format': 'jsonv2', 'limit': 1, 'countrycodes': (country or '').lower(), 'city': city}))
        if r:
            return float(r[0]['lat']), float(r[0]['lon'])
    except Exception as e:
        print(f'    géolocalisation impossible ({city}, {country}) : {e}')
    return None


# ---------------------------------------------------------------------------
# Wikidata : uniquement une organisation aéronautique du bon pays
# ---------------------------------------------------------------------------
_iso_cache = {}


def _claim(entity, prop):
    for c in (entity.get('claims') or {}).get(prop, []):
        v = (c.get('mainsnak') or {}).get('datavalue', {}).get('value')
        if v is not None:
            return v
    return None


def _claims(entity, prop):
    out = []
    for c in (entity.get('claims') or {}).get(prop, []):
        v = (c.get('mainsnak') or {}).get('datavalue', {}).get('value')
        if v is not None:
            out.append(v)
    return out


def country_iso(qid):
    if qid in _iso_cache:
        return _iso_cache[qid]
    try:
        e = get_json('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode(
            {'action': 'wbgetentities', 'ids': qid, 'props': 'claims', 'format': 'json'}))['entities'][qid]
        _iso_cache[qid] = _claim(e, 'P297')
    except Exception:
        _iso_cache[qid] = None
    return _iso_cache[qid]


def wikidata_org(name, country=None):
    """Entité Wikidata correspondant à l'exploitant, ou None si le moindre doute subsiste.

    Conditions : nom identique (libellé ou alias), description qui n'est pas celle d'une
    œuvre, d'une personne ou d'un lieu, activité aéronautique visible dans la description
    ou dans l'article Wikipédia, et même pays que l'exploitant quand les deux sont connus."""
    key = name_key(name)
    if len(key) < 4:
        return None
    ids = []
    for lang in ('en', 'fr'):
        q = urllib.parse.urlencode({'action': 'wbsearchentities', 'search': name, 'language': lang,
                                    'type': 'item', 'limit': 7, 'format': 'json'})
        for r in get_json('https://www.wikidata.org/w/api.php?' + q).get('search', []):
            if r['id'] not in ids:
                ids.append(r['id'])
    if not ids:
        return None
    entities = get_json('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode(
        {'action': 'wbgetentities', 'ids': '|'.join(ids[:14]), 'format': 'json',
         'props': 'labels|aliases|descriptions|claims|sitelinks'})).get('entities', {})
    matches = []
    for qid in ids:
        e = entities.get(qid) or {}
        names = [v['value'] for v in (e.get('labels') or {}).values()]
        names += [a['value'] for al in (e.get('aliases') or {}).values() for a in al]
        if key not in {name_key(n) for n in names}:
            continue
        desc = ' '.join(d['value'] for d in (e.get('descriptions') or {}).values())
        if NOT_ORG.search(desc):
            continue
        if any(v.get('id') == 'Q5' for v in _claims(e, 'P31') if isinstance(v, dict)):   # être humain
            continue
        countries = {country_iso(v['id']) for v in _claims(e, 'P17') if isinstance(v, dict)}
        countries.discard(None)
        if country and countries and country not in countries:
            continue
        matches.append((qid, e, desc))
    if len(matches) != 1:          # aucun, ou plusieurs homonymes : on s'abstient
        return None
    qid, e, desc = matches[0]

    info = {'wikidata_id': qid, '_aviation': bool(AVIATION.search(desc))}
    links = e.get('sitelinks') or {}
    for lang in ('fr', 'en', 'de', 'es', 'it', 'pt', 'nb', 'sv', 'fi', 'nl'):
        title = (links.get(f'{lang}wiki') or {}).get('title')
        if not title:
            continue
        try:
            s = get_json(f'https://{lang}.wikipedia.org/api/rest_v1/page/summary/' +
                         urllib.parse.quote(title.replace(' ', '_')))
        except Exception:
            continue
        extract = s.get('extract') or ''
        if s.get('type') == 'disambiguation' or NOT_ORG.search(s.get('description') or ''):
            return None
        if extract:
            info['_aviation'] = info['_aviation'] or bool(AVIATION.search(extract))
            info['summary'] = extract
            info['summary_source'] = ((s.get('content_urls') or {}).get('desktop') or {}).get('page') or \
                f'https://{lang}.wikipedia.org/wiki/' + urllib.parse.quote(title)
            img = s.get('originalimage') or s.get('thumbnail')
            if img:
                info['_wiki_image'] = img['source']
        break
    if not info['_aviation']:      # rien ne relie cette entité à l'aviation : on n'en garde rien
        return None
    site = _claim(e, 'P856')
    if site:
        info['website'] = site
    image = _claim(e, 'P18')
    if image:
        info['photo_url'] = ('https://commons.wikimedia.org/wiki/Special:FilePath/' +
                             urllib.parse.quote(image.replace(' ', '_')) + '?width=1200')
        info['photo_credit'] = 'Wikimedia Commons'
    elif info.get('_wiki_image'):
        info['photo_url'] = info['_wiki_image']
        info['photo_credit'] = 'Wikipédia'
    coord = _claim(e, 'P625')
    if isinstance(coord, dict):
        info['_lat'], info['_lon'] = coord.get('latitude'), coord.get('longitude')
    info['_countries'] = sorted({country_iso(v['id']) for v in _claims(e, 'P17') if isinstance(v, dict)} - {None})
    return info


def same_name(a, b):
    return name_key(a) == name_key(b)


def mentions(text, name):
    """Le texte cite-t-il l'exploitant (nom normalisé) ?"""
    t = name_key(strip_accents(text or ''))
    k = name_key(name)
    return len(k) >= 4 and f' {k} ' in f' {t} '
