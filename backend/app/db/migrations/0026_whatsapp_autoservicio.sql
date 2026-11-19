-- 0026 — WhatsApp self-service (decisión 068). La empresa conecta su número desde el panel
-- con su propia app de Meta: el App Secret (firma del webhook) y un verify token propio,
-- cifrados como el token. El webhook de la empresa se identifica por `webhook_key`.

alter table channels add column app_secret_enc text;
alter table channels add column verify_token_enc text;
alter table channels add column webhook_key text;
alter table channels add column last_test jsonb;
alter table channels add column updated_at timestamptz not null default now();

create unique index channels_webhook_key_idx on channels (webhook_key)
  where webhook_key is not null;
