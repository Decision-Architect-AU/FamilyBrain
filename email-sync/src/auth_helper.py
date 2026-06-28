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


def auth_outlook():
    import msal
    app = msal.PublicClientApplication(
        client_id=MICROSOFT_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}",
    )
    scopes = [
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Calendars.ReadWrite",
    ]
    flow = app.initiate_device_flow(scopes=scopes)
    print("\n=== Outlook/Hotmail OAuth2 ===")
    if "error" in flow:
        print(f"Error initiating device flow: {flow.get('error')}")
        print(f"Description: {flow.get('error_description')}")
        return
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "refresh_token" in result:
        print(f"\nrefresh_token: {result['refresh_token']}")
        print("Paste the refresh_token into personal.email_account.refresh_token\n")
    else:
        print(f"Error: {result.get('error_description')}")


if __name__ == "__main__":
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
