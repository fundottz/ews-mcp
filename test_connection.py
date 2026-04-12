#!/usr/bin/env python3
"""Test connection to MTS Exchange server via EWS."""

import os
from dotenv import load_dotenv
from exchangelib import Account, Credentials, Configuration, DELEGATE
import urllib3

load_dotenv()

EMAIL    = os.environ["EXCHANGE_EMAIL"]
PASSWORD = os.environ["EXCHANGE_PASSWORD"]
SERVER   = os.environ["EXCHANGE_SERVER"]

# Suppress SSL warnings if needed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_connection():
    print(f"Connecting to {SERVER} as {EMAIL}...")

    creds = Credentials(username=EMAIL, password=PASSWORD)

    config = Configuration(
        server=SERVER,
        credentials=creds,
    )

    account = Account(
        primary_smtp_address=EMAIL,
        config=config,
        autodiscover=False,
        access_type=DELEGATE,
    )

    inbox = account.inbox
    total = inbox.total_count
    unread = inbox.unread_count

    print(f"Connected successfully!")
    print(f"Inbox: {total} total, {unread} unread")
    print()
    print("Last 5 emails:")
    for item in inbox.all().order_by("-datetime_received")[:5]:
        sender = item.sender.email_address if item.sender else "unknown"
        print(f"  [{item.datetime_received:%Y-%m-%d %H:%M}] {sender}: {item.subject}")

if __name__ == "__main__":
    test_connection()
