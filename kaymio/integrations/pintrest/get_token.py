import os
import sys
import base64
import datetime
import requests
from pathlib import Path
from urllib.parse import urlencode

# Make 'kaymio' importable when this script is run directly from its subdirectory
sys.path.insert(0, str(Path(__file__).parents[3]))

from dotenv import load_dotenv
load_dotenv()


AUTH_URL = "https://www.pinterest.com/oauth/"
TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"

APP_ID = os.getenv("APP_ID") or os.getenv("PINTEREST_CLIENT_ID")
APP_SECRET = os.getenv("APP_SECRET_KEY") or os.getenv("PINTEREST_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI") or "http://localhost:8000/callback"

SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"


def get_authorization_url():
    params = {
        "response_type": "code",
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "xyz",
    }
    auth_url = f"https://www.pinterest.com/oauth/?{urlencode(params)}"
    print("Visit this URL to authorize:")
    print(auth_url)


def exchange_code_for_token(auth_code: str):
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {base64.b64encode(f'{APP_ID}:{APP_SECRET}'.encode()).decode()}",
    }
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
    }

    response = requests.post(TOKEN_URL, headers=headers, data=data)
    print("Response:", response.status_code)

    if response.status_code != 200:
        print("Error:", response.text)
        return

    token_data = response.json()

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    scope = token_data.get("scope")
    expires_in = token_data.get("expires_in", 2592000)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in)

    from kaymio.database.oauth import save_oauth_credential
    save_oauth_credential(
        platform="pinterest",
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=token_data.get("token_type", "bearer"),
        expires_at=expires_at,
        scope=scope,
        raw_data=token_data,
    )
    print("Saved Pinterest tokens to the oauth_credentials table.")
    print(f"  access_token : {access_token[:30]}...")
    print(f"  refresh_token: {(refresh_token or '')[:30]}...")
    print(f"  expires_at   : {expires_at}")
    print(f"  scope        : {scope}")


if __name__ == "__main__":
    print("Step 1: Get Authorization URL")
    get_authorization_url()

    print("\nStep 2: After authorizing, paste the 'code' parameter from the redirect URL here.")
    auth_code = input("Enter the authorization code: ").strip()

    print("\nStep 3: Exchanging code for tokens and saving to DB...")
    exchange_code_for_token(auth_code)
