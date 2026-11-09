-- 0006 — Ventana de 24 h de WhatsApp: dirección cifrada del contacto y mensajes pendientes.

-- wa_id y nombre de perfil del cliente, cifrados (Fernet con ENCRYPTION_KEY). Hacen falta para
-- escribirle cuando responde un humano; el teléfono en claro nunca se guarda.
alter table contacts add column wa_address_enc text;

-- Mensajes que no se pudieron enviar como texto libre (fuera de la ventana): se envía una
-- plantilla y el texto sale cuando el cliente responde.
create table outbound_messages (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  case_id    uuid not null,
  contact_id uuid not null,
  text       text not null,
  status     text not null default 'pending' check (status in ('pending', 'sent')),
  created_at timestamptz not null default clock_timestamp(),
  sent_at    timestamptz,
  foreign key (tenant_id, case_id) references cases (tenant_id, id),
  foreign key (tenant_id, contact_id) references contacts (tenant_id, id)
);
create index outbound_messages_pending_idx on outbound_messages (tenant_id, contact_id)
  where status = 'pending';

alter table outbound_messages enable row level security;
alter table outbound_messages force row level security;
create policy tenant_isolation on outbound_messages
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on outbound_messages to app_user;
