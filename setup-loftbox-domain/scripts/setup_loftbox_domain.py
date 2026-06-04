#!/usr/bin/env python3
"""LoftBox 도메인 온보딩 (고객 에이전트용, 읽기+쓰기).

자기 조직(org)의 커스텀 도메인을 추가하고, 설정할 DNS 레코드를 확인하고,
검증 상태를 폴링한다. org-scoped api_key(LOFTBOX_API_KEY)로만 동작하며 자기
조직 도메인만 다룬다.

명령:
  add <domain>     도메인 추가 (이미 존재하면 409 → 자기 org에서 활성 행 resolve)
  dns <id>         설정할 DNS 레코드 목록
  status <id>      구조화된 상태(inbound/outbound 플래그 + next_actions)
  verify <id>      검증 트리거(DNS 설정 후)
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


def _req(method, path, body=None):
    base = os.environ.get("LOFTBOX_BASE_URL", "https://api.loftbox.net").rstrip("/")
    key = os.environ["LOFTBOX_API_KEY"]
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


def _print_or_exit(code, body):
    if code == 200:
        print(body)
    else:
        print(body, file=sys.stderr)
        raise SystemExit(code)


def add(domain):
    code, body = _req("POST", "/v1/domains", {"domain": domain})
    if code in (200, 201):
        print(body)
        return
    if code == 409:
        # 자기 org 목록에서 status != 'deleted' 인 동일 도메인만 resolve.
        c2, b2 = _req("GET", "/v1/domains")
        rows = json.loads(b2).get("data", []) if c2 == 200 else []
        active = [r for r in rows if r.get("domain") == domain and r.get("status") != "deleted"]
        deleted = [r for r in rows if r.get("domain") == domain and r.get("status") == "deleted"]
        if active:
            print(json.dumps(active[0]))
            return
        if deleted:
            raise SystemExit(
                "이 도메인은 이전에 삭제되었습니다 — 재추가는 아직 지원되지 않습니다(운영자 조치 필요)."
            )
        raise SystemExit(
            "이 도메인은 다른 조직이 이미 사용 중일 수 있습니다 — 운영자 조치가 필요합니다."
        )
    print(body, file=sys.stderr)
    raise SystemExit(code)


def main():
    p = argparse.ArgumentParser(description="LoftBox 도메인 온보딩 (고객 에이전트용)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="도메인 추가")
    a.add_argument("domain")
    for cmd in ("dns", "status", "verify"):
        q = sub.add_parser(cmd)
        q.add_argument("id")
    args = p.parse_args()

    if args.cmd == "add":
        add(args.domain)
    elif args.cmd == "dns":
        _print_or_exit(*_req("GET", f"/v1/domains/{urllib.parse.quote(args.id, safe='')}/dns"))
    elif args.cmd == "status":
        _print_or_exit(*_req("GET", f"/v1/domains/{urllib.parse.quote(args.id, safe='')}/status"))
    elif args.cmd == "verify":
        _print_or_exit(*_req("POST", f"/v1/domains/{urllib.parse.quote(args.id, safe='')}/verify"))


if __name__ == "__main__":
    main()
