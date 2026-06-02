# LoftBox Agent Mail Skill

Public agent skill for registering an AI agent with LoftBox personal beta email.

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

Then use the register-loftbox-mail-agent skill to register my LoftBox personal beta mail agent.
Ask me for owner_email, organization_name, agent_external_id, agent_name, agent_slug, mailbox_local_part, and optional webhook_url.
```

The skill walks the agent through owner-email verification, one-time API key handling, duplicate checks by `external_id`, agent creation, and mailbox creation.

LoftBox API base URL: `https://api.loftbox.net`

General contact: `hello@loftbox.net`

User reports: `report@loftbox.net`
