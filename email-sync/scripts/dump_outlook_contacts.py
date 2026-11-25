"""Dump an account's Outlook contacts as JSON on stdout. Read-only.

Runs inside the email-sync container, because the refresh token and msal are
there:

    docker exec -w /app familybrain-email-sync \
        python -m scripts.dump_outlook_contacts --account 1

Nothing is written to Outlook. `scripts/compare_contacts.py` on the host
calls this and does the comparison; this half exists only because the
address book cannot be reached from the host.
"""
import argparse
import json
import sys

from src import db, outlook


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", type=int, default=1,
                    help="personal.email_account id (default 1)")
    args = ap.parse_args()

    accounts = {a["id"]: a for a in db.get_enabled_accounts()}
    account = accounts.get(args.account)
    if not account:
        print(f"no enabled personal.email_account with id {args.account}",
              file=sys.stderr)
        return 2
    if account["provider"] != "outlook":
        print(f"account {args.account} is {account['provider']}, not outlook",
              file=sys.stderr)
        return 2

    contacts = outlook.read_contacts(account)
    if contacts is None:
        # None means unreadable, which is not the same answer as an empty
        # address book — see outlook.read_contacts.
        print("contacts unreadable (see the message above for the scope)",
              file=sys.stderr)
        return 3

    json.dump({"account_id": account["id"],
               "email_address": account["email_address"],
               "contacts": contacts}, sys.stdout, default=str)
    print(f"\n{len(contacts)} contacts read from {account['email_address']}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
