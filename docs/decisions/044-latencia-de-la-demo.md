# 044 — Latencia de la demo en Render

**Estado:** medición y mejoras simples aplicadas (16 de noviembre de 2026). Las demás son propuestas: decidilas vos.

## Contexto

Para el video de la demo en inglés hay que saber en qué se va el tiempo de cada respuesta.

La decisión 025 ya medía cuatro pasos por turno, en la auditoría (`turn_timing`): base, modelo, búsqueda y herramientas. Faltaba:

- el tiempo de cada llamada al modelo;
- la espera por el límite de Groq, separada de la generación;
- los controles de la respuesta y el envío del email;
- verlo en el log del servidor y en el panel.

## Qué se mide ahora

`app/obs/timing.py` suma tres pasos:

- **`espera`:** la espera por el límite de tokens por minuto de Groq. Va dentro de la llamada al modelo, pero se cuenta aparte: no es tiempo de generación.
- **`verificaciones`:** los controles de la respuesta en código (fundamento, conducta y formato).
- **`email`:** el envío del código de verificación.

`turn_timing` trae además `pasos`, en orden:

- cada llamada al modelo, con su papel (`clasificador` o `agente`) y su espera;
- la búsqueda (`rag`);
- cada pedido a la API de la empresa, por su ruta (`GET /orders/{order_id}`);
- los controles y el email.

Se ve en dos lugares:

- **El log del servidor** (Render → Logs): una línea por turno, sin datos del cliente. Por ejemplo:

  ```
  [Latencia] caso=… 41.9 s · base 1.1 s (28 consultas) · modelo 2.3 s: clasificador 39.4 s (espera de Groq 37.8 s), agente 0.7 s · espera de Groq 37.8 s · búsqueda 0.6 s · herramientas 0.0 s · verificaciones 0.0 s · otros 0.0 s
  ```

- **El panel "Behind the scenes":** debajo de la latencia de cada mensaje, una línea con **Database** (y la cantidad de consultas), **Model** (con cada llamada) y, si pesan más de 50 ms, **Waiting for Groq**, **Search**, **Tools**, **Checks** y **Email**.

## Medición

Del 16 de noviembre de 2026, desde la máquina de desarrollo contra Neon (São Paulo):

- 45 ms por consulta;
- 260 a 280 ms por conexión nueva.

Con Groq real (`openai/gpt-oss-20b`), dos turnos de Northwind (5 llamadas en total), con la clienta ya verificada:

| Mensaje | Total | Base | Modelo | Espera de Groq | Búsqueda | Herramientas |
|---|---|---|---|---|---|---|
| Where is my order NW-4521? | 7,6 s | 1,5 s (36 consultas) | 2,1 s: clasificador 0,9, agente 0,5 y 0,8 | 0 | 3,9 s (primer embedding del proceso) | 0,04 s |
| What's your return policy? (enseguida) | 41,9 s | 1,1 s (28 consultas) | 2,3 s: clasificador 1,6, agente 0,7 | 37,8 s | 0,6 s | — |

Los controles de la respuesta tardan 1 a 2 ms.

**Conclusión:** el modelo genera rápido (0,5 a 0,9 s por llamada). Lo que pesa es:

1. **La espera por el límite de Groq**, cuando los mensajes llegan seguidos.
2. **La base de datos**, sobre todo desde Render (ver abajo).
3. **El primer mensaje después de un rato sin uso.**

## La región de Render

`render.yaml` tiene `region: virginia`, con el comentario de que Render no tiene región en Sudamérica. Neon está en São Paulo.

- **Distancia:** de Virginia a São Paulo, una consulta tarda unos 120 ms en ir y volver. Es una estimación: no se midió desde Render.
- **Base por turno en Render:** 30 a 47 consultas × 120 ms son **4 a 6 s**.
- **Conexiones:** cada mensaje abre dos (la del control de límites y la del turno). Cada una son unos 6 viajes: **cerca de 1,5 s** más.

Sí pesa: después de las esperas de Groq, la base es lo que más tarda en Render. Es más del doble de lo que tarda el modelo en generar.

Cuando esté desplegado, la línea `[Latencia]` de los logs de Render da el número real.

## Mejoras

### Aplicadas (simples y seguras)

1. **La búsqueda RAG en un solo viaje a la base.** Las dos listas de candidatos (vector y texto), en orden, y sus fragmentos salen de una consulta. Antes eran tres. El resultado es el mismo (los tests de recall siguen en verde).
   - Viajes por turno: de 31 a 29 en una consulta simple, de 38 a 36 con herramienta, de 49 a 47 con aprobación.
   - En Render son unos 0,25 s menos por mensaje con búsqueda.
2. **La medición, en el log y en el panel** (arriba).
3. **El indicador de "escribiendo" ya estaba.** El widget muestra los tres puntos desde que se envía el mensaje hasta la respuesta (evento `typing` del chat), y el botón de enviar queda apagado. No hizo falta cambiarlo.

### Propuestas (no aplicadas)

Ordenadas por impacto.

**A. Una base en Virginia para la demo de Render (recomendada).** Un proyecto de Neon en AWS `us-east-1`, solo para Render.

- **Impacto:** la base pasaría de 4–6 s a menos de 0,2 s por turno, y cada conexión de unos 0,7 s a unos 50 ms.
- **Además:** separa la demo de la base de desarrollo, que hoy comparten y se interfieren (tests contra la demo, deadlocks).
- **Costo:** crear el proyecto, correr `python -m app.db.migrate` y `make demo-setup` contra la base nueva, y cambiar `DATABASE_URL` en Render.
- **Por qué no se aplicó:** es infraestructura y la tenés que crear vos. La base de desarrollo puede seguir en São Paulo.

**B. Reusar conexiones.** Con un pool (`psycopg_pool`, del mismo proyecto que psycopg) o reusando la conexión del control de límites en el turno.

- **Impacto:** de 0,7 a 1,5 s menos por mensaje en Render.
- **Por qué no se aplicó:**
  - es una dependencia nueva y cambia cómo abren la conexión todas las rutas;
  - Neon suspende la base sin uso, así que el pool necesita revisar la conexión antes de usarla;
  - con la opción A, la ganancia baja a unos 100 ms.

**C. Auditoría en un viaje por evento.** Hoy cada evento son dos consultas: bloquear el caso y leer el último hash, y después insertar. Un turno registra 8 a 10 eventos, o sea 16 a 20 consultas.

- **Impacto:** guardar el último hash durante la transacción del turno ahorraría la mitad: 1 a 1,2 s en Render.
- **Por qué no se aplicó:** toca la cadena de auditoría, que es una regla no negociable. Habría que invalidar bien ese dato en commits, rollbacks y savepoints. Con la opción A, la ganancia baja a unos 20 ms.

**D. Groq: esperar solo lo necesario.** El transporte espera el reset completo del minuto cuando quedan menos de `GROQ_MIN_TOKENS` (4000) tokens. Opciones:

1. Esperar solo hasta tener los tokens que hacen falta, según la recarga que informa Groq.
2. Bajar `GROQ_MIN_TOKENS`: la variable ya existe y se cambia en Render. Si Groq responde 429, el transporte ya espera lo que pide `retry-after`.
3. Grabar con el plan pago de Groq o con Claude: sin esperas.

No se aplicó porque medirlo necesita más de las 5 llamadas a Groq permitidas.

Para el video alcanza con dejar 30 a 40 segundos entre mensajes, o cortar las esperas en la edición, como ya dice el guion.

**E. Clasificador y embedding en paralelo.**

- **Impacto:** unos 0,5 s menos por turno con búsqueda.
- **Por qué no se aplicó:**
  - el embedder registra su uso en la misma conexión, desde otro hilo;
  - el alcance de tenant y la medición son por hilo;
  - se gastaría un embedding en los mensajes que no buscan, como la charla.

**F. El primer mensaje en frío.** Tres cosas se suman:

- el plan gratis de Render duerme el servicio a los 15 minutos sin tráfico y tarda cerca de un minuto en despertar;
- Neon gratis suspende la base a los 5 minutos;
- el primer embedding del proceso tardó 3,9 s.

Antes de grabar: abrir la página, mandar un mensaje de prueba y después Reset demo. Quedó en `docs/demo-en.md`.
