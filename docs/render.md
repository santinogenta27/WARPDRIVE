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

## Pasar al panel (Hito 5)

El mismo servicio sirve el chat (`/`) y el panel de la empresa (`/panel`). No hace falta otro servicio ni otro plan. Si el servicio ya existe, hay que hacer dos cambios:

1. **Cambiar la rama.** En el servicio → **Settings** → **Build & Deploy** → **Branch**, elegí `hito-5` → **Save Changes**. Si lo creaste como Blueprint, el `render.yaml` de `hito-5` ya dice `branch: hito-5`; en **Blueprints** → `acme-chat-demo` → **Settings**, cambiá también la rama del Blueprint a `hito-5`.
2. **Agregar dos variables secretas.** En el servicio → **Environment** → **Add Environment Variable**:
   - `PANEL_EMAIL`: el email con el que vas a entrar al panel. Por ejemplo, `demo@acme.example`.
   - `PANEL_PASSWORD`: una clave larga, que no uses en otro lado.

   Después, **Save Changes**. Render redespliega solo.

La sesión del panel se firma con `ENCRYPTION_KEY`, que ya está cargada. Si querés una clave aparte, agregá `PANEL_SECRET`. Sin `PANEL_EMAIL` y `PANEL_PASSWORD`, el panel no deja entrar a nadie.

El reporte de preparación se lee de la base, porque la corrida del Hito 4 quedó guardada en Neon. No hay que correr nada en Render.

La API simulada de Acme vive en memoria. Cuando el servicio se duerme o se redespliega, los pedidos vuelven a su estado inicial; los casos, mensajes, aprobaciones y auditoría quedan en la base.

## Pasar al hito 6 (empresas, onboarding y métricas)

La demo de Acme sigue andando en `hito-5` sin tocar nada. Para sumar lo del hito 6:

1. **Cambiar la rama.** En el servicio → **Settings** → **Build & Deploy** → **Branch**, elegí `main` (después de aprobar el pull request de `hito-6`) o `hito-6` → **Save Changes**.
2. **No hace falta ninguna variable nueva.** Al arrancar, el servicio aplica solo las migraciones 0014 a 0016. Son aditivas: columnas y tablas nuevas.
3. **Opcional:**
   - `OTEL_EXPORTER_OTLP_ENDPOINT` y `OTEL_EXPORTER_OTLP_HEADERS`: envían las trazas a un backend de OpenTelemetry.
   - `OTEL_TRACES_CONSOLE=1`: escribe las trazas en **Logs**.
   - `ALERT_LATENCY_MS`, `ALERT_COST_PER_CASE_USD` y `ALERT_ERRORS`: umbrales de las alertas (decisión 028).

Qué cambia al pasar:

- **El operador** entra con `PANEL_EMAIL` y `PANEL_PASSWORD`, como antes, y ve a Acme. Tiene una pantalla más, **Empresas**, para crear empresas.
- **Las sesiones abiertas del panel se cierran** una vez: la cookie cambió de formato. Hay que volver a ingresar.
- **El análisis del onboarding corre dentro del mismo servicio**, en un hilo. No hace falta un worker aparte.
- **Las alertas** aparecen en **Logs**: buscá `ALERTA`.
- **Acme no cambia.** Sus tipos de caso, herramientas, casos y reporte quedan como están. `make demo-setup` es opcional: regenera sus herramientas desde el OpenAPI por el flujo nuevo, con el mismo resultado.

Para volver atrás, elegí otra vez la rama `hito-5`. Las columnas y tablas nuevas no molestan al código anterior.

## Después

- **Código de verificación:** el chat sigue sin enviar emails. El código aparece en **Logs** del servicio. Buscá `Mailer de consola`.
- **El plan gratis se duerme** después de 15 minutos sin visitas. La primera visita después de eso tarda alrededor de un minuto en responder; las siguientes, lo normal.
- **Cada push a la rama del servicio despliega solo** (`autoDeploy`). Hoy la rama es `hito-5`.
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
