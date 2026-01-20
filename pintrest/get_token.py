import os
import requests
from dotenv import load_dotenv
from urllib.parse import urlencode
import base64


# Load .env variables
load_dotenv()


REDIRECT_URI = "http://localhost:8000/callback"

AUTH_URL = "https://www.pinterest.com/oauth/"
TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"


APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET_KEY")
REDIRECT_URI = os.getenv("REDIRECT_URI")


SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"

def get_authorization_url():
    params = {
        "response_type": "code",
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "xyz"
    }

    auth_url = f"https://www.pinterest.com/oauth/?{urlencode(params)}"
    print("🔗 Visit this URL to authorize:")
    print(auth_url)



def exchange_code_for_token(auth_code):
    TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"

    auth_code = input("Paste the authorization code: ").strip()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {base64.b64encode(f'{APP_ID}:{APP_SECRET}'.encode()).decode()}"
    }

    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(TOKEN_URL, headers=headers, data=data)

    print("Response:", response.status_code)
    print(response.text)

    if response.status_code == 200:
        token_data = response.json()
        with open("pintrest_access_token.txt", "w") as f:
            f.write(token_data["access_token"])
    else:
        print("Error getting access token")


if __name__ == "__main__":
    print("Step 1: Get Authorization URL")
    get_authorization_url()
    print("\nStep 2: After authorizing, paste the 'code' parameter from the redirect URL here.")
    auth_code = input("Enter the authorization code: ").strip()
    print("\nStep 3: Exchange the authorization code for an access token.")
    exchange_code_for_token(auth_code)
