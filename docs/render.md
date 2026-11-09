# Desplegar el chat web en Render (plan gratis)

Deja el chat de Acme Store en un link fijo del tipo `https://acme-chat-demo.onrender.com`. La base sigue en Neon y la configuración está en `render.yaml`, un Blueprint de Render.

## Antes de empezar

- La rama `hito-3` tiene que estar en GitHub, con `render.yaml` en la raíz.
- Tené a mano tu `.env`: vas a copiar tres valores (`DATABASE_URL`, `GEMINI_API_KEY` y `ENCRYPTION_KEY`).
- La base de Neon ya tiene a Acme Store con el chat web asignado (`make demo-setup`). Si algún día usás otra base, corré `make demo-setup` contra ella antes.

## Paso a paso en render.com

1. **Creá la cuenta.** Entrá a https://render.com → **Get Started** → **GitHub**, y autorizá a Render con tu cuenta.
2. **Dale acceso al repositorio.** El repositorio es privado: cuando GitHub pregunte a qué repositorios puede acceder Render, elegí **Only select repositories** → `santinogenta27/WARPDRIVE` → **Install**.
3. **Creá el Blueprint.** En el panel de Render: **New +** → **Blueprint**.
   - Elegí el repositorio `WARPDRIVE`.
   - En **Branch**, elegí `hito-3`. `render.yaml` todavía no está en `main`.
   - En **Blueprint Name**, poné `acme-chat-demo`.
4. **Cargá los secretos.** Render muestra el servicio `acme-chat-demo` (Web Service, plan **Free**) y pide cuatro valores. Copialos de tu `.env`: lo que está después del `=`, sin comillas ni espacios.
   - `DATABASE_URL`
   - `GROQ_API_KEY`: el modelo del chat (`openai/gpt-oss-20b`).
   - `GEMINI_API_KEY`: los embeddings y el respaldo si Groq se queda sin cuota.
   - `ENCRYPTION_KEY`: tiene que ser la misma que en tu PC, porque con ella se leen los datos cifrados de la base.

   El resto (`LLM_PROVIDER=groq`, los modelos, los límites y la versión de Python) ya viene de `render.yaml`.

   **Si ya habías creado el servicio** antes de pasar a Groq, Render toma solo las variables nuevas con valor. `GROQ_API_KEY` es secreta y no se pide sola: agregala en el servicio → **Environment** → **Add Environment Variable** → **Save Changes**.
5. **Desplegá.** Tocá **Deploy Blueprint** (o **Apply**). Render instala las dependencias y arranca el servicio. Tarda unos minutos. En **Logs** vas a ver:
   - `Migraciones aplicadas: ninguna (al día)`
   - `Uvicorn running on http://0.0.0.0:10000`
   - el estado **Live**.
6. **Copiá el link.** Arriba, en la página del servicio, aparece la URL: `https://acme-chat-demo.onrender.com`, o con un sufijo si ese nombre ya estaba tomado. Ese es el link fijo de la demo.

## Después

- **Código de verificación:** el chat sigue sin enviar emails. El código aparece en **Logs** del servicio. Buscá `Mailer de consola`.
- **El plan gratis se duerme** después de 15 minutos sin visitas. La primera visita después de eso tarda alrededor de un minuto en responder; las siguientes, lo normal.
- **Cada push a `hito-3` despliega solo** (`autoDeploy`).
- **Cambiar los límites:** servicio → **Environment** → editá la variable → **Save Changes**. Se redespliega solo.
  - `WEB_CHAT_IP_DAILY_LIMIT`: mensajes por IP en 24 h. Por defecto, 30.
  - `WEB_CHAT_DAILY_LIMIT`: mensajes en total en 24 h. Por defecto, 300. Cuida la cuota diaria gratuita de Gemini.
  - `WEB_CHAT_CLIENT_IP`: de dónde sale la IP del visitante. Por defecto, `x-forwarded-for:first`. Ver "IP del visitante".
  - Si Groq se queda sin cuota diaria, responde Gemini. Si también Gemini se queda sin cuota, el chat responde "Tuvimos un problema para responderte" y no descuenta el mensaje.

## IP del visitante

Render pone un proxy delante de la app, así que la IP del visitante llega en el encabezado `X-Forwarded-For`. Según Render, la primera de la lista es la del cliente. Pero si Render conserva lo que manda el cliente, alguien podría inventarse otra IP para saltear el límite.

Después del primer despliegue se prueba así:
- Se manda un mensaje normal.
- Se manda otro con un `X-Forwarded-For` inventado.
- Si el contador del día (`remaining_today`) baja en los dos, la IP no se puede falsificar.
- Si el segundo arranca de cero, hay que cambiar `WEB_CHAT_CLIENT_IP` a `x-forwarded-for:last`.

Aunque se falsifique, el límite total diario (`WEB_CHAT_DAILY_LIMIT`) protege igual la cuota.

Decisión 019.
