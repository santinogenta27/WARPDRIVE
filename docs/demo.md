# Guion de la demo

Unos 10 minutos, más 10 si mostrás el onboarding con los datos del cliente (sección 6). Mostrás cuatro cosas: qué parte de la atención resolvería solo el asistente, cómo atiende al cliente, cómo la empresa mantiene el control y cómo queda registrado cada paso. Acme Store es una tienda ficticia y todos los datos son de demo.

## Antes de la reunión

- **Despertá el servicio** 5 minutos antes. El plan gratis de Render se duerme a los 15 minutos sin uso y la primera visita tarda alrededor de un minuto. Abrí el link del chat una vez.
- **Abrí dos ventanas**, una al lado de la otra:
  - **Cliente:** el chat, `https://<tu-servicio>.onrender.com/`.
  - **Empresa:** el panel, `https://<tu-servicio>.onrender.com/panel`. Entrá con `PANEL_EMAIL` y `PANEL_PASSWORD`.
- **Dejá abiertos los Logs de Render** en otra pestaña. Ahí aparece el código de verificación: buscá `Mailer de consola`. El chat no envía emails todavía.
- **Panel → Casos → "Reiniciar demo"**, para arrancar con el panel limpio:
  - archiva las conversaciones de prueba (la auditoría queda guardada);
  - rechaza las aprobaciones pendientes;
  - devuelve los pedidos de Acme a su estado inicial.
- **En el chat, "Cómo probarla" → "Empezar de nuevo"**, para arrancar con una conversación limpia.

## 1. El reporte de preparación (2 min): "esto es lo que el asistente resolvería en tu empresa"

Panel → **Reporte de preparación**. Es la pantalla para el cliente.

- **84%**: del volumen de consultas de Acme, el asistente resolvería solo esa proporción. Salió de 100 conversaciones reales del historial: un export de WhatsApp y tickets de helpdesk.
- **8 tipos de caso**, detectados solos, sin que nadie los cargara. Para cada uno, el reporte muestra:
  - cuánto volumen tiene;
  - qué porcentaje resolvería;
  - con qué herramientas cuenta.
- **"Qué falta"** es la parte honesta y la que más vende:
  - En facturación, falta una herramienta porque la API de Acme no tiene esas operaciones. Hoy el asistente solo puede informar.
  - En los tipos al 50%, se muestran los casos que fallaron. Por ejemplo, un cliente que pide cambiar la dirección sin decir cuál: el asistente lo deriva en vez de preguntarla.
- **3 de 4 ataques frenados.** Probamos pedidos indebidos: inyección de instrucciones, datos de otros clientes, pedir datos en masa y amenazas. El que no frenó está a la vista.
- **Mensaje para el cliente:** "Con tu historial, en un día te decimos qué parte de tu atención puede resolver el asistente y qué hay que conectar para el resto."

Si preguntan cómo se mide, está al pie de la página:

- El historial se anonimiza antes de usarlo.
- Los casos de prueba salen de conversaciones reales.
- Se corren contra una copia del sistema, nunca contra datos reales.

Con 2 casos por tipo, el número tiene margen. Con un cliente real se generan más (decisión 023).

## 2. Una consulta resuelta sola (2 min)

Ventana del cliente:

1. Escribí "¿Abren los domingos?". Responde con el horario de las sucursales, sacado de los documentos de Acme.
2. Escribí "¿Dónde está mi pedido 1001?". Antes de dar datos del pedido, pide el email de la compra.
3. Respondé `lucia@example.com`. Copiá el código de los Logs de Render y escribilo.
4. Responde con el estado real del pedido.

**Qué remarcar:**

- Antes de mostrar datos de una cuenta, verifica que sea el titular.
- Contesta con lo que dicen los documentos y el sistema de la empresa; no inventa.

## 3. Un reembolso: decide una persona (3 min)

1. Arrancá una conversación nueva: "Cómo probarla" → "Empezar de nuevo".
2. Escribí "Quiero el reembolso del pedido 1005". El asistente pide el email de la compra para mandarte un código.
3. Respondé `martin@example.com` y escribí el código de los Logs.
4. El asistente **no devuelve la plata solo**: avisa que el pedido pasa a revisión de una persona.
5. Panel → **Aprobaciones**: aparece "Reembolso · Pedido 1005 · $35.000", con el primer mensaje del cliente. Nunca se muestran el email ni el código.
6. Tocá **Aprobar**. Se ejecuta en el sistema de Acme a través del guardián.
7. En la ventana del cliente, en unos segundos, aparece el mensaje del **Equipo de Acme Store**: "¡Buenas noticias! Aprobamos el reembolso del pedido 1005 por $35.000…".

**Qué remarcar:**

- Las acciones con dinero siempre pasan por una persona; eso no lo cambia ningún prompt.
- Las de bajo riesgo, como cambiar una dirección, el asistente las hace solo.
- La empresa decide qué entra en cada nivel.

## 4. Una derivación: el equipo responde desde el panel (2 min)

1. En el chat, escribí "Quiero hablar con una persona". El asistente avisa que lo pasa con alguien del equipo.
2. Panel → **Casos**. El caso aparece **arriba de todo**, en rojo, como "Derivado". Primero van siempre los que esperan a una persona.
3. Abrilo. Arriba está el motivo: "El cliente pidió hablar con una persona". Abajo, la conversación completa.
4. Escribí la respuesta, por ejemplo "Hola, soy Ana del equipo de Acme. ¿En qué te ayudo?", y tocá **Enviar**.
5. En el chat aparece en unos segundos, firmado por el equipo.

6. En el chat, escribí "¡Gracias!". El asistente cierra la conversación y el caso pasa a **"Resuelto con una persona"**.

**Qué remarcar:**

- La persona recibe el caso con todo el contexto. El cliente no tiene que repetir nada.
- Un caso se resuelve cuando el cliente confirma o pasan 24 h sin respuesta.
- "Resuelto por el asistente" es lo que se cobra; si intervino una persona, el panel lo dice.

## 5. La auditoría (1 min): "cada paso queda registrado y no se puede alterar"

En el detalle del caso del reembolso, bajá a **Auditoría paso a paso**:

- **Mensaje del cliente → Clasificó el caso → Buscó en los documentos.** Están las fuentes consultadas, por ejemplo la política de devoluciones y reembolsos.
- **Usó una herramienta.** Se ve con qué datos y qué decidió el guardián: "necesita aprobación".
- **Respondió.** Se ve qué le dijo al cliente y en qué fuentes se basó.
- **Aprobó una persona del equipo.** Quién decidió y qué se le avisó al cliente.
- **Costo.** Cada llamada al modelo con sus tokens y su costo en USD. En la demo es 0, porque se usa el modelo gratuito de Groq.
- **"Cadena de auditoría íntegra".** Cada evento lleva el hash del anterior: si alguien modifica o borra un paso, la cadena se rompe y el panel lo marca.

## 6. El onboarding con los datos del cliente (10 min, opcional)

Es la parte que más vende con un cliente real: ve su propia empresa andando. Necesitás el servicio en la rama del hito 6 (`docs/render.md`, "Pasar al hito 6").

**Pedile antes de la reunión:**

- Un export de sus chats de WhatsApp (.txt, o un .zip con varios) o de sus tickets (.csv o .json). Con 100 conversaciones alcanza para mostrarlo; para un reporte confiable, 300.
- Dos o tres documentos de políticas: envíos, devoluciones, medios de pago. En .md, .txt o .docx.
- Si tiene, el OpenAPI de su sistema (.json o .yaml). Sin OpenAPI, el asistente solo informa.

**En la reunión:**

1. **Creá su empresa.** Panel → **Empresas** → nombre, email y una clave. Queda creada con su chat de prueba.
2. **Salí e ingresá con su usuario.** El encabezado ya dice el nombre de su empresa. Está vacía: nada de Acme.
3. **Configuración → Sistema de la empresa.** Si trajo el OpenAPI, subilo ahora: el análisis genera las herramientas desde ahí.
4. **Onboarding → subí sus archivos.**
   - Si son chats de WhatsApp, cargá primero con qué nombre figura la empresa en los chats.
   - La página confirma cuántas conversaciones y documentos entraron.
5. **Tocá "Analizar mi historial".** Las 6 etapas avanzan solas y la página se actualiza. Con 100 conversaciones tarda unos minutos.
   - Mientras corre, mostrale la demo de Acme (secciones 2 a 5) con el usuario operador en otra ventana.
6. **Reporte.** Sus tipos de caso, su volumen y qué porcentaje resolvería el asistente. "Qué falta" le dice qué conectar.
7. **Configuración.** Activá dos o tres tipos de caso y aprobá las herramientas. Mostrale los límites: sobre qué monto pide aprobación.
8. **Chat de prueba.** Abrí el enlace `/c/…` de Configuración y escribí una consulta de su historial. Responde con sus documentos. El caso aparece en **Casos**, con su auditoría.
9. **Métricas.** Resolución, latencia y costo de lo que acaban de probar.

**Qué remarcar:**

- **Sus datos están aislados.** Cada empresa ve solo lo suyo; lo garantiza la base, no una pantalla.
- **Los datos personales se reemplazan antes de guardarse.** El archivo original no se conserva.
- **Nada queda activo sin su aprobación.** Ni un tipo de caso ni una herramienta.
- **Su sistema no se toca hasta que lo active.** Hasta entonces las herramientas responden en modo simulado. Para pasar al sistema real: URL base, credenciales, prueba de conexión y activar.

**Si algo falla:**

- **Una etapa queda en "Falló".** Casi siempre es la cuota diaria del modelo gratuito. El análisis se reintenta solo; si no, tocá "Reintentar desde donde quedó". No repite lo hecho.
- **"Todavía no hay conversaciones".** El archivo no era un export de chats o de tickets. Revisá el formato en `docs/onboarding.md`.
- **Los chats figuran todos como del cliente.** Faltó cargar con qué nombre figura la empresa en WhatsApp.
- **El código de verificación.** En el chat de una empresa también sale en los **Logs** de Render: todavía no se envían emails.

## Si algo falla durante la demo

- **"Tuvimos un problema para responderte".** Se agotó la cuota diaria gratuita del modelo, o Groq tardó demasiado. Esperá un minuto y reintentá. Si no responde, mostrá el reporte y la auditoría de un caso anterior: no dependen del modelo.
- **El reembolso no pasa a aprobación.** Ahora el agente inicia el reembolso por el total, sin preguntar el motivo, y la verificación es siempre por código (decisión 024). Si igual responde otra cosa, escribí "iniciá el reembolso por el total, por favor".
- **El mensaje del equipo no aparece en el chat.** La página lo busca cada 6 segundos, solo con la pestaña visible. Hacé clic en la ventana del chat.
- **Los pedidos volvieron a su estado inicial.** La API de Acme de la demo vive en memoria y se reinicia cuando Render duerme o redespliega el servicio. Los casos, las aprobaciones y la auditoría quedan en la base.

## Configuración (una sola vez)

Está en `docs/render.md`, sección "Pasar al panel": la rama `hito-5` y las variables `PANEL_EMAIL` y `PANEL_PASSWORD`.
