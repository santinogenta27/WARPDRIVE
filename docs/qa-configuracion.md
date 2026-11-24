# QA de configuración → comportamiento (Hito QA 2, etapa 1)

24 de noviembre de 2026, rama `hito-13`, base `DATABASE_URL_STAGING`.

**Cómo se probó.** Cada opción se cambia con el mismo pedido HTTP que manda el navegador desde el
panel (formulario con CSRF y sesión). Después se mira lo que el sistema arma antes de llamar al
modelo: las instrucciones (`system`), las herramientas que se ofrecen, las decisiones del
guardián, los documentos de la búsqueda o la respuesta del widget. Por último, se vuelve atrás y
se comprueba que el comportamiento es el de antes. Todo está en
`backend/tests/test_qa_configuracion.py`, con el modelo simulado: sin modelo real y sin costo.
Agent y las acciones de la tienda se prueban sobre Acme. Knowledge, dominios, Check installation y
pausa, sobre una empresa nueva (no de demo).

Resultado: **22 de 22 opciones pasan** después de un arreglo (la pausa, decisión 090). La muestra
con el modelo real está al final.

## Agent

| Opción | Qué se cambió | Qué se esperaba | Qué pasó | Resultado |
|---|---|---|---|---|
| Idioma | `POST /panel/configuracion/idioma` es → en → es | Instrucciones en inglés y, al volver, voseo | Cambia el tono del prompt (`TONES["english"]` ↔ `TONES["rioplatense"]`) | ✅ |
| Nombre del asistente | `POST /panel/configuracion/widget` con `asistente=Luna`, después vacío | "Te llamás Luna" en el prompt; sin nombre, nada | Igual a lo esperado | ✅ |
| Saludo | El mismo formulario, `saludo=…`, después vacío | El widget muestra ese saludo; vacío, el de siempre | `/chat/widget/config` devuelve el saludo nuevo y después el original | ✅ |
| Trato usted / tú | `POST /panel/configuracion/voz` con `trato=usted` y `trato=tu`, después vacío | Tono de usted o tuteo; al volver, voseo | Cambia el tono del prompt y vuelve a voseo; el bloque `<voz_de_marca>` desaparece | ✅ |
| Tono de la marca | `tono=cálido y breve` | "Tono de la marca: cálido y breve." en el prompt | Igual a lo esperado | ✅ |
| Palabras a usar y a evitar | `usar=genial, con gusto`, `evitar=despachado` | Las dos listas en el prompt; una palabra a evitar no llega al cliente | El supervisor la reescribe en código; al volver atrás, "despachado" vuelve a aparecer | ✅ |
| Emojis | `emojis=no` → `si` | "Nunca uses emojis." y se sacan antes de enviar | Con `no`, un emoji de la respuesta se saca; con `si`, queda | ✅ |
| Firma | `firma=— Equipo Acme`, después vacía | Las respuestas del agente terminan con la firma | Igual a lo esperado; sin firma, no aparece | ✅ |
| Regla: monto máximo | `POST /panel/configuracion/herramientas/{reembolso}` con `monto_maximo=1000` y después vacío | Un reembolso mayor se niega; sin tope, pide aprobación | `denied` con el tope; `approval_required` antes y después | ✅ |
| Regla: qué requiere aprobación | "Siempre pide aprobación" en cambiar dirección, después sin marcar | Pasa de ejecutarse a pedir aprobación y vuelve | `executed` → `approval_required` → `executed` | ✅ |
| Regla: verificación de identidad | "Exige identidad verificada" en consultar pedido: sí → no → sí | Sin exigirla no se pide el código; exigida, se pide | Con "no", el guardián igual niega el pedido ajeno por dueño ("El recurso no pertenece al contacto"); con "sí" vuelve a pedir el código | ✅ (ver nota 1) |
| Horario de atención | `POST /panel/configuracion/reglas` lunes a viernes 09–18, consulta un domingo 23 h; después sin horario | La derivación dice cuándo responden; sin horario, no | "Nuestro equipo atiende de lunes a viernes de 09:00 a 18:00…" y después nada | ✅ |
| A quién derivar | `derivar_a=guardia@acme.example`, después vacío | El aviso de caso derivado va a ese email | `team_notify.recipients` y el email enviado van a ese destino; vacío, a los de siempre | ✅ |
| Ventas: producto y descuento máximo | `POST /panel/configuracion/ventas` (Medias de running, 10 %, horarios) y después borrar | Aparece `ofrecer_producto` con "hasta 10 %"; borrado, desaparece | Igual a lo esperado | ✅ |
| Supervisor de respuestas | `POST /panel/configuracion/supervisor` no → sí | Apagado no se llama al supervisor; encendido, sí | Las llamadas a `supervisar` se frenan y vuelven | ✅ |
| Modo supervisado | `POST /panel/configuracion/supervisado` sí → no | La respuesta queda como borrador en Inbox; apagado llega directo | Con "sí", el cliente no recibe nada y hay 1 borrador; con "no", llega directo | ✅ |

## Knowledge

| Opción | Qué se cambió | Qué se esperaba | Qué pasó | Resultado |
|---|---|---|---|---|
| Subir un documento | `POST /panel/knowledge/upload` (garantia.md) | El agente responde con él | Responde "El envío a Ushuaia tarda 18 días…" | ✅ |
| Cita la fuente | El mismo documento | La respuesta lleva la fuente | `final_action.sources = ["garantia"]` en la auditoría | ✅ |
| Reemplazar | Upload con `reemplazar=<id>` y otro nombre de archivo | Usa la versión nueva | Responde "24 días" y ya no "18 días" | ✅ |
| Borrar | `POST /panel/onboarding/indexados/{id}/borrar` | Deja de usarlo en el mensaje siguiente | No lo menciona ni le llega al modelo | ✅ |
| Páginas del sitio | `POST /panel/knowledge/site` y `…/site/pages` (quitar y volver a elegir "Envíos") | Sin la página no la usa; elegida, sí | Igual a lo esperado | ✅ |
| Ask your knowledge | `POST /panel/knowledge/ask` y la misma pregunta en el chat | La misma fuente y el mismo dato | Los dos usan "Envíos al sur" y el dato de 18 días (misma búsqueda híbrida; Ask usa 4 fragmentos y el chat 5) | ✅ |

## Deploy

| Opción | Qué se cambió | Qué se esperaba | Qué pasó | Resultado |
|---|---|---|---|---|
| Dominios permitidos | `POST /panel/deploy/domains` con `www.tiendaqa.example`, después vacío | El widget carga en ese dominio y no en otro | `allowed: true` en el dominio, `false` en otro y 403 al abrir sesión desde otro; vacío, `false` | ✅ |
| Check installation | `POST /panel/deploy/check-install`, con la página del sitio sin el código, con el código, y con el widget ya cargado | "missing" → "found" → "loaded" | Igual a lo esperado (sin dominios, avisa que falta agregarlos) | ✅ |
| Pausar y reanudar | `POST /panel/salir-en-vivo` con `activo=no` y después `si` | En pausa no responde el agente y deriva; reanudado, responde | **Fallaba:** pausar solo sacaba el widget del sitio y el agente seguía respondiendo por WhatsApp y en la página del chat. **Arreglado** (decisión 090): en pausa, el widget sigue y cada mensaje se deriva sin llamar al modelo | ✅ después del arreglo |
| Acciones desactivadas de la tienda | `POST /panel/configuracion/herramientas/{cancelar}` con `aprobada=no` y después `si` | Una acción apagada no se ofrece ni se ejecuta | No aparece en las herramientas; si el modelo la pidiera, el guardián la niega ("no aprobada"); al prenderla vuelve | ✅ (ver nota 2) |

**Nota 1 — identidad.** Apagar "Exige identidad verificada" en una acción que mira datos de un
pedido no lo abre a cualquiera: el guardián controla en código que el pedido sea del contacto, y
sin verificar no puede saberlo. Es lo correcto, pero en el panel puede confundir. Queda como
pendiente aclararlo en el texto de la opción.

**Nota 2 — tienda.** Se probó con la tienda de Acme, que usa la API simulada (`mocks/acme-api`).
La conexión de una tienda real de Shopify sigue pendiente de credenciales (ver
`docs/listo-para-vender.md`).

## Muestra con el modelo real

Ver la sección siguiente, que se completa al correr la muestra con Groq y Gemini (sin modelos
pagos).
