"""
auth_helper.py — run once per account to get OAuth2 refresh tokens.

Usage (from familybrain root):
    docker compose run --rm email-sync python -m src.auth_helper

Prints the refresh_token for each account you authorise.
Paste tokens into personal.email_account rows.
"""
import os
import json
import webbrowser

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
MICROSOFT_CLIENT_ID  = os.environ.get("MICROSOFT_CLIENT_ID", "")
MICROSOFT_TENANT_ID  = os.environ.get("MICROSOFT_TENANT_ID", "consumers")


def auth_gmail():
    from google_auth_oauthlib.flow import InstalledAppFlow
    client_config = {
        "installed": {
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uris": ["http://localhost"],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }
    scopes = [
        "https://www.googleapis.com/auth/gmail.modify",   # read + label + send
        "https://www.googleapis.com/auth/calendar",       # full calendar read/write
        "https://www.googleapis.com/auth/tasks",          # Increment 5 — Google Tasks channel
    ]
    flow = InstalledAppFlow.from_client_config(client_config, scopes,
                                               redirect_uri="http://localhost")
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print(f"\nVisit this URL to authorise:\n{auth_url}\n")
    print("After approving, your browser will redirect to http://localhost/?code=XXXX")
    print("Copy the full URL from your browser's address bar and paste it below.")
    redirected = input("Paste the full redirect URL here: ").strip()
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(redirected).query).get("code", [redirected])[0]
    flow.fetch_token(code=code)
    creds = flow.credentials
    print("\n=== Gmail OAuth2 tokens ===")
    print(f"refresh_token: {creds.refresh_token}")
    print(f"access_token:  {creds.token}")
    print("Paste the refresh_token into personal.email_account.refresh_token\n")


# What the account is asked to consent to. This is the *consent* list, not the
# list `outlook._token()` refreshes against — see the note on
# `outlook.CONTACT_SCOPES`. A scope added here only takes effect for an
# account that signs in again afterwards, because consent is recorded against
# the refresh token, not against the code.
OUTLOOK_CONSENT_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Calendars.ReadWrite",
    # Contacts.Read was never consented, so the address book was unreadable:
    # MSAL refused the scope outright with AADSTS70000 and Graph answered 403
    # ErrorAccessDenied on the mail token. Reading contacts is what lets the
    # register be reconciled against what Glenn already keeps by hand.
    "https://graph.microsoft.com/Contacts.Read",
]


def auth_outlook(save_account_id: int | None = None):
    """Device-flow sign-in for an Outlook/Hotmail account.

    With `save_account_id`, the new refresh token is written straight into
    `personal.email_account` and never printed. A refresh token printed to a
    terminal is a long-lived credential in somebody's scrollback, and this
    flow is normally driven by an agent whose output is kept.
    """
    import msal
    app = msal.PublicClientApplication(
        client_id=MICROSOFT_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}",
    )
    flow = app.initiate_device_flow(scopes=OUTLOOK_CONSENT_SCOPES)
    print("\n=== Outlook/Hotmail OAuth2 ===", flush=True)
    if "error" in flow:
        print(f"Error initiating device flow: {flow.get('error')}")
        print(f"Description: {flow.get('error_description')}")
        return
    # Printed before the blocking poll, and flushed, or nobody ever sees the
    # code they are being asked to type.
    print(flow["message"], flush=True)
    print(f"(waiting up to {flow.get('expires_in', 900)}s for the sign-in)", flush=True)

    result = app.acquire_token_by_device_flow(flow)
    if "refresh_token" not in result:
        print(f"Error: {result.get('error')}: {result.get('error_description')}")
        return

    granted = result.get("scope") or ""
    print(f"\nsigned in; granted scopes: {granted}", flush=True)
    if "Contacts.Read" not in granted:
        print("WARNING: Contacts.Read was NOT granted — the address book stays "
              "unreadable and outlook.read_contacts will skip.", flush=True)

    if save_account_id is None:
        print("\nRe-run with --save-account <id> to store this token, or copy it "
              "from the sign-in yourself. Not printing it here.")
        return

    from . import db
    from datetime import datetime, timezone, timedelta
    with db.conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE personal.email_account
                      SET refresh_token = %s, access_token = %s,
                          token_expiry = %s, updated_at = now()
                    WHERE id = %s
                RETURNING email_address""",
                (result["refresh_token"], result.get("access_token"),
                 datetime.now(timezone.utc)
                 + timedelta(seconds=result.get("expires_in", 3600)),
                 save_account_id),
            )
            row = cur.fetchone()
        c.commit()
    if row:
        print(f"refresh token stored for account {save_account_id} "
              f"({row['email_address']})", flush=True)
    else:
        print(f"no personal.email_account row with id {save_account_id}", flush=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="FamilyBrain email auth helper")
    ap.add_argument("--outlook", action="store_true", help="Outlook/Hotmail device flow")
    ap.add_argument("--gmail", action="store_true", help="Gmail installed-app flow")
    ap.add_argument("--save-account", type=int, metavar="ID",
                    help="store the refresh token on this personal.email_account row")
    args = ap.parse_args()

    if args.outlook or args.gmail:
        if args.gmail:
            if not GOOGLE_CLIENT_ID:
                raise SystemExit("GOOGLE_CLIENT_ID not set in environment")
            auth_gmail()
        if args.outlook:
            if not MICROSOFT_CLIENT_ID:
                raise SystemExit("MICROSOFT_CLIENT_ID not set in environment")
            auth_outlook(args.save_account)
        raise SystemExit(0)

    print("FamilyBrain Email Auth Helper")
    print("=" * 40)
    print("1) Authorise a Gmail account")
    print("2) Authorise an Outlook/Hotmail account")
    print("3) Both")
    choice = input("Choice [1/2/3]: ").strip()

    if choice in ("1", "3"):
        if not GOOGLE_CLIENT_ID:
            print("GOOGLE_CLIENT_ID not set in environment")
        else:
            auth_gmail()

    if choice in ("2", "3"):
        if not MICROSOFT_CLIENT_ID:
            print("MICROSOFT_CLIENT_ID not set in environment")
        else:
            auth_outlook()
