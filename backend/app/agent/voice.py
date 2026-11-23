"""Voz de marca (Hito 12, tarea 4; decisión 081).

Configurada por empresa en `settings.brand_voice`: tono, trato (tú, vos o usted), palabras a
usar y a evitar, emojis sí o no y firma. Se aplica en el prompt (`<voz_de_marca>`), en código
(firma y emojis) y la controla el supervisor (decisión 079): una palabra a evitar es una
reescritura sin llamar al modelo; el resto lo juzga el modelo del supervisor.

Sin voz configurada no cambia nada.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope

SETTING = "brand_voice"
# El trato elige el tono del agente en español: con `vos`, el voseo de siempre.
TRATOS = {"vos": "rioplatense", "tu": "neutro", "usted": "usted"}
TRATO_NAMES = {"vos": "vos", "tu": "tú", "usted": "usted"}
MAX_TONE = 120
MAX_SIGNATURE = 80
MAX_WORDS = 15
MAX_WORD = 40
# Emojis y pictogramas (no las letras con tilde ni la ñ).
EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿⌀-⏿️‍⃣]")


class VoiceError(ValueError):
    pass


@dataclass(frozen=True)
class Voice:
    tono: str = ""
    trato: str = ""
    usar: tuple[str, ...] = ()
    evitar: tuple[str, ...] = ()
    emojis: bool = True
    firma: str = ""

    @property
    def configured(self) -> bool:
        return bool(
            self.tono or self.trato or self.usar or self.evitar or self.firma or not self.emojis
        )


def _words(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, list | tuple) else str(value or "").split(",")
    found: list[str] = []
    for item in items:
        word = " ".join(str(item).split())[:MAX_WORD]
        if word and word.lower() not in (w.lower() for w in found):
            found.append(word)
    return tuple(found[:MAX_WORDS])


def from_settings(settings: dict[str, Any] | None) -> Voice:
    data = (settings or {}).get(SETTING)
    if not isinstance(data, dict):
        return Voice()
    trato = str(data.get("trato") or "")
    return Voice(
        tono=" ".join(str(data.get("tono") or "").split())[:MAX_TONE],
        trato=trato if trato in TRATOS else "",
        usar=_words(data.get("usar")),
        evitar=_words(data.get("evitar")),
        emojis=data.get("emojis") is not False,
        firma=" ".join(str(data.get("firma") or "").split())[:MAX_SIGNATURE],
    )


def load(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Voice:
    with tenant_scope(conn, tenant_id):
        row = conn.execute("select settings from tenants where id = %s", (tenant_id,)).fetchone()
    return from_settings(row[0] if row else None)


def save(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    tono: str,
    trato: str,
    usar: str,
    evitar: str,
    emojis: bool,
    firma: str,
) -> Voice:
    if trato and trato not in TRATOS:
        raise VoiceError("Elegí un trato de la lista.")
    if len(tono.strip()) > MAX_TONE or len(firma.strip()) > MAX_SIGNATURE:
        raise VoiceError(
            f"El tono va hasta {MAX_TONE} caracteres y la firma hasta {MAX_SIGNATURE}."
        )
    voice = from_settings(
        {
            SETTING: {
                "tono": tono,
                "trato": trato,
                "usar": usar,
                "evitar": evitar,
                "emojis": emojis,
                "firma": firma,
            }
        }
    )
    stored = {
        "tono": voice.tono,
        "trato": voice.trato,
        "usar": list(voice.usar),
        "evitar": list(voice.evitar),
        "emojis": voice.emojis,
        "firma": voice.firma,
    }
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = coalesce(settings, '{}'::jsonb) || %s where id = %s",
            (Jsonb({SETTING: stored}), tenant_id),
        )
    return voice


def tone_for(voice: Voice, default: str) -> str:
    """El tono del agente: el trato elegido manda (solo en español)."""
    return TRATOS.get(voice.trato, default)


def _quoted(words: tuple[str, ...]) -> str:
    return ", ".join(f'"{w}"' for w in words)


def describe(voice: Voice, english: bool = False) -> str:
    """La voz en pocas líneas: para el prompt y para el supervisor."""
    lines: list[str] = []
    if english:
        if voice.tono:
            lines.append(f"Brand tone: {voice.tono}.")
        if voice.usar:
            lines.append(f"Use these words when they fit: {_quoted(voice.usar)}.")
        if voice.evitar:
            lines.append(f"Never use these words: {_quoted(voice.evitar)}.")
        lines.append("Emojis are allowed, in moderation." if voice.emojis else "Never use emojis.")
        return " ".join(lines)
    if voice.tono:
        lines.append(f"Tono de la marca: {voice.tono}.")
    if voice.trato:
        lines.append(f"Tratá al cliente de {TRATO_NAMES[voice.trato]}.")
    if voice.usar:
        lines.append(f"Usá estas palabras cuando encajen: {_quoted(voice.usar)}.")
    if voice.evitar:
        lines.append(f"Nunca uses estas palabras: {_quoted(voice.evitar)}.")
    lines.append("Podés usar emojis, con mesura." if voice.emojis else "Nunca uses emojis.")
    return " ".join(lines)


def prompt_block(voice: Voice, english: bool = False) -> str:
    if not voice.configured:
        return ""
    return f"<voz_de_marca>\n{describe(voice, english)}\n</voz_de_marca>"


def avoided(text: str, voice: Voice) -> list[str]:
    """Las palabras a evitar que aparecen en el texto (palabra entera, sin mayúsculas)."""
    return [
        word
        for word in voice.evitar
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE)
    ]


def strip_emojis(text: str) -> str:
    cleaned = EMOJI.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).replace(" \n", "\n").strip()


def apply(text: str, voice: Voice) -> str:
    """En código, sobre una respuesta redactada por el agente: emojis y firma."""
    if not voice.emojis:
        text = strip_emojis(text)
    if voice.firma and not text.rstrip().endswith(voice.firma):
        text = f"{text.rstrip()}\n\n{voice.firma}"
    return text
