# 026 — Onboarding de una empresa nueva desde el panel

**Estado:** adoptada (10 de noviembre de 2026). Hito 6, etapa 1. Revisar los puntos marcados.

## Contexto

Hasta el hito 5, el sistema tenía una sola empresa en el panel (Acme) y el onboarding corría por línea de comandos. La configuración en vivo de Acme estaba cargada a mano desde archivos.

El pedido de esta etapa: que una empresa nueva pase de "no existe" a "probando su agente" sin comandos, aislada de las demás, y que Acme salga del mismo flujo.

## Decisiones

### 1. Quién crea una empresa

- **La crea el operador de la plataforma**, desde el panel, en la pantalla **Empresas**. El operador es el usuario de entorno que ya existía (`PANEL_EMAIL` y `PANEL_PASSWORD`).
- No hay registro público. La demo es pública y usa modelos con cuota gratuita: un registro abierto la dejaría sin cuota. **Revisar** cuando haya un plan pago.
- El alta crea tres cosas: la empresa, su primer usuario (dueño, con email y clave) y su chat de prueba.
- La empresa nace vacía: sin tipos de caso, herramientas ni documentos.
- El operador no entra al panel de otra empresa. Para verlo, hay que ingresar con el usuario de esa empresa.

### 2. Login por empresa y aislamiento

- **Clave propia por usuario**, guardada como hash scrypt en `users.password_hash` (migración 0015). Un email con clave pertenece a una sola empresa.
- **La empresa va dentro de la sesión.** La cookie firmada lleva el email y la empresa. Cada pantalla y cada endpoint leen la empresa de la sesión, nunca de la URL ni del formulario.
- **Toda consulta corre dentro de `tenant_scope`**, con la RLS de Postgres. Un id de otra empresa responde 404, igual que uno que no existe.
- **Consultas fuera de un tenant**, además del ruteo de webhooks que ya existía:
  - el login por email, cuando todavía no se sabe de qué empresa es;
  - la sesión del chat web, que se identifica por su token;
  - la lista de empresas, que solo ve el operador.
- **Freno a la fuerza bruta:** cinco intentos fallidos por email en 15 minutos bloquean el login. El contador vive en memoria: alcanza para un solo proceso.
- **No es el login de la spec.** El magic link necesita un `Mailer` real, que todavía no hay.
- `test_empresas.py` prueba el aislamiento en cada pantalla y en cada endpoint, en los dos sentidos.

### 3. Subir fuentes y correr el análisis

- **Pantalla Onboarding.** La empresa sube chats de WhatsApp (.txt o .zip), tickets (.csv o .json) y documentos (.md, .txt o .docx), hasta 50 MB.
- **Se ingiere al subir.** El texto se anonimiza antes de guardarse y el archivo original no se conserva. No hay almacenamiento S3 todavía.
- **Formularios con archivos sin dependencias nuevas:** `multipart/form-data` se lee con un parser propio de 25 líneas, probado con bytes binarios.
- **El análisis es un job.** "Analizar" encola `onboarding_run` en la tabla `jobs`. Con una corrida ya en la cola no se encola otra.
- **Lo corre un hilo dentro de la API** (`app/companies/worker.py`), con el mismo `Worker` de la cola. El plan gratis de Render tiene un solo servicio, sin proceso worker.
  - El hilo duerme hasta que alguien encola una corrida. No consulta la base todo el tiempo: eso mantendría despierta la base de Neon.
  - Al arrancar la API revisa una vez, para retomar una corrida que quedó a mitad por un reinicio.
  - `ONBOARDING_WORKER=off` lo apaga, por si el análisis pasa a un proceso aparte.
- **Avance:** la pantalla muestra las 6 etapas con su estado y se actualiza sola mientras corre. Si una etapa falla, por ejemplo por la cuota del modelo, el job se reintenta y retoma desde esa etapa.
- **Sin OpenAPI no hay herramientas.** La etapa queda hecha con cero herramientas y los tipos de caso solo informan.
- El reporte de preparación es el de la propia empresa.

### 4. Revisar y aprobar

- **Pantalla Configuración.** Muestra los tipos de caso y las herramientas que encontró el análisis. Nacen inactivos y sin aprobar.
- La empresa puede activar un tipo, editar sus instrucciones, aprobar una herramienta y fijar sus límites: sobre qué monto pide aprobación y sobre cuál se rechaza siempre.
- Una herramienta de riesgo alto pide aprobación siempre. Ese límite no se puede quitar.
- **Pasa al agente en vivo en el acto.** El agente lee de la base, en cada mensaje, los tipos activos y las herramientas aprobadas de su empresa. No hay un paso de publicación.
- Volver a analizar actualiza el contrato de una herramienta y conserva su aprobación y sus límites.

### 5. Conectar el sistema real

- **Tres datos:** el OpenAPI (archivo o pegado, JSON o YAML), la URL base y las credenciales. Se guardan en `system_connections`.
- **Autenticación:** ninguna, token Bearer, clave de API en un encabezado o usuario y clave.
- **Credenciales cifradas por empresa.** La clave de cifrado se deriva de `ENCRYPTION_KEY` y del id de la empresa (HKDF). Lo cifrado para una empresa no se descifra con la clave de otra. La credencial no se vuelve a mostrar.
- **Estados:** cargada → probada → activa. Cualquier cambio en la URL, las credenciales o el OpenAPI vuelve a "cargada": lo que se activa es siempre lo que se probó.
- **Prueba de conexión:** una consulta de solo lectura con las credenciales. Pasa si el sistema responde y no las rechaza.
- **Modo simulado hasta activar.** Mientras la conexión no está activa, las herramientas responden con datos armados desde el OpenAPI y nada llega al sistema de la empresa.
- **Verificación de identidad:** si el OpenAPI trae una búsqueda de clientes por email, queda como la forma de verificar por código.
- **Pedidos solo a direcciones públicas.** La URL base tiene que ser HTTPS y pública. Antes de conectar se resuelve el dominio y se rechaza si apunta a una dirección interna. El cliente no sigue redirecciones.
  - **Límite conocido:** entre la verificación del dominio y la conexión, el dominio podría cambiar de dirección. Para cerrarlo hay que fijar la dirección resuelta en la conexión.
- La API simulada de Acme queda solo para la demo: ninguna otra empresa la recibe.

### 6. Chat propio de cada empresa

- Cada empresa tiene su chat en `/c/<enlace>`. El enlace lleva una parte al azar y aparece en Configuración.
- Usa el agente de esa empresa: sus tipos activos, sus herramientas aprobadas y sus documentos. Las conversaciones aparecen en sus Casos.
- Es la misma página del chat de la demo, con el nombre de la empresa y sin los ejemplos de Acme.
- Rigen los mismos límites: 20 mensajes por sesión y los topes diarios.

### 7. Acme sale del mismo flujo

`configure_acme` (`app/demo/acme.py`) arma Acme por el camino de cualquier empresa:

1. Conecta su sistema: la API simulada, con su OpenAPI.
2. Genera las herramientas desde el OpenAPI con el generador del onboarding. Quedan sin aprobar.
3. Carga los tipos de caso inactivos.
4. Hace la revisión: aprueba cada herramienta con sus límites y activa cada tipo con sus instrucciones.

- **Se borraron los cuatro manifiestos escritos a mano.** Los generados son equivalentes: mismo esquema, riesgo, control de dueño e idempotencia.
- **Lo que sigue curado a mano** es lo que una empresa edita al revisar: las instrucciones de los 8 tipos de caso y los límites de las herramientas.
- **Límite:** los nombres de los 8 tipos de Acme no salen de una corrida del descubrimiento. Un modelo real les pone otros nombres en cada corrida, y los evals guionados dependen de esos nombres.
- El reporte de Acme del hito 4 quedó en otro tenant ("Acme Store (onboarding)"). El panel lo sigue mostrando si Acme no tiene una corrida propia.
- En una base que ya tenía la demo, `make demo-setup` lleva la configuración al mismo estado. No hace falta para que la demo siga andando.

## Pendiente

- Magic link y roles (hoy cada empresa tiene un usuario dueño).
- PDF en la ingesta y almacenamiento de archivos.
- Enviar por email el código de verificación: en el chat de una empresa sigue saliendo por la consola del servidor.
- Borrar una empresa con un comando (spec).
- Plantillas de herramientas para empresas sin OpenAPI.
- Avisar cuando termina el análisis.
