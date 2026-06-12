#!/usr/bin/env python3
"""LoftBox 도메인 온보딩 (고객 에이전트용, 읽기+쓰기).

자기 조직(org)의 커스텀 도메인을 추가하고, 설정할 DNS 레코드를 확인하고,
검증 상태를 폴링한다. org-scoped api_key(LOFTBOX_API_KEY)로만 동작하며 자기
조직 도메인만 다룬다.

명령:
  add <domain>     도메인 추가 — 코어가 멱등 처리(#209): 자기 org 활성=200 기존 행,
                   자기 org 삭제분=같은 id 재활성 201, 신규=201, 타 조직 점유=409
  dns <id>         설정할 DNS 레코드 목록
  status <id>      구조화된 상태(inbound/outbound 플래그 + next_actions)
  verify <id>      검증 트리거(DNS 설정 후)
  apply-dns <id>   next_actions DNS 레코드를 route53/cloudflare에 자동 적용
  delete <id>      도메인 soft-delete (해지). --yes 머신 게이트로 보호.
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
    # 코어 #209 이후 create 는 멱등: 200=이미 이 org 의 활성 도메인(기존 행),
    # 201=신규 또는 삭제분 같은 id 재활성. 자기 org 의 활성/삭제 행은 서버가
    # 직접 resolve 하므로 클라이언트측 목록 조회 분기가 불필요해졌다.
    if code in (200, 201):
        print(body)
        return
    if code == 409:
        # 남은 409 = 다른 조직이 활성으로 점유 중(또는 드문 동시-생성 경합 —
        # 경합이면 즉시 재시도 시 200 으로 수렴한다).
        raise SystemExit(
            "이 도메인은 다른 조직이 사용 중입니다 — 잠시 후 1회 재시도하고, "
            "여전히 409 면 운영자에게 에스컬레이션하세요."
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


def _is_dns_suffix(zone_fqdn, host_fqdn):
    """zone_fqdn 이 host_fqdn 의 DNS 접미사인지(같거나 라벨 경계로 끝남)."""
    zone = zone_fqdn.lower()
    host = host_fqdn.lower()
    return host == zone or host.endswith("." + zone)


def _route53_txt_value(value):
    """TXT rdata 를 route53 형식으로 만든다.

    DNS TXT 한 character-string 은 ≤255 바이트. 255 초과 값은 ≤255 청크로 나눠
    각각 큰따옴표로 감싸 공백으로 잇는다: `"chunk1" "chunk2" ...`. route53 은
    이 멀티-스트링 형식만 받아들인다(단일 따옴표로는 255 초과 시 거부).
    """
    if len(value.encode("utf-8")) <= 255:
        return '"' + value + '"'
    # 문자 경계를 보존하면서 각 청크의 바이트 길이를 ≤255 로 유지.
    chunks = []
    cur = ""
    cur_bytes = 0
    for ch in value:
        ch_bytes = len(ch.encode("utf-8"))
        if cur_bytes + ch_bytes > 255:
            chunks.append(cur)
            cur = ch
            cur_bytes = ch_bytes
        else:
            cur += ch
            cur_bytes += ch_bytes
    if cur:
        chunks.append(cur)
    return " ".join('"' + c + '"' for c in chunks)


def build_route53_change(record):
    """next_actions 한 건을 route53 ChangeBatch의 한 Change(UPSERT)로 매핑.

    TXT 는 리터럴 큰따옴표로 감싼다(route53 요구). 255바이트 초과 TXT 는 ≤255
    청크로 분할해 여러 큰따옴표 세그먼트로 잇는다. MX 는 'priority host' 형태를
    그대로 Value 로 둔다. CNAME 은 value 그대로.
    """
    rtype = record["type"]
    host = record["host"]
    value = record["value"]
    if rtype == "TXT":
        rr_value = _route53_txt_value(value)
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

    zone_name = None
    if not zone_id:
        zone_id, zone_name = _resolve_route53_zone(client, domain)
    if not zone_id:
        raise SystemExit(
            "route53 hosted zone을 찾지 못했습니다 — --zone-id 로 직접 지정하세요."
        )

    results = []
    for rec in records:
        change = build_route53_change(rec)
        rrset = change["ResourceRecordSet"]
        desired_value = rrset["ResourceRecords"][0]["Value"]

        # 안전장치: zone 이 레코드 host 의 DNS 접미사가 아니면 쓰지 않는다(cross-zone 방지).
        # zone_name 을 아는 경우(추론된 zone)만 검사 — --zone-id 직접 지정은 사용자 책임.
        if zone_name is not None and not _is_dns_suffix(zone_name, rrset["Name"]):
            results.append(
                (rec, "skipped", f"zone {zone_name} 이 host {rrset['Name']} 의 접미사가 아님")
            )
            continue

        # 기존 RRSet 의 전체 값 목록을 가져온다(name+type 당 route53 RRSet 은 1개).
        # 읽기 실패(권한 누락/throttling/transient)는 절대 UPSERT 로 이어지면 안 된다 —
        # 빈 set 으로 오인하면 멀티값 RRSet 을 LoftBox 값만으로 교체해 고객 레코드를
        # 삭제한다. 따라서 fail-closed: 이 레코드를 error 로 표시하고 건너뛴다.
        try:
            existing_values = _route53_existing_values(
                client, zone_id, rrset["Name"], rrset["Type"]
            )
        except Exception as exc:  # noqa: BLE001 - 읽기 실패는 쓰기 금지
            results.append((
                rec,
                "error",
                f"기존 RRSet 을 읽지 못해 건너뜀(덮어쓰기 방지) {rrset['Name']}/{rrset['Type']} "
                f"— ListResourceRecordSets 권한 확인 필요: {exc}",
            ))
            continue

        if rrset["Type"] in ("TXT", "MX"):
            # 멀티값 타입: route53 UPSERT 는 RRSet 전체를 교체하므로, LoftBox 값만
            # 넣으면 고객의 다른 TXT(SPF/DKIM 등)/MX 가 삭제된다. 따라서 항상 기존
            # 값을 보존하고 우리 값을 ADD 한다(merge). --overwrite 는 멀티값 타입에서
            # "다른 레코드 삭제"를 의미하지 않는다 — 무관한 값을 절대 지우지 않는다.
            if desired_value in existing_values:
                results.append((rec, "skipped", "이미 동일한 값(set에 존재)"))
                continue
            merged = existing_values + [desired_value]
            rrset["ResourceRecords"] = [{"Value": v} for v in merged]
            detail = f"UPSERT {rrset['Type']} {rrset['Name']} (merge, {len(merged)}개 값)"
        else:
            # 단일값 타입(CNAME): name 당 값 1개. 다르면 overwrite 여부에 따라 교체/건너뜀.
            if existing_values:
                if desired_value in existing_values:
                    results.append((rec, "skipped", "이미 동일한 값"))
                    continue
                if not overwrite:
                    results.append(
                        (rec, "conflict", f"기존 값과 다름(--overwrite 없이 건너뜀): {existing_values[0]}")
                    )
                    continue
            detail = f"UPSERT {rrset['Type']} {rrset['Name']}"

        try:
            client.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={"Changes": [change]},
            )
            results.append((rec, "applied", detail))
        except Exception as exc:  # noqa: BLE001 - provider 오류는 레코드 단위 보고
            results.append((rec, "error", str(exc)))
    return results


def _zone_candidates(base):
    """base 도메인에서 most-specific → registrable 순으로 후보 zone 이름을 만든다.

    예: `mail.acme.com` → [`mail.acme.com`, `acme.com`]. bare TLD(`com`)까지는
    내려가지 않는다(>=2 레이블에서 멈춤).
    """
    labels = _normalize_domain(base).split(".")
    candidates = []
    # 레이블이 2개 이상 남는 동안만(공개 접미사/ bare TLD 직전에서 멈춤).
    for start in range(0, len(labels) - 1):
        candidates.append(".".join(labels[start:]))
    return candidates


def _resolve_route53_zone(client, domain):
    """레코드가 서브도메인이어도 정확한 public hosted zone 을 찾는다.

    route53 list_hosted_zones_by_name 는 요청 이름에서 사전순으로 나열하므로
    부모 zone(`acme.com.`)이 서브도메인(`_loftbox.acme.com`)보다 앞에 정렬되면
    반환되지 않는다. 따라서 most-specific → registrable 후보 접미사를 만들어
    각 후보 이름으로 EXACT 매칭을 조회한다.

    split-horizon 주의: 한 계정에 같은 이름의 private/public hosted zone 이 모두
    있을 수 있다. private 이 사전순 앞에 정렬되면 첫 zone 만 보는 코드는 public
    을 놓친다. 따라서 각 후보에서 EXACT 이름 매칭 전체를 스캔해 PrivateZone==False
    인 첫 zone 을 고른다.

    반환: (zone_id, zone_fqdn) 또는 (None, None).
    """
    if not domain:
        return None, None
    for candidate in _zone_candidates(domain):
        try:
            resp = client.list_hosted_zones_by_name(DNSName=candidate)
        except Exception:  # noqa: BLE001
            continue
        target = candidate + "."
        for z in resp.get("HostedZones", []):
            # 정확한 이름 매칭만(접두사/접미사 무관 zone 제외).
            if z.get("Name", "") != target:
                continue
            # private zone 은 제외 — 같은 이름의 public zone 이 뒤에 있을 수 있다.
            if z.get("Config", {}).get("PrivateZone"):
                continue
            return z.get("Id"), z.get("Name", "")
    return None, None


def _route53_existing_values(client, zone_id, name, rtype):
    """name+type 에 해당하는 기존 RRSet 의 전체 Value 목록을 반환한다.

    route53 는 같은 name+type 을 단일 RRSet(여러 ResourceRecords)로 저장하므로
    멀티값(TXT/MX)을 보존하려면 전체 목록이 필요하다. API 가 성공했지만 해당
    RRSet 이 없으면 [] 반환(genuinely-empty). StartRecordName/Type 으로 조회 후
    정확히 name+type 인 RRSet 만 매칭한다.

    중요: list_resource_record_sets 실패(권한 누락/throttling/transient)는 절대
    [] 로 삼키지 않는다 — 그러면 호출부가 "기존 값 없음"으로 오인해 RRSet 전체를
    LoftBox 값만으로 UPSERT(=교체)하여 고객의 기존 SPF/TXT/백업 MX 를 삭제한다.
    예외는 그대로 전파하여 호출부가 fail-closed(쓰기 건너뜀) 하도록 한다.
    """
    resp = client.list_resource_record_sets(
        HostedZoneId=zone_id,
        StartRecordName=name,
        StartRecordType=rtype,
        MaxItems="1",
    )
    for rrset in resp.get("ResourceRecordSets", []):
        if rrset.get("Name") == name and rrset.get("Type") == rtype:
            return [r.get("Value") for r in rrset.get("ResourceRecords", []) if r.get("Value") is not None]
    return []


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


class CloudflareLookupError(Exception):
    """기존 cloudflare 레코드 조회 실패(non-200) 를 나타낸다 — fail-closed 신호."""


def _cf_existing_records(token, zone_id, rtype, name):
    """name+type 에 매칭되는 cloudflare 레코드 전체 목록을 반환한다.

    cloudflare 는 각 TXT/MX 를 별개 레코드로 저장하므로(첫 번째만 보면 무관한
    레코드를 오인/변형할 수 있음) 매칭 전체를 가져온다. 200-with-empty-result 면
    [] 반환(genuinely-empty — 정당한 create 케이스).

    중요: 조회 실패(권한 누락/throttling/transient 5xx 등 non-200)는 절대 [] 로
    삼키지 않는다 — 그러면 호출부가 "기존 레코드 없음"으로 오인해 중복 TXT/MX 를
    POST 하거나 잘못된 conflict 판단을 한다(route53 path 와 동일한 위험).
    따라서 non-200 은 CloudflareLookupError 를 던져 호출부가 fail-closed(쓰기
    건너뜀) 하도록 한다.
    """
    q = urllib.parse.urlencode({"type": rtype, "name": name})
    code, payload = _cf_req("GET", f"/zones/{zone_id}/dns_records?{q}", token)
    if code == 200 and isinstance(payload.get("result"), list):
        return payload["result"]
    errors = payload.get("errors") if isinstance(payload, dict) else None
    raise CloudflareLookupError(f"HTTP {code}: {errors}")


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

        # 기존 레코드 조회 실패(권한 누락/throttling/transient)는 절대 POST/PUT 으로
        # 이어지면 안 된다 — 빈 set 으로 오인하면 중복 TXT/MX 를 생성하거나 잘못된
        # conflict 판단을 한다. 따라서 fail-closed: 이 레코드를 error 로 표시하고
        # 어떤 쓰기보다 먼저 건너뛴다(route53 path 와 동일 구조).
        try:
            existing = _cf_existing_records(
                token, zone_id, payload["type"], payload["name"]
            )
        except CloudflareLookupError as exc:
            results.append((
                rec,
                "error",
                f"기존 cloudflare 레코드를 읽지 못해 건너뜀(중복/덮어쓰기 방지) "
                f"{payload['name']}/{payload['type']} — API 토큰 권한 확인 / 재시도 필요: {exc}",
            ))
            continue

        if payload["type"] in ("TXT", "MX"):
            # 멀티값 타입: cloudflare 는 각 값을 별개 레코드로 저장한다. 우리 값과
            # 동일한 레코드가 이미 있으면 skip, 없으면 새 레코드를 POST 로 ADD 한다.
            # 무관한 기존 레코드(고객 SPF/DKIM/백업 MX 등)는 절대 PUT 으로 변형하지
            # 않는다. --overwrite 는 멀티값 추가에서 무의미하다(우리는 add 만 한다).
            if any(_cf_same(e, payload) for e in existing):
                results.append((rec, "skipped", "이미 동일한 값(레코드 존재)"))
                continue
            code, resp = _cf_req("POST", f"/zones/{zone_id}/dns_records", token, payload)
            applied_detail = desc + " (created)"
        else:
            # 단일값 타입(CNAME): name 당 유효 레코드 1개. 동일하면 skip, 다르면
            # overwrite 여부에 따라 그 레코드를 PUT 으로 교체하거나 건너뛴다.
            match = existing[0] if existing else None
            if match is not None:
                if _cf_same(match, payload):
                    results.append((rec, "skipped", "이미 동일한 값"))
                    continue
                if not overwrite:
                    results.append(
                        (rec, "conflict", f"기존 값과 다름(--overwrite 없이 건너뜀): {match.get('content')}")
                    )
                    continue
                code, resp = _cf_req(
                    "PUT", f"/zones/{zone_id}/dns_records/{match['id']}", token, payload
                )
                applied_detail = desc
            else:
                code, resp = _cf_req("POST", f"/zones/{zone_id}/dns_records", token, payload)
                applied_detail = desc

        if code in (200, 201) and resp.get("success", True):
            results.append((rec, "applied", applied_detail))
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


# ---------------------------------------------------------------------------
# delete: 도메인 해지(soft-delete) — --yes 머신 게이트로 보호.
# ---------------------------------------------------------------------------


def _domain_name_for(domain_id):
    """도메인 id 로 도메인명을 싸게 조회(실패해도 무시 — 대상 표시는 best-effort)."""
    qid = urllib.parse.quote(domain_id, safe="")
    code, body = _req("GET", f"/v1/domains/{qid}/status")
    if code != 200:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload.get("domain") or payload.get("name")


def delete_domain(domain_id, yes):
    """도메인 soft-delete. --yes 없으면 대상만 표시하고 DELETE 를 발행하지 않는다.

    머신 게이트: 산문 가드에 더해 CLI 자체가 실수/주입에 한 겹 방어한다.
    """
    name = _domain_name_for(domain_id)
    target = domain_id if not name else f"{domain_id} ({name})"
    if not yes:
        print(f"대상 도메인: {target}")
        print("이것은 파괴적입니다(soft-delete). 확인 후 --yes 로 재실행하여 영구 soft-delete 하세요.")
        print("주의: 도메인을 삭제하기 전에 그 도메인에 바인딩된 메일박스(들)를 먼저 삭제하세요"
              " (register-loftbox-mail-agent offboarding 참고).")
        return
    qid = urllib.parse.quote(domain_id, safe="")
    code, body = _req("DELETE", f"/v1/domains/{qid}")
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
    dl = sub.add_parser("delete", help="도메인 soft-delete (해지) — --yes 필요")
    dl.add_argument("--yes", action="store_true",
                    help="머신 게이트: 이 플래그가 있을 때만 실제 DELETE 발행")
    dl.add_argument("id", help="도메인 id (dom_…)")
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
    elif args.cmd == "delete":
        delete_domain(args.id, args.yes)


if __name__ == "__main__":
    main()
