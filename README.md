# Helibase

Outil de recherche des exploitants d'hélicoptères Airbus livrés depuis 2024.

- `site/` : le site web publié par Cloudflare Pages (dossier de sortie : `site`, aucune commande de build).
- `supabase/` : les scripts SQL de la base de données, à exécuter dans Supabase → SQL Editor.
- `scripts/` : les imports des registres officiels (Python, sans dépendance externe).
- `.github/workflows/` : la mise à jour automatique du lundi et le maintien quotidien de la base.

Secrets GitHub nécessaires (Settings → Secrets and variables → Actions) :
`SUPABASE_URL` et `SUPABASE_SECRET_KEY`.
