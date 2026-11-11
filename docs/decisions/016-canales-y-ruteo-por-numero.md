# 016 — Tabla `channels` y ruteo por `phone_number_id`

**Estado:** adoptada (tarea 12e). Revisar.

## Contexto

La spec pide una tabla `channels` (tenant, tipo, phone_number_id, waba_id, token cifrado, estado) y que el webhook identifique la empresa por `metadata.phone_number_id`. Todas las tablas de negocio tienen RLS, pero el webhook todavía no sabe de qué empresa es el mensaje cuando llega.

## Decisiones

- **`channels`** tiene RLS por tenant como las demás tablas de negocio, y un índice único sobre `phone_number_id`: un número pertenece a una sola empresa.
- **Ruteo** (`channel_for_number`): es la única consulta que corre fuera de un tenant, con el rol dueño de la conexión, igual que el worker de la cola. Busca un número exacto y devuelve como mucho un canal. Todo lo demás (contacto, caso, agente, envío) corre dentro del tenant de ese canal.
- **Token por canal:** se guarda cifrado con Fernet (`ENCRYPTION_KEY`) y cada empresa responde con su número y su token. Si un canal no tiene token guardado, se usa `WHATSAPP_ACCESS_TOKEN`; es el caso de los números que migró la 0007.
- **Número desconocido:** se registra en el log (`logger.warning`) y se descarta, sin crear contacto ni caso.
- **Migración 0007:** pasa la asignación provisoria de la tarea 12 (`tenants.settings.whatsapp`) a `channels`.
- **Alta del número de la demo:** `make whatsapp-setup` usa `register_whatsapp_channel`. Se puede volver a correr cuando cambia el token de Meta.

## Pendiente

- Canal `web` (tarea 12b).
- Alta de números desde el panel y Embedded Signup (v2 según la spec).
