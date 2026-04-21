"""
One-time script to authorize Google APIs (Calendar + Drive).
Run once: python setup_google_auth.py
Deletes any existing token.json and creates a new one with all scopes.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]

CREDS_PATH = "credentials.json"
TOKEN_PATH = "token.json"

if not os.path.exists(CREDS_PATH):
    raise FileNotFoundError(f"File non trovato: {CREDS_PATH}")

if os.path.exists(TOKEN_PATH):
    os.remove(TOKEN_PATH)
    print(f"Rimosso token precedente: {TOKEN_PATH}")

flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_PATH, "w") as f:
    f.write(creds.to_json())

print(f"\ntoken.json salvato con scope:\n" + "\n".join(f"  - {s}" for s in SCOPES))
