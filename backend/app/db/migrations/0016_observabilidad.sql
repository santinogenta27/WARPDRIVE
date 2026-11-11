-- 0016 — Observabilidad (hito 6, decisión 028): lo que la página Métricas necesita y todavía
-- no se guardaba.

-- Qué pasó al conseguir cada respuesta del modelo: 'respaldo' (respondió el proveedor de
-- respaldo), 'recuperada' (llamada con el nombre roto, reparada) o 'rechazo'.
alter table usage add column events text[] not null default '{}';
create index usage_por_dia_idx on usage (tenant_id, created_at);

-- Errores que cortan un turno. El turno se revierte entero, así que el error se guarda aparte,
-- en su propia transacción. Sin texto del cliente: solo el tipo de error.
create table error_events (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  kind       text not null,
  detail     text,
  created_at timestamptz not null default now()
);
create index error_events_recientes_idx on error_events (tenant_id, created_at);

alter table error_events enable row level security;
alter table error_events force row level security;
create policy tenant_isolation on error_events
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert on error_events to app_user;

-- Historial de evals por versión de prompt y modelo.
alter table eval_runs add column mode text;
alter table eval_runs add column prompt_version text;
alter table eval_runs add column model text;
