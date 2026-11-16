"""Pays d'immatriculation déduit du préfixe de l'immatriculation (ex. F-HABC → FR)."""

# Préfixes OACI → code pays ISO à 2 lettres. Les plus longs sont testés en premier.
PREFIXES = {
    # Europe
    'F': 'FR', 'D': 'DE', 'G': 'GB', 'I': 'IT', 'EC': 'ES', 'EM': 'ES', 'OE': 'AT', 'HB': 'CH', 'OY': 'DK',
    'SE': 'SE', 'LN': 'NO', 'OH': 'FI', 'EI': 'IE', 'EJ': 'IE', 'PH': 'NL', 'OO': 'BE', 'LX': 'LU',
    'CS': 'PT', 'SX': 'GR', 'SP': 'PL', 'SN': 'PL', 'OK': 'CZ', 'OM': 'SK', 'HA': 'HU', 'YR': 'RO',
    'LZ': 'BG', 'S5': 'SI', '9A': 'HR', 'ES': 'EE', 'YL': 'LV', 'LY': 'LT', 'TF': 'IS', '5B': 'CY',
    '9H': 'MT', 'ER': 'MD', 'UR': 'UA', 'EW': 'BY', 'YU': 'RS', 'Z3': 'MK', 'ZA': 'AL', '4O': 'ME',
    'E7': 'BA', 'TC': 'TR', 'RA': 'RU', 'RF': 'RU', '3A': 'MC', 'T7': 'SM', 'M': 'IM', '2': 'GG',
    'VP-B': 'BM', 'VQ-B': 'BM', 'ZJ': 'JE',
    # Amériques
    'N': 'US', 'C': 'CA', 'CF': 'CA', 'XA': 'MX', 'XB': 'MX', 'XC': 'MX', 'PP': 'BR', 'PR': 'BR',
    'PT': 'BR', 'PS': 'BR', 'PU': 'BR', 'LV': 'AR', 'LQ': 'AR', 'CC': 'CL', 'HK': 'CO', 'OB': 'PE',
    'HC': 'EC', 'YV': 'VE', 'CP': 'BO', 'CX': 'UY', 'ZP': 'PY', 'TI': 'CR', 'HP': 'PA', 'TG': 'GT',
    'HR': 'HN', 'YN': 'NI', 'YS': 'SV', 'HI': 'DO', 'CU': 'CU', '6Y': 'JM', '9Y': 'TT', 'C6': 'BS',
    'PJ': 'CW', 'P4': 'AW', '8P': 'BB', 'V3': 'BZ', 'PZ': 'SR', '8R': 'GY',
    # Asie, Océanie, Moyen-Orient
    'VH': 'AU', 'ZK': 'NZ', 'JA': 'JP', 'HL': 'KR', 'B': 'CN', 'VT': 'IN', '9M': 'MY', '9V': 'SG',
    'PK': 'ID', 'RP': 'PH', 'HS': 'TH', 'VN': 'VN', 'XU': 'KH', 'RDPL': 'LA', 'XY': 'MM', 'S2': 'BD',
    '4R': 'LK', 'AP': 'PK', '9N': 'NP', 'UP': 'KZ', 'UK': 'UZ', '4K': 'AZ', '4L': 'GE', 'EK': 'AM',
    'A6': 'AE', 'A7': 'QA', 'A9C': 'BH', 'A4O': 'OM', '9K': 'KW', 'HZ': 'SA', '4X': 'IL', 'JY': 'JO',
    'OD': 'LB', 'YK': 'SY', 'YI': 'IQ', 'EP': 'IR', '7O': 'YE', 'P2': 'PG', 'DQ': 'FJ', 'FK': 'NC',
    'F-O': 'PF', 'JU': 'MN', 'P8': 'BN', 'V8': 'BN', '8Q': 'MV',
    # Afrique
    'ZS': 'ZA', 'ZT': 'ZA', 'ZU': 'ZA', 'SU': 'EG', 'CN': 'MA', '7T': 'DZ', 'TS': 'TN', '5A': 'LY',
    '5N': 'NG', '9G': 'GH', '5Y': 'KE', '5H': 'TZ', '5X': 'UG', 'ET': 'ET', '9J': 'ZM', 'Z': 'ZW',
    'A2': 'BW', 'V5': 'NA', 'C9': 'MZ', 'D2': 'AO', '9Q': 'CD', 'TN': 'CG', 'TR': 'GA', 'TJ': 'CM',
    '6V': 'SN', 'TU': 'CI', '5R': 'MG', '3B': 'MU', 'S7': 'SC', '5T': 'MR', 'TZ': 'ML', 'XT': 'BF',
    '5U': 'NE', 'TT': 'TD', 'ST': 'SD', '9XR': 'RW', '7Q': 'MW', '3D': 'SZ', '7P': 'LS', 'D4': 'CV',
}
_SORTED = sorted(PREFIXES, key=len, reverse=True)


def country_from_registration(reg):
    """Code ISO du pays d'immatriculation, ou None si le préfixe est inconnu."""
    r = (reg or '').strip().upper().replace(' ', '')
    if not r:
        return None
    if '-' in r:
        head, tail = r.split('-', 1)
        if head in ('VP', 'VQ') and tail[:1] == 'B':
            return 'BM'
        if head == 'F' and tail[:1] == 'O':
            return 'PF'
        if head == 'A9' and tail[:1] == 'C':
            return 'BH'
        if head == 'A4' and tail[:1] == 'O':
            return 'OM'
        return PREFIXES.get(head)
    if r[0] == 'N' and len(r) > 1 and r[1].isdigit():
        return 'US'
    for p in ('JA', 'HL', 'B'):
        if r.startswith(p) and r[len(p):len(p) + 1].isdigit():
            return PREFIXES[p]
    return None
