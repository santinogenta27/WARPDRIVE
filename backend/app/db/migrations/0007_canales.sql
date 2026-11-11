-- 0007 — Canales por empresa: el webhook identifica el tenant por phone_number_id.

create table channels (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references tenants (id),
  kind             text not null check (kind in ('whatsapp', 'web')),
  phone_number_id  text,
  waba_id          text,
  access_token_enc text, -- token de la Cloud API cifrado (Fernet con ENCRYPTION_KEY)
  status           text not null default 'active' check (status in ('active', 'inactive')),
  created_at       timestamptz not null default now(),
  unique (tenant_id, id)
);
-- Un número pertenece a una sola empresa.
create unique index channels_phone_number_idx on channels (phone_number_id)
  where phone_number_id is not null;

alter table channels enable row level security;
alter table channels force row level security;
create policy tenant_isolation on channels
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on channels to app_user;

-- La tarea 12 asignaba el número en tenants.settings: pasa a channels (sin token; se carga con
-- `make whatsapp-setup`).
insert into channels (tenant_id, kind, phone_number_id)
select id, 'whatsapp', settings -> 'whatsapp' ->> 'phone_number_id'
from tenants
where settings -> 'whatsapp' ->> 'phone_number_id' is not null;
update tenants set settings = settings - 'whatsapp' where settings ? 'whatsapp';
