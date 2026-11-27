"""Envoi d'e-mails par SMTP (n'importe quel fournisseur : Brevo, Azure Communication Services, Mailjet…).

Configuration (variables d'environnement) :
  SMTP_HOST      serveur ; vide = aucun e-mail envoyé (configured() faux, les envois sont ignorés)
  SMTP_PORT      587 (STARTTLS, défaut) ou 465 (TLS direct)
  SMTP_USER, SMTP_PASSWORD   identifiants (facultatifs selon le fournisseur ; le mot de passe n'est jamais journalisé)
  SMTP_FROM      expéditeur, ex. « Mire <bonjour@mire.fr> » (domaine authentifié SPF / DKIM chez le fournisseur)
  SMTP_REPLY_TO  facultatif : adresse lue par une personne, où arrivent les réponses (en-tête Reply-To)

Les tests remplacent l'envoi par un faux expéditeur (paramètre `sender` de digest.send_digests).
"""

import logging
import os
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from .net import TLS_CONTEXT

log = logging.getLogger("mire.mailer")

TIMEOUT_S = 20


@dataclass
class Mail:
    to: str
    subject: str
    text: str
    html: str
    headers: dict[str, str] = field(default_factory=dict)


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST", "").strip())


def sender_address() -> str:
    return os.environ.get("SMTP_FROM", "").strip() or formataddr(("Mire", "no-reply@localhost"))


def sender_name() -> str:
    """Nom affiché de l'expéditeur (« Julien de Mire » dans « Julien de Mire <…> »), pour signer le message.

    Pas de virgule dans le nom sans guillemets : les messageries y verraient deux adresses."""
    return parseaddr(sender_address())[0].strip() or "Mire"


def reply_to() -> str:
    return os.environ.get("SMTP_REPLY_TO", "").strip()


def build_message(mail: Mail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender_address()
    msg["To"] = mail.to
    if reply_to():
        msg["Reply-To"] = reply_to()
    msg["Subject"] = mail.subject
    domain = sender_address().rsplit("@", 1)[-1].strip("> ") or None
    msg["Message-ID"] = make_msgid(domain=domain)
    for name, value in mail.headers.items():
        msg[name] = value
    msg.set_content(mail.text)
    msg.add_alternative(mail.html, subtype="html")
    return msg


def send(mail: Mail) -> None:
    """Envoie un e-mail. Lève une exception si l'envoi échoue (l'appelant décide de continuer ou non)."""
    if not configured():
        raise RuntimeError("SMTP_HOST non configuré")
    host = os.environ["SMTP_HOST"].strip()
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    msg = build_message(mail)
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_S, context=TLS_CONTEXT)
    else:
        client = smtplib.SMTP(host, port, timeout=TIMEOUT_S)
    with client:
        if port != 465:
            client.starttls(context=TLS_CONTEXT)
        if user:
            client.login(user, password)
        client.send_message(msg)
