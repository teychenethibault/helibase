-- Helibase, version Europe : hélicoptères Airbus NEUFS livrés en Europe depuis 2024.
-- À exécuter UNE SEULE FOIS dans Supabase → SQL Editor (le relancer effacerait à nouveau les données importées).
-- ATTENTION : la partie 3 efface la flotte et les annonces existantes (la base est reconstruite).

-- 1. Nouvelles informations
alter table public.helicopters
  add column if not exists variant            text,      -- désignation exacte (ex. « BK117 D-3m ('H145M') »)
  add column if not exists test_registrations text,      -- immatriculations d'essai usine (F-WW…, D-H…)
  add column if not exists first_seen         date,      -- premier mois où l'appareil est apparu dans nos sources
  add column if not exists est_year           smallint;  -- année de livraison estimée

alter table public.assignments
  add column if not exists status         text not null default 'livre',
  add column if not exists date_precision text;          -- 'jour', 'mois', 'annee_estimee'
do $$ begin
  alter table public.assignments add constraint assignments_status_chk check (status in ('livre', 'prevu'));
exception when duplicate_object then null; end $$;

alter table public.orders
  add column if not exists firmness text;                -- 'ferme', 'loi' (lettre d'intention), 'options'

alter table public.operators
  add column if not exists is_placeholder boolean not null default false;   -- « Exploitant à identifier — pays »

-- Mémoire des sources (empreinte des pages, articles déjà analysés, calibrage des années)
create table if not exists public.source_cache (
  key        text primary key,
  hash       text,
  payload    jsonb,
  fetched_at timestamptz not null default now()
);
alter table public.source_cache enable row level security;      -- aucune lecture par le site
grant all on public.source_cache to service_role;

-- 2. Vue de la liste des exploitants
-- Exploitant déduit (site de l'exploitant, presse, recherche web vérifiée) : méthode et source
alter table public.assignments add column if not exists operator_method text;
alter table public.orders add column if not exists registrations text[];
alter table public.sources drop constraint if exists sources_kind_check;
alter table public.sources add constraint sources_kind_check check (kind in
  ('document_airbus', 'airbus', 'registre', 'helis', 'presse', 'utilisateur', 'site_exploitant', 'recherche_web'));

drop view if exists public.operator_overview;
create view public.operator_overview
with (security_invoker = true) as
select
  o.*,
  coalesce(f.fleet_count, 0)   as fleet_count,
  coalesce(f.planned_count, 0) as planned_count,
  ld.d                         as last_delivery_date,
  ld.model                     as last_delivery_model,
  ld.is_new                    as last_delivery_is_new,
  ld.precision                 as last_delivery_precision,
  coalesce(f.models, '{}')     as models,
  coalesce(y.years, '{}')      as delivery_years
from public.operators o
left join lateral (
  select count(distinct a.helicopter_id) filter (where a.status = 'livre') as fleet_count,
         count(distinct a.helicopter_id) filter (where a.status = 'prevu') as planned_count,
         array_remove(array_agg(distinct h.model), null)                    as models
    from public.assignments a
    join public.helicopters h on h.id = a.helicopter_id
   where a.operator_id = o.id and a.end_date is null
) f on true
left join lateral (
  select x.d, x.model, x.is_new, x.precision
    from (select a.start_date as d, h.model, a.is_new, a.date_precision as precision
            from public.assignments a
            join public.helicopters h on h.id = a.helicopter_id
           where a.operator_id = o.id and a.status = 'livre'
          union all
          select r.announced_on, r.model, true, 'jour'
            from public.orders r
           where r.operator_id = o.id and r.status = 'livree') x
   order by x.d desc nulls last
   limit 1
) ld on true
left join lateral (
  select array_agg(distinct z.y order by z.y) as years
    from (select extract(year from a.start_date)::int as y
            from public.assignments a
           where a.operator_id = o.id and a.status = 'livre' and a.end_date is null and a.start_date is not null
          union
          select extract(year from r.announced_on)::int
            from public.orders r
           where r.operator_id = o.id and r.status = 'livree' and r.announced_on is not null) z
) y on true;
grant select on public.operator_overview to authenticated;
grant all on public.operator_overview to service_role;

-- 3. Remise à zéro : la base est reconstruite pour l'Europe (appareils neufs uniquement).
--    Les exploitants qui portent des notes ou des favoris sont conservés.
delete from public.review_queue;
delete from public.orders;
delete from public.assignments;
delete from public.helicopters;
delete from public.bases;
delete from public.operators o
 where not exists (select 1 from public.notes n where n.operator_id = o.id)
   and not exists (select 1 from public.favorites f where f.operator_id = o.id);
delete from public.sources;
delete from public.source_cache;
