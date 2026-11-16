# Helibase

Outil de recherche des exploitants d'hélicoptères Airbus livrés depuis 2024.

- `site/` : le site web publié par Cloudflare Pages (dossier de sortie : `site`, aucune commande de build).
- `supabase/` : les scripts SQL de la base de données, à exécuter dans Supabase → SQL Editor.
- `scripts/` : les imports des registres officiels (Python, sans dépendance externe).
- `.github/workflows/` : la mise à jour automatique du lundi et le maintien quotidien de la base.

Secrets GitHub nécessaires (Settings → Secrets and variables → Actions) :
`SUPABASE_URL` et `SUPABASE_SECRET_KEY`.

- `scripts/import_registres.py` : registres de France (DGAC, flotte complète), Suisse, Brésil, Pays-Bas, Australie, Nouvelle-Zélande, Norvège, Finlande, Canada, puis base mondiale OpenSky pour les autres pays (Europe comprise).
- `scripts/enrichir.py` : présentation, coordonnées et photo des exploitants (Wikidata/Wikipédia, OpenStreetMap, site de l'exploitant).
- `supabase/04_fiche_exploitant.sql` : colonnes de la fiche exploitant (à exécuter une fois).
- `supabase/05_corrections.sql` : corrections après le premier import (à exécuter une fois).
- `scripts/veille_presse.py` : veille de la presse mondiale (Google Actualités, 6 langues) → Administration › À vérifier.
- `scripts/traiter_file.py` : validation automatique des annonces de presse (doublons fusionnés, pays retrouvé, fiches créées).
- `supabase/06_nettoyage.sql` : nettoyage des erreurs des premiers imports (à exécuter une fois).
- `scripts/helibase/verify.py` : contrôles de fiabilité (même nom, même pays, activité aéronautique, position cohérente).
