# 097 — Un caso ya derivado no repite la misma frase

**Contexto.** En las simulaciones del QA 2 con el modelo real, un cliente confundido siguió
escribiendo después de que el agente lo derivó ("¿Hola? ¿Hay alguien?"). El agente volvió a
derivar en cada mensaje y respondió cuatro veces la misma frase: "Te paso con una persona del
equipo, que te va a responder por este mismo chat." Para el cliente parece un bot trabado.

**Opciones.**
1. No responder nada mientras el caso espera a una persona. El cliente no sabe si su mensaje llegó.
2. Si el caso ya estaba derivado, responder otra frase fija: que ya está con el equipo, que le
   van a responder por el mismo chat y que su mensaje nuevo quedó sumado.

**Recomendación (aplicada): 2.** Sigue siendo un mensaje fijo, sin modelo, en el idioma de la
empresa. El aviso al equipo se manda una sola vez por caso, como siempre (decisión 054).
