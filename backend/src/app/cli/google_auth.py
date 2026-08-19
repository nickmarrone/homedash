"""One-time Google Calendar authorization.

    uv run homedash-google-auth --client-id ... --client-secret ...

Run this once, on a machine with a browser. It prints a consent URL, catches
Google's redirect on a loopback port, and prints the credential blob to paste
into HOMEDASH_CALENDAR_CREDENTIALS. The refresh token it returns is what lets
the wall panel sync forever without a browser session.

Setting up the client in Google Cloud, once:

  1. Create a project at console.cloud.google.com
  2. Enable the Google Calendar API
  3. Configure the OAuth consent screen; add your own Google account as a
     test user
  4. Create credentials -> OAuth client ID -> **Desktop app**
  5. Run this command with the client ID and secret it gives you

Publish the consent screen when you are done testing: while it is in Testing
mode Google expires refresh tokens after seven days, and the panel will stop
syncing a week after it starts working.
"""

import argparse
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from app.calendars.google_auth import (
    GoogleAuthError,
    authorization_url,
    exchange_code,
    make_code_verifier,
)

PAGE = b"""<!doctype html><meta charset="utf-8">
<title>HomeDash</title>
<body style="font-family: system-ui; padding: 3rem; text-align: center">
<h1>%s</h1><p>%s</p></body>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        query = parse_qs(urlsplit(self.path).query)
        type(self).result = {key: value[0] for key, value in query.items()}
        ok = "code" in type(self).result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if ok:
            self.wfile.write(PAGE % (b"Authorized", b"You can close this tab and return to the terminal."))
        else:
            error = type(self).result.get("error", "no code returned").encode()
            self.wfile.write(PAGE % (b"Authorization failed", error))

    def log_message(self, *args) -> None:
        """Silence the default per-request logging to stderr."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument(
        "--name",
        default="google",
        help="key to file the credentials under in HOMEDASH_CALENDAR_CREDENTIALS",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="loopback port for the redirect (default: any free port)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening it"
    )
    args = parser.parse_args()

    # Bind before building the URL: with port 0 the OS picks the port, and the
    # redirect_uri has to name the port Google will actually be redirected to.
    server = HTTPServer(("127.0.0.1", args.port), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"

    verifier = make_code_verifier()
    state = secrets.token_urlsafe(16)
    url = authorization_url(args.client_id, redirect_uri, verifier, state)

    print(f"Add this exact redirect URI to the OAuth client if it is not there already:\n  {redirect_uri}\n")
    print(f"Open this URL to authorize:\n  {url}\n")
    if not args.no_browser:
        webbrowser.open(url)

    print("Waiting for the redirect...")
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()

    result = _CallbackHandler.result
    if not result:
        print("Timed out waiting for authorization.", file=sys.stderr)
        return 1
    if "code" not in result:
        print(f"Authorization failed: {result.get('error', result)}", file=sys.stderr)
        return 1
    if result.get("state") != state:
        # A mismatched state means the response did not come from the request
        # we made, so the code it carries cannot be trusted.
        print("State mismatch - ignoring this response.", file=sys.stderr)
        return 1

    try:
        tokens = exchange_code(
            args.client_id, args.client_secret, result["code"], redirect_uri, verifier
        )
    except GoogleAuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(
            "Google returned no refresh token. Revoke HomeDash's access at\n"
            "  https://myaccount.google.com/permissions\n"
            "and run this again - Google only issues one on a fresh consent.",
            file=sys.stderr,
        )
        return 1

    blob = {
        args.name: {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "refresh_token": refresh_token,
        }
    }
    print("\nAuthorized. Add this to HOMEDASH_CALENDAR_CREDENTIALS:\n")
    print(f"HOMEDASH_CALENDAR_CREDENTIALS={json.dumps(blob)}")
    print(
        f"\nThen reference it from a calendar entry:\n"
        f'  {{"name": "Family", "kind": "google", '
        f'"calendar_id": "you@gmail.com", "credentials": "{args.name}"}}'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
