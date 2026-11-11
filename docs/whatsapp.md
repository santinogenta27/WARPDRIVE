# Probar el agente por WhatsApp desde tu celular

El agente atiende como Acme Store en el número de prueba de Meta. En tu PC corren tres procesos:

- la API, que recibe el webhook;
- el worker, que corre el agente y responde;
- un túnel de Cloudflare, que le da a la API una URL pública con HTTPS.

## 1. Una sola vez

1. `.env` con `DATABASE_URL`, `GEMINI_API_KEY`, `ENCRYPTION_KEY` y las cuatro `WHATSAPP_*`.
2. `make whatsapp-setup`. Crea Acme Store en la base y le asigna el número. Si cambia el token de Meta, se vuelve a correr.
3. cloudflared: `winget install Cloudflare.cloudflared`. Otra opción es bajar el `.exe` portable de github.com/cloudflare/cloudflared/releases, sin instalarlo.

## 2. Cada vez que quieras probar

Abrí tres terminales en la carpeta del proyecto:

```
make api                                              # API en http://localhost:8000
make worker                                           # agente
cloudflared tunnel --no-autoupdate --url http://localhost:8000
```

cloudflared muestra una URL del tipo `https://palabras-al-azar.trycloudflare.com`. **Cambia cada vez que lo arrancás**, así que hay que pegarla de nuevo en Meta. Para tener una URL fija hace falta un túnel con nombre, con una cuenta de Cloudflare y un dominio propio.

## 3. Dónde pegar la URL en Meta

1. Entrá a https://developers.facebook.com/apps y abrí tu app.
2. En el menú de la izquierda, entrá a **WhatsApp → Configuración** (Configuration). Si creaste la app con el caso de uso "Conectarte con clientes a través de WhatsApp", la ruta es **Casos de uso → Personalizar → Configuración**.
3. En la sección **Webhook**, tocá **Editar**:
   - **URL de devolución de llamada** (Callback URL): `https://<tu-url>.trycloudflare.com/webhooks/whatsapp`
   - **Token de verificación** (Verify token): el valor de `WHATSAPP_VERIFY_TOKEN` en tu `.env`.
   - Tocá **Verificar y guardar**. Meta hace el handshake contra tu PC; si da error, revisá que `make api` y el túnel estén corriendo.
4. Más abajo, en **Campos del webhook** (Webhook fields), tocá **Administrar** y suscribite a **messages**.
5. En **WhatsApp → Configuración de la API** (API Setup), en el campo **Para** (To), tocá **Administrar lista de números** y agregá tu celular. Meta te manda un código por WhatsApp para confirmarlo. En modo desarrollo, el número de prueba solo le escribe a números de esa lista.

## 4. Escribile

Desde tu celular, mandale un WhatsApp al número de prueba (**+1 555-176-2191**). Por ejemplo:

- "¿Abren los domingos?"
- "¿Tienen cuotas sin interés?"
- "Quiero hablar con una persona"

En la terminal del worker vas a ver cada mensaje procesado.

## Si no responde

- **No llega nada a la API** (la terminal de `make api` no muestra ningún `POST /webhooks/whatsapp`):
  - Revisá el paso 3, en especial la suscripción a **messages**.
  - Revisá que la URL sea la del túnel actual.
  - Meta avisa que algunos webhooks no se envían con la app en modo desarrollo. Si es tu caso, pasá la app a modo **En vivo**, que pide una política de privacidad.
- **La API responde 401:** `WHATSAPP_APP_SECRET` no coincide con el secreto de la app (Configuración de la app → Básica → Clave secreta de la app).
- **El worker muestra error 131030:** tu celular no está en la lista del paso 3.5. Para números de Argentina, el sistema reintenta solo sin el 9.
- **El worker se queda reintentando Gemini:** el modelo está saturado (503) o sin cuota. Cambiá `GEMINI_AGENT_MODEL` en `.env` a otro modelo gratuito de `pricing.py` y reiniciá `make worker`.
- **Consultas de pedidos:** tu número no es cliente de Acme, así que el agente te pide el email de la compra (por ejemplo `lucia@example.com`) y un código. No hay proveedor de email: el código aparece en la terminal del proceso que corre el agente, que para WhatsApp es `make worker` (ver `docs/chat-web.md`).

## Alternativa: la prueba de WhatsApp de Twilio

Sirve mientras la cuenta de Meta esté bloqueada (error 131031). Usa el mismo agente con Acme y los mismos tres procesos.

1. En `.env`, cargá `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` y `TWILIO_WHATSAPP_FROM` (por ejemplo `whatsapp:+14155238886`) y corré `make whatsapp-setup`. Asigna ese número a Acme.
2. Levantá `make api`, `make worker` y el túnel, igual que en el paso 2.
3. En la consola de Twilio, entrá a **Messaging → Try it out → Send a WhatsApp message**, pestaña **Sandbox settings** (en algunas cuentas, **Receive a message**). En **When a message comes in**:
   - URL: `https://<tu-url>.trycloudflare.com/webhooks/twilio`
   - Método: **POST**
   - Tocá **Save**.
4. Desde el celular unido al sandbox (`join <palabra>`), escribile al número de Twilio.

La URL tiene que ser exactamente la que guardaste en Twilio: Twilio la usa para firmar cada mensaje y la API rechaza con 401 los que no coinciden. Si cambia el túnel, cambiá también la URL en Twilio. La unión al sandbox vence a los 3 días; para renovarla, se manda de nuevo `join <palabra>`.

## Plantilla `caso_actualizado` (para respuestas humanas fuera de las 24 h)

Creala en WhatsApp Manager → Plantillas de mensajes:

- **Categoría:** Utilidad
- **Nombre:** `caso_actualizado`
- **Idioma:** Español (Argentina), es decir `es_AR`, o el que pongas en `WHATSAPP_TEMPLATE_LANGUAGE`
- **Cuerpo:** `Hola {{1}}, tenemos novedades sobre tu consulta. Respondé este mensaje para verlas.`
