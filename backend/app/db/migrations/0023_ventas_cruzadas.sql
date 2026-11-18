-- 0023 — Ventas cruzadas con reglas (hito 10, decisión 061).

-- Lo que la empresa elige ofrecer desde el panel: qué producto (por su nombre en la tienda), en
-- qué tipos de caso y con qué descuento máximo.
create table upsell_rules (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  product      text not null check (length(trim(product)) > 0),
  case_types   text[] not null default '{}',
  max_discount numeric(5, 2) not null default 0 check (max_discount between 0 and 90),
  active       boolean not null default true,
  created_at   timestamptz not null default now(),
  unique (tenant_id, id)
);

-- Una oferta hecha: como máximo una por conversación (caso). Guarda los SKU del producto
-- ofrecido para que el descuento valga solo para ellos en el link de compra.
create table upsell_offers (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  case_id    uuid not null,
  rule_id    uuid references upsell_rules (id) on delete set null,
  product    text not null,
  discount   numeric(5, 2) not null default 0,
  skus       text[] not null default '{}',
  created_at timestamptz not null default now(),
  unique (tenant_id, case_id),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);

alter table upsell_rules enable row level security;
alter table upsell_rules force row level security;
create policy tenant_isolation on upsell_rules
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update, delete on upsell_rules to app_user;

alter table upsell_offers enable row level security;
alter table upsell_offers force row level security;
create policy tenant_isolation on upsell_offers
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, delete on upsell_offers to app_user;
