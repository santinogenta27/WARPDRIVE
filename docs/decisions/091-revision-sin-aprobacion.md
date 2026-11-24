# 091 — "Quedó en revisión" sin ninguna aprobación

**Contexto.** En la muestra con el modelo real del QA 2 (etapa 1), con el reembolso limitado a
1000 desde Agent → Actions, el guardián negó un reembolso de 35.000. Pero gpt-oss-20b le dijo al
cliente "Iniciamos el proceso de reembolso…, queda en revisión por nuestro equipo" (y, en otra
corrida, "El reembolso está en revisión por nuestro equipo"). Es una promesa falsa: nadie lo iba a
revisar. Los controles de conducta (decisiones 040 y 048) no lo frenaban, porque solo miraban la
primera persona del singular, el plural con "ya" y las palabras de algo terminado. El supervisor de
respuestas (decisión 079), que es un modelo chico, tampoco lo frenó.

**Opciones.**
1. Pedírselo al prompt. No alcanza: el prompt ya dice que no prometa lo que no hizo una
   herramienta.
2. Un control en código: si la respuesta dice que algo quedó en revisión, pendiente de aprobación
   o que "iniciamos el proceso/la solicitud", tiene que haber una aprobación. Puede ser una
   creada en este turno o una pendiente del caso. Si no la hay, se rechaza una vez con la
   corrección de siempre y, si insiste, se deriva (`accion_sin_herramienta`).

**Recomendación (aplicada): 2.** Es el mismo mecanismo que las acciones afirmadas. "Sigue en
revisión" no se controla porque es el estado de una aprobación de antes. Las políticas en plural
("reembolsamos el total en 10 días") tampoco cambian. La consulta a `approvals` se hace solo
cuando la respuesta afirma una revisión, así que el resto de los mensajes no suma viajes a la base.
