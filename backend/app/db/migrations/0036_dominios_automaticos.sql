-- 0036 — Dominios de la tienda conectada (Hito 13, decisión 086). Al conectar Shopify o
-- Tiendanube, sus dominios se agregan solos a los permitidos del chat. Si la empresa todavía no
-- había guardado su chat, la fila nace "automática": no cuenta como marca configurada.
-- Aditiva: la tabla ya tiene tenant_id y RLS.

alter table widget_settings add column if not exists auto boolean not null default false;
