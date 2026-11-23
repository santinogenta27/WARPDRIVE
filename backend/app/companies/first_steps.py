"""Primeros pasos (Hito 11, decisión 074): lo que le falta a una empresa para salir en vivo, con
el progreso real. Cada paso se mide con lo que ya hizo, nunca con una casilla marcada a mano.
"""

import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import psycopg

from app.db.connection import tenant_scope

WIDGET_SEEN = "widget_seen_at"
WIDGET_HOST = "widget_seen_host"  # desde qué dominio cargó (decisión 086)


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    detail: str
    link: str
    done: bool


# (clave, título, detalle, enlace), en el orden en que conviene hacerlos.
STEPS = [
    (
        "documentos",
        "Cargá tu conocimiento",
        "Políticas, preguntas frecuentes o tu historial: el agente responde con ellos.",
        "/panel/knowledge",
    ),
    (
        "reglas",
        "Definí las reglas de atención",
        "El horario del equipo y a quién avisar cuando el agente deriva.",
        "/panel/agent",
    ),
    (
        "marca",
        "Elegí cómo se ve tu chat",
        "El nombre, el color y el logo del chat.",
        "/panel/agent/voice",
    ),
    (
        "playground",
        "Probá tu agente",
        "Escribile como un cliente y mirá cada paso que da.",
        "/panel/test/playground",
    ),
    (
        "widget",
        "Agregá el chat a tu sitio",
        "Pegá la línea del script en tu sitio, en uno de tus dominios.",
        "/panel/deploy#widget",
    ),
    (
        "en_vivo",
        "Salí en vivo",
        "Cuando el reporte de preparación da el visto bueno, el agente atiende en tu sitio.",
        "/panel/test",
    ),
]


def steps(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[Step]:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select exists (select 1 from documents),"
            " coalesce(jsonb_strip_nulls(t.settings -> 'rules'), '{}') <> '{}',"
            " exists (select 1 from widget_settings where not auto) or t.settings ? 'brand',"
            " exists (select 1 from web_sessions where messages > 0),"
            " t.settings ? %s,"
            " coalesce((t.settings ->> 'live')::boolean, false)"
            " from tenants t where t.id = %s",
            (WIDGET_SEEN, tenant_id),
        ).fetchone()
    flags = list(row) if row else [False] * len(STEPS)
    return [
        Step(key, title, detail, link, bool(done))
        for (key, title, detail, link), done in zip(STEPS, flags, strict=True)
    ]


def note_widget_seen(
    conn: psycopg.Connection, tenant_id: uuid.UUID, origin: str | None, domains: tuple[str, ...]
) -> None:
    """El widget pidió su configuración desde uno de los dominios de la empresa: está instalado.
    Se guarda una vez (la primera)."""
    if not origin or not domains:
        return
    try:
        site = (urlsplit(origin).hostname or "").lower()
    except ValueError:
        return
    if not any(site.endswith(d[1:]) if d.startswith("*.") else site == d for d in domains):
        return
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = settings || jsonb_build_object(%s::text, now(),"
            " %s::text, %s::text) where id = %s and not settings ? %s",
            (WIDGET_SEEN, WIDGET_HOST, site, tenant_id, WIDGET_SEEN),
        )


def widget_seen(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    """El dominio desde el que cargó el chat por primera vez, o "" si todavía no cargó."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ? %s, settings ->> %s from tenants where id = %s",
            (WIDGET_SEEN, WIDGET_HOST, tenant_id),
        ).fetchone()
    if not row or not row[0]:
        return ""
    return str(row[1] or "your site")
