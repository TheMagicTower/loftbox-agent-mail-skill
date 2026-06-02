# LoftBox Agent Mail Skill

Public Codex skill for registering an AI agent with LoftBox personal beta email.

## One-Line Install

```bash
git clone https://github.com/TheMagicTower/loftbox-agent-mail-skill.git /tmp/loftbox-agent-mail-skill && mkdir -p ~/.codex/skills && cp -R /tmp/loftbox-agent-mail-skill/register-loftbox-mail-agent ~/.codex/skills/
```

## Agent Prompt

```text
Use $register-loftbox-mail-agent to onboard my LoftBox personal beta agent. Owner email: <owner@example.com>. Organization: <org name>. Agent external_id: <stable-agent-id>. Agent name: <name>. Mailbox local_part: <local-part>.
```

The skill walks the agent through owner-email verification, one-time API key handling, duplicate checks by `external_id`, agent creation, and mailbox creation.

LoftBox API base URL: `https://api.loftbox.net`

General contact: `hello@loftbox.net`

User reports: `report@loftbox.net`
