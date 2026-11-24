# 098 — Sin ningún modelo disponible, el caso se deriva

**Contexto.** En la etapa 3 del QA 2 se agotaron en el mismo día las cuotas gratuitas de Groq y de
Gemini (el respaldo, decisión 042). Con las dos agotadas, el chat web mostraba "Tuvimos un problema
para responderte. Probá de nuevo en un rato". La transacción del mensaje se revertía, así que el
mensaje no quedaba en ningún caso y el equipo no se enteraba. Por WhatsApp, el job reintentaba y
después fallaba. Con un modelo pago también puede pasar: una caída del proveedor o el límite por
minuto.

**Opciones.**
1. Dejarlo así y confiar en que el plan pago no se agota.
2. Tratarlo como el tope de gasto (decisión 049): el mensaje se guarda, el caso se deriva con la
   causa `modelo_no_disponible`, el cliente recibe el mensaje fijo de derivación y el equipo recibe
   el aviso de caso derivado.

**Recomendación (aplicada): 2.** La regla de la spec es que, si no hay respuesta con fuente o de
una herramienta, se deriva a una persona. La alerta al operador por cuota sigue: queda en el log y
en la auditoría del caso.
