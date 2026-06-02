# LoftBox Agent Mail Skills

Public agent skills for registering an AI agent with LoftBox personal beta
email, sending operational email, and checking replies.

## One-Line Install

```bash
curl -fsSL https://loftbox.net/install.sh | sh
```

The installer auto-detects common local agent skill directories. Use an explicit target when needed:

```bash
curl -fsSL https://loftbox.net/install.sh | sh -s -- --agent codex
curl -fsSL https://loftbox.net/install.sh | sh -s -- --agent claude
curl -fsSL https://loftbox.net/install.sh | sh -s -- --target "$HOME/.my-agent/skills"
```

## Agent Prompt

```text
If the LoftBox mail skill is missing, install it with:
curl -fsSL https://loftbox.net/install.sh | sh

Use register-loftbox-mail-agent to register this agent for LoftBox personal beta.
Ask me only for my owner email.
After registration, use send-loftbox-mail to send and check-loftbox-mail to check replies.
```

The skills walk the agent through owner-email verification, one-time API key handling, duplicate checks by derived `external_id`, agent creation, mailbox creation, sending operational email, and receiving mail by webhook or inbox polling. After verification, LoftBox sends the owner a welcome notice with the personal beta limits and future admin/billing signup path.

Installed skills:

- `register-loftbox-mail-agent`: owner verification, API key, agent, mailbox.
- `send-loftbox-mail`: send operational email through `POST /v1/messages`.
- `check-loftbox-mail`: list inbox messages and ack processed replies.

Personal beta defaults:

- Ask the user only for owner email.
- 100 outbound sends per day.
- 7-day mailbox retention when omitted.
- Use signed webhooks when the agent has an HTTPS endpoint; otherwise poll the mailbox inbox and ack only after durable processing.

LoftBox API base URL: `https://api.loftbox.net`

General contact: `hello@loftbox.net`

User reports: `report@loftbox.net`
