-- 0025 — Ventas atribuidas al agente (hito 10, decisión 063).

-- Cada link de compra que armó el agente, con el identificador de su conversación (`ref`). Si la
-- tienda informa que esa compra se completó, se anota el pedido y el monto: es una venta
-- atribuida al agente. Una fila por link; completarla dos veces no cambia nada.
create table checkout_links (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  case_id      uuid not null,
  ref          text not null,
  url          text not null,
  total        numeric(12, 2) not null default 0,
  currency     text not null default '',
  created_at   timestamptz not null default now(),
  order_id     text,
  amount       numeric(12, 2),
  completed_at timestamptz,
  unique (tenant_id, ref, url),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);
create index checkout_links_open_idx on checkout_links (tenant_id) where completed_at is null;

alter table checkout_links enable row level security;
alter table checkout_links force row level security;
create policy tenant_isolation on checkout_links
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update, delete on checkout_links to app_user;
