# 083 — Resultados medibles (Hito 12, tarea 6)

**Contexto.** Para cobrar por resultado hace falta una "resolución autónoma" que la empresa pueda auditar. También hace falta saber de qué hablan los clientes y por qué se deriva, y que los admins reciban un resumen sin entrar al panel. La regla de cobro de hoy (decisión 027) cuenta un caso resuelto por el asistente y no reabierto en 7 días. El Hito 12 pide una regla más estricta y explícita, y la spec no define cómo se arma el informe ni cómo se agrupa el análisis.

**Recomendación.**

- **Resolución autónoma** de un caso resuelto en el mes. Se cumple si se dan las cuatro condiciones:
  1. sin derivación: nunca tuvo un resumen de derivación (`cases.escalation`);
  2. lo resolvió el asistente (`resolved_by = 'agent'`): ninguna persona intervino;
  3. sin reapertura en 72 h: no hay un `case_reopened` a menos de 72 h del cierre anterior. Desde ahora, la reapertura guarda en la auditoría las horas que pasaron desde el cierre. Una reapertura anterior a esta tarea, sin ese dato, cuenta como dentro de las 72 h, que es lo prudente;
  4. sin CSAT negativo: el cliente no respondió "no" a "¿se resolvió?".

  Un caso que cumple todo pero cerró hace menos de 72 h queda "en ventana": todavía no es definitivo. Cada caso tiene un estado y un motivo:
  - `autonoma`;
  - `en_ventana`;
  - `derivada`;
  - `con_persona`;
  - `reabierta_72h`;
  - `csat_negativo`.

  La regla de cobro anterior (`billing`) no cambia. Esta es la medida que se informa y con la que se puede cobrar por resultado.
- **Informe mensual por empresa.** Está en la página **Resultados** del panel: elegís el mes y ves:
  - el total;
  - las resoluciones autónomas y la tasa;
  - el desglose por estado.

  Se exporta como CSV: un renglón por caso con su id, el tipo, las fechas, cada condición (sí o no) y el estado, sin datos del cliente. Con el id, el caso se audita en el panel, con su cadena de auditoría.
- **Análisis de conversaciones** de los últimos 30 días, sin modelo y sin datos personales:
  - temas principales: casos por tipo;
  - motivos de derivación: por la causa registrada (decisión 045);
  - preguntas sin respuesta: los mensajes del cliente en casos derivados por falta de fuente, por tipo inactivo, porque el agente no encontró qué responder, o marcados por el equipo como "falta información".

  Las preguntas sin respuesta se agrupan en código por la palabra significativa que más comparten. Cada grupo muestra hasta 3 ejemplos anonimizados con el anonimizador del onboarding (emails, teléfonos, documentos, tarjetas y nombres conocidos de la empresa), y además se tapan los números de pedido.
- **Informe semanal por email a los admins** (rol `owner`). Sale cada lunes con la semana anterior (lunes a domingo, hora de Buenos Aires):
  - conversaciones;
  - resoluciones autónomas;
  - derivaciones;
  - CSAT;
  - los 3 temas principales;
  - los 3 motivos de derivación;
  - cuántas preguntas quedaron sin respuesta;
  - el enlace al panel.

  Funcionamiento:
  - **Idempotente:** la tabla `weekly_reports` (migración 0033) guarda una fila por empresa y semana. Se inserta antes de enviar, en la misma transacción. Si el envío falla, se revierte y se reintenta en la próxima vuelta.
  - **Quién lo dispara:** lo revisa el worker de la API en cada vuelta, cada 10 minutos.
  - **Se puede desactivar:** está activado por defecto (`settings.weekly_report`) y se apaga desde Resultados.
  - **Excepciones:** no sale para las empresas de demo ni para las que no tienen admins.
