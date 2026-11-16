"""Reconnaissance des hélicoptères Airbus (et de leurs prédécesseurs) à partir des
désignations des registres officiels, et traduction en nom commercial.

Chaque règle donne :
  - la famille de numérotation (les numéros de série sont uniques à l'intérieur d'une famille),
  - le nom commercial affiché dans Helibase.
"""
import re

# Constructeurs acceptés (le nom du constructeur dans le registre doit en contenir un)
MANUFACTURERS = ('AIRBUS', 'EUROCOPTER', 'AEROSPATIALE', 'MBB', 'MESSERSCHMITT')


def _norm(text):
    return re.sub(r'[^A-Z0-9]', '', (text or '').upper())


def _year(year):
    try:
        return int(str(year).strip()[:4])
    except (TypeError, ValueError):
        return None


def map_model(manufacturer, model, year_built=None):
    """Renvoie (famille, nom_commercial) ou None si l'appareil n'est pas un Airbus Helicopters."""
    mfr = (manufacturer or '').upper()
    if not any(m in mfr for m in MANUFACTURERS):
        return None
    m = _norm(model)
    y = _year(year_built) or 0

    # Écureuil : AS350 / H125, AS355, EC130 / H130 (numérotation commune)
    if m.startswith(('H125', 'AS350B3', 'AS350B3E')):
        return 'Ecureuil', 'H125' if y >= 2014 or m.startswith('H125') else 'AS350 B3'
    if m.startswith('AS350'):
        return 'Ecureuil', 'AS350 ' + m[5:7].strip() if len(m) > 5 else 'AS350'
    if m.startswith('AS355'):
        return 'Ecureuil', 'AS355'
    if m.startswith(('H130', 'EC130T2')):
        return 'Ecureuil', 'H130'
    if m.startswith('EC130'):
        return 'Ecureuil', 'EC130 B4'
    if m.startswith(('EC120', 'H120')):
        return 'EC120', 'H120' if y >= 2015 else 'EC120'

    # H135 / EC135
    if m.startswith(('H135', 'EC135', 'EC635', 'H135M')):
        return 'H135', 'H135' if y >= 2015 or m.startswith('H135') else 'EC135'
    # H140
    if m.startswith('H140'):
        return 'H140', 'H140'

    # H145 / EC145 / BK117 (MBB-BK117 D-2, D-3, C-2…)
    if 'BK117' in m or m.startswith(('H145', 'EC145')):
        if 'D3' in m or m.startswith('H145D3'):
            return 'BK117', 'H145 D3'
        if 'D2' in m or m.startswith('H145') or 'EC145T2' in m:
            return 'BK117', 'H145'
        if 'C2' in m or m.startswith('EC145'):
            return 'BK117', 'EC145'
        return 'BK117', 'BK117'

    # Dauphin / H155 / H160
    if m.startswith(('H160', 'EC160')):
        return 'H160', 'H160'
    if m.startswith(('EC155', 'H155')):
        return 'Dauphin', 'H155' if y >= 2015 else 'EC155'
    if m.startswith(('AS365', 'SA365', 'AS565', 'HH65', 'MH65')):
        return 'Dauphin', 'AS365 Dauphin'

    # H175
    if m.startswith(('EC175', 'H175')):
        return 'H175', 'H175'

    # Super Puma : H215 / H225 / AS332 / EC225
    if m.startswith(('EC225', 'H225')):
        return 'Super Puma', 'H225'
    if m.startswith(('AS332', 'H215')):
        return 'Super Puma', 'H215' if y >= 2015 else 'AS332 Super Puma'
    if m.startswith(('SA330', 'AS330')):
        return 'Puma', 'SA330 Puma'

    # Anciens modèles encore revendus
    if m.startswith(('BO105', 'BO105')):
        return 'BO105', 'BO105'
    if m.startswith(('SA341', 'SA342')):
        return 'Gazelle', 'Gazelle'
    if m.startswith('SA316') or m.startswith('SA319'):
        return 'Alouette', 'Alouette III'
    if m.startswith(('SA315', 'SA318', 'SE3130', 'SE313')):
        return 'Alouette', 'Alouette / Lama'
    return None


# Codes de type OACI (utilisés par les bases de transpondeurs) → modèle générique
TYPECODES = {
    'EC20': 'EC120', 'AS50': 'AS350B3', 'AS55': 'AS355', 'EC30': 'EC130', 'EC35': 'EC135',
    'EC45': 'EC145', 'BK17': 'BK117', 'H160': 'H160', 'EC55': 'EC155', 'AS65': 'AS365',
    'EC75': 'H175', 'EC25': 'EC225', 'AS32': 'AS332', 'H140': 'H140', 'B105': 'BO105',
    'GAZL': 'SA341', 'ALO3': 'SA316', 'ALO2': 'SA315', 'LAMA': 'SA315', 'PUMA': 'SA330',
}


def map_any(manufacturer, model, year_built=None, typecode=None):
    """Comme map_model, avec repli sur le code OACI quand la désignation est absente ou inconnue."""
    found = map_model(manufacturer, model, year_built)
    if found:
        return found
    generic = TYPECODES.get((typecode or '').strip().upper())
    if generic:
        y = _year(year_built) or 0
        if generic == 'EC145':
            return 'BK117', 'H145' if y >= 2014 else 'EC145'
        return map_model('AIRBUS', generic, year_built)
    return None
