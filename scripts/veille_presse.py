"""Veille de la presse mondiale : livraisons et commandes d'hélicoptères Airbus.

Interroge Google Actualités (flux RSS gratuits, sans clé), en six langues, qui agrège la
presse spécialisée (Vertical, AIN, Rotor & Wing, Helis, Air & Cosmos, Aviation Week…),
la presse générale et les communiqués d'Airbus et des exploitants.

Chaque titre qui annonce une livraison ou une commande est analysé (exploitant, modèle,
quantité) puis placé dans la file « À vérifier » de l'Administration. Rien n'entre dans la
base sans validation : la presse se trompe parfois, et un titre peut être mal compris.

Utilisation :
  python scripts/veille_presse.py                 # 60 derniers jours
  python scripts/veille_presse.py --depuis 2024-01-01   # rattrapage (par trimestres)
  python scripts/veille_presse.py --dry-run
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
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'
MODELS = ['H125', 'H130', 'H135', 'H140', 'H145', 'H160', 'H175', 'H215', 'H225', 'ACH130', 'ACH145', 'ACH160',
          'H145M', 'H225M', 'H125M', 'EC135', 'EC145', 'AS350']
MODEL_RE = re.compile(r'\b(ACH ?1[2-6][05]|H ?1(?:2[05]|3[05]|4[05]|60|75)(?:M|D3|e)?|H ?2(?:15|25)M?|EC ?1[2-7]5(?: ?T[1-3])?|'
                      r'AS ?350(?: ?B[23]e?)?|AS ?332|BK ?117(?: ?D-?[23])?|NH ?90|Tigre|Tiger|Super Puma|Caracal|'
                      r'Ecureuil|Écureuil|Dauphin|Panther|Jaguar)\b', re.I)

LANGS = {   # langue : (paramètres Google Actualités, mots « livraison », mots « commande »)
    'en': ('hl=en-US&gl=US&ceid=US:en', 'delivery OR "takes delivery" OR receives OR delivered', 'orders OR order OR contract OR selects'),
    'fr': ('hl=fr&gl=FR&ceid=FR:fr', 'livraison OR réceptionne OR reçoit OR livré', 'commande OR commandé OR contrat'),
    'de': ('hl=de&gl=DE&ceid=DE:de', 'Übergabe OR erhält OR übernimmt OR ausgeliefert', 'bestellt OR Bestellung OR Auftrag'),
    'es': ('hl=es&gl=ES&ceid=ES:es', 'entrega OR recibe OR incorpora', 'pedido OR encarga OR adquiere OR contrato'),
    'pt': ('hl=pt-BR&gl=BR&ceid=BR:pt-419', 'entrega OR recebe OR incorpora', 'encomenda OR adquire OR contrato'),
    'it': ('hl=it&gl=IT&ceid=IT:it', 'consegna OR riceve OR consegnato', 'ordine OR ordina OR contratto'),
}

DELIVER = (r'takes? delivery of|took delivery of|receives?|received|welcomes?|welcomed|adds?|added|accepts?|accepted|'
           r'inducts?|inducted|unveils?|introduces?|gets|launches|debuts|'
           r'réceptionne|reçoit|a reçu|prend livraison d[eu]s?|accueille|se dote d[eu]s?|'
           r'erhält|übernimmt|hat .{0,20} übernommen|nimmt .{0,30} in (?:Empfang|Dienst)|'
           r'recibe|reciben|incorpora|recebe|recebem|riceve|ricevono|prende in consegna|prendono in consegna')
ORDER = (r'orders?|ordered|signs? (?:a )?(?:contract|deal|agreement) for|signs? for|selects?|selected|chooses|buys?|bought|'
         r'purchases?|purchased|to acquire|acquires?|contracts? for|'
         r'commande|a commandé|choisit|acquiert|bestellt|kauft|ordina|acquista|encarga|compra|adquiere|adquire|encomenda')
NUM = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'twelve': 12,
       'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5, 'six ': 6, 'zwei': 2, 'drei': 3, 'vier': 4, 'fünf': 5,
       'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'dois': 2, 'três': 3, 'quatro': 4, 'due': 2, 'tre': 3, 'quattro': 4}
LEADING = re.compile(r"^(?:the|le|la|les|l'|die|der|das|el|los|las|o|os|a|as|il|lo|gli|new|nouvelle?|neue[rs]?)\s+", re.I)
BAD_OP = re.compile(r'^(?:airbus|eurocopter|it|this|first|new|video|photos?|watch|report|exclusive|breaking|update)\b', re.I)


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/rss+xml,text/xml,*/*'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def search(query, lang, after=None, before=None):
    q = query + (f' after:{after}' if after else '') + (f' before:{before}' if before else '')
    if not after:
        q += ' when:60d'
    url = f'https://news.google.com/rss/search?q={urllib.parse.quote(q)}&{LANGS[lang][0]}'
    root = ET.fromstring(fetch(url))
    for item in root.iter('item'):
        src = item.find('source')
        yield {
            'title': html.unescape(item.findtext('title') or ''),
            'link': item.findtext('link') or '',
            'date': _date(item.findtext('pubDate')),
            'source': src.text if src is not None else '',
            'lang': lang,
        }


def _date(rfc):
    try:
        return dt.datetime.strptime((rfc or '')[:25].strip(), '%a, %d %b %Y %H:%M:%S').date().isoformat()
    except ValueError:
        return None


def _clean_op(op):
    op = re.split(r'\s*[:|]\s*', op or '', 1)[0]
    op = re.sub(r'\s*[\[(].*?[\])]', '', op).strip(' :,-–—"\'«»“”')
    op = re.sub(r'\s+(?:in|en|au|aux|in der|im|em|no|na)\s+[A-ZÀ-Ý][\w\s-]*$', '', op)
    op = re.split(r'\s+(?:has|have|will|to|is|says|announces|annonce|va|ha|hat|wird|plans to)\s+', op, 1)[0]
    op = LEADING.sub('', op).strip()
    # « Le SAMU 74 » → « SAMU 74 » ; « Norway's police » → « Norway's police »
    if not op or len(op) > 70 or len(op) < 3 or BAD_OP.search(op) or not re.search(r'[A-Za-zÀ-ÿ]', op):
        return None
    return op


def analyse(title):
    """Renvoie {kind, operator, model, quantity} si le titre annonce une livraison ou une commande."""
    t = re.sub(r'\s+-\s+[^-]+$', '', title).strip()       # « … - Vertical Mag » : on retire le nom du média
    model_m = MODEL_RE.search(t)
    if not model_m:
        return None
    model = re.sub(r'\s+', '', model_m.group(0)).upper().replace('ÉCUREUIL', 'Ecureuil')
    kind, op = None, None
    m = re.search(rf'^(?P<op>.+?)\s+(?:{DELIVER})\b', t, re.I)
    if m and m.start('op') == 0 and m.end('op') < model_m.start():
        kind, op = 'livraison', m.group('op')
    if not op:
        m = re.search(rf'^(?P<op>.+?)\s+(?:{ORDER})\b', t, re.I)
        if m and m.end('op') < model_m.start():
            kind, op = 'commande', m.group('op')
    if not op:   # « Airbus delivers first H160 to Babcock », « Airbus livre … à … »
        m = re.search(r'\bAirbus(?: Helicopters)?\s+(?:delivers|hands over|livre|remet|liefert|übergibt|entrega|consegna)\b.*?'
                      r'\b(?:to|à|au|aux|an|a|para|al)\s+(?:the\s+|la\s+|le\s+|l\')?(?P<op>[^,;:–—]+)', t, re.I)
        if m:
            kind, op = 'livraison', m.group('op')
    op = _clean_op(op)
    if not op:
        return None
    qty = None
    q = re.search(r'\b(\d{1,3})\s*(?:x\s*)?(?:additional |more |new |further |nouveaux? |neue |nuevos? |novos? |nuovi )?'
                  r'(?:Airbus\s+)?' + re.escape(model_m.group(0)), t, re.I)
    if q:
        qty = int(q.group(1))
    else:
        w = re.search(r'\b(' + '|'.join(k.strip() for k in NUM) + r')\b[^.]{0,25}' + re.escape(model_m.group(0)), t, re.I)
        if w:
            qty = NUM.get(w.group(1).lower(), NUM.get(w.group(1).lower() + ' '))
    return {'kind': kind, 'operator': op, 'model': model, 'quantity': qty}


def windows(since):
    """Fenêtres de trois mois de la date de départ à aujourd'hui (Google renvoie au plus 100 articles par requête)."""
    d = dt.date.fromisoformat(since)
    today = dt.date.today()
    while d <= today:
        e = (d.replace(day=1) + dt.timedelta(days=95)).replace(day=1)
        yield d.isoformat(), min(e, today + dt.timedelta(days=1)).isoformat()
        d = e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--depuis', help='rattrapage depuis cette date (AAAA-MM-JJ)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not args.depuis and not args.dry_run:
        from helibase.store import Store
        if not Store().select('update_log', 'id', source='eq.Veille presse (Google Actualités)'):
            args.depuis = '2024-01-01'
            print('Premier passage : rattrapage des annonces depuis le 1er janvier 2024')
    models_or = '(' + ' OR '.join(f'"{m}"' for m in MODELS) + ')'
    queries = []
    for lang, (_, deliver, order) in LANGS.items():
        queries.append((lang, f'{models_or} ({deliver})'))
        queries.append((lang, f'{models_or} ({order})'))
        queries.append((lang, f'"Airbus Helicopters" ({deliver} OR {order})'))
    spans = list(windows(args.depuis)) if args.depuis else [(None, None)]

    seen, found, scanned = set(), [], 0
    for after, before in spans:
        for lang, q in queries:
            try:
                items = list(search(q, lang, after, before))
            except Exception as e:
                print(f'  recherche impossible ({lang}, {after or "récent"}) : {e}')
                continue
            time.sleep(1.5)
            for it in items:
                scanned += 1
                key = re.sub(r'\W+', ' ', it['title'].lower()).strip()[:120]
                if key in seen:
                    continue
                seen.add(key)
                a = analyse(it['title'])
                if a:
                    found.append({**it, **a})
        if after:
            print(f'  {after} → {before} : {len(found)} annonces retenues jusqu\'ici')
    print(f'{scanned} articles lus, {len(seen)} titres différents, {len(found)} livraisons ou commandes repérées')
    for f in found[:25]:
        print(f"  [{f['kind']}] {f['date']} {f['operator']} — {f['quantity'] or ''}{' × ' if f['quantity'] else ''}{f['model']}  ({f['source']})")
    if args.dry_run or not found:
        return

    from helibase.store import Store
    store = Store()
    known = {(r.get('payload') or {}).get('link') for r in store.select('review_queue', 'payload')}
    known |= {(r.get('payload') or {}).get('title') for r in store.select('review_queue', 'payload')}
    rows = []
    for f in found:
        if f['link'] in known or f['title'] in known:
            continue
        label = 'Livraison' if f['kind'] == 'livraison' else 'Commande'
        qty = f"{f['quantity']} × " if f['quantity'] else ''
        rows.append({'kind': f['kind'], 'summary': f"{label} : {f['operator']} — {qty}{f['model']}",
                     'payload': {'origin': 'presse', **f}})
    if rows:
        store.insert('review_queue', rows)
    store.insert('update_log', [{'source': 'Veille presse (Google Actualités)', 'added': 0, 'changed': 0, 'queued': len(rows)}])
    print(f'Résultat : {len(rows)} nouvelles annonces dans la file « À vérifier »')


if __name__ == '__main__':
    main()
