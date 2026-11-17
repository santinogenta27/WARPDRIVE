-- 0021 — El email con el que el cliente se verificó, cifrado con la clave de su empresa
-- (decisión 046). Se usa para el email de confirmación de un reembolso aprobado. Null si el
-- email no es de un cliente del sistema o si la verificación es anterior a esta columna.
alter table identity_verifications add column if not exists email_enc text;
