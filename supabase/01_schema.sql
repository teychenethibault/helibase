-- =====================================================================
-- HELIBASE — Étape 1 : création de la base de données
-- À coller dans Supabase → SQL Editor → New query, puis « Run ».
-- Le script peut être relancé sans danger : il ne recrée que ce qui manque.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. Réglage : l'adresse e-mail de l'administrateur principal
--    (le premier compte créé avec cette adresse devient administrateur)
-- ---------------------------------------------------------------------
create table if not exists public.app_settings (
  key   text primary key,
  value text not null
);
insert into public.app_settings (key, value)
values ('admin_email', 'teychene.thibault@gmail.com')
on conflict (key) do nothing;

-- ---------------------------------------------------------------------
-- 1. Comptes utilisateurs
-- ---------------------------------------------------------------------
create table if not exists public.profiles (
  id           uuid primary key references auth.users (id) on delete cascade,
  email        text not null,
  display_name text,
  role         text not null default 'user'   check (role in ('admin', 'user')),
  status       text not null default 'active' check (status in ('active', 'revoked')),
  created_at   timestamptz not null default now(),
  notified_at  timestamptz            -- date d'envoi de l'e-mail à l'administrateur
);

-- Création automatique du profil à la création d'un compte
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email, display_name, role)
  values (
    new.id,
    new.email,
    split_part(new.email, '@', 1),
    case
      when lower(new.email) = (select lower(value) from public.app_settings where key = 'admin_email')
      then 'admin' else 'user'
    end
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Fonctions d'aide pour les règles d'accès
create or replace function public.is_active_user()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and status = 'active'
  );
$$;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and status = 'active' and role = 'admin'
  );
$$;

-- Révocation / réactivation d'un compte, réservée à l'administrateur
create or replace function public.set_user_status(target uuid, new_status text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Réservé à l''administrateur';
  end if;
  if new_status not in ('active', 'revoked') then
    raise exception 'Statut inconnu : %', new_status;
  end if;
  if target = auth.uid() then
    raise exception 'Vous ne pouvez pas modifier votre propre compte';
  end if;
  update public.profiles set status = new_status where id = target;
end;
$$;

-- Nomination d'un autre administrateur
create or replace function public.set_user_role(target uuid, new_role text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Réservé à l''administrateur';
  end if;
  if new_role not in ('admin', 'user') then
    raise exception 'Rôle inconnu : %', new_role;
  end if;
  if target = auth.uid() then
    raise exception 'Vous ne pouvez pas modifier votre propre rôle';
  end if;
  update public.profiles set role = new_role where id = target;
end;
$$;

-- ---------------------------------------------------------------------
-- 2. Sources (d'où vient chaque information)
-- ---------------------------------------------------------------------
create table if not exists public.sources (
  id           uuid primary key default gen_random_uuid(),
  kind         text not null check (kind in
                 ('document_airbus', 'airbus', 'registre', 'helis', 'presse', 'utilisateur')),
  label        text not null,          -- ex. « Registre FAA », « Communiqué Airbus »
  url          text,
  retrieved_on date not null default current_date,
  confidence   smallint not null default 3 check (confidence between 1 and 5),
  created_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 3. Exploitants et bases
-- ---------------------------------------------------------------------
create table if not exists public.operators (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  aliases     text[] not null default '{}',
  country     text,                     -- code pays ISO à 2 lettres, ex. FR
  city        text,
  lat         double precision,
  lon         double precision,
  website     text,
  activities  text[] not null default '{}' check (activities <@ array[
                'Secours médical', 'Police et sécurité', 'Sécurité civile et incendie',
                'Recherche et sauvetage', 'Offshore et énergie', 'Tourisme',
                'Transport et charter', 'Affaires et VIP', 'Travail aérien',
                'Formation', 'Militaire']::text[]),
  sector      text not null default 'civil' check (sector in ('civil', 'etatique', 'militaire')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create unique index if not exists operators_name_country_uq
  on public.operators (lower(name), coalesce(country, ''));
create index if not exists operators_country_idx on public.operators (country);

create table if not exists public.bases (
  id          uuid primary key default gen_random_uuid(),
  operator_id uuid not null references public.operators (id) on delete cascade,
  name        text not null,
  country     text,
  lat         double precision,
  lon         double precision,
  created_at  timestamptz not null default now()
);
create index if not exists bases_operator_idx on public.bases (operator_id);

-- ---------------------------------------------------------------------
-- 4. Hélicoptères et affectations (historique : neuf, puis reventes)
-- ---------------------------------------------------------------------
create table if not exists public.helicopters (
  id             uuid primary key default gen_random_uuid(),
  family         text not null,   -- famille de numérotation : Ecureuil, H135, H145, H160, H175, Dauphin, Super Puma, NH90, Tigre…
  msn            text not null,   -- numéro de série constructeur
  model          text not null,   -- nom commercial : H125, H145 D3…
  registry_model text,            -- désignation dans les registres : AS350B3, MBB-BK117 D-3…
  year_built     smallint,
  created_at     timestamptz not null default now(),
  unique (family, msn)
);

create table if not exists public.assignments (
  id            uuid primary key default gen_random_uuid(),
  helicopter_id uuid not null references public.helicopters (id) on delete cascade,
  operator_id   uuid references public.operators (id) on delete set null,
  owner_name    text,             -- propriétaire ou loueur, s'il diffère de l'exploitant
  registration  text,             -- immatriculation, ex. N306MP
  start_date    date,             -- livraison ou début d'exploitation
  end_date      date,             -- vide = toujours en service chez cet exploitant
  is_new        boolean not null default true,  -- livré neuf (true) ou d'occasion (false)
  source_id     uuid references public.sources (id) on delete set null,
  created_at    timestamptz not null default now()
);
create index if not exists assignments_operator_idx   on public.assignments (operator_id);
create index if not exists assignments_helicopter_idx on public.assignments (helicopter_id);
create index if not exists assignments_start_idx      on public.assignments (start_date desc);

-- ---------------------------------------------------------------------
-- 5. Commandes annoncées (onglet « Livraisons à venir »)
-- ---------------------------------------------------------------------
create table if not exists public.orders (
  id           uuid primary key default gen_random_uuid(),
  operator_id  uuid not null references public.operators (id) on delete cascade,
  model        text not null,
  quantity     smallint check (quantity > 0),
  announced_on date,
  expected     text,              -- échéance annoncée, en clair : « 2027 », « fin 2026 »…
  status       text not null default 'annoncee' check (status in ('annoncee', 'en_livraison', 'livree')),
  source_id    uuid references public.sources (id) on delete set null,
  created_at   timestamptz not null default now()
);
create index if not exists orders_operator_idx on public.orders (operator_id);

-- ---------------------------------------------------------------------
-- 6. Notes partagées sous la fiche d'un exploitant
-- ---------------------------------------------------------------------
create table if not exists public.notes (
  id          uuid primary key default gen_random_uuid(),
  operator_id uuid not null references public.operators (id) on delete cascade,
  author_id   uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  body        text not null check (length(trim(body)) > 0),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists notes_operator_idx on public.notes (operator_id, created_at desc);

-- ---------------------------------------------------------------------
-- 7. Favoris (propres à chaque utilisateur)
-- ---------------------------------------------------------------------
create table if not exists public.favorites (
  user_id     uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  operator_id uuid not null references public.operators (id) on delete cascade,
  created_at  timestamptz not null default now(),
  primary key (user_id, operator_id)
);

-- ---------------------------------------------------------------------
-- 8. File « à vérifier » et journal des mises à jour
-- ---------------------------------------------------------------------
create table if not exists public.review_queue (
  id           uuid primary key default gen_random_uuid(),
  kind         text not null check (kind in ('livraison', 'commande', 'conflit', 'correction')),
  summary      text not null,       -- phrase lisible : « H145 D3 MSN 21391 → Avincis (Suède) »
  payload      jsonb not null default '{}',
  source_id    uuid references public.sources (id) on delete set null,
  proposed_by  uuid references public.profiles (id) on delete set null,
  status       text not null default 'a_verifier' check (status in ('a_verifier', 'valide', 'rejete')),
  created_at   timestamptz not null default now(),
  decided_by   uuid references public.profiles (id) on delete set null,
  decided_at   timestamptz
);
create index if not exists review_queue_status_idx on public.review_queue (status, created_at desc);

create table if not exists public.update_log (
  id        uuid primary key default gen_random_uuid(),
  run_at    timestamptz not null default now(),
  source    text not null,
  added     integer not null default 0,
  changed   integer not null default 0,
  queued    integer not null default 0,
  error     text
);

-- ---------------------------------------------------------------------
-- 9. Mise à jour automatique des dates « modifié le »
-- ---------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists operators_touch on public.operators;
create trigger operators_touch before update on public.operators
  for each row execute function public.touch_updated_at();

drop trigger if exists notes_touch on public.notes;
create trigger notes_touch before update on public.notes
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------
-- 10. Vue de synthèse : une ligne par exploitant
--     (flotte en service, dernière livraison)
-- ---------------------------------------------------------------------
create or replace view public.operator_overview
with (security_invoker = true) as
select
  o.*,
  count(distinct a.helicopter_id) filter (where a.end_date is null) as fleet_count,
  max(a.start_date)                                                   as last_delivery_date,
  (select h.model
     from public.assignments a2
     join public.helicopters h on h.id = a2.helicopter_id
    where a2.operator_id = o.id
    order by a2.start_date desc nulls last
    limit 1)                                                          as last_delivery_model,
  (select a3.is_new
     from public.assignments a3
    where a3.operator_id = o.id
    order by a3.start_date desc nulls last
    limit 1)                                                          as last_delivery_is_new,
  array_remove(array_agg(distinct h2.model), null)                    as models
from public.operators o
left join public.assignments a  on a.operator_id = o.id
left join public.helicopters h2 on h2.id = a.helicopter_id and a.end_date is null
group by o.id;

-- ---------------------------------------------------------------------
-- 11. Règles d'accès (Row Level Security)
--     Principe : seul un compte actif voit les données ;
--     les mises à jour automatiques passent par la clé secrète (hors règles).
-- ---------------------------------------------------------------------
alter table public.app_settings enable row level security;
alter table public.profiles     enable row level security;
alter table public.sources      enable row level security;
alter table public.operators    enable row level security;
alter table public.bases        enable row level security;
alter table public.helicopters  enable row level security;
alter table public.assignments  enable row level security;
alter table public.orders       enable row level security;
alter table public.notes        enable row level security;
alter table public.favorites    enable row level security;
alter table public.review_queue enable row level security;
alter table public.update_log   enable row level security;

-- Profils : chacun voit les comptes actifs de l'équipe (pour signer les notes),
-- l'administrateur voit tout ; chacun peut changer son nom affiché.
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles for select to authenticated
  using (id = auth.uid() or public.is_admin() or (public.is_active_user() and status = 'active'));

drop policy if exists profiles_update_self on public.profiles;
create policy profiles_update_self on public.profiles for update to authenticated
  using (id = auth.uid()) with check (id = auth.uid());

-- Données de référence : lecture pour tout compte actif, écriture par l'administrateur
do $$
declare t text;
begin
  foreach t in array array['sources', 'operators', 'bases', 'helicopters', 'assignments', 'orders', 'update_log'] loop
    execute format('drop policy if exists %1$s_read on public.%1$s', t);
    execute format('create policy %1$s_read on public.%1$s for select to authenticated using (public.is_active_user())', t);
    execute format('drop policy if exists %1$s_admin on public.%1$s', t);
    execute format('create policy %1$s_admin on public.%1$s for all to authenticated using (public.is_admin()) with check (public.is_admin())', t);
  end loop;
end $$;

-- Notes : tout compte actif lit et écrit ; chacun modifie ses propres notes ;
-- l'administrateur peut supprimer n'importe quelle note.
drop policy if exists notes_read on public.notes;
create policy notes_read on public.notes for select to authenticated
  using (public.is_active_user());
drop policy if exists notes_insert on public.notes;
create policy notes_insert on public.notes for insert to authenticated
  with check (public.is_active_user() and author_id = auth.uid());
drop policy if exists notes_update on public.notes;
create policy notes_update on public.notes for update to authenticated
  using (public.is_active_user() and author_id = auth.uid())
  with check (author_id = auth.uid());
drop policy if exists notes_delete on public.notes;
create policy notes_delete on public.notes for delete to authenticated
  using (public.is_active_user() and (author_id = auth.uid() or public.is_admin()));

-- Favoris : chacun ne voit et ne gère que les siens
drop policy if exists favorites_own on public.favorites;
create policy favorites_own on public.favorites for all to authenticated
  using (public.is_active_user() and user_id = auth.uid())
  with check (public.is_active_user() and user_id = auth.uid());

-- File « à vérifier » : l'administrateur gère tout ;
-- un compte actif peut proposer une correction et voir les siennes.
drop policy if exists review_admin on public.review_queue;
create policy review_admin on public.review_queue for all to authenticated
  using (public.is_admin()) with check (public.is_admin());
drop policy if exists review_propose on public.review_queue;
create policy review_propose on public.review_queue for insert to authenticated
  with check (public.is_active_user() and kind = 'correction' and proposed_by = auth.uid() and status = 'a_verifier');
drop policy if exists review_own on public.review_queue;
create policy review_own on public.review_queue for select to authenticated
  using (proposed_by = auth.uid());

-- app_settings : aucune règle = invisible depuis le site (accès par la clé secrète uniquement)

-- ---------------------------------------------------------------------
-- 12. Droits d'accès à l'API (les nouvelles tables ne sont pas exposées par défaut)
-- ---------------------------------------------------------------------
grant usage on schema public to authenticated;

grant select on public.sources, public.operators, public.bases, public.helicopters,
                public.assignments, public.orders, public.update_log,
                public.operator_overview
  to authenticated;
grant insert, update, delete on public.sources, public.operators, public.bases,
                public.helicopters, public.assignments, public.orders
  to authenticated;                       -- filtré par les règles : administrateur seulement

grant select, insert, delete on public.notes, public.favorites to authenticated;
grant update (body) on public.notes to authenticated;

grant select, insert, update on public.review_queue to authenticated;

grant select on public.profiles to authenticated;
grant update (display_name) on public.profiles to authenticated;

grant execute on function public.is_active_user(), public.is_admin(),
                 public.set_user_status(uuid, text), public.set_user_role(uuid, text)
  to authenticated;

-- Les visiteurs non connectés n'ont accès à rien
revoke all on all tables in schema public from anon;

-- =====================================================================
-- Fin du script. Vérification : Table Editor doit lister 12 tables
-- (app_settings, assignments, bases, favorites, helicopters, notes,
--  operators, orders, profiles, review_queue, sources, update_log).
-- =====================================================================
