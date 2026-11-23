-- 0031 — Simulaciones por empresa (Hito 12, decisión 080). Cada pedido de "Simular 20
-- conversaciones": sus clientes simulados, cada corrida con la calificación del juez y el resumen
-- pass^3 por tipo de caso.

create table simulation_runs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants (id),
  status        text not null default 'queued'
                check (status in ('queued', 'running', 'done', 'failed')),
  requested_by  text not null default '',
  size          integer not null default 20 check (size between 1 and 100),
  personas      jsonb not null default '[]',
  conversations jsonb not null default '[]',
  summary       jsonb,
  error         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (tenant_id, id)
);
create index simulation_runs_recent_idx on simulation_runs (tenant_id, created_at desc);

alter table simulation_runs enable row level security;
alter table simulation_runs force row level security;
create policy tenant_isolation on simulation_runs
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update, delete on simulation_runs to app_user;
