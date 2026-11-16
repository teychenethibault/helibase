"""Périmètre européen de Helibase (Russie et Biélorussie exclues)."""

# Code ISO → (nom, latitude, longitude du centre approximatif, pour placer les exploitants à identifier)
EUROPE = {
    'AL': ('Albanie', 41.15, 20.17), 'AD': ('Andorre', 42.55, 1.6), 'AT': ('Autriche', 47.6, 14.1),
    'BA': ('Bosnie-Herzégovine', 44.2, 17.8), 'BE': ('Belgique', 50.6, 4.6), 'BG': ('Bulgarie', 42.7, 25.3),
    'CH': ('Suisse', 46.8, 8.2), 'CY': ('Chypre', 35.1, 33.4), 'CZ': ('Tchéquie', 49.8, 15.5),
    'DE': ('Allemagne', 51.2, 10.4), 'DK': ('Danemark', 56.0, 9.5), 'EE': ('Estonie', 58.7, 25.5),
    'ES': ('Espagne', 40.2, -3.6), 'FI': ('Finlande', 62.5, 26.0), 'FO': ('Îles Féroé', 62.0, -6.9),
    'FR': ('France', 46.6, 2.4), 'GB': ('Royaume-Uni', 53.5, -1.9), 'GG': ('Guernesey', 49.45, -2.58),
    'GI': ('Gibraltar', 36.14, -5.35), 'GR': ('Grèce', 39.3, 22.0), 'HR': ('Croatie', 45.3, 16.0),
    'HU': ('Hongrie', 47.1, 19.4), 'IE': ('Irlande', 53.2, -8.0), 'IM': ('Île de Man', 54.23, -4.55),
    'IS': ('Islande', 64.9, -18.6), 'IT': ('Italie', 42.8, 12.6), 'JE': ('Jersey', 49.21, -2.13),
    'LI': ('Liechtenstein', 47.16, 9.55), 'LT': ('Lituanie', 55.3, 23.9), 'LU': ('Luxembourg', 49.8, 6.1),
    'LV': ('Lettonie', 56.9, 24.8), 'MC': ('Monaco', 43.74, 7.42), 'MD': ('Moldavie', 47.2, 28.5),
    'ME': ('Monténégro', 42.8, 19.3), 'MK': ('Macédoine du Nord', 41.6, 21.7), 'MT': ('Malte', 35.9, 14.4),
    'NL': ('Pays-Bas', 52.2, 5.5), 'NO': ('Norvège', 61.5, 9.0), 'PL': ('Pologne', 52.1, 19.4),
    'PT': ('Portugal', 39.6, -8.0), 'RO': ('Roumanie', 45.9, 24.9), 'RS': ('Serbie', 44.0, 20.9),
    'SE': ('Suède', 62.0, 15.0), 'SI': ('Slovénie', 46.1, 14.8), 'SK': ('Slovaquie', 48.7, 19.7),
    'SM': ('Saint-Marin', 43.94, 12.46), 'TR': ('Turquie', 39.0, 35.2), 'UA': ('Ukraine', 49.0, 31.4),
    'VA': ('Vatican', 41.9, 12.45), 'XK': ('Kosovo', 42.6, 20.9),
}


def is_europe(iso):
    return (iso or '').upper() in EUROPE


def country_name(iso):
    return EUROPE.get((iso or '').upper(), (iso or '?',))[0]
