-- 0035 — Preferencias de cada usuario del panel (Hito 13, decisión 085): el idioma del panel
-- (inglés por defecto) y el tema (claro u oscuro). Aditiva: la tabla ya tiene tenant_id y RLS.

alter table users add column if not exists preferences jsonb not null default '{}'::jsonb;
