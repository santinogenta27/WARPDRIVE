# 099 — Armado de la prueba gratis en menos de un minuto, sin quedar trabado

**Contexto.** Una prueba pedida desde enterprisedataai.com quedó más de 7 minutos en "Reading the
public pages of your site" sin avanzar ni explicar nada. Un test lo reprodujo con el 429 de cuota
diaria de Gemini en el embedder:

- `save_site` pedía un embedding por página y el 429 diario corta en la primera llamada.
- El worker corre el job en una sola transacción: al fallar, la revierte, y con ella también el
  paso a "failed". El job se reintentaba (3 veces) volviendo a rastrear las 30 páginas, y la prueba
  quedaba en `building` / `crawl` para siempre.
- Aunque todo anduviera, la página no veía el avance hasta el final: nada se confirmaba antes. Con
  30 páginas y la pausa de 6 s entre llamadas a Gemini, el armado tardaba unos 4 minutos.

**Opciones.**
1. Solo pasar a "failed" cuando falla el embedder. Es simple, pero la prueba falla cada vez que se
   agota la cuota gratuita.
2. Rearmar el armado dentro de `app/trial/` (`build.py`), sin tocar el rastreo ni el indexado
   compartidos del onboarding.

**Recomendación (aplicada): 2.**

- **Pocas páginas primero.** La portada y hasta 7 más: políticas de tipos distintos (envíos,
  devoluciones…), 3 productos y el resto. Se leen en paralelo (6 a la vez) con timeout de 8 s por
  página. A los 20 s sigue con lo que haya, y las que no llegaron quedan para después.
- **Embeddings en lote.** Una sola llamada para todas las páginas y el catálogo. Se saltean si el
  armado ya pasó los 37 s.
- **Sin embeddings, búsqueda por texto.** Con cuota agotada, una caída o un timeout, los fragmentos
  se guardan sin vector. La búsqueda por texto (tsvector) del RAG híbrido ya los encuentra: la
  búsqueda vectorial ignora los fragmentos sin vector. La prueba queda lista igual.
- **Segundo plano.** Otro job de la misma cola (`trial_build` con `phase: rest`) lee el resto de las
  páginas, hasta 30, y completa los vectores. Si sigue sin haber embeddings, vuelve a probar a los
  30 minutos, hasta 8 veces. No hizo falta tocar el worker.
- **Avance confirmado en cada paso.** Es el mismo patrón que el pipeline del onboarding. Los pasos
  que escriben en la base van en un savepoint. La página consulta `/estado` cada 1,5 s y muestra:
  - las páginas leídas, de cuántas, y la página actual;
  - los productos con foto y las políticas detectadas;
  - el tiempo de cada paso.
- **Nunca "building" para siempre.** Cualquier falla termina en "failed" con un código, y la página
  lo traduce en un mensaje claro: no se pudo abrir, el sitio bloquea, sin texto, no es público o
  error nuestro. El job no lanza, así que no se reintenta solo.
- **Reintentar.** Si falló, o si sigue armando después de 2 minutos, la página ofrece "Try again":
  - sube el número de intento y encola un job con la clave `trial-build-{id}-{intento}`, así un
    doble clic encola uno solo;
  - un job de un intento anterior no escribe nada, porque todas sus escrituras verifican el intento;
  - hay hasta 5 intentos por prueba. Cada página y el catálogo reemplazan lo suyo.
- **Email de "lista".** En la base solo está el hash del enlace. Por eso el job lleva el enlace
  cifrado con la clave de la empresa (`encrypt_for`). Se manda un solo email por prueba, con una
  marca en `progress`.
- **Clave propia (`TRIAL_GEMINI_API_KEY`).** El embedder de la prueba usa esa clave y, si no está,
  la de siempre. Es una sola llamada y no espera reintentos. El uso se registra en `usage`. Si la
  plataforma indexa con Voyage, la prueba también, porque la búsqueda del agente necesita vectores
  del mismo modelo.

**Fuera de alcance.** El chat de la prueba es el agente compartido (`companies/agent.py`): usa el
proveedor y el embedder de la plataforma. Para que use la clave de la prueba hay que agregar un
parámetro opcional en el armado del agente. Queda propuesto para una tarea aparte.
