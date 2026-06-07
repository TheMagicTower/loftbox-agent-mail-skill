#!/usr/bin/env python3
"""LoftBox 에이전트/메일박스 해지(offboarding) — soft-delete, --yes 머신 게이트.

자기 조직(org)의 에이전트/메일박스를 soft-delete 한다. org-scoped api_key
(LOFTBOX_API_KEY)로만 동작하며 자기 조직 리소스만 다룬다. soft-delete 이므로
복구는 운영자를 통해 가능하다.

에이전트 soft-delete 는 메일박스로 cascade 하지 않으므로 완전 해지 순서는:
  1) list-mailboxes <agent_id> 로 메일박스 열거
  2) 각 메일박스를 delete-mailbox <id> --yes
  3) delete-agent <agent_id> --yes

명령:
  list-mailboxes <agent_id>   에이전트의 메일박스 열거(GET /v1/agents/{id}/mailboxes)
  delete-mailbox <id> [--yes] 메일박스 soft-delete (DELETE /v1/mailboxes/{id})
  delete-agent <id> [--yes]   에이전트 soft-delete (DELETE /v1/agents/{id})

계정/조직 해지는 이 스킬의 범위가 아니다 — 이메일 소유자가 LoftBox 포털/운영자를
통해 직접 수행한다.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = os.environ.get(
    "LOFTBOX_USER_AGENT", "loftbox-agent-mail-skill/1.0 (+https://loftbox.net)"
)


def _env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"{name} is required")
    return value


def _req(method, path, body=None):
    base = os.environ.get("LOFTBOX_BASE_URL", "https://api.loftbox.net").rstrip("/")
    key = _env("LOFTBOX_API_KEY")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def list_mailboxes(agent_id):
    qid = urllib.parse.quote(agent_id, safe="")
    code, body = _req("GET", f"/v1/agents/{qid}/mailboxes")
    if code != 200:
        print(body, file=sys.stderr)
        raise SystemExit(code)
    try:
        payload = json.loads(body)
    except ValueError:
        print(body)
        return
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        print(body)
        return
    if not rows:
        print(f"에이전트 {agent_id} 에 메일박스가 없습니다.")
        return
    print(f"에이전트 {agent_id} 의 메일박스 {len(rows)}건:")
    for r in rows:
        mid = r.get("id") or r.get("mailbox_id") or "?"
        addr = r.get("address") or r.get("email") or r.get("local_part") or "?"
        print(f"  - {mid}  {addr}")


def _delete(kind, path_prefix, resource_id, yes):
    """공통 머신 게이트 delete. --yes 없으면 대상만 표시하고 DELETE 미발행."""
    target = f"{kind} {resource_id}"
    if not yes:
        print(f"대상: {target}")
        print("이것은 파괴적입니다(soft-delete). 확인 후 --yes 로 재실행하세요.")
        return
    qid = urllib.parse.quote(resource_id, safe="")
    code, body = _req("DELETE", f"{path_prefix}/{qid}")
    if code in (200, 204):
        print(f"삭제됨(soft-delete): {target}")
        if body:
            print(body)
        return
    if code == 404:
        print(f"없음/이미 삭제됨(404): {target}")
        return
    print(body, file=sys.stderr)
    raise SystemExit(code)


def delete_mailbox(mailbox_id, yes):
    _delete("mailbox", "/v1/mailboxes", mailbox_id, yes)


def delete_agent(agent_id, yes):
    _delete("agent", "/v1/agents", agent_id, yes)


def main():
    p = argparse.ArgumentParser(
        description="LoftBox 에이전트/메일박스 해지 (고객 에이전트용)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    lm = sub.add_parser("list-mailboxes", help="에이전트의 메일박스 열거")
    lm.add_argument("agent_id", help="에이전트 id")

    dm = sub.add_parser("delete-mailbox", help="메일박스 soft-delete — --yes 필요")
    dm.add_argument("--yes", action="store_true",
                    help="머신 게이트: 이 플래그가 있을 때만 실제 DELETE 발행")
    dm.add_argument("id", help="메일박스 id")

    da = sub.add_parser("delete-agent", help="에이전트 soft-delete — --yes 필요")
    da.add_argument("--yes", action="store_true",
                    help="머신 게이트: 이 플래그가 있을 때만 실제 DELETE 발행")
    da.add_argument("id", help="에이전트 id")

    args = p.parse_args()

    if args.cmd == "list-mailboxes":
        list_mailboxes(args.agent_id)
    elif args.cmd == "delete-mailbox":
        delete_mailbox(args.id, args.yes)
    elif args.cmd == "delete-agent":
        delete_agent(args.id, args.yes)


if __name__ == "__main__":
    main()
