-- 0033 — Informe semanal por email a los admins (Hito 12, decisión 083). Una fila por empresa y
-- semana enviada: la misma semana nunca sale dos veces.

create table weekly_reports (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  week_start  date not null,
  recipients  integer not null default 0,
  summary     jsonb not null default '{}',
  sent_at     timestamptz not null default now(),
  unique (tenant_id, week_start)
);

alter table weekly_reports enable row level security;
alter table weekly_reports force row level security;
create policy tenant_isolation on weekly_reports
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on weekly_reports to app_user;
