# 095 — El asistente se presenta una sola vez

**Contexto.** En el recorrido de punta a punta del QA 2 (etapa 2), con el modelo real y el
asistente llamado "Sunny", cada respuesta empezaba con "Hello, I am Sunny, the virtual assistant
for Brightside Outdoor", también después de verificar al cliente ("All set, you're verified!
Hello, I am Sunny…") y en medio de un reclamo. El prompt decía "Te llamás X: presentate con ese
nombre" (decisión 052) sin decir cuándo.

**Opciones.**
1. Sacar la presentación del prompt y dejar que la haga el saludo del widget. En WhatsApp no hay
   saludo del widget, así que el asistente nunca diría su nombre.
2. Presentarse solo en la primera respuesta, cuando `<historial>` está vacío, y no repetirlo
   después.

**Recomendación (aplicada): 2.** El modelo ya recibe el historial en cada turno, así que puede saber
si es la primera respuesta. No cambia nada para las empresas sin nombre de asistente.
