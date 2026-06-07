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
  apply-dns <id>   next_actions DNS 레코드를 route53/cloudflare에 자동 적용
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


def _normalize_domain(raw):
    # 서버 normalize_domain 과 동일: trim, 끝 점 제거, 소문자.
    return raw.strip().rstrip(".").lower()


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


def _print_or_exit(code, body):
    if code == 200:
        print(body)
    else:
        print(body, file=sys.stderr)
        raise SystemExit(code)


def add(domain):
    domain = _normalize_domain(domain)
    code, body = _req("POST", "/v1/domains", {"domain": domain})
    if code in (200, 201):
        print(body)
        return
    if code == 409:
        # 자기 org 목록에서 status != 'deleted' 인 동일 도메인만 resolve.
        # 도메인은 대소문자 무시 — 양쪽 정규화 후 비교(서버는 소문자 정규화).
        c2, b2 = _req("GET", "/v1/domains")
        try:
            rows = json.loads(b2).get("data", []) if c2 == 200 else []
        except (ValueError, AttributeError):
            rows = []
        same = [r for r in rows if _normalize_domain(str(r.get("domain", ""))) == domain]
        active = [r for r in same if r.get("status") != "deleted"]
        deleted = [r for r in same if r.get("status") == "deleted"]
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


# ---------------------------------------------------------------------------
# apply-dns: next_actions DNS 레코드를 provider(route53/cloudflare)에 자동 적용
# ---------------------------------------------------------------------------


def _fetch_next_actions(domain_id):
    """도메인 status에서 아직 미충족인 DNS 레코드(next_actions)를 가져온다.

    각 항목은 {purpose,type,host,value} 형태. status에 next_actions가 없으면
    /dns 의 records 로 폴백한다.
    """
    qid = urllib.parse.quote(domain_id, safe="")
    code, body = _req("GET", f"/v1/domains/{qid}/status")
    if code != 200:
        print(body, file=sys.stderr)
        raise SystemExit(code)
    try:
        payload = json.loads(body)
    except ValueError:
        raise SystemExit("status 응답을 JSON으로 파싱하지 못했습니다.")
    actions = payload.get("next_actions")
    if actions is None:
        # 폴백: /dns 의 records.
        c2, b2 = _req("GET", f"/v1/domains/{qid}/dns")
        if c2 != 200:
            print(b2, file=sys.stderr)
            raise SystemExit(c2)
        try:
            actions = json.loads(b2).get("records", [])
        except ValueError:
            raise SystemExit("dns 응답을 JSON으로 파싱하지 못했습니다.")
    records = []
    for a in actions or []:
        rtype = str(a.get("type", "")).upper()
        host = str(a.get("host", ""))
        value = str(a.get("value", ""))
        if not rtype or not host:
            continue
        records.append(
            {
                "purpose": a.get("purpose", ""),
                "type": rtype,
                "host": host,
                "value": value,
            }
        )
    return records


# --- route53 ----------------------------------------------------------------


def _fqdn(host):
    host = host.strip()
    if not host.endswith("."):
        host = host + "."
    return host


def build_route53_change(record):
    """next_actions 한 건을 route53 ChangeBatch의 한 Change(UPSERT)로 매핑.

    TXT 는 리터럴 큰따옴표로 감싼다(route53 요구). MX 는 'priority host' 형태를
    그대로 Value 로 둔다. CNAME 은 value 그대로.
    """
    rtype = record["type"]
    host = record["host"]
    value = record["value"]
    if rtype == "TXT":
        rr_value = '"' + value + '"'
    else:
        # MX("10 mail.loftbox.net"), CNAME 모두 value 그대로.
        rr_value = value
    return {
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": _fqdn(host),
            "Type": rtype,
            "TTL": 300,
            "ResourceRecords": [{"Value": rr_value}],
        },
    }


def _apply_route53(records, domain, zone_id, dry_run, overwrite):
    if dry_run:
        # dry-run은 자격/boto3 없이 의도된 변경만 미리보기.
        results = []
        for rec in records:
            change = build_route53_change(rec)
            rrset = change["ResourceRecordSet"]
            desired_value = rrset["ResourceRecords"][0]["Value"]
            results.append(
                (rec, "dry-run", f"UPSERT {rrset['Type']} {rrset['Name']} -> {desired_value}")
            )
        return results

    try:
        import boto3
    except ImportError:
        raise SystemExit(
            "route53 provider는 boto3가 필요합니다 — `pip install boto3` 하거나 "
            "--provider cloudflare 를 사용하세요."
        )

    client = boto3.client("route53")

    if not zone_id:
        zone_id = _resolve_route53_zone(client, domain)
    if not zone_id:
        raise SystemExit(
            "route53 hosted zone을 찾지 못했습니다 — --zone-id 로 직접 지정하세요."
        )

    results = []
    for rec in records:
        change = build_route53_change(rec)
        rrset = change["ResourceRecordSet"]
        desired_value = rrset["ResourceRecords"][0]["Value"]

        existing = _route53_existing_value(client, zone_id, rrset["Name"], rrset["Type"])
        if existing is not None:
            if existing == desired_value:
                results.append((rec, "skipped", "이미 동일한 값"))
                continue
            if not overwrite:
                results.append((rec, "conflict", f"기존 값과 다름(--overwrite 없이 건너뜀): {existing}"))
                continue
        try:
            client.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={"Changes": [change]},
            )
            results.append((rec, "applied", f"UPSERT {rrset['Type']} {rrset['Name']}"))
        except Exception as exc:  # noqa: BLE001 - provider 오류는 레코드 단위 보고
            results.append((rec, "error", str(exc)))
    return results


def _resolve_route53_zone(client, domain):
    if not domain:
        return None
    target = _fqdn(_normalize_domain(domain))
    try:
        resp = client.list_hosted_zones_by_name(DNSName=target)
    except Exception:  # noqa: BLE001
        return None
    best = None
    best_len = -1
    for z in resp.get("HostedZones", []):
        if z.get("Config", {}).get("PrivateZone"):
            continue
        zname = z.get("Name", "")
        # target 이 zone 으로 끝나면(서브도메인 포함) 후보.
        if target == zname or target.endswith("." + zname):
            if len(zname) > best_len:
                best = z.get("Id")
                best_len = len(zname)
    return best


def _route53_existing_value(client, zone_id, name, rtype):
    try:
        resp = client.list_resource_record_sets(
            HostedZoneId=zone_id,
            StartRecordName=name,
            StartRecordType=rtype,
            MaxItems="1",
        )
    except Exception:  # noqa: BLE001
        return None
    for rrset in resp.get("ResourceRecordSets", []):
        if rrset.get("Name") == name and rrset.get("Type") == rtype:
            values = [r.get("Value") for r in rrset.get("ResourceRecords", [])]
            if values:
                return values[0]
    return None


# --- cloudflare -------------------------------------------------------------


def build_cloudflare_payload(record):
    """next_actions 한 건을 cloudflare dns_records payload 로 매핑.

    TXT 는 따옴표 없이 content 그대로(CF가 자동 추가). MX 는 'priority host' 를
    분리해 priority 필드를 채운다. CNAME 은 content 그대로.
    """
    rtype = record["type"]
    host = record["host"]
    value = record["value"]
    payload = {"type": rtype, "name": host}
    if rtype == "MX":
        parts = value.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            payload["priority"] = int(parts[0])
            payload["content"] = parts[1]
        else:
            payload["content"] = value
    else:
        # TXT(따옴표 불요), CNAME 모두 value 그대로.
        payload["content"] = value
    return payload


def _cf_req(method, path, token, body=None):
    url = "https://api.cloudflare.com/client/v4" + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except ValueError:
            return exc.code, {}


def _resolve_cloudflare_zone(token, domain):
    if not domain:
        return None
    name = _normalize_domain(domain)
    # 서브도메인일 수 있으니 점차 상위 도메인으로 조회.
    labels = name.split(".")
    while len(labels) >= 2:
        candidate = ".".join(labels)
        code, payload = _cf_req(
            "GET", f"/zones?name={urllib.parse.quote(candidate, safe='')}", token
        )
        if code == 200 and payload.get("result"):
            return payload["result"][0]["id"]
        labels = labels[1:]
    return None


def _cf_existing_record(token, zone_id, rtype, name):
    q = urllib.parse.urlencode({"type": rtype, "name": name})
    code, payload = _cf_req("GET", f"/zones/{zone_id}/dns_records?{q}", token)
    if code == 200 and payload.get("result"):
        return payload["result"][0]
    return None


def _cf_same(existing, payload):
    if existing.get("content") != payload.get("content"):
        return False
    if payload.get("type") == "MX":
        return existing.get("priority") == payload.get("priority")
    return True


def _apply_cloudflare(records, domain, zone_id, dry_run, overwrite):
    if dry_run:
        # dry-run은 토큰/네트워크 없이 의도된 변경만 미리보기.
        results = []
        for rec in records:
            payload = build_cloudflare_payload(rec)
            desc = f"{payload['type']} {payload['name']} -> {payload['content']}"
            if payload.get("priority") is not None:
                desc += f" (priority {payload['priority']})"
            results.append((rec, "dry-run", desc))
        return results

    token = _env("CLOUDFLARE_API_TOKEN")
    if not zone_id:
        zone_id = _resolve_cloudflare_zone(token, domain)
    if not zone_id:
        raise SystemExit(
            "cloudflare zone을 찾지 못했습니다 — --zone-id 로 직접 지정하세요."
        )

    results = []
    for rec in records:
        payload = build_cloudflare_payload(rec)
        desc = f"{payload['type']} {payload['name']} -> {payload['content']}"
        if payload.get("priority") is not None:
            desc += f" (priority {payload['priority']})"

        if dry_run:
            results.append((rec, "dry-run", desc))
            continue

        existing = _cf_existing_record(token, zone_id, payload["type"], payload["name"])
        if existing is not None:
            if _cf_same(existing, payload):
                results.append((rec, "skipped", "이미 동일한 값"))
                continue
            if not overwrite:
                results.append(
                    (rec, "conflict", f"기존 값과 다름(--overwrite 없이 건너뜀): {existing.get('content')}")
                )
                continue
            code, resp = _cf_req(
                "PUT", f"/zones/{zone_id}/dns_records/{existing['id']}", token, payload
            )
        else:
            code, resp = _cf_req("POST", f"/zones/{zone_id}/dns_records", token, payload)

        if code in (200, 201) and resp.get("success", True):
            results.append((rec, "applied", desc))
        else:
            errors = resp.get("errors") if isinstance(resp, dict) else None
            results.append((rec, "error", f"HTTP {code}: {errors}"))
    return results


def apply_dns(domain_id, provider, dry_run, overwrite, zone_id):
    records = _fetch_next_actions(domain_id)
    if not records:
        print("적용할 DNS 레코드가 없습니다(next_actions 비어있음 — 이미 충족되었을 수 있음).")
        return

    # 도메인 이름은 status/dns 응답이 아니라 도메인 메타에서 추론.
    # next_actions host 중 가장 짧은 도메인 후보(소유 TXT _loftbox.<domain> 등)에서
    # 도메인을 추정한다. zone 해석은 provider별 resolver 가 처리.
    domain_guess = _guess_domain(records)

    print(f"provider={provider} domain={domain_guess or '?'} 레코드 {len(records)}건"
          + (" [dry-run]" if dry_run else ""))
    for rec in records:
        print(f"  - {rec['type']:6} {rec['host']}  ->  {rec['value']}  ({rec['purpose']})")

    if provider == "route53":
        results = _apply_route53(records, domain_guess, zone_id, dry_run, overwrite)
    elif provider == "cloudflare":
        results = _apply_cloudflare(records, domain_guess, zone_id, dry_run, overwrite)
    else:
        raise SystemExit(f"지원하지 않는 provider: {provider}")

    print("\n결과:")
    counts = {}
    for rec, status, detail in results:
        counts[status] = counts.get(status, 0) + 1
        print(f"  [{status:8}] {rec['type']:6} {rec['host']}  {detail}")
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"\n요약: {summary}")
    if any(s in ("error", "conflict") for _, s, _ in results) and not dry_run:
        # 부분 실패는 비-제로 종료로 알린다(도메인 상태엔 영향 없음).
        raise SystemExit(1)


def _guess_domain(records):
    """next_actions host 들에서 가장 짧은 등록 도메인 후보를 추정한다.

    예: _loftbox.acme.com / mail-cname.acme.com / acme.com 중 acme.com.
    host 들의 공통 접미사를 도메인으로 본다(2 레이블 이상).
    """
    hosts = [_normalize_domain(r["host"]) for r in records if r.get("host")]
    if not hosts:
        return None
    # 가장 짧은 host 의 후행 도메인 부분(서브도메인 제거)을 후보로.
    shortest = min(hosts, key=lambda h: len(h.split(".")))
    labels = shortest.split(".")
    if len(labels) <= 2:
        return shortest
    # 모든 host 가 끝부분으로 공유하는 가장 긴 도메인(>=2 레이블)을 찾는다.
    for start in range(len(labels) - 1):
        candidate = ".".join(labels[start:])
        if all(h == candidate or h.endswith("." + candidate) for h in hosts):
            return candidate
    return ".".join(labels[-2:])


def main():
    p = argparse.ArgumentParser(description="LoftBox 도메인 온보딩 (고객 에이전트용)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="도메인 추가")
    a.add_argument("domain")
    for cmd in ("dns", "status", "verify"):
        q = sub.add_parser(cmd)
        q.add_argument("id")
    ad = sub.add_parser("apply-dns", help="next_actions DNS 레코드를 provider에 적용")
    ad.add_argument("--provider", required=True, choices=["route53", "cloudflare"])
    ad.add_argument("--dry-run", action="store_true", help="적용 없이 변경 미리보기")
    ad.add_argument("--overwrite", action="store_true", help="기존 값과 다르면 덮어쓰기")
    ad.add_argument("--zone-id", default=None, help="hosted zone / CF zone id 직접 지정")
    ad.add_argument("id", help="도메인 id (dom_…)")
    args = p.parse_args()

    if args.cmd == "add":
        add(args.domain)
    elif args.cmd == "dns":
        _print_or_exit(*_req("GET", f"/v1/domains/{urllib.parse.quote(args.id, safe='')}/dns"))
    elif args.cmd == "status":
        _print_or_exit(*_req("GET", f"/v1/domains/{urllib.parse.quote(args.id, safe='')}/status"))
    elif args.cmd == "verify":
        _print_or_exit(*_req("POST", f"/v1/domains/{urllib.parse.quote(args.id, safe='')}/verify"))
    elif args.cmd == "apply-dns":
        apply_dns(args.id, args.provider, args.dry_run, args.overwrite, args.zone_id)


if __name__ == "__main__":
    main()
