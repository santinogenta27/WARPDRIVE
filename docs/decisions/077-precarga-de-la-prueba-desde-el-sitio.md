# 077 — La prueba gratis se completa desde el sitio web

**Contexto.** El sitio enterprisedataai.com tiene un formulario "Try it with your store" con la
dirección de la tienda y el email. El formulario de `/try` exige un sello de tiempo firmado y tiene
un campo trampa contra bots, así que el sitio no puede enviarlo directamente.

**Opciones.**
1. Que el sitio envíe el formulario a `/probar`: habría que abrir CORS y saltear el sello de tiempo.
2. Que el sitio solo lleve al visitante a `/try?sitio=…&email=…` y la página complete los campos.

**Decisión.** La opción 2. `/try` y `/probar` aceptan `sitio` y `email` por la URL y completan los
campos (escapados y recortados a 300 y 200 caracteres). El visitante confirma con un clic y el
envío sigue pasando por el formulario de siempre, con el sello de tiempo y la trampa. No cambia
ninguna regla de la prueba.
