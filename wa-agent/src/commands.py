"""
Detect and parse command intents from WhatsApp messages.

Supported:
  Email:         send email about <topic> to <email>
  Calendar:      what's on this week / upcoming events / my schedule
  Notifications: my notifications / any alerts
  Assets:        my assets / what assets do I have
  Add event:     add event / remind me / schedule <thing> on <date>

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


_CALENDAR_PATTERNS = re.compile(
    r"(what.?s (on|happening)|upcoming events?|my schedule|this week|next week|"
    r"what.?s coming up|calendar|what do i have on|what have i got)",
    re.I,
)

_NOTIFICATIONS_PATTERNS = re.compile(
    r"(my (notifications?|alerts?)|any (notifications?|alerts?|warnings?)|"
    r"what.?s (flagged|wrong|alerts?)|show (me )?(notifications?|alerts?))",
    re.I,
)

_ASSETS_PATTERNS = re.compile(
    r"(my assets?|what assets?|show (me )?my assets?|list (my )?assets?|"
    r"my (vehicles?|medications?|subscriptions?|properties|pets?))",
    re.I,
)

_ADD_EVENT_PATTERNS = [
    re.compile(r"(?:add|create|schedule)\s+(?:an?\s+)?(.+?)(?:\s+(?:for|on|at)\s+\S+)?$", re.I),
    re.compile(r"remind\s+me\s+(?:to\s+|about\s+)?(.+?)\s+(?:on|at)\s+(.+)", re.I),
]

_DAYS_PATTERNS = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"this (week|month)|next (week|month))\b",
    re.I,
)


def parse(message: str) -> dict | None:
    """
    Returns a command dict if the message matches a known command pattern,
    otherwise None.

    Command dicts:
      { "type": "send_email",       "topic": str, "to": str }
      { "type": "upcoming_events",  "window": str }
      { "type": "notifications" }
      { "type": "assets" }
      { "type": "add_event",        "description": str, "when": str | None }
    """
    # Email
    for pattern in _EMAIL_PATTERNS:
        m = pattern.search(message)
        if m:
            groups = m.groups()
            if pattern == _EMAIL_PATTERNS[0] or pattern == _EMAIL_PATTERNS[3] or pattern == _EMAIL_PATTERNS[4]:
                topic, to = groups[0].strip(), groups[1].strip()
            else:
                to, topic = groups[0].strip(), groups[1].strip()
            return {"type": "send_email", "topic": topic, "to": to}

    # Calendar / upcoming events
    if _CALENDAR_PATTERNS.search(message):
        window = "week"
        if re.search(r"next week", message, re.I):
            window = "next_week"
        elif re.search(r"today", message, re.I):
            window = "today"
        elif re.search(r"tomorrow", message, re.I):
            window = "tomorrow"
        elif re.search(r"this month|next month", message, re.I):
            window = "month"
        return {"type": "upcoming_events", "window": window}

    # Notifications
    if _NOTIFICATIONS_PATTERNS.search(message):
        return {"type": "notifications"}

    # Assets
    if _ASSETS_PATTERNS.search(message):
        return {"type": "assets"}

    # Add event
    for i, pattern in enumerate(_ADD_EVENT_PATTERNS):
        m = pattern.search(message)
        if m:
            groups = m.groups()
            if i == 1 and len(groups) >= 2:
                # "remind me to X on Y" → groups = (X, Y)
                description, when = groups[0].strip(), groups[1].strip()
            else:
                description = groups[0].strip() if groups else message
                when_match  = _DAYS_PATTERNS.search(message)
                when        = when_match.group(0) if when_match else None
            return {"type": "add_event", "description": description, "when": when}

    return None
