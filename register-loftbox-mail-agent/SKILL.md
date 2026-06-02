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

Personal beta customer input is intentionally minimal. Ask the user only for:

- `owner_email`: email address of the agent owner. LoftBox sends the verification token here.

Derive these values without asking the user, unless derivation is impossible or the user explicitly asks to override them:

- `organization_name`: derive from the owner email and agent label, such as `owner-local LoftBox Agents`.
- `slug`: derive from `organization_name`.
- `agent_external_id`: stable lowercase agent ID, unique inside the organization.
- `agent_name`: derive from the current agent/runtime name; fall back to `LoftBox Agent`.
- `agent_slug`: derive from `agent_name`.
- `mailbox_local_part`: derive from `agent_slug`.
- `domain_id`: omit for the personal beta default-domain path unless the user explicitly provides a verified domain.
- `webhook_url`: only use when the current agent has an HTTPS inbound webhook endpoint.
- `base_url`: default to `https://api.loftbox.net`.

`agent_external_id` must use lowercase letters, numbers, `_`, `.`, `:`, or `-`, up to 128 characters. Example: `hermes:support-agent`.

Use a stable derivation order for `agent_external_id`:

1. Current agent identity from local runtime/config, if available.
2. Agent product/runtime name plus stable instance or workspace identifier, if available.
3. Normalized `agent_name`.

If the derived `agent_external_id` collides with an unrelated existing agent, ask one short question for a distinguishing label, then retry with a new derived ID.

## Workflow

1. Register the personal beta organization. Use the derived `organization_name` and `slug`; do not ask the user for them.

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

The response returns the initial `api_key` once. Tell the user to store it in server-side secret storage. Do not print it again, write it to logs, commit it, or put it in browser/client code. LoftBox also sends the owner a welcome email with the personal beta limits and future admin/billing signup path.

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

6. Create the mailbox on the LoftBox default domain:

```bash
curl -sS -X POST "$BASE_URL/v1/agents/agent_uuid/mailboxes" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "local_part": "support-agent",
    "display_name": "Support Agent",
    "retention_days": 7
  }'
```

Custom domains are not required for personal beta registration; only use `domain_id` when the user explicitly supplies a verified domain.

7. Choose the receive path.

If the current agent has an HTTPS webhook endpoint, register it as an agent webhook:

```bash
curl -sS -X POST "$BASE_URL/v1/agents/agent_uuid/webhooks" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/webhooks/loftbox",
    "event_types": ["message.inbound", "thread.created"]
  }'
```

The webhook secret is returned once. Store it in server-side secret storage and verify `X-LoftBox-Signature` before trusting any webhook body.

If the current agent does not have a webhook endpoint, use inbox polling:

```bash
curl -sS "$BASE_URL/v1/mailboxes/mailbox_uuid/inbox?limit=20" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY"
```

Process each returned message exactly once. After processing has been durably recorded by the agent, acknowledge the message:

```bash
curl -sS -X POST "$BASE_URL/v1/mailboxes/mailbox_uuid/inbox/ack" \
  -H "Authorization: Bearer $LOFTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message_ids":["message_uuid_or_msg_public_id"]}'
```

Do not ack before processing is complete. Ack is idempotent, so retrying the same `message_ids` is safe. Poll every 30-60 seconds while active, back off when the inbox is empty or the API returns a rate-limit response, and keep the API key server-side.

## Follow-up Operation

- Use `send-loftbox-mail` to send operational email through the created mailbox.
- Use `check-loftbox-mail` to poll replies and ack processed messages when no webhook endpoint is available.

## Guardrails

- Personal beta accepts owner email only; enterprise billing and org membership are roadmap items.
- Personal beta outbound sending is capped at 100 messages per day.
- Default mailbox retention is 7 days.
- Do not use LoftBox for purchased lists, scraped recipients, bulk campaigns, or generic relay behavior.
- User reports go to `report@loftbox.net`; general contact goes to `hello@loftbox.net`.
