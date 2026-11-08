-- 0004 — Historial de mensajes por caso, resumen de derivación y configuración por tenant.

create table case_messages (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  case_id    uuid not null,
  role       text not null check (role in ('customer', 'agent', 'human')),
  text       text not null,
  -- clock_timestamp(): now() es fijo dentro de una transacción y no ordenaría los mensajes.
  created_at timestamptz not null default clock_timestamp(),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);
create index case_messages_case_idx on case_messages (tenant_id, case_id, created_at);

alter table case_messages enable row level security;
alter table case_messages force row level security;
create policy tenant_isolation on case_messages
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());

-- Resumen que ve el humano cuando el agente deriva (intención, datos verificados, pasos, motivo).
alter table cases add column escalation jsonb;

-- Configuración del tenant: tono, límites de monto, etc.
alter table tenants add column settings jsonb not null default '{}';
