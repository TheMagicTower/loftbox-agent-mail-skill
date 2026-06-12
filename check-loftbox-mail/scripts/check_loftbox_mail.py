#!/usr/bin/env python3
import argparse
import json
import os
import socket
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


class RequestError(Exception):
    """A failed request. `status` is the HTTP code, or None for transport
    errors (DNS/timeout/connection). `body` is the response/diagnostic text."""

    def __init__(self, status, body):
        super().__init__(f"request failed ({status})")
        self.status = status
        self.body = body


def http_text(method, url, api_key, payload=None):
    """Perform the request and return the response body text.

    Raises RequestError on any failure (HTTP non-2xx OR transport error) so
    both the single- and multi-mailbox callers handle failures uniformly: the
    single path aborts, the multi path records the error and continues."""
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
        raise RequestError(exc.code, exc.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise RequestError(None, str(exc))


def split_ids(raw):
    """Parse a comma-separated mailbox-id list, dropping blanks/whitespace."""
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def resolve_mailboxes(args):
    """Return the ordered list of mailbox ids to operate on, via ONE precedence
    used by BOTH list and ack (so they never disagree — the divergence that
    would let ack target a mailbox `list` never polled).

    Precedence (highest first):
      1. --mailbox-ids   (explicit CLI flag)
      2. --mailbox-id    (explicit CLI flag)
      3. LOFTBOX_MAILBOX_IDS   (env)
      4. LOFTBOX_MAILBOX_ID     (env)

    Argparse flags default to None (NOT from env), so an explicit CLI flag is
    distinguishable from a stray exported env var. The OUTPUT SHAPE is decided
    by the resulting count, not the source: exactly one mailbox -> legacy raw
    output; more than one -> wrapped multi output. So any single-effective
    mailbox config stays byte-identical to the legacy behavior."""
    if args.mailbox_ids is not None:
        ids = split_ids(args.mailbox_ids)
        if not ids:
            raise SystemExit("--mailbox-ids is empty")
        return ids
    if args.mailbox_id is not None:
        if not args.mailbox_id.strip():
            raise SystemExit("--mailbox-id is empty")
        return [args.mailbox_id.strip()]
    env_ids = split_ids(os.environ.get("LOFTBOX_MAILBOX_IDS"))
    if env_ids:
        return env_ids
    single = (os.environ.get("LOFTBOX_MAILBOX_ID") or "").strip()
    if single:
        return [single]
    raise SystemExit("LOFTBOX_MAILBOX_ID or LOFTBOX_MAILBOX_IDS is required")


def resolve_ack_mailbox(args):
    """Return the single mailbox id to ack against, using the same precedence
    as resolve_mailboxes. Ack must hit exactly one mailbox: an explicit
    --mailbox-id always wins; otherwise the effective set must contain exactly
    one mailbox. When more than one mailbox is configured and none is named
    explicitly, fail closed rather than guess — acking against the wrong
    mailbox would silently drop another inbox's messages."""
    if args.mailbox_id is not None:
        target = args.mailbox_id.strip()
        if not target:
            raise SystemExit("--mailbox-id is empty")
        return target
    ids = resolve_mailboxes(args)
    if len(ids) == 1:
        return ids[0]
    raise SystemExit("ack requires --mailbox-id when multiple mailboxes are configured")


def list_inbox(base, api_key, mailbox_ids, limit, cursor):
    """Print unacknowledged inbound messages.

    One mailbox: prints the raw upstream response (byte-identical legacy
    output). More than one: prints {"mailboxes":[{"mailbox_id","inbox"|"error"}]}
    so the agent can tell which inbox each message came from and ack the right
    one. A failing mailbox (HTTP or transport or unparseable body) is recorded
    under "error" and does not block the others; exit 1 only if all fail."""
    params = {"limit": str(limit)}
    if cursor:
        params["cursor"] = cursor
    query = urllib.parse.urlencode(params)

    if len(mailbox_ids) == 1:
        mailbox = urllib.parse.quote(mailbox_ids[0], safe="")
        try:
            print(http_text("GET", f"{base}/v1/mailboxes/{mailbox}/inbox?{query}", api_key))
        except RequestError as exc:
            print(exc.body, file=sys.stderr)
            raise SystemExit(exc.status or 1)
        return

    out = {"mailboxes": []}
    any_ok = False
    for mb in mailbox_ids:
        mailbox = urllib.parse.quote(mb, safe="")
        try:
            text = http_text("GET", f"{base}/v1/mailboxes/{mailbox}/inbox?{query}", api_key)
            out["mailboxes"].append({"mailbox_id": mb, "inbox": json.loads(text)})
            any_ok = True
        except RequestError as exc:
            out["mailboxes"].append({"mailbox_id": mb, "error": {"status": exc.status, "body": exc.body}})
        except json.JSONDecodeError as exc:
            out["mailboxes"].append({"mailbox_id": mb, "error": {"status": "parse_error", "body": str(exc)}})
    print(json.dumps(out, ensure_ascii=False))
    if not any_ok:
        raise SystemExit(1)


def ack_inbox(base, api_key, mailbox_id, message_ids):
    mailbox = urllib.parse.quote(mailbox_id, safe="")
    try:
        print(http_text("POST", f"{base}/v1/mailboxes/{mailbox}/inbox/ack", api_key, {"message_ids": message_ids}))
    except RequestError as exc:
        print(exc.body, file=sys.stderr)
        raise SystemExit(exc.status or 1)


def build_parser():
    parser = argparse.ArgumentParser(description="Check or acknowledge LoftBox inbox messages.")
    parser.add_argument("--base-url", default=os.environ.get("LOFTBOX_BASE_URL", "https://api.loftbox.net"))
    parser.add_argument("--api-key", default=os.environ.get("LOFTBOX_API_KEY"))
    # NOTE: --mailbox-id / --mailbox-ids default to None (NOT from env) so an
    # explicit CLI flag is distinguishable from an exported env var. Env vars
    # are read inside resolve_mailboxes/resolve_ack_mailbox with a single,
    # consistent precedence shared by list and ack.
    parser.add_argument("--mailbox-id", default=None)
    parser.add_argument(
        "--mailbox-ids",
        default=None,
        help="Comma-separated mailbox ids to poll (multi-mailbox). Overrides --mailbox-id.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List unacknowledged inbound messages.")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--cursor")

    ack_parser = sub.add_parser("ack", help="Acknowledge processed inbound messages.")
    ack_parser.add_argument("--message-id", action="append", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    api_key = args.api_key or env("LOFTBOX_API_KEY")
    base = args.base_url.rstrip("/")

    if args.command == "list":
        list_inbox(base, api_key, resolve_mailboxes(args), args.limit, args.cursor)
    elif args.command == "ack":
        ack_inbox(base, api_key, resolve_ack_mailbox(args), args.message_id)


if __name__ == "__main__":
    main()
