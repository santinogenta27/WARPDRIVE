-- 0027 — Modo supervisado (Hito 11, decisión 072). Con `tenants.settings.supervised`, la
-- respuesta del agente queda como borrador hasta que una persona la envía (tal cual o editada) o
-- la rechaza. `message_id`: el mensaje del historial con el texto enviado; el chat web lo trae.

create table reply_drafts (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  case_id     uuid not null,
  channel     text not null,
  text        text not null,
  status      text not null default 'pending' check (status in ('pending', 'sent', 'rejected')),
  final_text  text,
  edited      boolean not null default false,
  decided_by  uuid references users (id),
  decided_at  timestamptz,
  message_id  uuid references case_messages (id),
  created_at  timestamptz not null default clock_timestamp(),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);
create index reply_drafts_pending_idx on reply_drafts (tenant_id, case_id) where status = 'pending';
create index reply_drafts_message_idx on reply_drafts (message_id) where message_id is not null;

alter table reply_drafts enable row level security;
alter table reply_drafts force row level security;
create policy tenant_isolation on reply_drafts
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on reply_drafts to app_user;
