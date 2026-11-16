"""Import du registre américain (FAA) dans Helibase.

Le registre complet est publié chaque nuit dans ReleasableAircraft.zip :
  - ACFTREF.txt : constructeur et modèle de chaque code avion
  - MASTER.txt  : une ligne par aéronef immatriculé

Utilisation :
  python scripts/import_faa.py                 # télécharge le registre et met la base à jour
  python scripts/import_faa.py --zip fichier.zip --dry-run   # test sans écrire dans la base
"""
import argparse
import csv
import io
import os
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from helibase.ingest import ingest  # noqa: E402
from helibase.models import map_model  # noqa: E402

FAA_URL = 'https://registry.faa.gov/database/ReleasableAircraft.zip'
BROWSER_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'


def download(url=FAA_URL):
    req = urllib.request.Request(url, headers={'User-Agent': BROWSER_UA, 'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()


def _rows(zf, name):
    member = next(n for n in zf.namelist() if n.upper().endswith(name))
    text = zf.read(member).decode('utf-8-sig', errors='replace')
    reader = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(reader)]
    for values in reader:
        yield {h: (values[i].strip() if i < len(values) else '') for i, h in enumerate(header) if h}


def _date(yyyymmdd):
    v = (yyyymmdd or '').strip().replace('/', '')
    return f'{v[:4]}-{v[4:6]}-{v[6:8]}' if len(v) == 8 and v.isdigit() else None


def parse(zip_bytes):
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    # Codes des modèles d'hélicoptères Airbus (TYPE-ACFT 6 = giravion)
    models = {}
    for ref in _rows(zf, 'ACFTREF.TXT'):
        if ref.get('TYPE-ACFT') != '6':
            continue
        if map_model(ref.get('MFR'), ref.get('MODEL')):
            models[ref['CODE']] = (ref['MFR'], ref['MODEL'])
    print(f'{len(models)} codes de modèles Airbus Helicopters dans la table de référence FAA')

    records = []
    for m in _rows(zf, 'MASTER.TXT'):
        ref = models.get(m.get('MFR MDL CODE'))
        if not ref:
            continue
        records.append({
            'registration': 'N' + m.get('N-NUMBER', ''),
            'msn': m.get('SERIAL NUMBER'),
            'manufacturer': ref[0],
            'registry_model': ref[1],
            'year_built': m.get('YEAR MFR'),
            'owner': m.get('NAME'),
            'city': m.get('CITY'),
            'region': m.get('STATE'),
            'country': 'US',
            'registered_on': _date(m.get('CERT ISSUE DATE')),
            'government': m.get('TYPE REGISTRANT') == '5',
        })
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', help='fichier ReleasableAircraft.zip déjà téléchargé')
    parser.add_argument('--dry-run', action='store_true', help='affiche le résultat sans écrire dans la base')
    parser.add_argument('--since', default='2024-01-01')
    args = parser.parse_args()

    data = open(args.zip, 'rb').read() if args.zip else download()
    print(f'Registre FAA : {len(data) / 1e6:.1f} Mo')
    records = parse(data)
    print(f'{len(records)} hélicoptères Airbus immatriculés aux États-Unis')

    if args.dry_run:
        recent = [r for r in records if (r['registered_on'] or '') >= args.since]
        for r in sorted(recent, key=lambda r: r['registered_on'])[-15:]:
            print(f"  {r['registered_on']}  {r['registration']:<8} {r['registry_model']:<20} n° {r['msn']:<6} {r['owner']}")
        print(f'{len(recent)} immatriculés depuis {args.since} (mode test : rien n\'a été écrit)')
        return

    from helibase.store import Store
    ingest(Store(), records, 'Registre FAA (États-Unis)', 'https://registry.faa.gov/aircraftinquiry', since=args.since)


if __name__ == '__main__':
    main()
