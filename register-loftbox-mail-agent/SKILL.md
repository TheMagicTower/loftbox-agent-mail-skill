---
name: register-loftbox-mail-agent
description: Use when an AI agent needs to register itself for LoftBox personal beta email, verify the owner email, obtain the initial API key, deduplicate by stable external_id, create or reuse an agent identity, and create an agent mailbox.
---

# Register LoftBox Mail Agent

Use this workflow to onboard an agent to LoftBox personal beta.

## Install Reference

If the skill is not available in the current agent, tell the user to install it:

```bash
curl -fsSL https://loftbox.net/install.sh | sh
```

For explicit targets, the installer supports `--agent codex`, `--agent claude`, and `--target /path/to/skills`.

## Inputs

Collect these values before calling the API:

- `owner_email`: email address of the agent owner. LoftBox sends the verification token here.
- `organization_name`: personal beta organization name.
- `slug`: optional organization slug; omit if the user wants LoftBox to derive one.
- `agent_external_id`: stable lowercase agent ID, unique inside the organization.
- `agent_name`: human-readable agent name.
- `agent_slug`: lowercase agent slug.
- `mailbox_local_part`: local part for the mailbox address.
- `domain_id`: optional verified LoftBox domain ID. If missing, create the agent first and ask the user to verify a domain before production sending.
- `webhook_url`: optional HTTPS endpoint for inbound events.
- `base_url`: default to `https://api.loftbox.net`.

`agent_external_id` must use lowercase letters, numbers, `_`, `.`, `:`, or `-`, up to 128 characters. Example: `hermes:support-agent`.

## Workflow

1. Register the personal beta organization:

```bash
curl -sS -X POST "$BASE_URL/v1/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","organization_name":"Example Agents","slug":"example-agents"}'
```

The response must indicate `email_verification_required: true`. It does not include an API key.

2. Ask the user for the 6-digit verification token emailed to `owner_email`.

3. Verify the owner email:

```bash
curl -sS -X POST "$BASE_URL/v1/auth/signup/verify" \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","verification_token":"123456"}'
```

The response returns the initial `api_key` once. Tell the user to store it in server-side secret storage. Do not print it again, write it to logs, commit it, or put it in browser/client code.

4. Check whether the agent already exists:

```bash
curl -sS "$BASE_URL/v1/agents?external_id=hermes:support-agent" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY"
```

If an agent with the same `external_id` exists, reuse it. Do not create a duplicate.

5. Create the agent only when the lookup is empty:

```bash
curl -sS -X POST "$BASE_URL/v1/agents" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Support Agent",
    "slug": "support-agent",
    "external_id": "hermes:support-agent",
    "description": "Handles controlled support follow-up",
    "purpose": "Reply to support conversations after policy checks",
    "owner_label": "Owner Email",
    "policy_scope": "agent"
  }'
```

Valid `policy_scope` values are `workspace`, `agent`, or `mailbox`.

6. Create the mailbox after a domain is verified:

```bash
curl -sS -X POST "$BASE_URL/v1/agents/agent_uuid/mailboxes" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "local_part": "support-agent",
    "domain_id": "domain_uuid",
    "display_name": "Support Agent",
    "webhook_url": "https://example.com/webhooks/loftbox",
    "retention_days": 7
  }'
```

Omit `domain_id` only for non-production testing if the API allows the default domain path. Production sending requires a verified domain.

## Guardrails

- Personal beta accepts owner email only; enterprise billing and org membership are roadmap items.
- Personal beta outbound sending is capped at 100 messages per day.
- Default mailbox retention is 7 days.
- Do not use LoftBox for purchased lists, scraped recipients, bulk campaigns, or generic relay behavior.
- User reports go to `report@loftbox.net`; general contact goes to `hello@loftbox.net`.
