"""
Email composition and sending for the WhatsApp agent.

Compose: LLM drafts a subject + body based on knowledge base context.
Send:    SMTP with STARTTLS (works with Gmail App Password, Outlook, any SMTP).

Setup in .env / docker-compose:
  EMAIL_FROM_ADDRESS  e.g. glenn@gmail.com
  EMAIL_FROM_NAME     e.g. Glenn
  EMAIL_SMTP_HOST     e.g. smtp.gmail.com
  EMAIL_SMTP_PORT     587  (STARTTLS)
  EMAIL_SMTP_USER     e.g. glenn@gmail.com
  EMAIL_SMTP_PASSWORD app password or SMTP password

Gmail:   smtp.gmail.com:587  — generate an App Password at myaccount.google.com/apppasswords
Outlook: smtp.office365.com:587 — use your Microsoft account password
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from src.llm import generate
from src.search import retrieve

EMAIL_FROM    = os.environ.get("EMAIL_FROM_ADDRESS", "")
EMAIL_NAME    = os.environ.get("EMAIL_FROM_NAME", "FamilyBrain")
SMTP_HOST     = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("EMAIL_SMTP_USER", EMAIL_FROM)
SMTP_PASSWORD = os.environ.get("EMAIL_SMTP_PASSWORD", "")

_COMPOSE_SYSTEM = """You are a personal assistant composing emails on behalf of the user.
Write professional but warm emails. Use the knowledge base context provided to fill in specific details.
Format your response as:
SUBJECT: <subject line>
BODY:
<email body>

Do not add sign-off — the sender name will be added automatically.
Do not include placeholder text like [Name] — use the actual information from the context."""


def compose(topic: str, recipient: str) -> dict:
    """
    Look up topic in the knowledge base, then draft a subject and body.
    Returns { subject, body, context_found } or raises.
    """
    # Search personal graph first (most likely for appointments etc), then others
    context = retrieve(topic, ["personal_graph", "decision_graph", "property_graph"])

    if context:
        prompt = (
            f"Knowledge base information:\n{context}\n\n"
            f"Task: Write an email to {recipient} about: {topic}\n\n"
            f"Use the knowledge base information above to fill in specific details."
        )
    else:
        prompt = (
            f"Task: Write an email to {recipient} about: {topic}\n\n"
            f"Note: No specific information found in the knowledge base — write a general email."
        )

    raw = generate(prompt, system=_COMPOSE_SYSTEM)

    # Parse subject and body from response
    subject = ""
    body    = ""
    lines   = raw.strip().splitlines()

    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("SUBJECT:"):
            subject = stripped[8:].strip()
        elif stripped.upper() == "BODY:":
            body_start = i + 1
            break

    body = "\n".join(lines[body_start:]).strip()

    # Fallback if LLM didn't follow format
    if not subject:
        subject = f"Re: {topic.capitalize()}"
    if not body:
        body = raw.strip()

    return {
        "subject":       subject,
        "body":          body,
        "context_found": bool(context),
    }


def send(to: str, subject: str, body: str) -> bool:
    """Send the email via SMTP. Returns True on success."""
    if not SMTP_PASSWORD:
        raise ValueError("EMAIL_SMTP_PASSWORD not configured")
    if not EMAIL_FROM:
        raise ValueError("EMAIL_FROM_ADDRESS not configured")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{EMAIL_NAME} <{EMAIL_FROM}>" if EMAIL_NAME else EMAIL_FROM
    msg["To"]      = to

    # Plain text + simple HTML
    plain = body
    html  = "<html><body>" + body.replace("\n", "<br>") + "</body></html>"
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [to], msg.as_string())

    return True


def smtp_configured() -> bool:
    return bool(SMTP_PASSWORD and EMAIL_FROM)
