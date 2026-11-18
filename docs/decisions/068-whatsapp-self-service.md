# 068 — WhatsApp self-service: la empresa conecta su número desde el panel

**Contexto.** Hoy el número de WhatsApp de una empresa lo carga el operador (`make whatsapp-setup`), con una sola app de Meta para todos (`WHATSAPP_APP_SECRET` y `WHATSAPP_VERIFY_TOKEN` en el entorno). Cada cliente tiene ahora su propia app de Meta, y tiene que poder conectar su número solo, una vez hecho el trámite de Meta.

**Qué se reutiliza.**
- La tabla `channels`: un número es de una sola empresa, y el ruteo de cada mensaje sigue por `phone_number_id`.
- El token cifrado del canal, que el worker ya usa para responder.
- La validación de firma, el parseo y la cola del webhook.
- El cliente de la Graph API (`v27.0`) y el módulo de guías.
- El webhook global y `make whatsapp-setup` siguen para la demo.

**Qué se decidió.**

1. **Configuración → Canales → WhatsApp.** Un formulario con:
   - Phone Number ID;
   - WhatsApp Business Account ID;
   - token permanente de un usuario del sistema;
   - App Secret.

   El token y el App Secret se cifran como el token del canal (`app.crypto`) y nunca se vuelven a mostrar. Si se dejan vacíos, se conservan los guardados. Un número que ya es de otra empresa se rechaza.

2. **Un webhook por empresa:** `/webhooks/whatsapp/{webhook_key}`, con una clave aleatoria por canal. La verificación de Meta (GET) no trae el número, así que la URL identifica a la empresa.
   - **GET:** acepta el verify token de esa empresa. El token es aleatorio, se guarda cifrado y se muestra para copiar.
   - **POST:** valida `X-Hub-Signature-256` con el App Secret de esa empresa. Solo encola los mensajes de su número y solo con el canal activo. Después sigue el mismo camino que el webhook global.

3. **Probar conexión** (solo lectura, contra la Graph API). Mira:
   - el número (`platform_type` = `CLOUD_API`, es decir, registrado);
   - los permisos del token (`/me/permissions`);
   - que el número sea de esa WhatsApp Business Account.

   Errores claros:

   | Caso | Señal de Meta |
   |---|---|
   | Token vencido | código 190 |
   | Faltan permisos | 10, 200 o 403, o un permiso no otorgado |
   | ID inexistente | 100 |
   | Número sin registrar | 133010 o `platform_type` distinto |
   | El número no es de la cuenta | — |

4. **Mensaje de prueba** a un número que escribe la empresa. Si está fuera de la ventana de 24 h (131047), manda la plantilla `hello_world`, que Meta crea en cada cuenta.

5. **Activar o desactivar.**
   - Un canal guardado queda inactivo.
   - Activar exige la prueba aprobada y suscribe la app a la cuenta (`POST /{waba_id}/subscribed_apps`); sin eso, Meta no manda mensajes al webhook.
   - Desactivar corta el ruteo: `channel_for_number` solo ve canales activos.
   - Cambiar los datos vuelve el canal a inactivo, como la conexión del sistema (decisión 026).

6. **Guía paso a paso** (`guides.whatsapp_guide`), en español y en inglés. Reemplaza "Lo conecta el operador". Cubre:
   - crear la app y agregar WhatsApp;
   - el usuario del sistema y su token permanente con `whatsapp_business_messaging` y `whatsapp_business_management`;
   - copiar el App Secret y pegar el webhook;
   - publicar la app.

**Base de datos.** La migración `0026` agrega a `channels`: `app_secret_enc`, `verify_token_enc`, `webhook_key` (única), `last_test` y `updated_at`. La tabla ya tiene `tenant_id` y RLS.

**Sin verificar contra Meta real.** Los tests usan una Graph API falsa con respuestas de la forma de la documentación. Hay que probarlo con una app real antes del primer cliente:
- que `/me/permissions` responda con el token de un usuario del sistema; si no responde, la prueba lo saltea y el mensaje de prueba detecta el permiso que falta;
- los nombres exactos de los menús de Meta en la guía.
