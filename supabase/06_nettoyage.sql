-- Helibase : nettoyage des erreurs des premiers imports.
-- À exécuter une fois dans Supabase → SQL Editor, AVANT de relancer la mise à jour. Sans danger si relancé.

-- 1. Exploitants sans aucun appareil, aucune livraison, aucune note et aucun favori :
--    créés par un import interrompu. Ils seront recréés proprement, avec leurs appareils.
delete from public.operators o
 where not exists (select 1 from public.assignments a where a.operator_id = o.id)
   and not exists (select 1 from public.orders r     where r.operator_id = o.id)
   and not exists (select 1 from public.notes n      where n.operator_id = o.id)
   and not exists (select 1 from public.favorites f  where f.operator_id = o.id);

-- 2. Positions placées par l'ancienne recherche d'informations (parfois dans la mauvaise ville) :
--    effacées pour être recalculées à partir de la ville du registre.
update public.operators
   set lat = null, lon = null
 where info_edited_at is null
   and enriched_at is not null;

-- 3. Informations trouvées automatiquement avec l'ancienne méthode (parfois sans rapport) :
--    effacées. Ce qui a été saisi à la main (bouton « Modifier ») est conservé.
update public.operators
   set summary = null, summary_source = null, website = null, phone = null, email = null,
       photo_url = null, photo_credit = null, wikidata_id = null, enriched_at = null,
       address = case when country in ('FR', 'CH') then address else null end
 where info_edited_at is null;
