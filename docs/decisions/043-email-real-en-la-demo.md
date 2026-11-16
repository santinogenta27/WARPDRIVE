# 043 — El código de la demo en inglés, a un email real

**Estado:** adoptada (16 de noviembre de 2026). Para grabar el video de LinkedIn en Render. Decidida sin aprobación previa: revisala.

## Contexto

El código de verificación se envía solo si el email que escribe el cliente es el de un cliente del sistema de la empresa. En la demo de Northwind ese sistema es la API simulada (`mocks/northwind-api/`), y la clienta de los pedidos del guion (NW-4521 y NW-4528) tiene un email de prueba: `emily@example.com`. Resend no envía a dominios de prueba (decisión 029), así que el código sale en los logs.

Para el video, el código tiene que llegar a una casilla real. Esa dirección no puede quedar en el código, los fixtures, los tests ni los docs.

## Opciones

1. **Cambiar el email de la clienta en los fixtures.** Descartada: el email real quedaría en el repo.
2. **Reemplazar su email con una variable de entorno.** `emily@example.com` dejaría de andar donde la variable esté definida, y los ensayos locales sin Resend cambiarían.
3. **Sumarle un segundo email con una variable de entorno.** La clienta del guion responde a los dos.

## Decisión: opción 3

### La variable

`NORTHWIND_DEMO_CUSTOMER_EMAIL`, opcional. Va solo en el entorno: Render o el `.env` local.

- **Con la variable:** la API simulada que comparten el chat y el panel (`shared_northwind`) suma un cliente con los datos de la dueña de NW-4521 y NW-4528 y ese email. El agente lo encuentra por `/customers?email=…` y verifica al titular de esos pedidos (y de NW-4533, que también es suyo).
- **Sin la variable:** todo queda como antes.
- **Si el valor no es un email, o ya es el de un cliente:** se ignora, con un aviso en el log.

### Cuándo se aplica

La API simulada vive en la memoria del proceso de la API. Por eso la asociación se arma:

- **al arrancar la API**, con el primer mensaje;
- **después de "Reset demo"** del panel, que vuelve a armar la API simulada.

`make demo-setup` corre en otro proceso y no guarda pedidos: solo avisa si la variable está definida, sin mostrar el email.

### El email

Asunto `Your Northwind Outfitters verification code` (en español, `Tu código de verificación de …`). Va en HTML y en texto:

- el nombre de la empresa arriba;
- una línea que explica para qué es;
- el código solo, grande (34 px, monoespaciado y espaciado), sobre un fondo gris;
- la vigencia: "It expires in 10 minutes.";
- "If you didn't ask for it, you can ignore this email…".

El HTML usa tablas y estilos en línea, sin imágenes ni scripts: así lo muestran Gmail y Outlook. `ResendMailer` manda los dos (`text` y `html`). El de consola sigue mostrando el texto.

## Qué tener en cuenta

- **El email se ve en el video:** se escribe en el chat. Conviene una casilla dedicada a la demo.
- **Resend sin dominio verificado:** con el remitente de prueba (`onboarding@resend.dev`), Resend solo envía al email de la cuenta de Resend. Alcanza si ese es el email de la variable.
