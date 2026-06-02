---
name: check-loftbox-mail
description: Use when an AI agent needs to check a LoftBox mailbox inbox for inbound mail, process unacknowledged messages, and acknowledge messages only after durable processing.
---

# Check LoftBox Mail

Use this skill when the agent does not have an HTTPS webhook endpoint, or when
the user asks the agent to check replies manually.

## Required Configuration

- `LOFTBOX_API_KEY`: server-side API key.
- `LOFTBOX_MAILBOX_ID`: mailbox UUID or `mb_...` public ID.
- `LOFTBOX_BASE_URL`: optional, defaults to `https://api.loftbox.net`.

Keep the API key server-side. Do not print it or store it in client-visible
places.

## List Inbox

Use the bundled script:

```bash
python3 "$SKILL_DIR/scripts/check_loftbox_mail.py" list --limit 20
```

Equivalent API request:

```bash
curl -sS "${LOFTBOX_BASE_URL:-https://api.loftbox.net}/v1/mailboxes/$LOFTBOX_MAILBOX_ID/inbox?limit=20" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY"
```

The response contains unacknowledged inbound messages in `data` and may include
`next_cursor`. Process messages in a durable way before acking.

## Acknowledge Processed Messages

Ack only after the agent has durably recorded the outcome or completed the user
visible work. Ack is idempotent.

```bash
python3 "$SKILL_DIR/scripts/check_loftbox_mail.py" ack --message-id msg_...
```

Equivalent API request:

```bash
curl -sS -X POST "${LOFTBOX_BASE_URL:-https://api.loftbox.net}/v1/mailboxes/$LOFTBOX_MAILBOX_ID/inbox/ack" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message_ids":["msg_..."]}'
```

## Polling Policy

- Poll every 30-60 seconds while active.
- Back off when the inbox is empty.
- Back off on `429` responses.
- Do not ack messages that the agent has not processed.
- Summarize new mail to the user with message ID/public ID, from, subject,
  created time, and any action taken.
