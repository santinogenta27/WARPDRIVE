# Listo para vender — informe del Hito QA 2

24 de noviembre de 2026, sobre `hito-13` y la base `DATABASE_URL_STAGING`. Cada falla se arregló en
su rama, con su PR contra `hito-13` mergeado con el CI en verde (#60 a #71). Sin modelos pagos: Groq
(gpt-oss-20b) con Gemini de respaldo. La auditoría del Hito 9 que había en este archivo está en el
historial de git.

## En una línea

El producto funciona de punta a punta para una empresa nueva: se registra, carga sus documentos, se
configura, instala el chat y atiende con verificación, guardián, aprobaciones y derivación. En el
camino aparecieron **9 fallas**, y las 9 se arreglaron. Antes del primer cliente faltan dos cosas que
dependen de vos: un plan de modelo que aguante tráfico real y llevar `hito-13` a `main` (Render).

## Qué funciona (con evidencia)

| Área | Qué se probó | Resultado | Evidencia |
|---|---|---|---|
| Configuración → comportamiento | Cada opción de Agent, Knowledge y Deploy se cambia desde el panel, se verifica el efecto y se vuelve atrás | 22/22 | `backend/tests/test_qa_configuracion.py`, `docs/qa-configuracion.md` |
| Lo mismo con el modelo real | Idioma, trato, firma, tope de monto, documento borrado y agente pausado | 9/9 | `evals/qa_config_real.py`, `evals/last_qa_config_real.json` |
| Cliente nuevo de punta a punta | Registro, email, PDF + DOCX + CSV, Agent, tienda, widget en una página de prueba con Chrome headless, política con fuente, pedido verificado, cupón del 25 % negado, reembolso aprobado desde Inbox, derivación con el asistente del equipo, venta con link, y todo en Home, Inbox, Customers e Insights | 27/27 | `evals/qa_e2e.py`, `evals/last_qa_e2e.json` |
| Servidor real (Render, `main`) | Registro hasta "Check your email", chat real en `/c/northwind-outfitters` y casos en el panel | 5/5 | `evals/qa_render.py`, `evals/last_qa_render.json` |
| Aislamiento y seguridad | Dos empresas no se ven en el chat ni en el panel, zona del operador cerrada, cliente sin verificar sin datos, manipulación en el chat y en un PDF subido | 11/11 (simulado) y 7/7 (modelo real) | `backend/tests/test_qa_seguridad.py`, `evals/qa_seguridad_real.py` |
| Calidad con el modelo real | 10 simulaciones de Acme con juez | 9/10 (90 %) | `evals/last_qa_simulations-acme.json` |

## Qué falló y se arregló

| # | Falla | Cómo apareció | Arreglo | Decisión · PR |
|---|---|---|---|---|
| 1 | Pausar el agente no lo pausaba: solo sacaba el widget del sitio y el agente seguía respondiendo por WhatsApp y en la página del chat | Matriz (etapa 1) | En pausa, cada mensaje se deriva sin llamar al modelo y el widget sigue en el sitio | 090 · #60 |
| 2 | Con el reembolso negado por el tope, el modelo decía "está en revisión por nuestro equipo" o "tu solicitud ya fue registrada" | Modelo real (etapa 1) | Control en código: decir que algo quedó en revisión exige una aprobación | 091 · #61 |
| 3 | Una empresa nueva derivaba las preguntas que su documento respondía, porque el supervisor pedía "citar la fuente" en el texto | Modelo real (etapa 1) | El supervisor recibe las fuentes citadas aparte | 092 · #62 |
| 4 | En inglés, "10 a.m. to 8 p.m." y "24/7" se rechazaban contra "de 10 a 20 hs" y "24 horas, todos los días", y la pregunta terminaba derivada | Modelo real (etapa 1) | El control de fundamento compara las horas en 24 y acepta "24/7" con respaldo | 093 · #63 |
| 5 | El asistente con nombre se presentaba en cada respuesta ("Hello, I am Sunny…") | De punta a punta (etapa 2) | Se presenta solo en la primera respuesta | 095 · #65 |
| 6 | Con la página de registro en inglés, el email de confirmación (y la invitación al equipo) llegaban en español | De punta a punta (etapa 2) | Salen en el idioma de la página donde se pidieron | 096 · #66 |
| 7 | Un cliente que seguía escribiendo en un caso derivado recibía la misma frase cuatro veces | Simulaciones (etapa 3) | Si el caso ya estaba derivado, responde que ya está con el equipo | 097 · #69 |
| 8 | Con las cuotas de los dos modelos agotadas, el chat mostraba un error, el mensaje se perdía y el equipo no se enteraba | Etapa 3, al agotarse Groq y Gemini el mismo día | Se deriva con la causa `modelo_no_disponible`, como con el tope de gasto | 098 · #71 |
| 9 | Los evals con respaldo se cortaban: el juez de Groq (qwen) no tiene equivalente en Gemini | Etapa 3 (herramienta de QA) | Con `--fallback`, el juez es el alias del agente | #70 |

También: la decisión 094 explica por qué la etapa 2 corrió en dos servidores (Render tiene `main`).

## Qué sigue pendiente, con su riesgo

| Pendiente | Riesgo | Qué hace falta |
|---|---|---|
| **Cuota de los modelos gratuitos.** En un día de QA (unos 300 turnos), se agotaron la cuota diaria de Groq y la de Gemini. Un solo cliente con tráfico real la agota antes del mediodía. | **Alto.** Desde el arreglo 8 no se pierden mensajes, pero todo se deriva a personas | Elegir un plan pago (ver la lista de abajo) |
| Etapa 3 incompleta: faltan los evals de Acme y Northwind y 4 de las 10 simulaciones de Northwind (de las 6 hechas pasaron 4). La meta de 90 % y cero fallas críticas está medida solo en Acme | Medio | Retomar mañana con los comandos de `docs/progreso.md` |
| Northwind "shopping": el agente derivó en lugar de decir que no tiene el producto, cuando la búsqueda del sandbox de la simulación no devolvió nada. No se pudo reproducir porque ya no había cuota | Medio: una consulta de compra sin resultados termina en una persona | Reproducir con `evals/qa_simulations.py` mañana |
| Acme "facturación": la regla de demo "si piden corregir una factura, derivá" se aplicó también a "¿cómo se corrige?" | Bajo: deriva de más, no inventa | Si se repite, aclarar la regla en el tipo de caso |
| Trato de usted con gpt-oss-20b: en 1 de 3 corridas mezcló formas de vos. El supervisor lo frena y deriva | Bajo con el modelo chico; hay que medirlo con el modelo que se elija | Medir con el plan elegido |
| Un documento con instrucciones maliciosas no cambia nada, pero el agente tampoco responde lo legítimo de ese documento | Bajo | Avisarle al dueño en Knowledge cuando un documento trae instrucciones |
| Render corre `main` (Hito 12): no tiene el panel del Hito 13 ni estos arreglos | Alto hasta mergear | Mergear el PR de `hito-13` contra `main` |
| La confirmación del email en Render no se pudo probar: la base de Render ya no es la del `.env` y el email necesita una casilla real | Medio | Ver la lista de abajo |
| Con la tienda simulada, el link de compra lleva al carrito de la demo de Northwind | Ninguno con Shopify real, pero no está probado | `evals/shopify_e2e.py` con una tienda de desarrollo |
| Avisos al equipo (casos derivados, alertas) siempre en español | Bajo | Pasarlos al idioma del panel |
| Apagar "Exige identidad verificada" en una acción de pedidos no la abre: el guardián igual controla que el pedido sea del cliente. Es lo correcto, pero el panel no lo explica | Bajo | Aclararlo en el texto de la opción |

## Qué tenés que hacer vos antes del primer cliente

1. **Elegir el modelo para producción y cargar la clave.** Con el nivel gratuito no alcanza
   (pendiente 1). Opciones: Claude (`ANTHROPIC_API_KEY`; es el stack de la spec, con
   `claude-sonnet-5-5` para el agente), el plan pago de Groq o el de Gemini. Cargala en Render y, si
   es por empresa, en Settings → Model del panel. Después corré los evals con ese modelo:
   `make evals` y `make evals-en`.
2. **Mergear el PR de `hito-13` contra `main`.** Render despliega solo. Después, en Render →
   **Logs**, confirmá que la migración corrió sin errores.
3. **Email real en Render.** Cargá `RESEND_API_KEY` y `MAIL_FROM` con un dominio verificado
   (`docs/render.md`). Después registrate con tu email y corré:
   `python evals/qa_render.py --browser-python <python con playwright> --confirm-link "<el enlace del email>"`.
4. **Tienda real de Shopify.** Creá la tienda de desarrollo y la app (pasos en `docs/progreso.md`,
   Hito 13), cargá `SHOPIFY_TEST_STORE`, `SHOPIFY_TEST_CLIENT_ID` y `SHOPIFY_TEST_CLIENT_SECRET` en
   `.env` y corré `python evals/shopify_e2e.py`.
5. **Retomar la etapa 3** con los comandos de `docs/progreso.md`, sección del Hito QA 2, y revisar
   que dé 90 % o más sin fallas críticas.
6. **Revisar las decisiones 090 a 098** (`docs/decisions/`). Las tomé sin tu aprobación, como pide
   el hito.
7. **Legal y operación** (pendientes de antes): términos y privacidad revisados por un abogado,
   primer backup restaurado y rotación de secretos (`docs/render.md`).
