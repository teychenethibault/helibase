"""Nettoyage des noms d'exploitants issus des registres."""
import re
import unicodedata

# Formes juridiques retirées pour comparer deux noms
LEGAL_FORMS = r'\b(INC|INCORPORATED|LLC|L L C|LTD|LIMITED|CORP|CORPORATION|CO|COMPANY|PLC|SA|SAS|SARL|GMBH|AG|AS|AB|OY|BV|NV|PTY|PTE|LLP|LP|DBA|THE)\b'

# Propriétaires financiers : l'appareil est exploité par quelqu'un d'autre
FINANCIAL = re.compile(
    r'\b(TRUST|TRUSTEE|BANK|BANCORP|LEASING|LEASE|LESSOR|FINANCE|FINANCIAL|CAPITAL|CREDIT|FUNDING|'
    r'MILESTONE AVIATION|WAYPOINT|LCI|GD HELICOPTER FINANCE|ZEPHYRUS|ROTORTRADE|AIRCRAFT HOLDINGS?)\b', re.I)

# Constructeur et centres d'aménagement : l'appareil n'est pas encore livré à son exploitant
STOCK = re.compile(
    r'\b(AIRBUS HELICOPTERS|AIRBUS HELICOPTER|AMERICAN EUROCOPTER|EUROCOPTER|HELIBRAS|'
    r'METRO AVIATION|DAVENPORT AVIATION|CNC TECHNOLOGIES|'
    r'AIR COMM CORP|SPRINGSHOT|ROTORCRAFT SERVICES GROUP)\b', re.I)

ACTIVITY_RULES = [
    (r'SHERIFF|POLICE|PATROL|PUBLIC SAFETY|LAW ENFORCEMENT|MARSHAL|BORDER|CUSTOMS|CONSTABULARY|GENDARM|POLIZ|POLITI|POLIISI|\bPOLIS|POLICIA|GUARDIA CIVIL|CARABINIERI|RAJAVARTIO|GRENZ|DOUANE|\bZOLL', 'Police et sécurité'),
    (r'\bFIRE\b|CAL FIRE|FORESTRY|FOREST|WILDFIRE|NATURAL RESOURCES|SECURITE CIVILE|FEUERWEHR|PROTEZIONE CIVILE|VIGILI DEL FUOCO|BOMBEROS|BRANDWEER|KATASTROPHENSCHUTZ', 'Sécurité civile et incendie'),
    (r'MEDIC|AIR METHODS|LIFE ?FLIGHT|LIFE ?NET|AIR ?EVAC|CARE ?FLIGHT|AMBULANCE|HEALTH|HOSPITAL|MED ?TRANS|'
     r'AIRMED|RESCUE|LUFTRETTUNG|RETTUNG|AMBULANS|HEMS|AIR CARE|MERCY|SAMU|MEDEVAC|CHRISTOPHORUS|\bREGA\b', 'Secours médical'),
    (r'SEARCH AND RESCUE|\bSAR\b|COAST ?GUARD|KYSTVAKT|REDNING|SALVAMENTO|SAUVETAGE', 'Recherche et sauvetage'),
    (r'OFFSHORE|PETROLEUM|ENERGY|\bOIL\b|\bGAS\b|\bPHI\b|BRISTOW|\bCHC\b|ERA HELICOPTERS', 'Offshore et énergie'),
    (r'TOURS?\b|SIGHTSEEING|SCENIC|GRAND CANYON|MAVERICK|SUNDANCE|PAPILLON|BLUE HAWAIIAN|RUNDFL|TOURIS', 'Tourisme'),
    (r'CHARTER|JET|EXECUTIVE|SHUTTLE|BLADE', 'Transport et charter'),
    (r'NEWS|TELEVISION|BROADCAST|MEDIA|\bTV\b|POWERLINE|POWER LINE|UTILIT|CONSTRUCTION|LOGGING|'
     r'AERIAL|AG ?AVIATION|SURVEY|CINEMA|FILM|TRAVAUX|ARBEITSFLUG|HELILOG', 'Travail aérien'),
    (r'ACADEMY|SCHOOL|TRAINING|UNIVERSITY|COLLEGE|FLIGHT ?SCHOOL|ECOLE|FLUGSCHULE|SCUOLA|ESCUELA', 'Formation'),
]

KEEP_UPPER = {'LLC', 'INC', 'USA', 'US', 'PHI', 'EMS', 'HEMS', 'SAR', 'CHC', 'ERA', 'DPS', 'TX', 'CA', 'NY', 'FL',
              'AZ', 'NV', 'ADAC', 'DRF', 'UK', 'NZ', 'AU', 'II', 'III', 'LP', 'LLP', 'CBP', 'AMO', 'DBA', 'MD'}


def strip_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def name_key(name):
    """Clé de comparaison : majuscules, sans accents, ponctuation ni forme juridique."""
    t = strip_accents((name or '').upper())
    t = t.replace('&', ' AND ').replace("'", '')
    t = re.sub(r'[^A-Z0-9 ]', ' ', t)
    t = re.sub(LEGAL_FORMS, ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def pretty_name(name):
    """« MIAMI-DADE SHERIFFS OFFICE » → « Miami-Dade Sheriffs Office »."""
    raw = re.sub(r'\s+', ' ', (name or '').strip())
    # Retire la forme juridique en fin de nom : « NIAGARA HELICOPTERS INC » → « NIAGARA HELICOPTERS »
    raw = re.sub(r'[ ,]+(INC|INCORPORATED|LLC|L L C|CORP|CORPORATION|CO|PTY\.? LTD|PTY|LTD|LIMITED|LP|LLP|G?GMBH|SAS|SARL|S\.?A\.?|S\.?P\.?A\.?|S\.?R\.?L\.?|S\.?L\.?|B\.?V\.?|N\.?V\.?|OYJ?|AB|ASA|AS|AG)\.?$', '', raw, flags=re.I)
    if not raw:
        return raw
    if raw != raw.upper():          # déjà en casse mixte : on garde tel quel
        return raw

    small = {'OF', 'AND', 'THE', 'DE', 'DU', 'DES', 'LA', 'LE', 'ET', 'FOR'}

    def fix(word, first):
        core = re.sub(r'[^A-Z0-9]', '', word)
        if core in small and not first:
            return word.lower()
        if core in KEEP_UPPER or (len(core) <= 3 and not re.search(r'[AEIOUY]', core)):
            return word
        return '-'.join(p[:1] + p[1:].lower() for p in word.split('-'))
    return ' '.join(fix(w, i == 0) for i, w in enumerate(raw.split(' ')))


def is_financial(owner):
    return bool(FINANCIAL.search(owner or ''))


def is_stock(owner):
    return bool(STOCK.search(owner or ''))


def infer_activities(name):
    up = strip_accents((name or '').upper())
    found = []
    for pattern, activity in ACTIVITY_RULES:
        if re.search(pattern, up) and activity not in found:
            found.append(activity)
    return found
