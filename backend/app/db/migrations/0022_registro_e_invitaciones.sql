-- 0022 — Registro self-service e invitaciones con roles (hito 9, decisión 058).

-- Un registro pendiente: la empresa todavía no existe, así que no tiene tenant_id (como
-- operator_audit, es de la plataforma). Al confirmar el email se crea la empresa y se borra.
-- Solo se guarda el hash del enlace y el de la clave elegida.
create table signups (
  id            uuid primary key default gen_random_uuid(),
  company       text not null,
  email         text not null,
  password_hash text not null,
  token_hash    text not null unique,
  expires_at    timestamptz not null,
  created_at    timestamptz not null default now()
);
create index signups_email_idx on signups (lower(email));
revoke all on signups from app_user;

-- Una invitación a sumarse al panel de una empresa, con su rol: admin (`owner`) o persona del
-- equipo (`agent`). Se acepta una sola vez, eligiendo la clave.
create table invitations (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  email       text not null,
  role        text not null check (role in ('owner', 'agent')),
  token_hash  text not null unique,
  expires_at  timestamptz not null,
  accepted_at timestamptz,
  created_at  timestamptz not null default now(),
  unique (tenant_id, id)
);

alter table invitations enable row level security;
alter table invitations force row level security;
create policy tenant_isolation on invitations
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update, delete on invitations to app_user;
