"""
authorize_youtube.py
Run this ONCE locally to generate the YouTube OAuth token.
The token JSON is then stored as a GitHub Secret (YOUTUBE_TOKEN_JSON).

Usage:
  python authorize_youtube.py --client_secret client_secret_xxx.json

After running:
  1. A browser window opens — log in with ihaveacause@gmail.com
  2. Grant the permissions
  3. The script prints the token JSON
  4. Copy that JSON and add it as GitHub Secret: YOUTUBE_TOKEN_JSON
"""

import argparse
import json
import os
import pickle

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import google.oauth2.credentials

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client_secret", required=True,
                        help="Path to client_secret_xxx.json downloaded from Google Cloud")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes),
    }

    token_json = json.dumps(token_data)

    print("\n" + "="*60)
    print("✅ AUTHORISATION COMPLETE")
    print("="*60)
    print("\nCopy the JSON below and add it as GitHub Secret: YOUTUBE_TOKEN_JSON\n")
    print(token_json)
    print("\n" + "="*60)

    # Also save locally for convenience
    with open("youtube_token.json", "w") as f:
        json.dump(token_data, f, indent=2)
    print("Also saved to: youtube_token.json")
    print("⚠️  Do NOT commit youtube_token.json to GitHub!")


if __name__ == "__main__":
    main()
