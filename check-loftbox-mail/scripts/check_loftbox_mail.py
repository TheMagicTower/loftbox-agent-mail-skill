#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Identify the client so upstream WAF/CDN rules do not block the default
# urllib User-Agent ("Python-urllib/x.y"), which Cloudflare flags as a bot.
USER_AGENT = os.environ.get("LOFTBOX_USER_AGENT", "loftbox-mail-skill/1.0 (+https://loftbox.net)")


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"{name} is required")
    return value


def request_json(method, url, api_key, payload=None):
    data = None
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        raise SystemExit(exc.code)


def main():
    parser = argparse.ArgumentParser(description="Check or acknowledge LoftBox inbox messages.")
    parser.add_argument("--base-url", default=os.environ.get("LOFTBOX_BASE_URL", "https://api.loftbox.net"))
    parser.add_argument("--api-key", default=os.environ.get("LOFTBOX_API_KEY"))
    parser.add_argument("--mailbox-id", default=os.environ.get("LOFTBOX_MAILBOX_ID"))
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List unacknowledged inbound messages.")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--cursor")

    ack_parser = sub.add_parser("ack", help="Acknowledge processed inbound messages.")
    ack_parser.add_argument("--message-id", action="append", required=True)

    args = parser.parse_args()
    api_key = args.api_key or env("LOFTBOX_API_KEY")
    mailbox_id = args.mailbox_id or env("LOFTBOX_MAILBOX_ID")
    base = args.base_url.rstrip("/")
    mailbox = urllib.parse.quote(mailbox_id, safe="")

    if args.command == "list":
        params = {"limit": str(args.limit)}
        if args.cursor:
            params["cursor"] = args.cursor
        url = f"{base}/v1/mailboxes/{mailbox}/inbox?{urllib.parse.urlencode(params)}"
        request_json("GET", url, api_key)
    elif args.command == "ack":
        url = f"{base}/v1/mailboxes/{mailbox}/inbox/ack"
        request_json("POST", url, api_key, {"message_ids": args.message_id})


if __name__ == "__main__":
    main()
