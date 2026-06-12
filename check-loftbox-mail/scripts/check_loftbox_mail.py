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


class HttpError(Exception):
    """HTTP error carrying the upstream status code and response body."""

    def __init__(self, code, body):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.body = body


def http_text(method, url, api_key, payload=None):
    """Perform the request and return the response body text.

    Raises HttpError on a non-2xx response so callers can decide whether one
    failing mailbox should abort (single-mailbox mode) or be recorded and
    skipped (multi-mailbox mode)."""
    data = None
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read().decode("utf-8", errors="replace"))


def resolve_mailbox_ids(args):
    """Return the list of mailbox ids to poll.

    `--mailbox-ids` / `LOFTBOX_MAILBOX_IDS` (comma-separated) takes precedence
    and enables multi-mailbox polling. Otherwise fall back to the single
    `--mailbox-id` / `LOFTBOX_MAILBOX_ID` (unchanged legacy behavior)."""
    raw_ids = args.mailbox_ids or os.environ.get("LOFTBOX_MAILBOX_IDS")
    if raw_ids:
        ids = [m.strip() for m in raw_ids.split(",") if m.strip()]
        if not ids:
            raise SystemExit("LOFTBOX_MAILBOX_IDS is set but empty")
        return ids
    single = args.mailbox_id or os.environ.get("LOFTBOX_MAILBOX_ID")
    if not single:
        raise SystemExit("LOFTBOX_MAILBOX_ID or LOFTBOX_MAILBOX_IDS is required")
    return [single]


def resolve_ack_mailbox(args):
    """Return the single mailbox id to ack against.

    Ack must target exactly one mailbox. An explicit `--mailbox-id` always
    wins; otherwise a single configured mailbox is used. When multiple
    mailboxes are configured and none is named explicitly, fail closed rather
    than guess (acking against the wrong mailbox would silently drop another
    inbox's messages)."""
    if args.mailbox_id:
        return args.mailbox_id
    single = os.environ.get("LOFTBOX_MAILBOX_ID")
    if single:
        return single
    raw_ids = args.mailbox_ids or os.environ.get("LOFTBOX_MAILBOX_IDS")
    ids = [m.strip() for m in (raw_ids or "").split(",") if m.strip()]
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1:
        raise SystemExit("ack requires --mailbox-id when multiple mailboxes are configured")
    raise SystemExit("LOFTBOX_MAILBOX_ID or --mailbox-id is required for ack")


def list_inbox(base, api_key, mailbox_ids, limit, cursor):
    """Print unacknowledged inbound messages.

    Single mailbox: prints the raw upstream response (unchanged legacy output).
    Multiple mailboxes: prints {"mailboxes": [{"mailbox_id", "inbox"|"error"}]}
    so the agent can tell which inbox each message came from and ack the right
    one. A failing mailbox is recorded as an error and does not block others."""
    params = {"limit": str(limit)}
    if cursor:
        params["cursor"] = cursor
    query = urllib.parse.urlencode(params)

    if len(mailbox_ids) == 1:
        mailbox = urllib.parse.quote(mailbox_ids[0], safe="")
        try:
            print(http_text("GET", f"{base}/v1/mailboxes/{mailbox}/inbox?{query}", api_key))
        except HttpError as exc:
            print(exc.body, file=sys.stderr)
            raise SystemExit(exc.code)
        return

    out = {"mailboxes": []}
    any_ok = False
    for mb in mailbox_ids:
        mailbox = urllib.parse.quote(mb, safe="")
        try:
            text = http_text("GET", f"{base}/v1/mailboxes/{mailbox}/inbox?{query}", api_key)
            out["mailboxes"].append({"mailbox_id": mb, "inbox": json.loads(text)})
            any_ok = True
        except HttpError as exc:
            out["mailboxes"].append({"mailbox_id": mb, "error": {"status": exc.code, "body": exc.body}})
    print(json.dumps(out, ensure_ascii=False))
    if not any_ok:
        raise SystemExit(1)


def ack_inbox(base, api_key, mailbox_id, message_ids):
    mailbox = urllib.parse.quote(mailbox_id, safe="")
    try:
        print(http_text("POST", f"{base}/v1/mailboxes/{mailbox}/inbox/ack", api_key, {"message_ids": message_ids}))
    except HttpError as exc:
        print(exc.body, file=sys.stderr)
        raise SystemExit(exc.code)


def main():
    parser = argparse.ArgumentParser(description="Check or acknowledge LoftBox inbox messages.")
    parser.add_argument("--base-url", default=os.environ.get("LOFTBOX_BASE_URL", "https://api.loftbox.net"))
    parser.add_argument("--api-key", default=os.environ.get("LOFTBOX_API_KEY"))
    parser.add_argument("--mailbox-id", default=os.environ.get("LOFTBOX_MAILBOX_ID"))
    parser.add_argument(
        "--mailbox-ids",
        default=os.environ.get("LOFTBOX_MAILBOX_IDS"),
        help="Comma-separated mailbox ids to poll (multi-mailbox). Overrides --mailbox-id for list.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List unacknowledged inbound messages.")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--cursor")

    ack_parser = sub.add_parser("ack", help="Acknowledge processed inbound messages.")
    ack_parser.add_argument("--message-id", action="append", required=True)

    args = parser.parse_args()
    api_key = args.api_key or env("LOFTBOX_API_KEY")
    base = args.base_url.rstrip("/")

    if args.command == "list":
        list_inbox(base, api_key, resolve_mailbox_ids(args), args.limit, args.cursor)
    elif args.command == "ack":
        ack_inbox(base, api_key, resolve_ack_mailbox(args), args.message_id)


if __name__ == "__main__":
    main()
