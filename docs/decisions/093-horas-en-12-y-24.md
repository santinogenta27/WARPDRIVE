# 093 — Horas en 12 y en 24, y "24/7", en el control de fundamento

**Contexto.** En la muestra con el modelo real del QA 2, con Acme en inglés, "Are you open on
Sundays?" terminaba derivado. El agente respondía bien ("open Monday to Saturday from 10 a.m. to
8 p.m."), pero el documento dice "de 10 a 20 hs" y el control de fundamento (decisión 006) no
encontraba el 8 en las fuentes. Después pasó lo mismo con "24/7", cuando el documento dice "las 24
horas, todos los días". Rechazaba dos veces y derivaba. Le pasaría a cualquier empresa que cambie
el idioma del agente a inglés con documentos en español, o al revés.

**Opciones.**
1. Pedirle al prompt que copie las horas tal cual. Un modelo chico no lo cumple siempre, y "8 p.m."
   es la forma natural en inglés.
2. Normalizar en el control: las horas con a.m./p.m. se pasan a 24 horas, tanto en la respuesta
   como en las fuentes ("8 p.m." es 20, "6:30 p.m." es 18:30). "24/7" vale como 24 si las fuentes
   dicen 24 y "todos los días" (o "every day", "7 días").

**Recomendación (aplicada): 2.** El control sigue rechazando una hora que no está en las fuentes
("until 9 p.m." con un documento que dice 20 hs) y un "24/7" que el documento no respalda ("24
horas hábiles").
