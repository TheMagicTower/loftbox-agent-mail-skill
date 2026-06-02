---
name: send-loftbox-mail
description: Use when an AI agent needs to send an operational email through an existing LoftBox mailbox using the LoftBox API, while respecting personal beta limits, approval holds, and server-side API key handling.
---

# Send LoftBox Mail

Use this skill after `register-loftbox-mail-agent` has produced an API key and
mailbox.

## Required Configuration

- `LOFTBOX_API_KEY`: server-side API key returned once by owner-email verification.
- `LOFTBOX_MAILBOX_ID`: mailbox UUID or `mb_...` public ID.
- `LOFTBOX_BASE_URL`: optional, defaults to `https://api.loftbox.net`.

Never ask the user to paste the API key into chat if it can be read from secret
storage or environment variables. Never put API keys in browser/client code,
logs, screenshots, issue comments, or message metadata.

## Inputs

Ask only for the missing mail content:

- recipient email address or addresses;
- subject;
- body text;
- optional cc, html, markdown, reply headers, and metadata.

Do not send bulk campaigns, purchased-list mail, scraped-recipient mail, generic
relay traffic, or messages unrelated to the agent's operational purpose.
Personal beta outbound sending is capped at 100 messages per day.

## Send

Prefer the bundled script because it builds JSON safely:

```bash
python3 "$SKILL_DIR/scripts/send_loftbox_mail.py" \
  --to recipient@example.com \
  --subject "Support follow-up" \
  --text "Thanks for your message. We are checking this now."
```

If the runtime cannot expose `SKILL_DIR`, run the script from this skill
directory or use the equivalent API request:

```bash
curl -sS -X POST "${LOFTBOX_BASE_URL:-https://api.loftbox.net}/v1/messages" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "mailbox_id": "'"$LOFTBOX_MAILBOX_ID"'",
    "to": ["recipient@example.com"],
    "subject": "Support follow-up",
    "body_text": "Thanks for your message. We are checking this now."
  }'
```

## Response Handling

- `201`: message was accepted by LoftBox, usually with status `queued`,
  `pending_approval`, or `blocked`.
- `429`: stop sending and back off. Do not bypass the daily or scoped limit.
- `400` or `404`: verify mailbox ID, recipient addresses, subject, and body.
- `pending_approval`: tell the user/operator that delivery is held by policy.
- `blocked`: do not retry unless policy or content is changed by an operator.

Return a concise result to the user: message ID/public ID, status, recipient
count, and whether any follow-up action is required.
