"""Veille presse européenne : commandes, lettres d'intention et livraisons d'hélicoptères Airbus.

Sources (flux RSS officiels, aucune page protégée n'est lue) :
  - Airbus, communiqués « Hélicoptères » ;
  - HeliHub, rubrique Airbus Helicopters (et ses pages d'archives pour remonter à 2024) ;
  - Vertical Magazine, rubrique Airbus Helicopters (si disponible).

Chaque article est lu par Gemini (Google, offre gratuite), qui renvoie un JSON strict :
exploitant, pays, modèle, quantité, statut (commande ferme, lettre d'intention, options,
livraison), secteur, date. Seuls les événements européens, de confiance haute ou moyenne,
sont retenus. Les doublons (même exploitant, modèle et statut à moins de 60 jours) sont fusionnés.
Un article n'est jamais analysé deux fois (mémoire dans la table source_cache).

Clé requise : secret GitHub GEMINI_API_KEY (aistudio.google.com → Get API key).

Utilisation :
  python scripts/presse_europe.py              # nouveaux articles (rattrapage depuis 2024 au premier passage)
  python scripts/presse_europe.py --dry-run    # affiche les événements trouvés, n'écrit rien
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
from helibase.europe import EUROPE, country_name, is_europe  # noqa: E402
from helibase.names import infer_activities, name_key  # noqa: E402

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Version/17.5 Safari/605.1.15')
SINCE = '2024-01-01'
FEEDS = [
    ('HeliHub', 'https://www.helihub.com/tag/airbus-helicopters/feed/', True),
    ('Vertical', 'https://verticalmag.com/feed/', True),
]
MODEL_RE = re.compile(r'\b(ACH ?1[2-7][05]|H ?1(?:2[05]|3[05]|4[05]|60|75)M?|H ?2(?:15|25)M?|NH ?90|Tig(?:er|re)|'
                      r'Caracal|Gu[ée]pard|EC ?1[2-7]5|BK ?117|AS ?350|Lakota|Sea Tiger)\b', re.I)
ACTIVITIES = ['Secours médical', 'Police et sécurité', 'Sécurité civile et incendie', 'Recherche et sauvetage',
              'Offshore et énergie', 'Tourisme', 'Transport et charter', 'Affaires et VIP', 'Travail aérien',
              'Formation', 'Militaire']
NS = {'content': 'http://purl.org/rss/1.0/modules/content/'}


# ---------------------------------------------------------------------------
# Lecture des flux
# ---------------------------------------------------------------------------
def get(url, timeout=60, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/rss+xml,text/xml,*/*', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_date(s):
    s = (s or '').strip()
    for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z', '%a, %m/%d/%Y - %H:%M'):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    return m.group(0) if m else None


def clean(text):
    text = re.sub(r'<(script|style)[\s\S]*?</\1>', ' ', text or '', flags=re.I)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text))).strip()


def read_feed(name, url, paged, backfill, log=print):
    items, page = [], 1
    while True:
        u = url if page == 1 else url + ('&' if '?' in url else '?') + f'paged={page}'
        try:
            raw = get(u)
        except Exception as e:
            if page == 1:
                log(f'  {name} : flux indisponible ({e})')
            break
        batch = []
        try:
            root = ET.fromstring(raw)
            for it in root.iter('item'):
                content = it.find('content:encoded', NS)
                batch.append({'source': name, 'title': clean(it.findtext('title')), 'link': (it.findtext('link') or '').strip(),
                              'date': parse_date(it.findtext('pubDate')),
                              'text': clean((content.text if content is not None else '') or it.findtext('description'))[:1800]})
        except ET.ParseError:
            # Flux légèrement mal formé (fréquent) : lecture article par article
            txt = raw.decode('utf-8', 'replace')
            def tag(block, t):
                m = re.search(rf'<{t}[^>]*>([\s\S]*?)</{t}>', block)
                v = m.group(1) if m else ''
                return re.sub(r'^<!\[CDATA\[|\]\]>$', '', v.strip())
            for block in re.findall(r'<item[\s>][\s\S]*?</item>', txt):
                batch.append({'source': name, 'title': clean(tag(block, 'title')), 'link': clean(tag(block, 'link')),
                              'date': parse_date(clean(tag(block, 'pubDate'))),
                              'text': clean(tag(block, 'content:encoded') or tag(block, 'description'))[:1800]})
            if not batch and page == 1:
                log(f'  {name} : flux illisible (début : {txt[:120]!r})')
        items += batch
        oldest = min((b['date'] for b in batch if b['date']), default=None)
        if not (paged and backfill and batch and oldest and oldest >= SINCE and page < 60):
            break
        page += 1
        time.sleep(3)
    log(f'  {name} : {len(items)} articles')
    return items


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
API = 'https://generativelanguage.googleapis.com/v1beta'


def pick_models(key):
    # La clé passe dans l'en-tête (jamais dans l'adresse) : marche avec les clés « AQ. » comme « AIza »
    data = json.loads(get(f'{API}/models?pageSize=200', headers={'x-goog-api-key': key}))
    names = [m['name'] for m in data.get('models', [])
             if 'generateContent' in m.get('supportedGenerationMethods', [])
             and 'flash' in m['name'] and not re.search(r'image|tts|live|audio|thinking|exp|preview|embedding', m['name'])]
    if not names:
        raise RuntimeError('aucun modèle Gemini « flash » disponible pour cette clé')
    def score(n):   # version la plus récente, et la version complète avant « lite »
        v = re.findall(r'(\d+(?:\.\d+)?)', n)
        return (float(v[0]) if v else 0, 'lite' not in n)
    return sorted(names, key=score, reverse=True)


def pick_model(key):
    return pick_models(key)[0]


def working_models(key, log=print, want=3):
    """Essaie chaque modèle avec une toute petite question et garde ceux qui répondent vraiment
    (les modèles les plus récents sont souvent saturés pour l'offre gratuite)."""
    ok = []
    body = json.dumps({'contents': [{'parts': [{'text': 'Réponds juste OK.'}]}],
                       'generationConfig': {'temperature': 0, 'maxOutputTokens': 5}}).encode()
    for m in pick_models(key):
        req = urllib.request.Request(f'{API}/{m}:generateContent', data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                resp.read()
            ok.append(m)
            log(f'  {m.split("/")[-1]} : disponible')
            if len(ok) >= want:
                break
        except urllib.error.HTTPError as e:
            log(f'  {m.split("/")[-1]} : indisponible ({e.code} — {quota_reason(e.read())[1][:160]})')
        except Exception as e:
            log(f'  {m.split("/")[-1]} : indisponible ({e})')
        time.sleep(2)
    return ok


def quota_reason(err_body):
    """Explication courte d'un refus 429 de Gemini : (quota du jour ou par minute ?, texte)."""
    try:
        msg = json.loads(err_body).get('error', {}).get('message', '')
    except Exception:
        msg = err_body.decode('utf-8', 'replace') if isinstance(err_body, bytes) else str(err_body)
    per_minute = bool(re.search(r'PerMinute|per minute', msg, re.I)) and not re.search(r'PerDay|per day|limit: 0', msg, re.I)
    return per_minute, re.sub(r'\s+', ' ', msg)[:300]


PROMPT = """Tu es un analyste de l'industrie des hélicoptères. Pour chaque article ci-dessous, extrais les
événements concernant des hélicoptères Airbus Helicopters (H125, H130, H135, H140, H145, H145M, H160, H160M,
H175, H215, H225, H225M, NH90, Tigre, ACH…) et UNIQUEMENT pour un client situé en Europe
(UE, Royaume-Uni, Suisse, Norvège, Islande, Balkans, Ukraine, Turquie ; Russie et Biélorussie exclues).

Types d'événement : "order" (commande ferme), "loi" (lettre d'intention, protocole d'accord, accord-cadre non ferme),
"options", "delivery" (livraison effective d'un ou plusieurs appareils neufs à l'exploitant).
Ignore : contrats de maintenance ou de support, simulateurs, essais, salons, résultats financiers,
appareils d'occasion, location seule, rumeurs.

Réponds UNIQUEMENT en JSON, sous la forme :
{"events": [{"article": <numéro>, "operator": "<nom officiel de l'exploitant final, ou null si non divulgué>",
"undisclosed": <true si le client n'est pas nommé>, "country": "<code ISO à 2 lettres du pays de l'exploitant>",
"model": "<modèle Airbus, ex. H145>", "quantity": <nombre ou null>, "type": "order|loi|options|delivery",
"sector": "civil|etatique|militaire", "activity": "<une valeur de la liste ou null>",
"date": "<AAAA-MM-JJ de l'événement, sinon date de l'article>",
"registrations": ["<immatriculations des appareils citées dans l'article, ex. D-HDRA, G-NHAC ; liste vide sinon>"],
"confidence": "high|medium|low"}]}
Liste des activités : """ + ', '.join(ACTIVITIES) + """.
Un événement = un seul exploitant final, désigné par son nom officiel (pas de « A / B ») ; si deux exploitants
sont concernés, fais deux événements. Pour une armée ou un ministère de la Défense, mets le secteur "militaire".
Livraisons : uniquement des appareils NEUFS sortis d'usine (ignore occasion, revente, location d'un appareil existant).
N'invente rien : si une information n'est pas dans l'article, mets null. Aucun événement → {"events": []}.

ARTICLES :
"""


def ask_gemini(key, model, articles, log=print):
    body = PROMPT + '\n\n'.join(f'[{i}] {a["date"]} — {a["source"]} — {a["title"]}\n{a["text"]}'
                                for i, a in enumerate(articles))
    payload = json.dumps({'contents': [{'parts': [{'text': body}]}],
                          'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json'}}).encode()
    for attempt in range(4):
        req = urllib.request.Request(f'{API}/{model}:generateContent', data=payload, method='POST',
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                out = json.loads(resp.read())
            text = out['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text).get('events', [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                per_minute, why = quota_reason(e.read())
                if not per_minute:
                    log(f'    quota épuisé pour {model.split("/")[-1]} ({why})')
                    return 'quota'
            if e.code in (500, 502, 503, 504) and attempt >= 1:
                log(f'    {model.split("/")[-1]} momentanément surchargé ({e.code}) : essai avec un autre modèle')
                return 'busy'
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                wait = 20
                log(f'    Gemini occupé ({e.code}), nouvel essai dans {wait} s')
                time.sleep(wait)
                continue
            log(f'    Gemini a refusé la demande ({e.code}) : lot reporté au prochain passage')
            return None
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            if attempt < 3:
                log(f'    Gemini ne répond pas ({e}), nouvel essai dans 20 s')
                time.sleep(20)
                continue
            log('    Gemini injoignable : lot reporté au prochain passage')
            return None
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            log(f'    réponse Gemini illisible ({e}), lot ignoré')
            return []
    return None


# ---------------------------------------------------------------------------
MILITARY = re.compile(r'arm(ed|ée|ee)s?\b|armée|army|air force|navy|marine nationale|luftwaffe|bundeswehr|heer\b|'
                      r'défense|defen[cs]e|minist(ry|ère|ere|erio|erium) (of |de la |della |der )?(def|vert)|'
                      r'\bdga\b|armement|procurement agency|forces aériennes|aviation légère de l|alat\b|'
                      r'esercito|ejército|ejercito|armada|força aérea|forca aerea|krijgsmacht|landmacht|luchtmacht|'
                      r'försvar|forsvar|puolustus|wojsk|siły zbrojne|ozbrojen|honvéd|military', re.I)


def canonical_name(name, country, sector):
    """Un seul exploitant par armée : « Forces armées — pays », comme pour les livraisons Rotorspot."""
    if sector == 'militaire' or MILITARY.search(name or ''):
        return f'Forces armées — {country_name(country)}', 'militaire'
    return name, sector


def merge_military(store):
    """Regroupe sous « Forces armées — pays » les exploitants militaires nommés autrement par la presse."""
    ops = store.select('operators', 'id,name,country,sector')
    notes = {n['operator_id'] for n in store.select('notes', 'operator_id')}
    favs = {f['operator_id'] for f in store.select('favorites', 'operator_id')}
    canon = {o['country']: o for o in ops if o['name'].startswith('Forces armées — ')}
    moved = 0
    for o in ops:
        if o['name'].startswith('Forces armées — ') or not o.get('country') or o['id'] in notes or o['id'] in favs:
            continue
        if not (o.get('sector') == 'militaire' or MILITARY.search(o['name'])):
            continue
        target = canon.get(o['country'])
        if not target:
            target = store.insert('operators', [{'name': f'Forces armées — {country_name(o["country"])}', 'country': o['country'],
                                                 'sector': 'militaire', 'activities': ['Militaire'],
                                                 'lat': EUROPE[o['country']][1] if o['country'] in EUROPE else None,
                                                 'lon': EUROPE[o['country']][2] if o['country'] in EUROPE else None}])[0]
            canon[o['country']] = target
        for t in ('orders', 'assignments'):
            store.update(t, {'operator_id': target['id']}, operator_id=f'eq.{o["id"]}')
        store.delete('operators', id=f'eq.{o["id"]}')
        moved += 1
    if moved:
        print(f'{moved} exploitant(s) militaire(s) regroupé(s) sous « Forces armées — pays »')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--batch', type=int, default=5)
    args = p.parse_args()

    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        print('GEMINI_API_KEY absente : veille presse non lancée (ajoutez le secret dans GitHub).')
        return
    store = None
    if not args.dry_run:
        from helibase.store import Store
        store = Store()

    done = set()
    if store:
        done = {r['key'] for r in store.select('source_cache', 'key', key='like.press:*')}
    if store:
        try:
            merge_military(store)
        except Exception as e:
            print(f'Regroupement des exploitants militaires impossible ({e})')
    # Rattrapage depuis 2024 tant qu'il n'a pas été mené à son terme sans erreur
    backfill = not (store and store.select('source_cache', 'key', key='eq.meta:press_backfill_done'))
    if backfill:
        print(f'Rattrapage des articles depuis {SINCE} (jusqu\'à ce que tout soit analysé)')

    articles = []
    for name, url, paged in FEEDS:
        articles += read_feed(name, url, paged, backfill)
    seen, todo = set(), []
    for a in articles:
        k = 'press:' + hashlib.sha1((a['link'] or a['title']).encode()).hexdigest()[:20]
        if k in done or k in seen or not a['date'] or a['date'] < SINCE:
            continue
        seen.add(k)
        if MODEL_RE.search(a['title'] + ' ' + a['text']):
            todo.append({**a, 'key': k})
    print(f'{len(todo)} articles à analyser (sur {len(articles)} lus)')
    all_failed = set()
    if not todo:
        if store and backfill:
            store.insert('source_cache', [{'key': 'meta:press_backfill_done', 'payload': {'date': dt.date.today().isoformat()}}],
                         on_conflict='key')
        return

    print('Test des modèles Gemini :')
    try:
        models = working_models(key)
    except Exception as e:
        print(f'Gemini injoignable ({e}) : veille presse reportée au prochain passage')
        return
    if not models:
        print('Aucun modèle Gemini ne répond aujourd\'hui : veille presse reportée au prochain passage')
        return
    def process(todo):
        events, failed = [], set()
        for i in range(0, len(todo), args.batch):
            chunk = todo[i:i + args.batch]
            res, busy_rounds = 'busy', 0
            while res in ('busy', 'quota') and models and busy_rounds < 3 * len(models):
                res = ask_gemini(key, models[0], chunk)
                if res == 'quota':
                    models.pop(0)       # quota du jour épuisé pour ce modèle : abandonné pour ce passage
                elif res == 'busy':
                    models.append(models.pop(0))     # surcharge passagère : on alterne entre les modèles
                    busy_rounds += 1
                    if busy_rounds % len(models) == 0:
                        print('    tous les modèles sont chargés : pause d\'une minute')
                        time.sleep(60)
            if res in ('busy', 'quota') or res is None:
                failed.update(x['key'] for x in chunk)
                res = []
            for e in res:
                try:
                    a = chunk[int(e.get('article'))]
                except (TypeError, ValueError, IndexError):
                    continue
                events.append({**e, '_article': a})
            time.sleep(6)     # offre gratuite : quelques requêtes par minute

        kept = []
        for e in events:
            c = (e.get('country') or '').upper()
            if e.get('confidence') not in ('high', 'medium') or not is_europe(c) or not e.get('model') \
                    or e.get('type') not in ('order', 'loi', 'options', 'delivery'):
                continue
            if not e.get('operator') and not e.get('undisclosed'):
                continue
            e['country'] = c
            kept.append(e)
        def remember():   # articles analysés : jamais relus
            all_failed.update(failed)
            ok = [a for a in todo if a['key'] not in failed]
            for i in range(0, len(ok), 200):
                store.insert('source_cache', [{'key': a['key'], 'payload': {'title': a['title'], 'link': a['link']}}
                                              for a in ok[i:i + 200]], on_conflict='key')

        print(f'{len(events)} événements extraits, {len(kept)} retenus (Europe, confiance haute ou moyenne)')
        for e in kept:
            print(f"  {e['_article']['date']} {e['type']:<8} {e.get('operator') or 'client non divulgué'} ({e['country']}) — "
                  f"{e.get('quantity') or ''}{'×' if e.get('quantity') else ''}{e['model']}  [{e['_article']['source']}]")
        if args.dry_run:
            return
        if not kept:
            remember()
            return

        ops = store.select('operators', 'id,name,aliases,country,is_placeholder')
        by_key = {}
        for o in ops:
            for n in [o['name'], *(o.get('aliases') or [])]:
                by_key[(name_key(n), o.get('country') or '')] = o
        orders = store.select('orders', 'id,operator_id,model,status,firmness,announced_on,quantity')
        added = merged = created = 0
        for e in kept:
            art = e['_article']
            undisclosed = bool(e.get('undisclosed')) or not e.get('operator')
            name = f'Client non divulgué — {country_name(e["country"])}' if undisclosed else e['operator'].strip()
            if not undisclosed:
                name, e['sector'] = canonical_name(name, e['country'], e.get('sector'))
            k = (name_key(name), e['country'])
            op = by_key.get(k)
            if not op:
                sector = e.get('sector') if e.get('sector') in ('civil', 'etatique', 'militaire') else 'civil'
                acts = [x for x in ([e.get('activity')] + infer_activities(name)) if x in ACTIVITIES]
                if sector == 'militaire' and 'Militaire' not in acts:
                    acts.append('Militaire')
                row = {'name': name, 'country': e['country'], 'sector': sector, 'is_placeholder': undisclosed,
                       'activities': list(dict.fromkeys(acts))}
                if undisclosed:
                    row['lat'], row['lon'] = EUROPE[e['country']][1], EUROPE[e['country']][2]
                op = store.insert('operators', [row])[0]
                by_key[k] = op
                created += 1
            status = 'livree' if e['type'] == 'delivery' else 'annoncee'
            firmness = {'order': 'ferme', 'loi': 'loi', 'options': 'options'}.get(e['type'])
            date = (e.get('date') or art['date'] or '')[:10]
            if len(date) == 7:
                date += '-01'
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
                date = art['date']
            ev_model = re.sub(r'\s+', '', e['model']).upper().replace('TIGER', 'Tigre').replace('TIGRE', 'Tigre')
            qty = e.get('quantity') if isinstance(e.get('quantity'), int) and e['quantity'] > 0 else None
            dup = next((o for o in orders if o['operator_id'] == op['id'] and o['model'] == ev_model and o['status'] == status
                        and (o.get('firmness') or None) == firmness and o.get('announced_on')
                        and abs((dt.date.fromisoformat(o['announced_on']) - dt.date.fromisoformat(date)).days) <= 60), None)
            if dup:
                if qty and (dup.get('quantity') or 0) < qty:
                    store.update('orders', {'quantity': qty}, id=f'eq.{dup["id"]}')
                merged += 1
                continue
            src = store.insert('sources', [{'kind': 'airbus' if art['source'] == 'Airbus' else 'presse',
                                            'label': art['source'], 'url': art['link'] or None,
                                            'retrieved_on': dt.date.today().isoformat(),
                                            'confidence': 4 if art['source'] == 'Airbus' else 3}])[0]
            regs = sorted({re.sub(r'\s+', '', r).upper() for r in (e.get('registrations') or [])
                           if isinstance(r, str) and re.fullmatch(r'[A-Z0-9]{1,2}-?[A-Z0-9]{2,5}', re.sub(r'\s+', '', r).upper())})
            row = store.insert('orders', [{'operator_id': op['id'], 'model': ev_model, 'quantity': qty, 'announced_on': date,
                                           'status': status, 'firmness': firmness, 'source_id': src['id'],
                                           'registrations': regs or None}])[0]
            orders.append(row)
            added += 1
        remember()
        store.insert('update_log', [{'source': 'Veille presse Europe (Gemini)', 'added': added, 'changed': merged, 'queued': 0}])
        print(f'Résultat : {added} commandes ou livraisons ajoutées, {merged} doublons fusionnés, {created} exploitants créés')

    step = args.batch * 10
    for r in range(0, len(todo), step):
        print(f'— Tranche {r // step + 1} / {(len(todo) - 1) // step + 1}')
        process(todo[r:r + step])
    if store and backfill and not all_failed:
        store.insert('source_cache', [{'key': 'meta:press_backfill_done', 'payload': {'date': dt.date.today().isoformat()}}],
                     on_conflict='key')
        print('Rattrapage terminé : les prochains passages ne liront que les nouveaux articles')
    elif all_failed:
        print(f'{len(all_failed)} article(s) non analysé(s) : repris au prochain passage')


if __name__ == '__main__':
    main()
