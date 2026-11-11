-- 0015 — Empresas con usuario propio y conexión con su sistema (hito 6, decisión 026).

-- Login del panel por empresa: el usuario tiene su clave (hash scrypt). Un email con clave
-- pertenece a una sola empresa, así el login sabe a cuál entra.
alter table users add column password_hash text;
create unique index users_login_email_idx on users (lower(email))
  where password_hash is not null;

-- Conexión con el sistema real de la empresa: su OpenAPI, la URL base y las credenciales
-- (cifradas con una clave derivada por empresa). `draft`: cargada; `tested`: la prueba de
-- conexión pasó; `active`: el agente la usa. Hasta entonces las herramientas responden en modo
-- simulado, sin tocar el sistema.
create table system_connections (
  tenant_id       uuid primary key references tenants (id),
  openapi         jsonb,
  base_url        text,
  auth_kind       text not null default 'none'
                  check (auth_kind in ('none', 'bearer', 'api_key', 'basic')),
  auth_header     text,
  credentials_enc text,
  status          text not null default 'draft' check (status in ('draft', 'tested', 'active')),
  last_test       jsonb,
  tested_at       timestamptz,
  activated_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

alter table system_connections enable row level security;
alter table system_connections force row level security;
create policy tenant_isolation on system_connections
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on system_connections to app_user;

-- Fuentes del onboarding: nombre visible y resumen de lo que se ingirió de cada archivo.
alter table sources add column summary jsonb;

-- Corrida del onboarding lanzada desde el panel: quién la pidió y el último error.
alter table onboarding_runs add column requested_by uuid;
