"""Email de confirmación de un reembolso aprobado por una persona (decisión 046).

Se envía al email con el que el cliente se verificó, en el idioma de la empresa, una sola vez
por aprobación: la fila de la aprobación se bloquea y el envío queda en `audit_events`
(`refund_confirmation_sent`), así aprobar dos veces o reintentar no manda otro. La auditoría no
guarda la dirección.
"""

import html
import uuid
from typing import Any, Literal

import psycopg

from app import i18n
from app.audit import log as audit_log
from app.db.connection import tenant_scope
from app.identity import email_code
from app.mailer import Email, Mailer, MailError
from app.obs import alerts

REFUND_TOOL = "iniciar_reembolso"
SENT = "refund_confirmation_sent"
FAILED = "refund_confirmation_failed"
Outcome = Literal["sent", "already_sent", "no_email", "failed", "not_a_refund"]


def refund_email(
    *,
    to: str,
    company: str,
    language: str,
    order: str | None,
    amount: Any,
    method: str | None,
    within: str | None,
    reference: str,
) -> Email:
    """Asunto, texto plano y HTML con el mismo estilo que el email del código (decisión 043):
    tablas y estilos en línea, sin imágenes."""

    def say(key: str, **values: Any) -> str:
        return i18n.text(language, key, empresa=company, **values)

    about = say("about_order", order=order) if order else ""
    subject = say("refund_subject", order=order) if order else say("refund_subject_plain")
    shown_amount = i18n.money(amount, language)
    facts = [
        (say("refund_method"), method or say("refund_method_default")),
        (say("refund_when"), within or say("refund_when_default")),
        (say("refund_reference"), reference),
    ]
    intro, footer = say("refund_intro", about=about), say("refund_footer")
    body = "\n".join(
        [intro, "", f"{say('refund_amount')}: {shown_amount}"]
        + [f"{label}: {value}" for label, value in facts]
        + ["", footer]
    )
    font = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    rows = "".join(
        '<tr><td style="padding:10px 0;border-top:1px solid #e4e4e7;font-size:13px;'
        f'color:#71717a">{html.escape(label)}</td>'
        '<td align="right" style="padding:10px 0;border-top:1px solid #e4e4e7;font-size:14px;'
        f'font-weight:600;color:#18181b">{html.escape(value)}</td></tr>'
        for label, value in facts
    )
    page = (
        f'<!doctype html><html lang="{i18n.normalize(language)}"><body style="margin:0;'
        'padding:0;background:#f4f4f5">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        ' style="background:#f4f4f5;padding:32px 16px"><tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        ' style="max-width:460px;background:#ffffff;border-radius:16px;'
        f'border:1px solid #e4e4e7;font-family:{font};color:#18181b">'
        '<tr><td style="padding:28px 32px 0;font-size:15px;font-weight:600">'
        f"{html.escape(company)}</td></tr>"
        '<tr><td style="padding:12px 32px 0;font-size:15px;line-height:1.5;color:#3f3f46">'
        f"{html.escape(intro)}</td></tr>"
        '<tr><td style="padding:20px 32px 0"><table role="presentation" width="100%"'
        ' cellpadding="0" cellspacing="0"><tr>'
        '<td align="center" style="background:#f4f4f5;border-radius:12px;padding:16px 0">'
        '<div style="font-size:12px;letter-spacing:.04em;text-transform:uppercase;'
        f'color:#71717a">{html.escape(say("refund_amount"))}</div>'
        '<div style="font-size:34px;font-weight:700;letter-spacing:-.01em;color:#18181b;'
        f'padding-top:4px">{html.escape(shown_amount)}</div></td>'
        "</tr></table></td></tr>"
        '<tr><td style="padding:16px 32px 0"><table role="presentation" width="100%"'
        f' cellpadding="0" cellspacing="0">{rows}</table></td></tr>'
        '<tr><td style="padding:12px 32px 28px;font-size:13px;line-height:1.5;color:#71717a">'
        f"{html.escape(footer)}</td></tr>"
        "</table></td></tr></table></body></html>"
    )
    return Email(to=to, subject=subject, body=body, html=page)


def send(
    conn: psycopg.Connection,
    mailer: Mailer,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    result: dict[str, Any],
) -> Outcome:
    """Envía la confirmación de un reembolso aprobado y ejecutado. `result`: lo que devolvió la
    herramienta (`{"ok": True, "data": {...}}`). Idempotente por aprobación."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select a.case_id, a.status, a.params, t.name, k.contact_id, n.name"
            " from approvals a join tools t on t.id = a.tool_id"
            " join cases k on k.id = a.case_id join tenants n on n.id = a.tenant_id"
            " where a.id = %s for update of a",
            (approval_id,),
        ).fetchone()
        if row is None:
            return "not_a_refund"
        case_id, status, params, tool, contact_id, company = row
        if status != "approved" or tool != REFUND_TOOL:
            return "not_a_refund"
        done = conn.execute(
            "select 1 from audit_events where case_id = %s and kind = %s"
            " and payload ->> 'approval_id' = %s",
            (case_id, SENT, str(approval_id)),
        ).fetchone()
    if done is not None:
        return "already_sent"
    to = email_code.verified_email(conn, tenant_id, contact_id)
    if to is None:
        return "no_email"
    found = result.get("data")
    data: dict[str, Any] = found if isinstance(found, dict) else {}
    order = params.get("order_id") or data.get("order_id")
    amount = data.get("amount") or params.get("amount")
    reference = str(data.get("id") or f"REF-{approval_id.hex[:8].upper()}")
    letter = refund_email(
        to=to,
        company=company,
        language=i18n.tenant_language(conn, tenant_id),
        order=str(order) if order else None,
        amount=amount,
        method=_text(data.get("payment_method")),
        within=_text(data.get("expected_within")),
        reference=reference,
    )
    audit = {"approval_id": str(approval_id), "order_id": order, "reference": reference}
    try:
        mailer.send(letter)
    except MailError as error:
        audit_log.record(conn, tenant_id, case_id, FAILED, {**audit, "motivo": str(error)})
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "insert into error_events (tenant_id, kind, detail) values (%s, %s, %s)",
                (tenant_id, "MailError", str(error)[:500]),
            )
        alerts.record_error(tenant_id, "MailError")
        return "failed"
    audit_log.record(conn, tenant_id, case_id, SENT, {**audit, "amount": amount})
    return "sent"


def _text(value: Any) -> str | None:
    return str(value).strip() or None if value else None
