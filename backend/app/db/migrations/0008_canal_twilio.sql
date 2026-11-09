-- 0008 — Canal de la prueba de WhatsApp de Twilio. El número de envío ("whatsapp:+1...")
-- va en phone_number_id y rutea al tenant igual que un número de la Cloud API de Meta.

alter table channels drop constraint channels_kind_check;
alter table channels add constraint channels_kind_check
  check (kind in ('whatsapp', 'web', 'twilio_whatsapp'));
