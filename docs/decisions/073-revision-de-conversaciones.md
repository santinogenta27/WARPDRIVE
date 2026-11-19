# 073 — Revisión de conversaciones (Hito 11, tarea 2)

**Contexto.** En los pilotos, el equipo tiene que poder decir qué respuestas del asistente estuvieron mal y convertirlas en pruebas, para que no se repitan. La spec no lo define.

**Recomendación.**

- **Marcar.** En el detalle de un caso, cada respuesta del asistente tiene un formulario: "Correcta" o "Incorrecta", un motivo (solo para las incorrectas) y un comentario. Se guarda en `reply_reviews`, una por mensaje. Volver a marcar reemplaza la anterior. Lo puede hacer cualquier persona del equipo.
- **Motivos.**
  - Información faltante
  - Información incorrecta
  - Acción equivocada
  - Debía derivar a una persona
  - Tono
  - Otro
- **Para mejorar.** Una pantalla nueva del menú lista las incorrectas que todavía no son un caso de eval. Cada una muestra:
  - la pregunta del cliente, que es su mensaje anterior a la respuesta;
  - la respuesta, el motivo y el comentario;
  - un formulario con la respuesta esperada, que escribe el equipo, y palabras clave opcionales.
- **Convertir en caso de eval.** Se crea una fila en `eval_cases` de la empresa, con el mismo esquema que los generados en el onboarding (`EVAL_CASE_SCHEMA`):
  - `mensaje`: la pregunta;
  - `esperado.respuesta_esperada`: la respuesta que escribió el equipo, para el juez;
  - `palabras_clave`: las que cargó el equipo;
  - `debe_escalar` y `accion`: `derivar` si el motivo es "Debía derivar"; si no, `responder` con las acciones que no derivan;
  - `categoria`: `revision`;
  - `origen`: `revision:<caso>`.
  El reporte de preparación ya corre todos los `eval_cases` de la empresa, así que el caso nuevo entra solo.
- **Información faltante.** Se sugiere qué documento cargar, sin llamar a un modelo:
  - el tema sale del tipo de caso;
  - si la búsqueda del turno no encontró fuentes, se dice;
  - el enlace lleva a Onboarding, donde se suben documentos.
- **Auditoría.** `reply_reviewed`, con el veredicto, el motivo y quién lo marcó, y `eval_case_created`. Los dos quedan en el caso.
