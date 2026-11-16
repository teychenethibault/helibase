"""Traitement automatique de la file « À vérifier ».

Presse :
  1. écarte les titres mal compris (nom d'exploitant douteux) ;
  2. fusionne les articles qui parlent du même événement (même exploitant, même modèle,
     même type, à moins de 4 mois d'écart) ;
  3. trouve le pays de l'exploitant : exploitant déjà connu, nom de pays dans le titre,
     Wikidata, sinon la langue de l'article ;
  4. crée l'exploitant s'il n'existe pas, puis la livraison ou la commande, avec le lien
     vers l'article ; la ligne passe en « validée ».

Registres (appareils au nom d'une banque ou d'un loueur) : impossibles à résoudre sans
autre source ; ils sont classés automatiquement (l'appareil reste dans la base, au nom du
propriétaire) pour ne plus encombrer la file.

Utilisation :
  python scripts/traiter_file.py            # traite toute la file
  python scripts/traiter_file.py --dry-run  # affiche les décisions sans rien écrire
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from helibase import verify as V  # noqa: E402
from helibase.names import infer_activities, name_key, strip_accents  # noqa: E402

UA = 'Helibase/1.0 (https://helibase.pages.dev; outil interne de prospection)'

# Noms de pays (anglais, français, allemand, espagnol, portugais, italien) et adjectifs courants → code ISO
COUNTRIES = {
    'FR': 'france french français française francaise frankreich francia',
    'DE': 'germany german allemagne allemande allemand deutschland deutsche deutscher alemania germania alemanha',
    'GB': 'uk united kingdom britain british royaume-uni britannique england english scotland scottish wales welsh',
    'IT': 'italy italian italie italienne italien italia italiana italiano',
    'ES': 'spain spanish espagne espagnol espagnole españa española español spagna',
    'PT': 'portugal portuguese portugais portugaise',
    'CH': 'switzerland swiss suisse schweiz schweizer svizzera',
    'AT': 'austria austrian autriche österreich österreichische',
    'BE': 'belgium belgian belgique belge',
    'NL': 'netherlands dutch pays-bas néerlandais holland',
    'LU': 'luxembourg luxembourgeois',
    'IE': 'ireland irish irlande irlandais',
    'NO': 'norway norwegian norvège norvégien norsk',
    'SE': 'sweden swedish suède suédois svensk',
    'FI': 'finland finnish finlande finlandais',
    'DK': 'denmark danish danemark danois',
    'IS': 'iceland icelandic islande',
    'PL': 'poland polish pologne polonais polska',
    'CZ': 'czech tchèque czechia',
    'SK': 'slovakia slovak slovaquie',
    'HU': 'hungary hungarian hongrie hongrois',
    'RO': 'romania romanian roumanie roumain',
    'BG': 'bulgaria bulgarian bulgarie',
    'GR': 'greece greek grèce grec hellenic',
    'HR': 'croatia croatian croatie',
    'SI': 'slovenia slovenian slovénie',
    'RS': 'serbia serbian serbie',
    'LT': 'lithuania lithuanian lituanie', 'LV': 'latvia latvian lettonie', 'EE': 'estonia estonian estonie',
    'TR': 'turkey turkish turquie türkiye',
    'US': 'usa us u.s. united states american américain américaine etats-unis états-unis',
    'CA': 'canada canadian canadien canadienne',
    'MX': 'mexico mexican mexique mexicain', 'BR': 'brazil brazilian brésil brésilien brasil brasileira brasileiro',
    'AR': 'argentina argentine argentin', 'CL': 'chile chilean chili chilien', 'CO': 'colombia colombian colombie',
    'PE': 'peru peruvian pérou', 'EC': 'ecuador équateur',
    'AU': 'australia australian australie australien', 'NZ': 'new zealand nouvelle-zélande',
    'JP': 'japan japanese japon japonais', 'CN': 'china chinese chine chinois', 'KR': 'korea korean corée coréen',
    'IN': 'india indian inde indien', 'ID': 'indonesia indonesian indonésie', 'MY': 'malaysia malaysian malaisie',
    'SG': 'singapore singapour', 'TH': 'thailand thai thaïlande', 'PH': 'philippines philippine',
    'VN': 'vietnam vietnamese', 'PK': 'pakistan pakistani', 'BD': 'bangladesh',
    'AE': 'uae emirates emirati émirats', 'SA': 'saudi arabie saoudite', 'QA': 'qatar qatari',
    'KW': 'kuwait koweït', 'OM': 'oman omani', 'BH': 'bahrain bahreïn', 'IL': 'israel israeli israël',
    'EG': 'egypt egyptian égypte', 'MA': 'morocco moroccan maroc marocain', 'DZ': 'algeria algérie',
    'TN': 'tunisia tunisie', 'ZA': 'south africa african afrique du sud', 'NG': 'nigeria nigerian',
    'KE': 'kenya kenyan', 'KZ': 'kazakhstan kazakh', 'UA': 'ukraine ukrainian ukrainien',
}
WORDS = {}
for iso, names in COUNTRIES.items():
    for n in names.split():
        WORDS[strip_accents(n).lower()] = iso
for multi, iso in [('united kingdom', 'GB'), ('united states', 'US'), ('new zealand', 'NZ'), ('south africa', 'ZA'),
                   ('saudi arabia', 'SA'), ('arabie saoudite', 'SA'), ('afrique du sud', 'ZA'), ('south korea', 'KR')]:
    WORDS[multi] = iso
# Exploitants institutionnels reconnaissables à leur seul nom (police, armée, secours…)
INSTITUTION = re.compile(r'\b(police|polizei|politi|polis|policía|polizia|gendarm\w*|carabinieri|guardia civil|sheriff|'
                         r'army|navy|air force|armée|marine|bundeswehr|luftwaffe|heer|ejército|armada|esercito|exército|'
                         r'coast ?guard|garde-côtes|kystvakt|border guard|rajavartio|sécurité civile|civil protection|'
                         r'protezione civile|fire (?:department|service|brigade)|pompiers|feuerwehr|bomberos|'
                         r'ambulance|ambulans\w*|luftrettung|rettungsflugwacht|samu|rega|air zermatt|hems|'
                         r'ministry|ministère|ministerio|ministero)\b', re.I)

JUNK = re.compile(r'\b(video|photos?|watch|report|exclusive|how|why|what|when|breaking|live|opinion|analysis|interview|'
                  r'podcast|news|update|week|today|first|new|nouveau|nouvelle|helicopter market|industry|study|'
                  r'airbus|eurocopter|helicopters? (?:market|industry)|contract|contrat|order|commande|deal)\b', re.I)
MILITARY = re.compile(r'\b(army|navy|air force|armée|marine|armed forces|forces|bundeswehr|heer|luftwaffe|ministry of defen[cs]e|'
                      r'ministère des armées|defen[cs]e|militar|ejército|armada|esercito|aeronautica militare|exército|força aérea)\b', re.I)
GOV = re.compile(r'\b(police|polizei|politi|polis|gendarm|sheriff|state|government|gouvernement|ministry|ministère|'
                 r'coast ?guard|garde[- ]côtes|border|douane|customs|sécurité civile|civil protection|fire|pompiers|'
                 r'carabinieri|guardia|federal|bundes|county|city of)\b', re.I)


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def country_in_text(text):
    t = ' ' + re.sub(r"[^\w\s.-]", ' ', strip_accents(text or '').lower()) + ' '
    for multi in [w for w in WORDS if ' ' in w]:
        if f' {multi} ' in t:
            return WORDS[multi]
    for w in t.split():
        if w in WORDS and w not in ('us',):
            return WORDS[w]
    return None


def operator_ok(op):
    if not op or len(op) < 3 or len(op) > 60:
        return False
    words = op.split()
    if len(words) > 7 or not op[0].isupper() and not op[0].isdigit():
        return False
    if JUNK.search(op) and not GOV.search(op) and not MILITARY.search(op):
        return False
    return bool(re.search(r'[A-Za-zÀ-ÿ]{3}', op))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    from helibase.store import Store
    store = Store()
    queue = store.select('review_queue', 'id,kind,summary,payload,created_at', status='eq.a_verifier')
    press = [q for q in queue if (q.get('payload') or {}).get('origin') == 'presse']
    registry = [q for q in queue if (q.get('payload') or {}).get('origin') != 'presse']
    print(f'{len(queue)} éléments à traiter : {len(press)} de la presse, {len(registry)} des registres')

    operators = store.select('operators', 'id,name,aliases,country')
    by_key = {}
    for o in operators:
        for n in [o['name'], *(o.get('aliases') or [])]:
            by_key.setdefault(name_key(n), []).append(o)
    existing_orders = store.select('orders', 'id,operator_id,model,status,announced_on')

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    decisions = {'valide': [], 'rejete': []}
    groups = {}
    for q in sorted(press, key=lambda q: (q['payload'].get('date') or '')):
        p = q['payload']
        op = (p.get('operator') or '').strip()
        if not operator_ok(op) or not p.get('model') or q['kind'] not in ('livraison', 'commande'):
            decisions['rejete'].append(q['id'])
            continue
        model = re.sub(r'\s+', '', p['model']).upper()
        key = (name_key(op), model, q['kind'])
        d = p.get('date') or q['created_at'][:10]
        g = next((g for g in groups.get(key, []) if abs((dt.date.fromisoformat(d) - dt.date.fromisoformat(g['date'])).days) <= 120), None)
        if g:
            g['ids'].append(q['id'])
            g['links'].append((p.get('source'), p.get('link')))
            g['quantity'] = max(filter(None, [g['quantity'], p.get('quantity')]), default=None)
            g['titles'].append(p.get('title') or '')
        else:
            groups.setdefault(key, []).append({'operator': op, 'model': model, 'kind': q['kind'], 'date': d,
                                               'quantity': p.get('quantity'), 'country': (p.get('country') or '').upper() or None,
                                               'lang': p.get('lang'), 'ids': [q['id']], 'titles': [p.get('title') or ''],
                                               'links': [(p.get('source'), p.get('link'))]})
    events = [g for gs in groups.values() for g in gs]
    print(f'{len(decisions["rejete"])} titres écartés (exploitant illisible), {len(events)} événements distincts '
          f'après fusion des articles en double')

    created_ops = created_orders = 0
    pending = []
    for ev in events:
        known = by_key.get(name_key(ev['operator'])) or []
        wd = None
        country = ev['country'] or (known[0]['country'] if known else None) or country_in_text(ev['operator'])
        if not known:
            try:
                wd = V.wikidata_org(ev['operator'], country)
            except Exception as ex:
                print(f'    Wikidata indisponible : {ex}')
            if wd and not country and len(wd.get('_countries') or []) == 1:
                country = wd['_countries'][0]
        if not country and not known and INSTITUTION.search(ev['operator']):
            country = country_in_text(' '.join(ev['titles']))
        op = next((o for o in known if o.get('country') == country), None) or (known[0] if known and not country else None)
        # Création d'un nouvel exploitant uniquement s'il est confirmé : déjà connu, reconnu par Wikidata
        # comme organisation aéronautique, ou institution identifiable (police, armée, secours…) de pays connu
        confirmed = bool(op) or bool(wd) or (bool(INSTITUTION.search(ev['operator'])) and bool(country))
        if not confirmed:
            pending.extend(ev['ids'])
            print(f"  en attente (exploitant non confirmé) : {ev['operator']} — {ev['model']}")
            continue
        label = f"{ev['kind']:<9} {ev['date']} {ev['operator']} ({country or '?'}) — {ev['quantity'] or ''}{'×' if ev['quantity'] else ''}{ev['model']}"
        print('  ' + label + (f'  [{len(ev["ids"])} articles]' if len(ev['ids']) > 1 else ''))
        if args.dry_run:
            continue
        if not op:
            sector = 'militaire' if MILITARY.search(ev['operator']) else 'etatique' if GOV.search(ev['operator']) \
                or ev['operator'].strip().lower() in WORDS else 'civil'
            try:
                op = store.insert('operators', [{'name': ev['operator'], 'country': country,
                                                 'activities': infer_activities(ev['operator']), 'sector': sector}])[0]
            except Exception:
                found = store.select('operators', 'id,name,aliases,country', name=f'ilike.{ev["operator"]}')
                if not found:
                    raise
                op = found[0]
            by_key.setdefault(name_key(ev['operator']), []).append(op)
            created_ops += 1
        status = 'livree' if ev['kind'] == 'livraison' else 'annoncee'
        dup = any(o['operator_id'] == op['id'] and o['model'] == ev['model'] and o['status'] == status and o.get('announced_on')
                  and abs((dt.date.fromisoformat(o['announced_on']) - dt.date.fromisoformat(ev['date'])).days) <= 120
                  for o in existing_orders)
        if not dup:
            src_name, src_link = ev['links'][0]
            src = store.insert('sources', [{'kind': 'presse', 'label': src_name or 'Presse', 'url': src_link,
                                            'retrieved_on': dt.date.today().isoformat(), 'confidence': 3}])[0]
            row = store.insert('orders', [{'operator_id': op['id'], 'model': ev['model'], 'quantity': ev['quantity'],
                                           'announced_on': ev['date'], 'status': status, 'source_id': src['id']}])[0]
            existing_orders.append(row)
            created_orders += 1
        decisions['valide'].extend(ev['ids'])

    if not args.dry_run:
        for status, ids in decisions.items():
            for i in range(0, len(ids), 100):
                chunk = ids[i:i + 100]
                store.update('review_queue', {'status': status, 'decided_at': now}, id=f'in.({",".join(chunk)})')
        # Appareils de loueurs : classés (l'appareil reste au nom du propriétaire)
        ids = [q['id'] for q in registry]
        for i in range(0, len(ids), 100):
            store.update('review_queue', {'status': 'rejete', 'decided_at': now}, id=f'in.({",".join(ids[i:i + 100])})')
        store.insert('update_log', [{'source': 'Traitement automatique de la file', 'added': created_orders,
                                     'changed': created_ops, 'queued': 0}])
    print(f'Résultat : {created_ops} exploitants créés, {created_orders} livraisons ou commandes ajoutées, '
          f'{len(decisions["valide"])} articles validés, {len(decisions["rejete"])} écartés, '
          f'{len(registry)} appareils de loueurs classés, {len(pending)} laissés « À vérifier » (exploitant non confirmé)')


if __name__ == '__main__':
    main()
