# 024 — Arreglos de la demo después de probarla en Render

**Estado:** adoptada (10 de noviembre de 2026).

## Contexto

Probando la demo en Render aparecieron ocho problemas. El más grave estaba en el flujo central:

1. Ante "Quiero el reembolso del pedido 1005", el agente pidió "tu número de teléfono o correo" en texto libre en lugar de usar la verificación por código. Cuando el cliente mandó el email, nada lo tomó como verificación y el caso se derivó.
2. Un mensaje del equipo apareció dos veces en el chat.
3. Los casos nunca pasaban a "Resuelto".
4. Los tipos de caso se veían como identificadores (`donde_esta_mi_pedido`).
5. La charla figuraba como "sin clasificar".
6. El resumen de un caso mostraba un código de verificación o el email del cliente.
7. No había forma de empezar una demo con el panel limpio.
8. El agente a veces tuteaba ("indícanos").

## Decisiones

### 1. Verificación por código, siempre

**Causa:** el prompt decía "antes de cualquier acción sobre una cuenta, el contacto debe estar verificado". Con el arreglo 4 de calidad, la verificación arranca cuando el guardián niega una herramienta por falta de identidad. Groq leía esa regla, no llamaba a la herramienta y pedía el email por su cuenta.

Se corrigió en tres capas; las dos últimas no dependen del modelo:

- **Prompt `system_v3`:** sin verificar, usar igual la herramienta (el sistema pide el código) y nunca pedir email, teléfono, DNI ni otros datos personales.
- **Pedido de datos en texto:** si el contacto no está verificado, el tipo de caso usa herramientas con datos de la cuenta y el modelo igual pide un dato personal, esa respuesta se reemplaza por el flujo de verificación por código.
- **Email sin que se lo pidan:** un contacto sin verificar que manda un email inicia la verificación. Si el email vino solo, la consulta pendiente es su mensaje anterior.
- **Historial:** el intercambio de verificación (el pedido del email, el email, el código) ya no entra en el historial que ve el modelo. Al verlo, Groq volvía a "enviar un código" a un cliente ya verificado.
- **Red de seguridad:** si el contacto ya está verificado y el modelo igual habla de códigos o de verificar, se le devuelve una corrección una vez; si insiste, se deriva.
- **Instrucciones del reembolso:** se inicia por el total del pedido, sin preguntar el motivo ni el monto. `sync_demo_config` lleva los cambios de los fixtures a una base que ya tenía la demo cargada. Ya se aplicó en Neon.

**Medición:**

- Caso 11 de los evals, de varios turnos: reembolso → email → código → pedido de aprobación.
- `test_flujo_reembolso.py`: el flujo completo con el modelo simulado en cuatro comportamientos (normal, pide el email en texto, vuelve a pedir la verificación y tutea).
- `test_groq_real.py`: el mismo flujo con Groq real. Corre solo con `RUN_GROQ_TESTS=1`, porque gasta cuota. Resultado en "Medición con Groq".

### 2. Sin mensajes duplicados

- **En el chat:** `/chat/updates` devuelve el id de cada mensaje. La página hace una consulta a la vez y descarta los ids que ya mostró. El duplicado venía de dos consultas superpuestas, la del intervalo y la del cambio de pestaña.
- **En el panel:** cada formulario de respuesta lleva un token de envío. Un segundo POST con el mismo token no manda nada; el caso se bloquea mientras se verifica, así que dos envíos simultáneos tampoco pasan. Una aprobación ya decidida vuelve a la lista sin error. En el navegador, un formulario enviado no se vuelve a enviar.

### 3. Regla de cierre de la spec, la unidad de cobro (`app/cases/closing.py`)

- **El cliente confirma:** el mensaje entero es un agradecimiento o un "eso era todo" ("gracias", "listo, gracias", "eso era todo"). Además, ya tiene que haber una respuesta del asistente o del equipo. El agente responde con un cierre fijo, sin modelo, y marca el caso resuelto. "Gracias, ¿y el pedido 1002?" no es una confirmación.
- **24 h sin respuesta:**
  - Se resuelve un caso abierto cuyo último mensaje es del asistente o del equipo y tiene más de 24 h.
  - Un derivado se cierra solo después de que una persona le respondió.
  - Uno que espera aprobación nunca se cierra solo.
  - No hay un proceso programado (el plan gratis de Render tiene un solo servicio). El cierre corre cuando se abre la bandeja del panel y cuando el mismo cliente vuelve a escribir. Si escribe a las 25 h, su caso anterior queda resuelto y empieza uno nuevo.
- **Quién lo resolvió:**
  - `resolved_by` es `human` si una persona intervino: hubo derivación o una respuesta desde el panel.
  - Decidir una aprobación no cuenta como intervención: es parte del flujo previsto. **Revisar con el modelo comercial.**
  - El panel muestra "Resuelto por el asistente" o "Resuelto con una persona". El cierre queda en la auditoría (`case_resolved`).
- **Pendiente:**
  - La regla de reapertura de la spec: no cuenta como autónomo si se reabre en 7 días.
  - El conteo para facturar (tarea 22).

### 4 y 5. Nombres legibles

- `type_label` restituye las tildes de los identificadores: "Dónde está mi pedido", "Facturación".
- Las etiquetas del clasificador que no son tipos de la empresa tienen nombre propio: "Conversación general", "Consulta general", "Pidió hablar con una persona".
- Si el caso no tiene tipo de la empresa, el panel usa la última clasificación de la auditoría. Por eso la charla ya no figura "sin clasificar".

### 6. Resúmenes sin datos sensibles (`app/panel/privacy.py`)

- El resumen de un caso en la bandeja y en las aprobaciones es el primer mensaje del cliente que dice algo: no un mensaje que es solo el email o el código.
- Además, se le quitan:
  - los códigos de 6 dígitos (no los montos);
  - los emails, teléfonos, DNI, CUIT y tarjetas, con los mismos patrones del anonimizador del onboarding;
  - los nombres que el cliente da al presentarse.
- En el detalle del caso el equipo ve la conversación, pero los códigos se enmascaran.
- **Pendiente:** el código se guarda en claro en `case_messages` y en la auditoría. Conviene no guardarlo.

### 7. Reiniciar demo

- La auditoría es de solo inserción, también para el dueño de la base (trigger). Por eso un caso con eventos no se puede borrar.
- "Reiniciar demo" **archiva** los casos (`cases.archived_at`, migración 0013):
  - salen del panel, de las aprobaciones y del chat;
  - el agente no los retoma: un cliente que vuelve a escribir abre un caso nuevo;
  - las aprobaciones pendientes se rechazan, así no se ejecutan después;
  - los pedidos de la API simulada vuelven a su estado inicial;
  - la cadena de auditoría de cada caso sigue íntegra y lo registra (`case_archived`).
- Pide confirmación y el token anti-CSRF.

### 8. Voseo

- El tono rioplatense del prompt pide voseo con ejemplos y prohíbe "tú" y "usted".
- Antes de enviar, `to_voseo` pasa a "vos" las formas de "tú" más comunes y sin ambigüedad: "indícanos" → "indicanos", "dime" → "decime", "puedes" → "podés".
- Las formas que pueden ser de otra persona del verbo ("indica", "revisa") no se tocan.

## Medición con Groq

Ver la sección del mismo nombre en `docs/progreso.md`.
