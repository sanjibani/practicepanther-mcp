"""One-shot OAuth helper for PracticePanther MCP.

PracticePanther uses OAuth 2 authorization-code grant. This script runs the
flow locally: opens the user's browser to PracticePanther's authorize endpoint,
spins up a temporary localhost HTTP server to capture the redirect, exchanges
the code for an access_token + refresh_token, and prints the env vars to set.

Usage:
    practicepanther-mcp-auth --client-id <id> --client-secret <secret>
        [--redirect-port 8765] [--base-url https://app.practicepanther.com]

After it succeeds, copy the printed export lines into your shell (or your
Claude Desktop config), then run `practicepanther_mcp`.
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
DEFAULT_PORT = 8765


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the PracticePanther OAuth flow and print tokens.",
    )
    p.add_argument("--client-id", required=True, help="Your OAuth app client_id")
    p.add_argument("--client-secret", required=True, help="Your OAuth app client_secret")
    p.add_argument(
        "--redirect-port", type=int, default=DEFAULT_PORT,
        help=f"Localhost port to capture the redirect (default: {DEFAULT_PORT})",
    )
    p.add_argument(
        "--base-url", default="https://app.practicepanther.com",
        help="PracticePanther base URL (default: production)",
    )
    p.add_argument(
        "--no-browser", action="store_true",
        help="Don't auto-open the browser — print the URL for you to click.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    base = args.base_url.rstrip("/")
    redirect_uri = f"http://localhost:{args.redirect_port}/callback"
    state = "practicepanther-mcp"

    # 1. Build the authorize URL and capture the redirect.
    auth_params = {
        "response_type": "code",
        "client_id": args.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    authorize_url = f"{base}{AUTHORIZE_PATH}?{urlencode(auth_params)}"

    auth_code_holder: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            code_list = qs.get("code")
            code = code_list[0] if code_list else None
            err_list = qs.get("error")
            err = err_list[0] if err_list else None
            if err:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                err_desc = qs.get("error_description", [""])
                desc = err_desc[0] if err_desc else ""
                self.wfile.write(
                    f"<h1>OAuth error</h1><p>{err}: {desc}</p>".encode()
                )
                auth_code_holder["error"] = err
                return
            if not code:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>Missing code</h1>")
                return
            auth_code_holder["code"] = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authenticated.</h1><p>You can close this tab and return to the terminal.</p>"
            )

        def log_message(self, _format: str, *_args: object) -> None:  # silence default logs
            return

    server = HTTPServer(("127.0.0.1", args.redirect_port), CallbackHandler)
    server.timeout = 180  # 3 minutes to complete the flow

    print("Opening browser for PracticePanther login...", file=sys.stderr)
    print(f"If your browser does not open, visit:\n  {authorize_url}\n", file=sys.stderr)
    if not args.no_browser:
        try:
            webbrowser.open(authorize_url)
        except Exception as exc:
            print(f"Could not auto-open browser ({exc}). Visit the URL above.", file=sys.stderr)

    print(
        f"Waiting for redirect on http://localhost:{args.redirect_port}/callback ...",
        file=sys.stderr,
    )
    while "code" not in auth_code_holder and "error" not in auth_code_holder:
        server.handle_request()
    server.server_close()

    if "error" in auth_code_holder:
        print(f"OAuth failed: {auth_code_holder['error']}", file=sys.stderr)
        return 1

    code = auth_code_holder["code"]

    # 2. Exchange code for tokens.
    print("Exchanging code for tokens...", file=sys.stderr)
    resp = httpx.post(
        f"{base}{TOKEN_PATH}",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        print(f"Token exchange failed (HTTP {resp.status_code}): {resp.text}", file=sys.stderr)
        return 1

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token or not refresh_token:
        print(f"Token response missing fields: {data}", file=sys.stderr)
        return 1

    # 3. Print the env vars the user should set.
    print(
        "\nSuccess. Add these to your shell (or Claude Desktop env block):\n",
        file=sys.stderr,
    )
    print(f'export PRACTICEPANTHER_ACCESS_TOKEN="{access_token}"')
    print(f'export PRACTICEPANTHER_REFRESH_TOKEN="{refresh_token}"')
    print(f'export PRACTICEPANTHER_CLIENT_ID="{args.client_id}"')
    print(f'export PRACTICEPANTHER_CLIENT_SECRET="{args.client_secret}"')
    print(
        "\nRefresh tokens last 60 days. Run this script again to re-authenticate.",
        file=sys.stderr,
    )
    # Also dump to stdout as JSON so it can be piped.
    print(json.dumps({
        "PRACTICEPANTHER_ACCESS_TOKEN": access_token,
        "PRACTICEPANTHER_REFRESH_TOKEN": refresh_token,
        "PRACTICEPANTHER_CLIENT_ID": args.client_id,
        "PRACTICEPANTHER_CLIENT_SECRET": args.client_secret,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
