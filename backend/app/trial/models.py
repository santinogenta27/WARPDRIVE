"""El agente del chat de una prueba gratis (decisión 100).

Es el mismo agente que se vende (`Agent`), con el modelo y los embeddings de la prueba: Gemini con
la clave propia de la prueba (la lee `keys`), por `LLMClient` y con el uso en `usage`. Nunca con
las claves de la plataforma.

Sin la clave, o con su cuota agotada, el agente no tiene modelo y deriva el caso, como cualquier
empresa (decisión 098): el mensaje del visitante queda guardado. Lo único que cambia es lo que
ve el visitante: "la demo está muy ocupada, probá en unos minutos", en el chat y en el historial.

Una prueba convertida en piloto deja de ser prueba (`service.for_tenant`): vuelve al agente de la
plataforma.
"""

import uuid
from dataclasses import replace

import psycopg

from app.agent.loop import Agent, AgentResult
from app.db.connection import tenant_scope
from app.llm.client import LLMClient, PostgresUsageRecorder
from app.policy.guardian import Guardian
from app.trial import keys, service
from app.trial import language as visitor

BUSY = {
    "en": (
        "Our demo is very busy right now. Please try again in a few minutes; your message is saved."
    ),
    "es": (
        "Nuestra demo tiene mucha demanda en este momento. Probá de nuevo en unos minutos; tu "
        "mensaje quedó guardado."
    ),
}


class TrialAgent(Agent):
    def __init__(
        self,
        conn: psycopg.Connection,
        guardian: Guardian,
        language: str,
        store_name: str | None = None,
    ) -> None:
        recorder = PostgresUsageRecorder(conn)
        self.busy_hit = False
        llm = LLMClient(provider=keys.TrialProvider(on_busy=self._hit), recorder=recorder)
        super().__init__(conn, llm, keys.TrialEmbedder(recorder, on_busy=self._hit), guardian)
        self._language = "en" if language == "en" else "es"
        self._reply = self._language  # el idioma del visitante (decisión 101)
        self._store_name = store_name

    def _hit(self) -> None:
        self.busy_hit = True

    def use_language_of(self, tenant_id: uuid.UUID, contact_id: uuid.UUID, text: str) -> str:
        """Contesta en el idioma en que escribe el visitante: el de este mensaje o, si no es
        claro (un número de pedido, un código), el de sus mensajes anteriores."""
        with tenant_scope(self._conn, tenant_id):
            rows = self._conn.execute(
                "select m.text from case_messages m join cases c on c.id = m.case_id"
                " where c.contact_id = %s and m.role = 'customer'"
                " order by m.created_at desc limit 5",
                (contact_id,),
            ).fetchall()
        self._reply = visitor.choose(text, [r[0] for r in rows], self._language)
        return self._reply

    def _tenant(self, tenant_id: uuid.UUID) -> tuple[str, str, str]:
        """Como el de siempre, con dos cambios: el nombre es el de la tienda (el mismo del
        encabezado del chat), nunca el interno "Prueba · dominio"; y el idioma es el del
        visitante."""
        name, tone, lang = super()._tenant(tenant_id)
        if self._store_name is None:  # se lee antes del turno: el agente no ve `trials`
            trial = service.for_tenant(self._conn, tenant_id)
            self._store_name = trial.name if trial is not None else name
        name = self._store_name
        if self._reply != lang:
            lang = self._reply
            tone = "english" if lang == "en" else "rioplatense"
        return name, tone, lang

    def handle_message(self, tenant_id: uuid.UUID, contact_id: uuid.UUID, text: str) -> AgentResult:
        self.busy_hit = False
        self.use_language_of(tenant_id, contact_id, text)
        result = super().handle_message(tenant_id, contact_id, text)
        if not (self.busy_hit and result.action == "derivar"):
            return result
        message = BUSY[self._reply]
        with tenant_scope(self._conn, tenant_id):
            self._conn.execute(
                "update case_messages set text = %s where id = (select id from case_messages"
                " where case_id = %s and role = 'agent' and text = %s"
                " order by created_at desc limit 1)",
                (message, result.case_id, result.message),
            )
        return replace(result, message=message)


def agent_for(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Agent | None:
    """El agente de prueba si la empresa es una prueba en curso; si no, None (el de siempre)."""
    trial = service.for_tenant(conn, tenant_id)
    if trial is None:
        return None
    from app.companies.agent import build_guardian

    return TrialAgent(conn, build_guardian(conn, tenant_id), trial.language, trial.name)


def reset() -> None:
    keys.reset()
