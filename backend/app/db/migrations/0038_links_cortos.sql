-- 0038 — Links cortos para WhatsApp (decisión 103). Aditiva.
--
-- `{PUBLIC_BASE_URL}/go/{code}` redirige a `target`, un destino que guardó el sistema (nunca uno
-- que venga en la dirección) y de un dominio permitido de la empresa. El código es al azar (12
-- caracteres de 62) y el mismo destino de la misma empresa tiene un solo código: el destino ya
-- lleva el `ref` de la conversación, así el mismo carrito con el mismo ref da el mismo link.

create table short_links (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  code       text not null unique check (code ~ '^[A-Za-z0-9]{12}$'),
  target     text not null check (target ~* '^https?://'),
  ref        text,
  created_at timestamptz not null default now(),
  unique (tenant_id, target)
);

alter table short_links enable row level security;
alter table short_links force row level security;
create policy tenant_isolation on short_links
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on short_links to app_user;
