-- =====================================================================
-- HELIBASE — Mise à jour : comptes avec mot de passe, suivi par l'administrateur
-- À coller dans Supabase → SQL Editor → New query, puis « Run ».
-- =====================================================================

-- 1. Le nom saisi à la création du compte devient le nom affiché
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
    coalesce(nullif(trim(new.raw_user_meta_data ->> 'display_name'), ''), split_part(new.email, '@', 1)),
    case
      when lower(new.email) = (select lower(value) from public.app_settings where key = 'admin_email')
      then 'admin' else 'user'
    end
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

-- 2. Liste des comptes pour la page Administration, avec la dernière connexion
create or replace function public.admin_list_users()
returns table (
  id uuid, email text, display_name text, role text, status text,
  created_at timestamptz, last_sign_in_at timestamptz
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Réservé à l''administrateur';
  end if;
  return query
    select p.id, p.email, p.display_name, p.role, p.status, p.created_at, u.last_sign_in_at
    from public.profiles p
    left join auth.users u on u.id = p.id
    order by p.created_at desc;
end;
$$;

revoke all on function public.admin_list_users() from public, anon;
grant execute on function public.admin_list_users() to authenticated;
