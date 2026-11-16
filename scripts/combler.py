"""Combler les « Exploitant à identifier » : retrouver l'exploitant réel de chaque appareil, avec une source vérifiable.

Un appareil n'est rattaché à un exploitant que si une preuve explicite le relie à SON immatriculation
(ou, à défaut, à une correspondance unique et datée). Rien n'est deviné. Méthodes, de la plus sûre à la moins sûre :

  1. Presse — immatriculation citée : un article (Airbus, HeliHub, Vertical) annonçant une livraison cite
     l'immatriculation de l'appareil.
  2. Site de l'exploitant : la page « flotte » d'un exploitant connu affiche l'immatriculation.
     robots.txt respecté, 6 pages au plus par site, pause entre les pages, robot identifié.
  3. Wikimedia Commons : photos de l'appareil (immatriculation dans le titre, la description ou les catégories)
     classées dans la catégorie d'un exploitant ; un seul exploitant possible, sinon rien.
  4. Recherche web vérifiée (Gemini + Google Search, offre gratuite) : Gemini propose un exploitant et ses
     sources ; le rattachement n'est fait que si une page source, relue par nos soins, contient à la fois
     l'immatriculation ET le nom de l'exploitant.
  5. Presse — correspondance unique : une livraison annoncée dans la presse (exploitant, pays, modèle, mois)
     correspond à UN SEUL appareil « à identifier » apparu le même mois. Deux candidats ou plus : rien n'est fait.

Chaque rattachement crée une source (lien cliquable sur la fiche) et note la méthode employée.
Un exploitant trouvé n'est jamais remplacé par « à identifier » lors des passages suivants.

Utilisation :
  python scripts/combler.py                 # toutes les méthodes
  python scripts/combler.py --dry-run       # affiche ce qui serait rattaché, n'écrit rien
  python scripts/combler.py --only site     # une seule méthode (presse, site, web, unique)
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

sys.path.insert(0, os.path.dirname(__file__))
from helibase.names import infer_activities, is_financial, is_stock, name_key, pretty_name  # noqa: E402
from helibase.europe import country_name, is_europe  # noqa: E402

BOT_UA = 'Helibase-bot/1.0 (+https://helibase.pages.dev)'
PAGE_PAUSE = 2.0
FLEET_WORDS = re.compile(r'fleet|flotte|flotta|flota|frota|vloot|flåte|flotte|flott|hubschrauber|helikopter|'
                         r'helicopt|elicott|aircraft|luftfahrzeug|maschinen|our-aircraft|equipment|materiel|matériel',
                         re.I)
GENERIC = {'helicopters', 'helicopter', 'hélicoptères', 'helikopter', 'hubschrauber', 'aviation', 'air', 'aero',
           'services', 'service', 'group', 'groupe', 'gmbh', 'ltd', 'limited', 'company', 'international', 'the',
           'de', 'la', 'le', 'du', 'des', 'and', 'et', 'und', 'flight', 'flying', 'airlines', 'charter', 'heli'}


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def fetch(url, timeout=30, ua=BOT_UA, max_bytes=3_000_000):
    req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept': 'text/html,*/*;q=0.8',
                                               'Accept-Language': 'en,fr,de;q=0.8'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get('Content-Type', '')
        if 'html' not in ctype and 'text' not in ctype and 'json' not in ctype:
            return resp.geturl(), ''
        raw = resp.read(max_bytes)
        cs = resp.headers.get_content_charset() or 'utf-8'
        return resp.geturl(), raw.decode(cs, 'replace')


def page_text(htm):
    htm = re.sub(r'<(script|style|noscript)[\s\S]*?</\1>', ' ', htm, flags=re.I)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', htm)))


def reg_pattern(reg):
    """Motif tolérant : G-NPAD, GNPAD, G NPAD ; D-HYAB, DHYAB…"""
    m = re.fullmatch(r'([A-Z0-9]{1,2})-([A-Z0-9]{2,5})', reg.upper())
    if not m:
        return re.compile(r'(?<![A-Z0-9])' + re.escape(reg) + r'(?![A-Z0-9])', re.I)
    return re.compile(r'(?<![A-Z0-9-])' + m.group(1) + r'[-\s\u00a0\u2010\u2011\u2012\u2013]?' + m.group(2) + r'(?![A-Z0-9])', re.I)


def name_in_text(name, text):
    """Le nom de l'exploitant apparaît-il dans le texte ? (mots distinctifs, au moins un de 4 lettres ou plus)"""
    words = [w for w in re.findall(r"[\wÀ-ÿ]+", name.lower()) if len(w) >= 3 and w not in GENERIC]
    if not words:
        return False
    low = text.lower()
    strong = [w for w in words if len(w) >= 4] or words
    hits = [w for w in strong if re.search(r'(?<!\w)' + re.escape(w) + r'(?!\w)', low)]
    return len(hits) >= max(1, (len(strong) + 1) // 2)


def norm_model(m):
    m = re.sub(r'\s+', '', (m or '')).upper()
    m = re.sub(r'^EC135|^BK117D-?3', lambda x: 'H135' if x.group(0).startswith('EC') else 'H145', m)
    return re.sub(r'(D3|D2|T3|P3|B3E|B2)$', '', m)


# ---------------------------------------------------------------------------
# Contexte : appareils à identifier, exploitants connus
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, store, dry):
        self.store, self.dry = store, dry
        self.ops = store.select('operators', 'id,name,aliases,country,sector,is_placeholder,website')
        self.by_id = {o['id']: o for o in self.ops}
        self.by_key = {}
        for o in self.ops:
            for n in [o['name'], *(o.get('aliases') or [])]:
                self.by_key[(name_key(n), o.get('country') or '')] = o
        heli = {h['id']: h for h in store.select('helicopters', 'id,family,msn,model,first_seen')}
        self.todo = []
        for a in store.select('assignments', 'id,helicopter_id,operator_id,registration,status,start_date,date_precision',
                              end_date='is.null'):
            op = self.by_id.get(a.get('operator_id'))
            if not op or not op.get('is_placeholder') or not a.get('registration'):
                continue
            if op['name'].startswith(("Aéronefs d'État", 'Aéronefs militaires')):
                continue        # aéronefs d'État et militaires français : pas d'exploitant civil à chercher
            h = heli.get(a['helicopter_id'], {})
            self.todo.append({**a, 'country': op.get('country'), 'model': h.get('model'), 'msn': h.get('msn'),
                              'family': h.get('family'), 'first_seen': h.get('first_seen')})
        self.done = set()
        self.log = []

    def pending(self):
        return [a for a in self.todo if a['id'] not in self.done]

    def operator(self, name, country, government=False):
        name = pretty_name(name.strip())
        k = (name_key(name), country or '')
        o = self.by_key.get(k)
        if o:
            return o
        row = {'name': name, 'country': country, 'sector': 'etatique' if government else 'civil',
               'is_placeholder': False, 'activities': infer_activities(name)}
        o = row if self.dry else self.store.insert('operators', [row])[0]
        o.setdefault('id', 'nouveau:' + name)
        self.by_key[k] = o
        self.by_id[o['id']] = o
        return o

    def link(self, a, op_name, country, method, kind, label, url, confidence):
        if a['id'] in self.done or not op_name or is_stock(op_name) or is_financial(op_name):
            return False
        country = country if is_europe(country or '') else a['country']
        op = self.operator(op_name, country, government=bool(re.search(
            r'polic|gendarm|bundespolizei|politi|ministr|sécurité civile|protezione civile|guardia|state|county|regione',
            op_name, re.I)))
        self.done.add(a['id'])
        self.log.append((method, a['registration'], a.get('model'), op['name'], url))
        if self.dry:
            return True
        src = self.store.insert('sources', [{'kind': kind, 'label': label, 'url': url,
                                             'retrieved_on': dt.date.today().isoformat(), 'confidence': confidence}])[0]
        self.store.update('assignments', {'operator_id': op['id'], 'source_id': src['id'], 'operator_method': method},
                          id=f'eq.{a["id"]}')
        return True


# ---------------------------------------------------------------------------
# 1. Presse : immatriculation citée dans un article
# ---------------------------------------------------------------------------
def from_press_registrations(ctx):
    orders = ctx.store.select('orders', 'id,operator_id,model,status,announced_on,registrations,sources(label,url)')
    n = 0
    for o in orders:
        regs = o.get('registrations') or []
        op = ctx.by_id.get(o['operator_id'])
        if not regs or not op or op.get('is_placeholder'):
            continue
        for a in ctx.pending():
            if any(reg_pattern(a['registration']).fullmatch(r) or r.replace('-', '') == a['registration'].replace('-', '')
                   for r in regs):
                src = o.get('sources') or {}
                n += ctx.link(a, op['name'], op.get('country'), 'presse', 'presse',
                              f"Presse : {src.get('label') or 'article'} (immatriculation citée)", src.get('url'), 4)
    print(f'  presse (immatriculation citée) : {n} appareil(s) rattaché(s)')


# ---------------------------------------------------------------------------
# 2. Sites des exploitants
# ---------------------------------------------------------------------------
def site_pages(website, budget):
    """Page d'accueil puis pages « flotte » du même site, dans le respect de robots.txt."""
    if not website:
        return []
    if not website.startswith('http'):
        website = 'https://' + website
    base = urllib.parse.urlsplit(website)
    root = f'{base.scheme}://{base.netloc}'
    rp = urllib.robotparser.RobotFileParser()
    try:
        _, robots = fetch(root + '/robots.txt', timeout=15)
        rp.parse(robots.splitlines())
    except Exception:
        rp.parse([])
    out, seen, queue = [], set(), [website]
    while queue and len(out) < budget:
        url = queue.pop(0)
        if url in seen or not rp.can_fetch(BOT_UA, url):
            continue
        seen.add(url)
        try:
            final, htm = fetch(url)
        except Exception:
            continue
        out.append((final, page_text(htm)))
        if len(out) == 1:     # liens « flotte » de la page d'accueil (même site uniquement)
            for href, label in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>([\s\S]{0,200}?)</a>', htm, re.I):
                full = urllib.parse.urljoin(final, href)
                if urllib.parse.urlsplit(full).netloc.replace('www.', '') != base.netloc.replace('www.', ''):
                    continue
                if FLEET_WORDS.search(href) or FLEET_WORDS.search(page_text(label)):
                    queue.append(full)
        time.sleep(PAGE_PAUSE)
    return out


def from_operator_sites(ctx, minutes):
    pend = ctx.pending()
    if not pend:
        return
    countries = {a['country'] for a in pend}
    cache = {}
    if not ctx.dry:
        cache = {r['key']: r for r in ctx.store.select('source_cache', 'key,fetched_at', key='like.site:*')}
    month_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=28)).isoformat()
    sites = [o for o in ctx.ops if o.get('website') and not o.get('is_placeholder')
             and not o['name'].startswith('Forces armées')
             and (o.get('country') in countries or not o.get('country'))
             and (cache.get('site:' + o['id'], {}).get('fetched_at') or '') < month_ago]
    print(f'  sites d\'exploitants à lire : {len(sites)}')
    start, n = time.time(), 0
    for o in sites:
        if time.time() - start > minutes * 60 or not ctx.pending():
            break
        pages = site_pages(o['website'], 6)
        for url, text in pages:
            for a in ctx.pending():
                if reg_pattern(a['registration']).search(text):
                    host = urllib.parse.urlsplit(url).netloc.replace('www.', '')
                    n += ctx.link(a, o['name'], o.get('country'), 'site_exploitant', 'site_exploitant',
                                  f"Site de l'exploitant ({host})", url, 4)
        if not ctx.dry:
            ctx.store.insert('source_cache', [{'key': 'site:' + o['id'], 'payload': {'pages': len(pages)},
                                               'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}],
                             on_conflict='key')
    print(f'  sites d\'exploitants : {n} appareil(s) rattaché(s)')


# ---------------------------------------------------------------------------
# 2 bis. Wikimedia Commons (photos d'aviation classées par immatriculation et par exploitant)
# ---------------------------------------------------------------------------
COMMONS = 'https://commons.wikimedia.org/w/api.php'
NOT_OPERATOR = re.compile(r'airbus|eurocopter|aerospatiale|mbb|\bh ?1\d\d|\bec ?1\d\d|bk ?117|helicopters? in |'
                          r'aircraft in |registered|photograph|taken|airport|aerodrome|heliport|flugplatz|\bin \d{4}|'
                          r'^\d{4}|files? |quality|assessed|license|cc-by|self-published|uploaded|spotting|'
                          r'by registration|by operator|by country|by type|airshow|air show|flight|\bmodels?\b', re.I)


def commons_api(params):
    q = urllib.parse.urlencode({**params, 'format': 'json', 'formatversion': 2})
    _, txt = fetch(f'{COMMONS}?{q}', timeout=30)
    return json.loads(txt or '{}')


def commons_candidates(reg):
    """Exploitants cités par les photos Commons de cette immatriculation : {nom: [url du fichier]}"""
    found = commons_api({'action': 'query', 'list': 'search', 'srsearch': f'"{reg}"', 'srnamespace': 6, 'srlimit': 15})
    titles = [r['title'] for r in found.get('query', {}).get('search', [])]
    if not titles:
        return {}
    time.sleep(1)
    info = commons_api({'action': 'query', 'titles': '|'.join(titles[:15]), 'prop': 'categories|imageinfo',
                        'clshow': '!hidden', 'cllimit': 'max', 'iiprop': 'extmetadata|url'})
    pat = reg_pattern(reg)
    out = {}
    for pg in info.get('query', {}).get('pages', []):
        meta = ((pg.get('imageinfo') or [{}])[0].get('extmetadata') or {})
        desc = page_text((meta.get('ImageDescription') or {}).get('value', ''))
        cats = [c['title'].split(':', 1)[-1] for c in pg.get('categories', [])]
        if not (pat.search(pg['title']) or pat.search(desc) or any(pat.search(c) for c in cats)):
            continue        # la photo doit concerner CETTE immatriculation
        url = (pg.get('imageinfo') or [{}])[0].get('descriptionurl') or \
            'https://commons.wikimedia.org/wiki/' + urllib.parse.quote(pg['title'].replace(' ', '_'))
        for c in cats:
            m = (re.match(r'^(?:Aircraft|Helicopters|Airbus Helicopters [\w\- ]+?) (?:of|operated by) (.+)$', c)
                 or re.match(r'^(.+?) \((?:aircraft|helicopters|fleet)\)$', c, re.I))
            if m:
                name = m.group(1).strip()
                if not NOT_OPERATOR.search(name) and len(name) > 2:
                    out.setdefault(name, []).append(url)
    return out


def from_commons(ctx, minutes):
    pend = ctx.pending()
    if not pend:
        return
    tried = {}
    if not ctx.dry:
        tried = {r['key']: r['fetched_at'] for r in ctx.store.select('source_cache', 'key,fetched_at', key='like.commons:*')}
    retry_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    pend = [a for a in pend if (tried.get('commons:' + a['registration']) or '') < retry_after]
    print(f'  Wikimedia Commons : {len(pend)} immatriculation(s) à chercher')
    start, n, ambiguous = time.time(), 0, 0
    for a in pend:
        if time.time() - start > minutes * 60:
            break
        try:
            cands = commons_candidates(a['registration'])
        except Exception as e:
            print(f'  Wikimedia Commons : {a["registration"]} ignoré ({e})')
            continue
        finally:
            time.sleep(1.5)
        if not ctx.dry:
            ctx.store.insert('source_cache', [{'key': 'commons:' + a['registration'], 'payload': {'candidats': list(cands)},
                                               'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}],
                             on_conflict='key')
        if len(cands) == 1:
            name, urls = next(iter(cands.items()))
            n += ctx.link(a, name, a['country'], 'commons', 'recherche_web',
                          'Wikimedia Commons (photo de l\'appareil classée chez cet exploitant)', urls[0], 3)
        elif len(cands) > 1:
            ambiguous += 1      # plusieurs exploitants possibles (revente, location…) : on ne choisit pas
    print(f'  Wikimedia Commons : {n} appareil(s) rattaché(s), {ambiguous} cas ambigu(s) laissé(s) de côté')


# ---------------------------------------------------------------------------
# 3. Recherche web vérifiée (Gemini + Google Search)
# ---------------------------------------------------------------------------
API = 'https://generativelanguage.googleapis.com/v1beta'


def gemini_search(key, model, a):
    prompt = (f"Quel exploitant (compagnie, organisme, service public) utilise l'hélicoptère Airbus {a.get('model') or ''} "
              f"immatriculé {a['registration']} (numéro de série {a.get('msn')}, pays : {country_name(a['country'])}) ? "
              "Cherche sur le web (registres, sites des exploitants, presse spécialisée, sites de photographes "
              "aéronautiques). Réponds UNIQUEMENT par un objet JSON : "
              '{"operator": "<nom de l\'exploitant ou null>", "country": "<code ISO à 2 lettres>", '
              '"urls": ["<adresses des pages qui citent cette immatriculation avec cet exploitant>"], '
              '"confidence": "high|medium|low"}. Si tu ne trouves pas de source qui cite cette immatriculation, '
              'réponds {"operator": null}.')
    body = json.dumps({'contents': [{'parts': [{'text': prompt}]}], 'tools': [{'google_search': {}}],
                       'generationConfig': {'temperature': 0}}).encode()
    req = urllib.request.Request(f'{API}/{model}:generateContent', data=body, method='POST',
                                 headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read())
    cand = (out.get('candidates') or [{}])[0]
    text = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', []))
    m = re.search(r'\{[\s\S]*\}', text)
    ans = {}
    if m:
        try:
            ans = json.loads(m.group(0))
        except json.JSONDecodeError:
            ans = {}
    grounded = [c.get('web', {}).get('uri') for c in (cand.get('groundingMetadata') or {}).get('groundingChunks', [])]
    urls = [u for u in (ans.get('urls') or []) if isinstance(u, str) and u.startswith('http')]
    return ans, list(dict.fromkeys(urls + [u for u in grounded if u]))[:8]


def from_web_search(ctx, minutes, limit):
    key = os.environ.get('GEMINI_API_KEY')
    pend = ctx.pending()
    if not key:
        print('  recherche web : GEMINI_API_KEY absente, méthode ignorée')
        return
    if not pend:
        return
    from presse_europe import working_models, quota_reason
    try:
        models = working_models(key, log=lambda m: print('  ' + m))
    except Exception as e:
        print(f'  recherche web : Gemini injoignable ({e}), méthode reportée')
        return
    tried = {}
    if not ctx.dry:
        tried = {r['key']: r['fetched_at'] for r in ctx.store.select('source_cache', 'key,fetched_at', key='like.web:*')}
    retry_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    pend = [a for a in pend if (tried.get('web:' + a['registration']) or '') < retry_after][:limit]
    print(f'  recherche web : {len(pend)} immatriculation(s) à chercher (modèles disponibles : {", ".join(m.split("/")[-1] for m in models[:4])})')
    start, n, rejected = time.time(), 0, 0
    for a in pend:
        if time.time() - start > minutes * 60 or not models:
            break
        ans = None
        while models and ans is None:
            try:
                ans, urls = gemini_search(key, models[0], a)
            except urllib.error.HTTPError as e:
                body = e.read()
                if e.code == 429:
                    per_minute, why = quota_reason(body)
                    if per_minute:
                        time.sleep(60)
                        continue
                    print(f'  recherche web : quota épuisé pour {models[0].split("/")[-1]} ({why})')
                else:
                    print(f'  recherche web : {models[0].split("/")[-1]} refuse la recherche (erreur {e.code} : {body[:200]!r})')
                models.pop(0)       # modèle suivant (les quotas gratuits sont propres à chaque modèle)
            except Exception as e:
                print(f'  recherche web : {a["registration"]} ignoré ({e})')
                ans, urls = {}, []
        time.sleep(7)
        if ans is None:
            print('  recherche web : aucun modèle Gemini disponible aujourd\'hui, reprise au prochain passage')
            break
        if not ctx.dry:
            ctx.store.insert('source_cache', [{'key': 'web:' + a['registration'], 'payload': {'answer': ans},
                                               'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}],
                             on_conflict='key')
        name = ans.get('operator') if isinstance(ans.get('operator'), str) else None
        if not name or ans.get('confidence') == 'low':
            continue
        # Vérification indépendante : une page source doit citer l'immatriculation ET l'exploitant
        proof = None
        for u in urls:
            try:
                final, htm = fetch(u, timeout=25, ua='Mozilla/5.0 (compatible; Helibase-bot/1.0; +https://helibase.pages.dev)')
            except Exception:
                continue
            text = page_text(htm)
            if reg_pattern(a['registration']).search(text) and name_in_text(name, text):
                proof = final
                break
        if not proof:
            rejected += 1
            continue
        host = urllib.parse.urlsplit(proof).netloc.replace('www.', '')
        n += ctx.link(a, name, (ans.get('country') or '').upper(), 'recherche_web', 'recherche_web',
                      f'Recherche web vérifiée ({host})', proof, 3)
    print(f'  recherche web : {n} appareil(s) rattaché(s), {rejected} proposition(s) rejetée(s) faute de preuve')


# ---------------------------------------------------------------------------
# 4. Presse : correspondance unique (exploitant, pays, modèle, mois)
# ---------------------------------------------------------------------------
def from_press_unique(ctx):
    orders = ctx.store.select('orders', 'id,operator_id,model,status,announced_on,quantity,sources(label,url)',
                              status='eq.livree')
    n = 0
    for o in orders:
        op = ctx.by_id.get(o['operator_id'])
        if not op or op.get('is_placeholder') or not o.get('announced_on') or (o.get('quantity') or 1) != 1:
            continue
        d = dt.date.fromisoformat(o['announced_on'])
        cands = [a for a in ctx.pending()
                 if a['country'] == op.get('country') and norm_model(a.get('model')) == norm_model(o['model'])
                 and a.get('date_precision') == 'mois' and a.get('start_date')
                 and abs((dt.date.fromisoformat(a['start_date']) - d.replace(day=1)).days) <= 35]
        if len(cands) == 1:
            src = o.get('sources') or {}
            n += ctx.link(cands[0], op['name'], op.get('country'), 'presse_correspondance', 'presse',
                          f"Presse : {src.get('label') or 'article'} (seul appareil correspondant ce mois-là)",
                          src.get('url'), 3)
    print(f'  presse (correspondance unique) : {n} appareil(s) rattaché(s)')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--only', choices=['presse', 'site', 'commons', 'web', 'unique'])
    p.add_argument('--minutes', type=int, default=60)
    p.add_argument('--web-limit', type=int, default=200, help='recherches web au plus par passage')
    args = p.parse_args()
    from helibase.store import Store
    store = Store()
    ctx = Ctx(store, args.dry_run)
    before = len(ctx.todo)
    print(f'{before} appareils dont l\'exploitant est à identifier')
    steps = [('presse', lambda: from_press_registrations(ctx)),
             ('site', lambda: from_operator_sites(ctx, args.minutes // 3)),
             ('commons', lambda: from_commons(ctx, args.minutes // 3)),
             ('web', lambda: from_web_search(ctx, args.minutes // 3, args.web_limit)),
             ('unique', lambda: from_press_unique(ctx))]
    for name, fn in steps:
        if args.only in (None, name):
            fn()
    print(f'Résultat : {len(ctx.done)} appareil(s) rattaché(s) à leur exploitant, {before - len(ctx.done)} encore à identifier')
    for method, reg, model, op, url in ctx.log:
        print(f'  {reg:<9} {model or "":<7} → {op}  [{method}] {url or ""}')
    if not args.dry_run:
        store.insert('update_log', [{'source': 'Exploitants retrouvés (presse, sites, recherche web)',
                                     'added': 0, 'changed': len(ctx.done), 'queued': 0}])


if __name__ == '__main__':
    main()
