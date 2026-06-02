#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"{name} is required")
    return value


def main():
    parser = argparse.ArgumentParser(description="Send an email through LoftBox.")
    parser.add_argument("--base-url", default=os.environ.get("LOFTBOX_BASE_URL", "https://api.loftbox.net"))
    parser.add_argument("--api-key", default=os.environ.get("LOFTBOX_API_KEY"))
    parser.add_argument("--mailbox-id", default=os.environ.get("LOFTBOX_MAILBOX_ID"))
    parser.add_argument("--to", action="append", required=True, help="Recipient email. Repeat for multiple recipients.")
    parser.add_argument("--cc", action="append", default=[], help="CC email. Repeat for multiple recipients.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--text", dest="body_text")
    parser.add_argument("--html", dest="body_html")
    parser.add_argument("--markdown", dest="body_markdown")
    parser.add_argument("--in-reply-to")
    parser.add_argument("--reference", action="append", default=[], dest="references")
    parser.add_argument("--metadata-json")
    args = parser.parse_args()

    api_key = args.api_key or env("LOFTBOX_API_KEY")
    mailbox_id = args.mailbox_id or env("LOFTBOX_MAILBOX_ID")
    if not (args.body_text or args.body_html or args.body_markdown):
        raise SystemExit("one of --text, --html, or --markdown is required")

    payload = {
        "mailbox_id": mailbox_id,
        "to": args.to,
        "cc": args.cc,
        "subject": args.subject,
    }
    for key in ("body_text", "body_html", "body_markdown", "in_reply_to"):
        value = getattr(args, key)
        if value:
            payload[key] = value
    if args.references:
        payload["references"] = args.references
    if args.metadata_json:
        payload["metadata"] = json.loads(args.metadata_json)

    url = args.base_url.rstrip("/") + "/v1/messages"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            print(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(body, file=sys.stderr)
        raise SystemExit(exc.code)


if __name__ == "__main__":
    main()
