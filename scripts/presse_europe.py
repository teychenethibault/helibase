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
    ('Airbus', 'https://www.airbus.com/en/generate-rss-feeds?tid=15581&fid=29716', False),
    ('HeliHub', 'https://www.helihub.com/tag/airbus-helicopters/feed/', True),
    ('Vertical', 'https://verticalmag.com/tag/airbus-helicopters/feed/', True),
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
            root = ET.fromstring(get(u))
        except Exception as e:
            if page == 1:
                log(f'  {name} : flux indisponible ({e})')
            break
        batch = []
        for it in root.iter('item'):
            content = it.find('content:encoded', NS)
            batch.append({'source': name, 'title': clean(it.findtext('title')), 'link': (it.findtext('link') or '').strip(),
                          'date': parse_date(it.findtext('pubDate')),
                          'text': clean((content.text if content is not None else '') or it.findtext('description'))[:3000]})
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
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                wait = 30 * (attempt + 1)
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
def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--batch', type=int, default=8)
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
    backfill = not done
    if backfill:
        print(f'Premier passage : rattrapage des articles depuis {SINCE}')

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
    if not todo:
        return

    try:
        models = pick_models(key)
    except Exception as e:
        print(f'Gemini injoignable ({e}) : veille presse reportée au prochain passage')
        return
    print(f'Modèles Gemini : {", ".join(m.split("/")[-1] for m in models[:4])}')
    def process(todo):
        events, failed = [], set()
        for i in range(0, len(todo), args.batch):
            chunk = todo[i:i + args.batch]
            res = 'quota'
            while res == 'quota' and models:
                res = ask_gemini(key, models[0], chunk)
                if res == 'quota':
                    models.pop(0)       # quotas gratuits propres à chaque modèle : on passe au suivant
            if res == 'quota' or res is None:
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


if __name__ == '__main__':
    main()
