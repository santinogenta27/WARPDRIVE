# 050 — Carga de documentos desde el panel

**Contexto.** El Hito 9 pide subir PDF, DOCX, TXT, MD, CSV, páginas del sitio y exportes de chats y tickets desde el panel, con progreso y errores claros; reemplazar un archivo lo reindexa y borrarlo lo saca del RAG. Y anonimizar datos personales en documentos o justificar por qué no.

**Qué se decidió.**

- **CSV:** si tiene columnas de ticket, rol y texto, es un export de tickets (como antes). Si no, es una tabla (catálogo, precios, envíos) y se indexa como documento, una línea por fila con "columna: valor".
- **Sitio web:** Onboarding → "Sitio web de la empresa". Se leen hasta 30 páginas (`CRAWL_MAX_PAGES`) siguiendo los enlaces del mismo dominio; imágenes y archivos se saltean.
  - **Nunca una dirección interna:** se usa el mismo control que la conexión del sistema (`check_public_host`), y las redirecciones se siguen a mano, solo dentro del dominio.
  - Volver a leer el sitio reemplaza sus páginas.
  - Sin `robots.txt` ni JavaScript: es el sitio de la propia empresa, que lo pide, y la mayoría de las tiendas sirven el texto en el HTML.
- **Reemplazar:** cada documento queda con un `source` (el nombre del archivo sin extensión, o la página). Subir otro con el mismo nombre borra el anterior y lo indexa de nuevo. Un archivo idéntico se sigue salteando.
- **Borrar:** Onboarding → "Documentos indexados" → Borrar. Saca el documento y sus fragmentos del RAG en el momento. El archivo deja de figurar como subido, así se puede volver a subir.
- **Progreso y errores:** ya estaban (las 6 etapas y un aviso por archivo ilegible, decisión 030). El sitio suma su error ("no se pudo leer el sitio") sin cortar el resto.
- **Datos personales en documentos:** se mantiene la decisión 035 y no se anonimiza el resto. Las tarjetas y los DNI se reemplazan siempre; un documento con 3 o más emails o teléfonos distintos espera la confirmación de la empresa. Anonimizar todos los nombres, emails y teléfonos de un documento borraría los datos de contacto de la propia empresa, que el asistente tiene que poder dar. Las páginas del sitio son públicas: solo se les sacan tarjetas y DNI.
- **Zendesk, Freshdesk o Intercom:** sus exportes CSV y JSON ya entran por el lector de tickets, que reconoce los nombres de columna habituales. No se agregó una integración por API.
