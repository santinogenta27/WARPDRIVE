# 092 — El supervisor ve las fuentes citadas

**Contexto.** En la muestra con el modelo real del QA 2, una empresa nueva subió su documento de
envíos y preguntó "¿Cuánto tarda el envío a Ushuaia?". El agente respondió bien ("tarda 18 días
hábiles") y citó el documento en `fuentes`, pero el supervisor de respuestas (decisión 079) pidió
reescribir dos veces con el motivo "Falta citar la fuente de la información" y el caso terminó
derivado. Las reglas de consulta general dicen "citalos en fuentes" y el supervisor solo veía el
texto de la respuesta, no el campo `fuentes`. Así, una empresa que solo cargó documentos derivaba
casi todas las preguntas.

**Opciones.**
1. Sacar "citalos en fuentes" de las reglas que ve el supervisor. Arregla este caso, pero el
   supervisor seguiría sin saber qué documento usó la respuesta.
2. Pasarle al supervisor las fuentes citadas en `<fuentes_citadas>` y decirle, en su prompt, que
   las fuentes van aparte y que no pida nombrarlas en el texto.

**Recomendación (aplicada): 2.** Le da al supervisor el dato que le faltaba para comparar la
respuesta con el documento correcto, y no cambia las reglas de la empresa ni el prompt del agente.
Verificado con gpt-oss-20b: la misma pregunta ahora se responde con el documento.
