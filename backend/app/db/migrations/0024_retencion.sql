-- 0024 — Retención ante una cancelación o devolución (hito 10, decisión 062).

-- Las alternativas ya ofrecidas en un caso para una acción sobre un recurso (el pedido): la
-- primera vez se ofrecen; si el cliente insiste, la acción sigue sin volver a ofrecer.
create table retention_offers (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  case_id      uuid not null,
  tool         text not null,
  resource_key text not null default '',
  alternatives text[] not null default '{}',
  created_at   timestamptz not null default now(),
  unique (tenant_id, case_id, tool, resource_key),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);

alter table retention_offers enable row level security;
alter table retention_offers force row level security;
create policy tenant_isolation on retention_offers
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, delete on retention_offers to app_user;
