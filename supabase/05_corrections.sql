-- Helibase : corrections après le premier import complet.
-- À exécuter une fois dans Supabase → SQL Editor. Sans danger si relancé.

-- 1. « Neuf ou occasion » peut être inconnu (registres sans année de construction)
alter table public.assignments alter column is_new drop not null;
alter table public.assignments alter column is_new drop default;

-- 2. Canada : le premier import a mal lu le fichier des propriétaires.
--    On efface ce qu'il a créé ; le prochain passage réimporte le Canada correctement.
delete from public.review_queue where source_id in (select id from public.sources where label = 'Registre CCARCS (Canada)');
delete from public.assignments  where source_id in (select id from public.sources where label = 'Registre CCARCS (Canada)');
delete from public.operators o
 where o.country = 'CA'
   and not exists (select 1 from public.assignments a where a.operator_id = o.id);
delete from public.update_log where source = 'Registre CCARCS (Canada)';
delete from public.sources    where label  = 'Registre CCARCS (Canada)';

-- 3. Livraisons relevées dans la presse et validées par l'administrateur (commandes au statut « livrée ») :
--    elles comptent comme « dernière livraison » de l'exploitant, au même titre que les registres.
drop view if exists public.operator_overview;
create view public.operator_overview
with (security_invoker = true) as
select
  o.*,
  coalesce(f.fleet_count, 0) as fleet_count,
  ld.d                       as last_delivery_date,
  ld.model                   as last_delivery_model,
  ld.is_new                  as last_delivery_is_new,
  coalesce(f.models, '{}')   as models
from public.operators o
left join lateral (
  select count(distinct a.helicopter_id) as fleet_count,
         array_remove(array_agg(distinct h.model), null) as models
    from public.assignments a
    join public.helicopters h on h.id = a.helicopter_id
   where a.operator_id = o.id and a.end_date is null
) f on true
left join lateral (
  select x.d, x.model, x.is_new
    from (select a.start_date as d, h.model, a.is_new
            from public.assignments a
            join public.helicopters h on h.id = a.helicopter_id
           where a.operator_id = o.id
          union all
          select r.announced_on, r.model, true
            from public.orders r
           where r.operator_id = o.id and r.status = 'livree') x
   order by x.d desc nulls last
   limit 1
) ld on true;

grant select on public.operator_overview to authenticated;
grant all on public.operator_overview to service_role;
