-- 0018 — CSAT (hito 7, decisión 033): al cerrar un caso, el chat pregunta si se resolvió.

-- Una fila por caso preguntado. `resolved` y `answered_at` quedan vacíos hasta que el cliente
-- responde. El comentario es texto del cliente: se muestra solo en el panel de su empresa.
create table csat_responses (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  case_id     uuid not null,
  asked_at    timestamptz not null default now(),
  resolved    boolean,
  comment     text,
  answered_at timestamptz,
  unique (tenant_id, case_id),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);
create index csat_responses_recientes_idx on csat_responses (tenant_id, asked_at);

alter table csat_responses enable row level security;
alter table csat_responses force row level security;
create policy tenant_isolation on csat_responses
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on csat_responses to app_user;
