"""
Detect and parse command intents from WhatsApp messages.

Currently supported:
  send email about <topic> to <email>
  email <email> about <topic>
  send <email> the details of <topic>
  ... and natural variations

Returns a structured command dict or None if the message is not a command.
"""
import re

# Patterns ordered most-specific to least-specific
_EMAIL_PATTERNS = [
    # "send email about the appointment to x@y.com"
    re.compile(
        r'send\s+(?:an?\s+)?email\s+about\s+(.+?)\s+to\s+([\w._%+\-]+@[\w.\-]+\.\w+)',
        re.I,
    ),
    # "email x@y.com about the appointment"
    re.compile(
        r'email\s+([\w._%+\-]+@[\w.\-]+\.\w+)\s+(?:about|regarding|with)\s+(.+)',
        re.I,
    ),
    # "send x@y.com the details of / about the appointment"
    re.compile(
        r'send\s+([\w._%+\-]+@[\w.\-]+\.\w+)\s+(?:the\s+details\s+of|about|regarding)\s+(.+)',
        re.I,
    ),
    # "send details about the appointment to x@y.com"
    re.compile(
        r'send\s+(?:the\s+)?details\s+(?:of|about)\s+(.+?)\s+to\s+([\w._%+\-]+@[\w.\-]+\.\w+)',
        re.I,
    ),
    # "forward the appointment details to x@y.com"
    re.compile(
        r'forward\s+(.+?)\s+to\s+([\w._%+\-]+@[\w.\-]+\.\w+)',
        re.I,
    ),
]


def parse(message: str) -> dict | None:
    """
    Returns a command dict if the message matches a known command pattern,
    otherwise None.

    Email command dict:
      { "type": "send_email", "topic": str, "to": str }
    """
    for pattern in _EMAIL_PATTERNS:
        m = pattern.search(message)
        if m:
            groups = m.groups()
            # First pattern: (topic, email) — all others: (email, topic)
            if pattern == _EMAIL_PATTERNS[0] or pattern == _EMAIL_PATTERNS[3] or pattern == _EMAIL_PATTERNS[4]:
                topic, to = groups[0].strip(), groups[1].strip()
            else:
                to, topic = groups[0].strip(), groups[1].strip()
            return {"type": "send_email", "topic": topic, "to": to}

    return None
