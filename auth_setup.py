import os
import sys
import json
import base64
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

auth_code = None

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed_url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_url.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            success_html = """
            <html>
            <head><title>Authentication Successful</title></head>
            <body style="font-family: Arial, sans-serif; text-align: center; padding-top: 50px;">
                <h1 style="color: #27ae60;">🎉 YouTube Authorization Successful!</h1>
                <p style="font-size: 18px; color: #555;">You have connected your YouTube channel. You can now close this tab and return to the terminal/chat.</p>
            </body>
            </html>
            """
            self.wfile.write(success_html.encode("utf-8"))
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Error: No authorization code received.</h1>")

    def log_message(self, format, *args):
        # Suppress standard http server logging to keep terminal clean
        pass


def main():
    print("=" * 65, flush=True)
    print("   YouTube OAuth Token Generator (Custom Server)", flush=True)
    print("=" * 65, flush=True)

    client_secret_file = Path("credentials/client_secret.json")
    if not client_secret_file.exists():
        print(f"\n[ERROR] '{client_secret_file}' not found!", flush=True)
        sys.exit(1)

    tokens_dir = Path("tokens")
    tokens_dir.mkdir(parents=True, exist_ok=True)
    token_file = tokens_dir / "oauth_token.json"

    redirect_uri = "http://localhost:8080/"
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_file),
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print(f"\nAuth URL:\n{auth_url}\n", flush=True)

    # Start HTTP server on port 8080
    server_address = ("localhost", 8080)
    httpd = HTTPServer(server_address, OAuthCallbackHandler)
    print("Listening on http://localhost:8080 for callback...", flush=True)

    # Open explicitly in Chrome Profile 18
    os.system(f'open -na "Google Chrome" --args --profile-directory="Profile 18" "{auth_url}"')

    # Wait until authorization code is received
    while auth_code is None:
        httpd.handle_request()

    httpd.server_close()
    print("\nAuthorization code received! Exchanging for token...", flush=True)

    flow.fetch_token(code=auth_code)
    creds = flow.credentials

    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print("\n" + "=" * 65, flush=True)
    print(f"✅ Success! OAuth Token saved to: {token_file.resolve()}", flush=True)
    print("=" * 65, flush=True)

    # Print GitHub Secrets Helper
    with open(client_secret_file, "rb") as f:
        b64_client_secret = base64.b64encode(f.read()).decode("utf-8")
    with open(token_file, "rb") as f:
        b64_token = base64.b64encode(f.read()).decode("utf-8")

    print(f"YOUTUBE_CLIENT_SECRET_B64:\n{b64_client_secret}\n", flush=True)
    print(f"YOUTUBE_OAUTH_TOKEN_B64:\n{b64_token}\n", flush=True)

if __name__ == "__main__":
    main()
