"""Widget instalable (decisión 031): la marca de cada empresa y los dominios donde se puede usar.

El cliente pega una línea de `<script>` en su sitio. El navegador manda el encabezado `Origin`
en cada pedido al chat: si ese sitio no está en la lista de la empresa, el servidor responde
403. Es un control de navegador (como CORS): evita que otro sitio muestre el chat de la empresa,
no reemplaza los límites de uso.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import psycopg

from app import i18n
from app.db.connection import tenant_scope

DEFAULT_ACCENT = "#1e3a5f"
MAX_LOGO_BYTES = 200 * 1024
MAX_DOMAINS = 20
MAX_SUGGESTIONS = 4
_ACCENT = re.compile(r"^#[0-9a-f]{6}$")
_HOST = re.compile(r"^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
# Tipos de imagen aceptados, por sus primeros bytes. SVG no: puede llevar scripts.
_IMAGES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


class WidgetError(ValueError):
    """Dato inválido de la configuración: el mensaje se le muestra a quien la guarda."""


@dataclass(frozen=True)
class WidgetSettings:
    name: str
    accent: str
    domains: tuple[str, ...]
    suggestions: tuple[str, ...]
    has_logo: bool
    updated_at: datetime | None
    language: str = "es"  # el de la empresa (decisión 036)


def load(conn: psycopg.Connection, tenant_id: uuid.UUID) -> WidgetSettings:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select t.name, w.display_name, w.accent, w.domains, w.suggestions,"
            " w.logo is not null, w.updated_at, t.settings ->> 'language' from tenants t"
            " left join widget_settings w on w.tenant_id = t.id where t.id = %s",
            (tenant_id,),
        ).fetchone()
    assert row is not None
    return WidgetSettings(
        name=str(row[1] or row[0]),
        accent=str(row[2] or DEFAULT_ACCENT),
        domains=tuple(row[3] or ()),
        suggestions=tuple(row[4] or ()),
        has_logo=bool(row[5]),
        updated_at=row[6],
        language=i18n.normalize(row[7]),
    )


def logo(conn: psycopg.Connection, tenant_id: uuid.UUID) -> tuple[bytes, str] | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select logo, logo_type from widget_settings where logo is not null"
        ).fetchone()
    return (bytes(row[0]), str(row[1])) if row else None


def _image_type(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((kind for magic, kind in _IMAGES if data.startswith(magic)), None)


def clean_domains(text: str) -> tuple[str, ...]:
    """Una lista de dominios escrita por una persona (uno por línea o separados por comas):
    acepta `https://www.tienda.com`, `www.tienda.com`, `*.tienda.com` y `localhost`."""
    found: list[str] = []
    for raw in re.split(r"[\s,;]+", text.strip().lower()):
        if not raw:
            continue
        host = raw
        if "://" in raw:
            parts = urlsplit(raw)
            if parts.path not in ("", "/") or parts.query or parts.fragment:
                raise WidgetError(
                    f"{raw}: cargá solo el dominio, sin la ruta. Por ejemplo, www.tutienda.com."
                )
            host = parts.hostname or ""
        host = host.rstrip("/").split(":")[0]
        # `*.com` o `*` no pasan: el comodín vale para subdominios de un dominio propio.
        if host != "localhost" and _HOST.match(host) is None:
            raise WidgetError(
                f"{raw}: no es un dominio válido. Ejemplos: www.tutienda.com o *.tutienda.com."
            )
        if host not in found:
            found.append(host)
    if len(found) > MAX_DOMAINS:
        raise WidgetError(f"Podés cargar hasta {MAX_DOMAINS} dominios.")
    return tuple(found)


def save(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    name: str,
    accent: str,
    domains: str,
    suggestions: str,
    logo: bytes | None = None,
    remove_logo: bool = False,
) -> WidgetSettings:
    name, accent = name.strip(), accent.strip().lower()
    if not 2 <= len(name) <= 60:
        raise WidgetError("El nombre tiene que tener entre 2 y 60 caracteres.")
    if not _ACCENT.match(accent):
        raise WidgetError("El color tiene que ser hexadecimal, como #1e3a5f.")
    hosts = clean_domains(domains)
    lines = [line.strip() for line in suggestions.splitlines() if line.strip()]
    if len(lines) > MAX_SUGGESTIONS or any(len(line) > 80 for line in lines):
        raise WidgetError(
            f"Hasta {MAX_SUGGESTIONS} preguntas sugeridas, de 80 caracteres como máximo."
        )
    logo_type = None
    if logo:
        logo_type = _image_type(logo)
        if logo_type is None:
            raise WidgetError("El logo tiene que ser una imagen PNG, JPG o WebP.")
        if len(logo) > MAX_LOGO_BYTES:
            raise WidgetError("El logo pesa más de 200 KB. Subí una versión más chica.")
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into widget_settings (tenant_id, display_name, accent, domains, suggestions)"
            " values (%s, %s, %s, %s, %s) on conflict (tenant_id) do update set"
            " display_name = excluded.display_name, accent = excluded.accent,"
            " domains = excluded.domains, suggestions = excluded.suggestions, updated_at = now()",
            (tenant_id, name, accent, list(hosts), lines),
        )
        if logo:
            conn.execute(
                "update widget_settings set logo = %s, logo_type = %s where tenant_id = %s",
                (logo, logo_type, tenant_id),
            )
        elif remove_logo:
            conn.execute(
                "update widget_settings set logo = null, logo_type = null where tenant_id = %s",
                (tenant_id,),
            )
    return load(conn, tenant_id)


def origin_allowed(origin: str | None, host: str, domains: tuple[str, ...]) -> bool:
    """¿Puede el sitio `origin` usar el chat? Sin `Origin` no es un navegador en otro sitio. El
    propio servidor (`host`, donde vive `/c/…`) siempre puede."""
    if not origin:
        return True
    try:
        parts = urlsplit(origin)
        site = (parts.hostname or "").lower()
    except ValueError:
        return False
    if not site:
        return False
    if parts.netloc.lower() == host.lower():  # mismo servidor y mismo puerto
        return True
    for domain in domains:
        if domain.startswith("*."):
            if site.endswith(domain[1:]):
                return True
        elif site == domain:
            return True
    return False


def greeting(name: str, language: str = "es") -> str:
    return i18n.text(language, "greeting", empresa=name)


def snippet(base_url: str, slug: str) -> str:
    """La línea que el cliente pega en su sitio."""
    return f'<script src="{base_url.rstrip("/")}/widget.js" data-chat="{slug}" async></script>'
