# 030 — PDF en la ingesta del onboarding

**Estado:** adoptada (11 de noviembre de 2026).

## Contexto

La spec lista PDF entre los documentos de la ingesta. La decisión 021 lo dejó afuera para no sumar una dependencia: la pantalla pedía subirlo como DOCX o Markdown. Las políticas de una empresa suelen estar en PDF.

## Decisiones

### 1. Se acepta `.pdf` con texto, con el mismo flujo que DOCX

- La pantalla Onboarding acepta `.pdf` junto con `.md`, `.txt` y `.docx`.
- Se extrae el texto, se parte en fragmentos y se indexa en el RAG de la empresa. El archivo original no se conserva.
- Cada página es un párrafo. El título es la primera línea del documento.
- Subir dos veces el mismo PDF no duplica nada (hash del archivo), como con los demás.

### 2. Dependencia nueva: `pypdf` 7.0.1

- **Por qué hace falta:** leer el texto de un PDF pide interpretar su estructura, sus fuentes y sus codificaciones. No es razonable hacerlo a mano, como sí se hizo con DOCX (un zip con XML).
- **Por qué `pypdf`:**
  - Python puro, sin dependencias propias ni binarios del sistema: se instala igual en Render gratis, en el CI y en Windows.
  - Licencia BSD de 3 cláusulas.
  - Mantenida y muy usada.
- **Descartadas:**
  - `pdfminer.six` y `pdfplumber`: extraen mejor las tablas, pero traen más dependencias.
  - `PyMuPDF`: más rápida, pero es un binario con licencia AGPL.
  - OCR (`tesseract`): necesita un binario del sistema. No entra en el plan gratis de Render.

### 3. Un PDF sin texto se avisa, no falla

- **PDF escaneado:** sus páginas son imágenes. Si no sale ninguna letra, la pantalla dice: "`archivo.pdf` es un PDF escaneado: son imágenes, sin texto para leer. Subí una versión con texto (exportada desde Word o Google Docs), o pasalo a DOCX o Markdown."
- **PDF con contraseña:** pide una copia sin contraseña.
- **PDF dañado:** pide exportarlo de nuevo.
- **PDF de más de 500 páginas:** pide subirlo en partes. Extraer el texto es lento y el servicio gratis tiene un solo proceso.
- **El resto de la carga sigue.** Si en la misma carga hay otros archivos, se guardan. El que no se pudo leer no deja nada guardado: se puede subir de nuevo, ya corregido.
- No hay OCR. Queda pendiente.

### 4. Datos personales: los documentos no se anonimizan

**Actualización (12 de noviembre de 2026, decisión 035):** en los documentos ahora se reemplazan los números de tarjeta y los DNI, y un documento con datos de varias personas espera la confirmación de la empresa antes de indexarse. Los emails, teléfonos y CUIT de la empresa siguen sin tocarse, por el motivo de abajo.

Esto ya era así con DOCX y Markdown, y PDF sigue la misma regla.

- **Chats y tickets:** se anonimizan antes de guardarse. Son conversaciones con clientes.
- **Documentos:** se indexan como están. Son las políticas de la empresa, y sus datos de contacto (el email de soporte, un teléfono, el CUIT) son parte de la respuesta que el asistente tiene que dar. El anonimizador no distingue el email de soporte del email de un cliente: los reemplazaría a los dos.
- El texto de la pantalla decía que los datos personales se reemplazan "antes de guardar", sin aclarar. Ahora dice que eso vale para chats y tickets.
- **Riesgo:** si alguien sube como documento un PDF con datos de clientes (una factura, una planilla), ese texto queda en el RAG. ~~Queda pendiente decidir si se anonimizan tarjetas y DNI también en los documentos.~~ Resuelto en la decisión 035.

### 5. Arreglo: documentos con saltos de línea de Windows

- Un Markdown o `.txt` guardado en Windows (CRLF) quedaba sin fragmentos: el documento figuraba subido, pero el asistente no encontraba nada en él.
- Ahora los saltos de línea se normalizan al leer.
- Se encontró porque el test del onboarding fallaba en Windows cuando Git traía los documentos de Acme con CRLF.

## Fixtures

`evals/fixtures/acme/onboarding/generate_pdf.py` genera los dos PDF de prueba de Acme, sin dependencias: `garantia.pdf` (dos páginas con texto) y `garantia_escaneada.pdf` (una imagen). Un test verifica que los archivos guardados son los que produce el generador.

## Pendiente

- OCR para PDF escaneados.
- Tablas: el texto de una tabla sale fila por fila, sin columnas.
- ~~Anonimizar tarjetas y DNI en documentos.~~ Hecho en la decisión 035.
