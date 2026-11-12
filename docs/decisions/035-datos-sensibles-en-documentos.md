# 035 — Datos sensibles en los documentos del onboarding

**Estado:** adoptada (12 de noviembre de 2026). Hito 7, tarea 5. Cierra el pendiente de la decisión 030.

## Contexto

Los chats y tickets se anonimizan antes de guardarse. Los documentos no: son las políticas de la empresa, y su email de soporte o su teléfono son parte de lo que el asistente responde (decisión 030). Quedaba un riesgo: que alguien suba como documento un archivo con datos de clientes, y ese texto termine en el RAG y en una respuesta a otro cliente.

## Decisiones

### 1. Tarjetas y DNI se reemplazan siempre

En todo documento (PDF, DOCX, Markdown, texto o URL), antes de guardarlo:

- **Números de tarjeta:** 13 a 19 dígitos que pasan el control de Luhn → `<CARD_1>`.
- **DNI:** con puntos (`28.456.789`) o con la palabra DNI adelante (`DNI 30111222`) → `<DNI_1>`.
- El mismo número recibe siempre el mismo marcador.

Ninguno de los dos hace falta en una política: reemplazarlos no le quita nada al asistente.

### 2. Lo de la empresa no se toca

- Emails, teléfonos y CUIT quedan como están.
- **Los montos no son DNI:** `$ 28.456.789` o `12.500.000 pesos` no se reemplazan. Se reconocen por el signo `$` adelante, o por "pesos", "dólares", "ARS", "USD" o decimales después.
- Un número de 8 dígitos sin la palabra DNI (un número de pedido) tampoco.

### 3. Un documento con datos de varias personas espera confirmación

- Si el documento tiene **3 o más emails distintos, o 3 o más teléfonos distintos**, parece una lista de personas y no una política.
- **No se indexa.** La pantalla Onboarding avisa cuál es y por qué ("4 emails y 4 teléfonos distintos"), con dos botones: **Indexar igual** y **Descartar**.
- El resto de la carga sigue: los demás archivos se guardan.
- El aviso muestra el motivo, nunca los datos.

Por qué 3: una empresa suele tener una o dos casillas (ventas, soporte) y una o dos líneas. Con dos, una política normal no frena.

### 4. Dónde queda el documento mientras espera

- En la tabla nueva `pending_documents` (migración 0020), con RLS. Guarda el texto **ya sin tarjetas ni DNI**, y el hash del archivo original.
- **Indexar igual:** pasa al RAG, queda como fuente y sale de la espera.
- **Descartar:** se borra. No queda nada guardado, y se puede volver a subir.
- Subir el mismo archivo mientras espera no lo duplica.
- El archivo original no se conserva, como siempre.

Opción descartada: frenar toda la carga y pedir que la suban de nuevo. No hace falta guardar nada, pero la empresa pierde los demás archivos de esa carga y tiene que repetirla.

### 5. Por línea de comandos no hay espera

El pipeline por comando (`python -m app.onboarding.pipeline`) no tiene pantalla donde confirmar: indexa el documento, ya sin tarjetas ni DNI. La espera es solo del panel.

## Límites conocidos

- **Es por patrones.** Un DNI escrito sin puntos y sin la palabra DNI no se detecta. Un número con puntos que no es un DNI ni un monto (un código interno `12.345.678`) se reemplaza.
- **Los nombres no se detectan.** En una política, un nombre suele ser el de la empresa o el de un sector.
- **Un documento con uno o dos emails de clientes no frena.** El umbral evita molestar con políticas normales; no reemplaza mirar qué se sube.
- **Los documentos ya indexados no se revisan.** Vale para lo que se sube desde ahora. Para limpiar uno viejo hay que subirlo de nuevo con otro nombre o contenido; hoy no hay una pantalla para borrar un documento.

## Pendiente

- Pantalla para ver y borrar documentos ya indexados.
- Revisar los documentos indexados antes de esta decisión.
- Umbral configurable por empresa.
