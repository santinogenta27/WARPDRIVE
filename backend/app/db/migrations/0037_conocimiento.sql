-- 0037 — Knowledge (Hito 13, decisión 088). Aditiva.
--
-- documents: el texto extraído (para mostrar qué aprendió de cada uno), el archivo de origen, su
-- estado (ready o failed, con el motivo) y de qué sitio salió si es una página.
-- sites y site_pages: el sitio de la empresa, sus páginas encontradas, cuáles usa y la
-- sincronización (a mano o cada semana).

alter table documents add column if not exists text text;
alter table documents add column if not exists file_name text;
alter table documents add column if not exists status text not null default 'ready';
alter table documents add column if not exists error text;
alter table documents add column if not exists updated_at timestamptz not null default now();
alter table documents add column if not exists site_id uuid;

create table sites (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  url          text not null,
  auto_sync    boolean not null default false,
  last_sync_at timestamptz,
  last_error   text,
  created_at   timestamptz not null default now(),
  unique (tenant_id, id),
  unique (tenant_id, url)
);

create table site_pages (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  site_id    uuid not null,
  url        text not null,
  title      text not null,
  text       text not null,
  selected   boolean not null default true,
  found_at   timestamptz not null default now(),
  unique (tenant_id, url),
  foreign key (tenant_id, site_id) references sites (tenant_id, id) on delete cascade
);

alter table sites enable row level security;
alter table sites force row level security;
alter table site_pages enable row level security;
alter table site_pages force row level security;

create policy tenant_isolation on sites
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
create policy tenant_isolation on site_pages
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update, delete on sites to app_user;
grant select, insert, update, delete on site_pages to app_user;
