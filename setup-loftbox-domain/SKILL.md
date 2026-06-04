---
name: setup-loftbox-domain
description: Use when a LoftBox customer wants to connect (onboard) their own custom email domain (e.g. acme.com) to LoftBox — add the domain, surface the DNS records the human must set, then verify. Read+write against the customer's OWN organization only.
---

# Set up a LoftBox custom domain

Use this skill to onboard a customer's **own** custom email domain to LoftBox so
they can send/receive mail from it. The flow is: **add → tell the human which DNS
records to set → poll status → verify**.

## Required configuration

- `LOFTBOX_API_KEY`: the customer's **organization** API key (issued from the LoftBox
  portal). Scopes the agent to that org only. Keep it server-side; never print it.
- `LOFTBOX_BASE_URL`: optional, defaults to `https://api.loftbox.net`.

## Important rules

1. Operate on the **caller's own organization** only — the API key is org-scoped.
2. **You cannot set DNS for the customer.** Surface the records (from `status`
   `next_actions` or `dns`) clearly and ask the human to add them at their DNS
   provider. Then poll/verify.
3. Do not invent or fabricate DNS values — use exactly what the API returns.
4. If onboarding cannot proceed (see "Conflicts" below), report it and stop;
   do not loop.

## Commands

```bash
# $SKILL_DIR is this skill's own directory (…/setup-loftbox-domain).
PY="$SKILL_DIR/scripts/setup_loftbox_domain.py"

# 1) Add the domain (returns the domain object incl. its id / dom_ public id)
python3 "$PY" add acme.com

# 2) See the structured status: which DNS records are still needed (next_actions),
#    and inbound/outbound verification flags.
python3 "$PY" status <domain_id>

# 3) (Alternative) full DNS record list to set
python3 "$PY" dns <domain_id>

# 4) After the human has set the DNS records, trigger verification
python3 "$PY" verify <domain_id>
```

## Onboarding flow

1. `add <domain>` → note the returned domain `id` (or `dom_…` public id).
2. `status <id>` → read `next_actions` (a list of `{purpose,type,host,value}` DNS
   records the human must create). Present them to the human clearly (host, type,
   value) and ask them to add them at their DNS provider.
3. Wait for the human to confirm DNS is set, then `verify <id>`.
4. Poll `status <id>` until `status` is `verified` (both `inbound` and `outbound`
   verified). DNS propagation can take minutes to hours; poll patiently, not in a
   tight loop.
5. Report the final verified status to the human.

`next_actions` includes the ownership TXT (`_loftbox.<domain>` =
`loftbox-verification=…`), inbound MX (`10 mail.loftbox.net`), sender SPF/DMARC,
and provider DKIM/return-path records. SPF/DMARC are shown until the domain is
fully verified.

## Conflicts (when `add` returns a conflict)

- **Previously deleted**: "이 도메인은 이전에 삭제되었습니다 — 재추가 미지원(운영자 조치 필요)."
  → report and stop; re-adding a deleted domain is not supported yet.
- **In use by another organization**: "다른 조직이 이미 사용 중일 수 있습니다 — 운영자 조치 필요."
  → report and stop; escalate to a LoftBox operator.

## Errors

- 401: API key 누락/무효 → 고객에게 키 확인 요청.
- 404: 도메인 없음 또는 삭제됨.
- 5xx: 일시 장애 → 잠시 후 재시도, 지속되면 보고.
