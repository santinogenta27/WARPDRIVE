# 007 — Ciclo del agente

**Estado:** adoptada durante la corrida sin supervisión (opción más simple). Revisar.

## Decisiones

- **Acción final por herramientas.** El modelo termina llamando `responder` (mensaje, fuentes, factual) o `derivar` (motivo, resumen). La acción del resultado se deriva en código: `derivar`; `pedir_aprobacion` si el guardián creó una aprobación (tarea 8); `ejecutar` si se ejecutó con éxito una herramienta que modifica datos; si no, `responder`.
- **Garantías en código, no en el prompt:**
  - Pedir "hablar con una persona" o un tipo de caso no activo deriva sin llamar al agente.
  - Si el tipo de caso usa herramientas que piden contacto verificado y el contacto no lo está, se pide verificación sin llamar al agente (paso 4 de la spec).
  - Solo se ofrecen las herramientas que el YAML del tipo de caso habilita; el guardián vuelve a controlarlo al ejecutar.
  - Dueño del recurso: si el pedido no existe o es de otro cliente, el modelo recibe el mismo error genérico (no revela que existe).
  - El teléfono del cliente no llega al modelo.
- **Contenido externo como dato:** documentos, historial y mensaje van en etiquetas con `<` y `>` escapados, así un mensaje no puede cerrar una etiqueta ni hacerse pasar por instrucción.
- **Historial** (`case_messages`, últimos 20) como bloque de texto en el turno del usuario, no como turnos alternados: simple y sin problemas de alternancia.
- **Modelos:** Haiku 5.5 para clasificar y Sonnet 5.5 para el agente, ambos con `effort: low` (chat y clasificación; subir si los evals lo piden). Sin `temperature` (los modelos 5.5 la rechazan).
- **Rechazos del modelo** (`stop_reason: refusal`): se deriva. Queda pendiente evaluar el `fallbacks` del lado del servidor.
- **Límite de 8 pasos**: al agotarse, se deriva.

## Advertencia sobre el test

`test_agent.py` usa un Claude simulado (`evals/simulated/claude.py`) con reglas guionadas. Prueba el cableado completo (clasificador, RAG, ciclo, guardián, API, historial) y las garantías en código. **No prueba la calidad de Sonnet ni de Haiku**: eso lo miden los evals con claves reales (tarea 11).
