# 080 — Simulaciones por empresa (Hito 12, tarea 3)

**Contexto.** El reporte de preparación prueba el agente con evals de un solo mensaje, armados desde el historial. No prueba conversaciones de varios turnos, con clientes de distinto ánimo, ni mide si el resultado es estable cuando se repite la misma conversación. El Hito 12 pide hacer eso:

- generar clientes simulados desde los documentos, los tipos de caso y las reglas;
- conversar varios turnos con el agente;
- correr cada conversación 3 veces;
- que un juez califique;
- dar el resultado pass^3 por tipo de caso, con un botón en el panel, dentro del informe de preparación.

La spec no define cómo se aísla la simulación ni cómo se cuenta.

**Recomendación.**

- **Clientes simulados.** Una sola llamada al modelo que genera evals (`claude-opus-5-5`) arma N clientes. Cada uno tiene:
  - tipo de caso;
  - idioma;
  - tono: `enojado`, `confundido` o `apurado`;
  - objetivo;
  - primer mensaje.

  El modelo recibe, como dato:
  - los tipos de caso activos, con su objetivo y sus condiciones;
  - un extracto de los documentos;
  - las reglas de atención.

  Si la llamada falla o devuelve menos clientes, se completa en código: se recorren los tipos y los tonos, y el objetivo es el del tipo de caso.
- **Conversación.** El cliente simulado lo juega el modelo chico (`claude-haiku-5-5`), con la herramienta `hablar_como_cliente`. Esa herramienta devuelve el próximo mensaje o `terminar` cuando el cliente logró su objetivo o se cansa. Son hasta 4 mensajes del cliente por conversación.
- **Repeticiones y resultado.** Cada cliente conversa 3 veces, desde cero. Un juez (`claude-opus-5-5`, herramienta `juzgar_simulacion`) califica cada corrida con tres preguntas: logró el objetivo, respetó las reglas y la marca, la respuesta fue correcta. Una corrida pasa si cumple las tres.
  - **pass^3 de un cliente:** pasó las 3 corridas.
  - **pass^3 de un tipo de caso:** la proporción de sus clientes con pass^3.
  - **pass^3 general:** la proporción de todos los clientes con pass^3.
- **Aislamiento.** La simulación nunca toca el sistema real ni deja datos:
  - Cada corrida va en un savepoint que se revierte (casos, mensajes, auditoría, aprobaciones).
  - Las herramientas usan el sandbox armado desde el OpenAPI de la empresa (`SandboxRuntime`, el mismo del reporte de preparación), también con la conexión real activa. Para Acme se usa el OpenAPI de su API simulada.
  - El cliente simulado está verificado en el sandbox.
  - El uso de los modelos se guarda aparte y se escribe en `usage` después de revertir: el costo de simular queda registrado.
- **Tamaño.** "Simular 20 conversaciones" son 20 clientes por 3 corridas, o sea 60 conversaciones. Corre en segundo plano como un job (`simulation`) del worker de la API. La tabla `simulation_runs` (migración 0031, con RLS) guarda el estado, los clientes, cada corrida con la calificación del juez y el resumen. El job es idempotente: una corrida ya terminada no se repite, y una que falló a mitad empieza de nuevo sin duplicar nada, porque todo va en la transacción del job.
- **En el panel.** El Reporte suma la tarjeta **Simulaciones** con:
  - el botón "Simular 20 conversaciones" (admins);
  - el estado de la última corrida;
  - el pass^3 general y por tipo de caso;
  - las corridas que fallaron, con el motivo del juez.

  El informe de preparación para salir en vivo (la tarjeta de salir en vivo) muestra el pass^3 de la última simulación. Es informativo: no cambia el umbral de salida de la decisión 056.
- **Tests.** Usan el modelo simulado: genera clientes, conversa y juzga de forma determinista.
