-- 0034 — Avisos programados (Hito 12, decisión 084). Lo que el sistema se comprometió a avisarle
-- al cliente: la decisión de una aprobación o la respuesta del equipo a un caso derivado. Una
-- promesa a futuro del agente solo vale si hay uno pendiente.

create table scheduled_notices (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  case_id    uuid not null,
  kind       text not null check (kind in ('approval_decision', 'team_reply')),
  reference  text not null default '',
  status     text not null default 'pending' check (status in ('pending', 'sent', 'cancelled')),
  created_at timestamptz not null default now(),
  done_at    timestamptz,
  unique (tenant_id, case_id, kind, reference),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);
create index scheduled_notices_pending_idx on scheduled_notices (tenant_id, case_id)
  where status = 'pending';

alter table scheduled_notices enable row level security;
alter table scheduled_notices force row level security;
create policy tenant_isolation on scheduled_notices
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on scheduled_notices to app_user;
