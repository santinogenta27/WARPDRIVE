-- 0017 — Widget instalable (hito 7, decisión 031): marca y dominios permitidos por empresa.

-- Una fila por empresa. Sin fila, el widget usa el nombre de la empresa y el color por defecto,
-- y no funciona en ningún sitio ajeno (lista de dominios vacía).
create table widget_settings (
  tenant_id    uuid primary key references tenants (id),
  display_name text,
  accent       text check (accent ~ '^#[0-9a-f]{6}$'),
  logo         bytea,            -- PNG, JPEG o WebP chico; se sirve en /chat/widget/logo/<enlace>
  logo_type    text,
  domains      text[] not null default '{}',   -- sitios donde se puede instalar
  suggestions  text[] not null default '{}',   -- preguntas sugeridas con la conversación vacía
  updated_at   timestamptz not null default now()
);

alter table widget_settings enable row level security;
alter table widget_settings force row level security;
create policy tenant_isolation on widget_settings
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on widget_settings to app_user;
