-- 0010 — Sesiones del chat web (tarea 12b): sin login, cada sesión es un contacto del tenant y
-- tiene un límite de mensajes. El token de la sesión no se guarda: solo su hash.

create table web_sessions (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  token_hash  text not null unique,
  messages    integer not null default 0,
  created_at  timestamptz not null default clock_timestamp(),
  unique (tenant_id, id)
);

alter table web_sessions enable row level security;
alter table web_sessions force row level security;
create policy tenant_isolation on web_sessions
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on web_sessions to app_user;
