import os
import sys
import json
import base64
import urllib.parse
import subprocess
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
        
        # Ignore favicon requests
        if parsed_url.path == "/favicon.ico":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed_url.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            success_html = """
            <html>
            <head><title>Authentication Successful</title></head>
            <body style="font-family: Arial, sans-serif; text-align: center; padding-top: 50px; background: #0f172a; color: #fff;">
                <h1 style="color: #22c55e; font-size: 32px;">🎉 YouTube Channel Connected!</h1>
                <p style="font-size: 18px; color: #94a3b8;">Authorization code received. You can now close this tab!</p>
            </body>
            </html>
            """
            self.wfile.write(success_html.encode("utf-8"))
        elif "error" in params:
            err = params["error"][0]
            print(f"\n[Google OAuth Error]: {err}", flush=True)
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(f"<body style='font-family:sans-serif; text-align:center; padding-top:50px;'><h2>Access Denied: {err}</h2><p>Please try again and click Continue.</p></body>".encode("utf-8"))
        else:
            self.send_response(200)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    print("=" * 65, flush=True)
    print("   YouTube OAuth Token Generator - Karma Stories (Test 2)", flush=True)
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

    # Open explicitly in Chrome Profile 36 (arahad7861412@gmail.com)
    os.system(f'open -na "Google Chrome" --args --profile-directory="Profile 36" "{auth_url}"')

    # Wait until valid authorization code is received
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

    # Automatically set secrets on GitHub repository
    repo_name = "sciencefiction879-cmyk/karma-chronicles-automation"
    print(f"Setting GitHub secrets on {repo_name}...", flush=True)
    subprocess.run(["/opt/homebrew/bin/gh", "secret", "set", "YOUTUBE_CLIENT_SECRET_B64", "--body", b64_client_secret, "--repo", repo_name])
    subprocess.run(["/opt/homebrew/bin/gh", "secret", "set", "YOUTUBE_OAUTH_TOKEN_B64", "--body", b64_token, "--repo", repo_name])
    print("✅ GitHub Secrets configured successfully on runner!", flush=True)

if __name__ == "__main__":
    main()
