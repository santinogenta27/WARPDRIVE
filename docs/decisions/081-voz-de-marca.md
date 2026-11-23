# 081 — Voz de marca (Hito 12, tarea 4)

**Contexto.** Hoy la empresa elige solo el idioma, y con él el tono:

- rioplatense con voseo;
- neutro con tuteo;
- inglés.

El Hito 12 pide una voz de marca en Configuración, que se aplique en el prompt y que la controle el supervisor (decisión 079). Tiene seis campos:

- tono;
- trato (tú, vos o usted);
- palabras a usar;
- palabras a evitar;
- emojis sí o no;
- firma.

La spec no define cómo se guarda ni qué parte se controla en código.

**Recomendación.**

- **Dónde se guarda.** En `settings.brand_voice` de la empresa:
  - `tono`: texto libre, hasta 120 caracteres, por ejemplo "cálido y cercano, sin vueltas";
  - `trato`: `vos`, `tu` o `usted`;
  - `usar` y `evitar`: hasta 15 palabras o frases cada una;
  - `emojis`: sí o no;
  - `firma`: hasta 80 caracteres.

  Sin voz de marca configurada, nada cambia: el prompt y las líneas base de los evals siguen iguales.
- **Trato.** Se guarda también como el tono de la empresa:
  - `vos` es `rioplatense`, con la conversión a voseo de siempre;
  - `tu` es `neutro`;
  - `usted` es un tono nuevo, `usted`.

  En una empresa en inglés el trato no aplica y no se muestra.
- **En el prompt.** El bloque `<voz_de_marca>` del prompt del sistema lleva el tono, el trato, las palabras a usar y a evitar y la regla de emojis. La firma no va al modelo.
- **En código:**
  - **Firma:** se agrega al final de cada respuesta redactada por el agente, la que pasó por la herramienta responder. Los mensajes fijos (verificación, negativa, cierre) no la llevan.
  - **Emojis:** con los emojis apagados, se sacan de la respuesta antes de enviarla.
- **El supervisor.** Recibe la voz de marca en `<tono>`. Antes de llamar al modelo hace un control fijo, sin costo: si la respuesta usa una palabra a evitar, pide reescribir con el motivo concreto. Los emojis no hace falta controlarlos: el código ya los saca. El resto (tono, trato, palabras a usar) lo juzga el modelo del supervisor. Si el supervisor está apagado, la voz igual se aplica en el prompt, la firma y los emojis.
- **Panel.** Configuración → Voz de marca, un formulario, solo para admins (Configuración ya lo es).
