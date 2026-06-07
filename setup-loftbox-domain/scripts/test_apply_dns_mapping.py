#!/usr/bin/env python3
"""apply-dns 레코드 매핑 단위 테스트 (의존성 없음, python3 직접 실행).

route53: TXT 만 리터럴 큰따옴표로 감싼다. MX 는 'priority host' 그대로.
cloudflare: TXT 는 따옴표 불요. MX 는 priority/content 분리.

실행: python3 setup-loftbox-domain/scripts/test_apply_dns_mapping.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import setup_loftbox_domain as m  # noqa: E402


SAMPLE_NEXT_ACTIONS = [
    {"purpose": "ownership", "type": "TXT", "host": "_loftbox.acme.com",
     "value": "loftbox-verification=abc123"},
    {"purpose": "inbound", "type": "MX", "host": "acme.com",
     "value": "10 mail.loftbox.net"},
    {"purpose": "return-path", "type": "CNAME", "host": "bounce.acme.com",
     "value": "rp.loftbox.net"},
]


def _norm(actions):
    out = []
    for a in actions:
        out.append({
            "purpose": a["purpose"],
            "type": a["type"].upper(),
            "host": a["host"],
            "value": a["value"],
        })
    return out


def test_route53_txt_quoted():
    records = _norm(SAMPLE_NEXT_ACTIONS)
    txt = m.build_route53_change(records[0])
    val = txt["ResourceRecordSet"]["ResourceRecords"][0]["Value"]
    assert val == '"loftbox-verification=abc123"', val
    assert txt["Action"] == "UPSERT"
    assert txt["ResourceRecordSet"]["Type"] == "TXT"
    assert txt["ResourceRecordSet"]["Name"] == "_loftbox.acme.com."  # FQDN


def test_route53_mx_asis():
    records = _norm(SAMPLE_NEXT_ACTIONS)
    mx = m.build_route53_change(records[1])
    val = mx["ResourceRecordSet"]["ResourceRecords"][0]["Value"]
    assert val == "10 mail.loftbox.net", val  # priority+host 그대로, 따옴표 없음


def test_route53_cname_asis():
    records = _norm(SAMPLE_NEXT_ACTIONS)
    cn = m.build_route53_change(records[2])
    val = cn["ResourceRecordSet"]["ResourceRecords"][0]["Value"]
    assert val == "rp.loftbox.net", val
    assert '"' not in val


def test_cloudflare_txt_no_quotes():
    records = _norm(SAMPLE_NEXT_ACTIONS)
    txt = m.build_cloudflare_payload(records[0])
    assert txt["content"] == "loftbox-verification=abc123", txt
    assert '"' not in txt["content"]
    assert "priority" not in txt


def test_cloudflare_mx_split():
    records = _norm(SAMPLE_NEXT_ACTIONS)
    mx = m.build_cloudflare_payload(records[1])
    assert mx["type"] == "MX"
    assert mx["content"] == "mail.loftbox.net", mx
    assert mx["priority"] == 10, mx


def test_cloudflare_cname():
    records = _norm(SAMPLE_NEXT_ACTIONS)
    cn = m.build_cloudflare_payload(records[2])
    assert cn["type"] == "CNAME"
    assert cn["content"] == "rp.loftbox.net"
    assert "priority" not in cn


def test_guess_domain():
    records = _norm(SAMPLE_NEXT_ACTIONS)
    assert m._guess_domain(records) == "acme.com", m._guess_domain(records)


def test_route53_txt_chunked_over_255():
    # 255 초과 TXT(예: DKIM 공개키)는 ≤255 청크로 분할되어 여러 따옴표 세그먼트가 된다.
    long_value = "k=rsa;p=" + ("A" * 400)  # 408 chars > 255
    rec = {"purpose": "zeptomail-dkim", "type": "TXT",
           "host": "zmail._domainkey.acme.com", "value": long_value}
    change = m.build_route53_change(rec)
    val = change["ResourceRecordSet"]["ResourceRecords"][0]["Value"]
    # 여러 따옴표 세그먼트로 분할되어야 한다.
    segments = val.split('" "')
    assert len(segments) >= 2, val
    # 따옴표(") 가 정확히 짝수개(각 세그먼트 양끝).
    assert val.count('"') == 2 * (val.count('" "') + 1), val
    # 각 따옴표 안 청크는 ≤255 바이트.
    import re
    inner = re.findall(r'"([^"]*)"', val)
    assert inner, val
    for chunk in inner:
        assert len(chunk.encode("utf-8")) <= 255, len(chunk)
    # 모든 청크를 이으면 원래 값이 복원된다(데이터 손실 없음).
    assert "".join(inner) == long_value


def test_route53_txt_short_single_quoted():
    rec = {"purpose": "ownership", "type": "TXT",
           "host": "_loftbox.acme.com", "value": "x" * 255}
    val = m.build_route53_change(rec)["ResourceRecordSet"]["ResourceRecords"][0]["Value"]
    # 정확히 255 는 단일 따옴표 쌍.
    assert val == '"' + "x" * 255 + '"', val
    assert val.count('"') == 2


class _FakeRoute53Client:
    """list_hosted_zones_by_name 를 사전순으로 흉내내는 가짜 클라이언트.

    실제 route53 처럼 요청 DNSName 이상으로 사전순 정렬된 zone 들을 반환한다.
    부모 zone(acme.com.)은 서브도메인(_loftbox.acme.com)보다 사전순 앞에 온다.
    """

    def __init__(self, zone_names):
        # {name(fqdn): {"Id": ..., "PrivateZone": bool}}
        self.zones = []
        for i, name in enumerate(sorted(zone_names)):
            self.zones.append({
                "Id": f"/hostedzone/Z{i}",
                "Name": name,
                "Config": {"PrivateZone": False},
            })

    def list_hosted_zones_by_name(self, DNSName=None, MaxItems=None):
        # route53: DNSName 부터 사전순으로 나열.
        ordered = sorted(self.zones, key=lambda z: z["Name"])
        start = DNSName if DNSName.endswith(".") else DNSName + "."
        result = [z for z in ordered if z["Name"] >= start]
        if MaxItems is not None:
            result = result[: int(MaxItems)]
        return {"HostedZones": result}


def test_route53_zone_inference_subdomain_walks_to_parent():
    # 레코드 host 가 서브도메인이고 base 가 acme.com 인데, 계정에 acme.com. zone 만 있음.
    # 무관한 evil.com. zone 도 같은 계정에 존재 — 선택되면 안 된다.
    client = _FakeRoute53Client(["acme.com.", "evil.com."])
    zone_id, zone_name = m._resolve_route53_zone(client, "acme.com")
    assert zone_name == "acme.com.", (zone_id, zone_name)
    assert zone_id == "/hostedzone/Z0", zone_id  # acme.com. 정렬 인덱스 0


def test_route53_zone_inference_picks_most_specific():
    # mail.acme.com 와 acme.com 둘 다 zone 으로 존재하면 most-specific(mail.acme.com)을 선택.
    client = _FakeRoute53Client(["acme.com.", "mail.acme.com.", "evil.com."])
    zone_id, zone_name = m._resolve_route53_zone(client, "mail.acme.com")
    assert zone_name == "mail.acme.com.", (zone_id, zone_name)


def test_route53_zone_inference_no_match_returns_none():
    # base 가 acme.com 인데 계정엔 evil.com. zone 만 있음 → 매칭 없음.
    client = _FakeRoute53Client(["evil.com."])
    zone_id, zone_name = m._resolve_route53_zone(client, "acme.com")
    assert zone_id is None and zone_name is None, (zone_id, zone_name)


def test_route53_zone_inference_excludes_private():
    client = _FakeRoute53Client(["acme.com."])
    client.zones[0]["Config"]["PrivateZone"] = True
    zone_id, zone_name = m._resolve_route53_zone(client, "_loftbox.acme.com")
    assert zone_id is None, (zone_id, zone_name)


class _FakeRoute53SplitHorizonClient:
    """같은 이름의 private/public zone 을 함께 반환하는 가짜 클라이언트.

    private zone 이 사전순(또는 반환 순서)으로 먼저 오도록 둔다. exact-name 스캔이
    private 를 건너뛰고 public zone 을 골라야 한다.
    """

    def __init__(self, zones):
        # zones: [{"Id","Name","PrivateZone"}] — 반환 순서 그대로 유지.
        self.zones = [
            {"Id": z["Id"], "Name": z["Name"],
             "Config": {"PrivateZone": z["PrivateZone"]}}
            for z in zones
        ]

    def list_hosted_zones_by_name(self, DNSName=None, MaxItems=None):
        start = DNSName if DNSName.endswith(".") else DNSName + "."
        result = [z for z in self.zones if z["Name"] >= start]
        if MaxItems is not None:
            result = result[: int(MaxItems)]
        return {"HostedZones": result}


def test_route53_zone_inference_split_horizon_picks_public():
    # 같은 이름의 private(먼저) + public zone → public 의 id 를 골라야 한다.
    client = _FakeRoute53SplitHorizonClient([
        {"Id": "/hostedzone/ZPRIVATE", "Name": "acme.com.", "PrivateZone": True},
        {"Id": "/hostedzone/ZPUBLIC", "Name": "acme.com.", "PrivateZone": False},
    ])
    zone_id, zone_name = m._resolve_route53_zone(client, "_loftbox.acme.com")
    assert zone_id == "/hostedzone/ZPUBLIC", (zone_id, zone_name)
    assert zone_name == "acme.com.", (zone_id, zone_name)


def test_route53_zone_inference_all_private_returns_none():
    # 같은 이름이 전부 private 면 매칭 없음(None).
    client = _FakeRoute53SplitHorizonClient([
        {"Id": "/hostedzone/ZPRIVATE", "Name": "acme.com.", "PrivateZone": True},
    ])
    zone_id, zone_name = m._resolve_route53_zone(client, "_loftbox.acme.com")
    assert zone_id is None and zone_name is None, (zone_id, zone_name)


def test_zone_candidates_walk():
    assert m._zone_candidates("mail.acme.com") == ["mail.acme.com", "acme.com"]
    assert m._zone_candidates("acme.com") == ["acme.com"]
    # bare TLD 까지 내려가지 않음.
    assert "com" not in m._zone_candidates("a.b.acme.com")


def test_is_dns_suffix_guard():
    assert m._is_dns_suffix("acme.com.", "_loftbox.acme.com.")
    assert m._is_dns_suffix("acme.com.", "acme.com.")
    assert not m._is_dns_suffix("evil.com.", "acme.com.")
    # 부분 라벨 매칭은 접미사가 아님.
    assert not m._is_dns_suffix("me.com.", "acme.com.")


# ---------------------------------------------------------------------------
# Fix 1 (route53): 멀티값 RRSet 보존/merge — 고객 TXT/MX 를 절대 삭제하지 않음.
# ---------------------------------------------------------------------------


class _FakeRoute53ApplyClient:
    """change_resource_record_sets 와 list_resource_record_sets 를 흉내내는 클라이언트.

    rrsets: {(name, type): [values]} — 기존 RRSet 상태.
    change_resource_record_sets 호출은 self.changes 에 기록한다(쓰기 검증용).
    """

    def __init__(self, rrsets=None):
        # {(name, type): [Value, ...]}
        self.rrsets = dict(rrsets or {})
        self.changes = []

    def list_resource_record_sets(self, HostedZoneId=None, StartRecordName=None,
                                  StartRecordType=None, MaxItems=None):
        values = self.rrsets.get((StartRecordName, StartRecordType))
        if values is None:
            return {"ResourceRecordSets": []}
        return {"ResourceRecordSets": [{
            "Name": StartRecordName,
            "Type": StartRecordType,
            "ResourceRecords": [{"Value": v} for v in values],
        }]}

    def change_resource_record_sets(self, HostedZoneId=None, ChangeBatch=None):
        self.changes.append(ChangeBatch)
        return {"ChangeInfo": {"Id": "/change/C1", "Status": "PENDING"}}


def _upserted_values(client):
    """기록된 change 들 중 마지막 UPSERT 의 Value 목록을 반환."""
    last = client.changes[-1]["Changes"][0]
    return [r["Value"] for r in last["ResourceRecordSet"]["ResourceRecords"]]


def _run_route53_with_client(client, records, domain="acme.com",
                             zone_id="/hostedzone/Z0", overwrite=False):
    """가짜 route53 client 를 boto3.client 로 주입해 _apply_route53 실행."""
    import sys as _sys
    import types as _types
    backup = _sys.modules.get("boto3")
    _sys.modules["boto3"] = _types.SimpleNamespace(client=lambda *_a, **_k: client)
    try:
        return m._apply_route53(records, domain, zone_id, dry_run=False,
                                overwrite=overwrite)
    finally:
        if backup is not None:
            _sys.modules["boto3"] = backup
        else:
            _sys.modules.pop("boto3", None)


def test_route53_txt_merge_preserves_existing():
    # apex 에 고객의 기존 SPF TXT 가 있고, LoftBox TXT 를 추가 → UPSERT 에 둘 다 포함.
    client = _FakeRoute53ApplyClient(
        {("acme.com.", "TXT"): ['"v=spf1 include:other -all"']}
    )
    rec = {"purpose": "ownership", "type": "TXT", "host": "acme.com",
           "value": "loftbox-verification=abc123"}
    results = _run_route53_with_client(client, [rec])
    assert results[0][1] == "applied", results
    vals = _upserted_values(client)
    # 기존 SPF 와 LoftBox 값이 모두 있어야 한다(merge — 고객 값 삭제 금지).
    assert '"v=spf1 include:other -all"' in vals, vals
    assert '"loftbox-verification=abc123"' in vals, vals
    assert len(vals) == 2, vals


def test_route53_txt_skip_when_already_present():
    # LoftBox 값이 이미 set 에 있으면 skip — 쓰기 없음.
    client = _FakeRoute53ApplyClient(
        {("acme.com.", "TXT"): ['"v=spf1 -all"', '"loftbox-verification=abc123"']}
    )
    rec = {"purpose": "ownership", "type": "TXT", "host": "acme.com",
           "value": "loftbox-verification=abc123"}
    results = _run_route53_with_client(client, [rec])
    assert results[0][1] == "skipped", results
    assert client.changes == [], client.changes  # 쓰기 없음


def test_route53_mx_merge_preserves_existing():
    # 기존 백업 MX 가 있고 LoftBox MX 추가 → merge 된 set 에 둘 다.
    client = _FakeRoute53ApplyClient(
        {("acme.com.", "MX"): ["20 backup.mail"]}
    )
    rec = {"purpose": "inbound", "type": "MX", "host": "acme.com",
           "value": "10 mail.loftbox.net"}
    results = _run_route53_with_client(client, [rec])
    assert results[0][1] == "applied", results
    vals = _upserted_values(client)
    assert "20 backup.mail" in vals, vals
    assert "10 mail.loftbox.net" in vals, vals
    assert len(vals) == 2, vals


def test_route53_txt_create_when_no_existing():
    # 기존 RRSet 이 없으면 우리 값 하나로 생성.
    client = _FakeRoute53ApplyClient({})
    rec = {"purpose": "ownership", "type": "TXT", "host": "_loftbox.acme.com",
           "value": "loftbox-verification=abc123"}
    results = _run_route53_with_client(client, [rec])
    assert results[0][1] == "applied", results
    vals = _upserted_values(client)
    assert vals == ['"loftbox-verification=abc123"'], vals


def test_route53_cname_skip_same():
    client = _FakeRoute53ApplyClient(
        {("bounce.acme.com.", "CNAME"): ["rp.loftbox.net"]}
    )
    rec = {"purpose": "return-path", "type": "CNAME", "host": "bounce.acme.com",
           "value": "rp.loftbox.net"}
    results = _run_route53_with_client(client, [rec])
    assert results[0][1] == "skipped", results
    assert client.changes == [], client.changes


def test_route53_cname_conflict_without_overwrite():
    client = _FakeRoute53ApplyClient(
        {("bounce.acme.com.", "CNAME"): ["old.example.net"]}
    )
    rec = {"purpose": "return-path", "type": "CNAME", "host": "bounce.acme.com",
           "value": "rp.loftbox.net"}
    results = _run_route53_with_client(client, [rec], overwrite=False)
    assert results[0][1] == "conflict", results
    assert client.changes == [], client.changes


def test_route53_cname_overwrite_diff():
    client = _FakeRoute53ApplyClient(
        {("bounce.acme.com.", "CNAME"): ["old.example.net"]}
    )
    rec = {"purpose": "return-path", "type": "CNAME", "host": "bounce.acme.com",
           "value": "rp.loftbox.net"}
    results = _run_route53_with_client(client, [rec], overwrite=True)
    assert results[0][1] == "applied", results
    vals = _upserted_values(client)
    assert vals == ["rp.loftbox.net"], vals


class _FakeRoute53ReadFailClient:
    """list_resource_record_sets 가 항상 예외를 던지는 클라이언트.

    change_resource_record_sets 호출은 self.changes 에 기록한다 — fail-closed 면
    한 번도 호출되지 않아야 한다.
    """

    def __init__(self):
        self.changes = []

    def list_resource_record_sets(self, **_kwargs):
        raise RuntimeError("AccessDenied: ListResourceRecordSets")

    def change_resource_record_sets(self, HostedZoneId=None, ChangeBatch=None):
        self.changes.append(ChangeBatch)
        return {"ChangeInfo": {"Id": "/change/C1", "Status": "PENDING"}}


def test_route53_read_failure_fails_closed_no_upsert():
    # 기존 RRSet 읽기가 실패하면 UPSERT 를 절대 하지 않는다(고객 레코드 삭제 방지).
    client = _FakeRoute53ReadFailClient()
    rec = {"purpose": "ownership", "type": "TXT", "host": "acme.com",
           "value": "loftbox-verification=abc123"}
    results = _run_route53_with_client(client, [rec])
    # 레코드는 error 로 표시되고 쓰기는 없어야 한다.
    assert results[0][1] == "error", results
    assert client.changes == [], client.changes  # UPSERT 없음 — fail-closed
    err_count = sum(1 for _r, s, _d in results if s == "error")
    assert err_count > 0, results


# ---------------------------------------------------------------------------
# Fix 2 (cloudflare): 모든 매칭 레코드 확인 — 무관한 레코드 변형 없이 새 레코드 추가.
# ---------------------------------------------------------------------------


class _FakeCfState:
    """_cf_req 를 흉내내는 가짜 상태. 같은 name+type 에 여러 레코드를 둔다.

    records: [{"id","type","name","content","priority"?}]
    POST/PUT/DELETE 호출을 self.calls 에 기록한다.
    """

    def __init__(self, records=None):
        self.records = list(records or [])
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/dns_records?" in path:
            q = path.split("?", 1)[1]
            import urllib.parse as up
            params = dict(up.parse_qsl(q))
            matched = [
                r for r in self.records
                if r.get("type") == params.get("type") and r.get("name") == params.get("name")
            ]
            return 200, {"success": True, "result": matched}
        if method == "POST":
            new = dict(body or {})
            new["id"] = f"rec{len(self.records)}"
            self.records.append(new)
            return 200, {"success": True, "result": new}
        if method == "PUT":
            rid = path.rsplit("/", 1)[-1]
            for r in self.records:
                if r.get("id") == rid:
                    r.update(body or {})
                    return 200, {"success": True, "result": r}
            return 404, {"success": False, "errors": ["not found"]}
        return 200, {"success": True, "result": {}}


def _run_cloudflare_with_state(state, records, overwrite=False):
    backup = m._cf_req
    m._cf_req = state
    backup_env = os.environ.get("CLOUDFLARE_API_TOKEN")
    os.environ["CLOUDFLARE_API_TOKEN"] = "test-token"
    try:
        return m._apply_cloudflare(records, "acme.com", "zone123",
                                   dry_run=False, overwrite=overwrite)
    finally:
        m._cf_req = backup
        if backup_env is None:
            os.environ.pop("CLOUDFLARE_API_TOKEN", None)
        else:
            os.environ["CLOUDFLARE_API_TOKEN"] = backup_env


def test_cloudflare_txt_adds_new_record_not_mutating_existing():
    # apex 에 무관한 TXT 2개(둘 다 desired 아님) → 새 레코드 POST, 기존 변형 없음.
    state = _FakeCfState([
        {"id": "rec0", "type": "TXT", "name": "acme.com", "content": "v=spf1 -all"},
        {"id": "rec1", "type": "TXT", "name": "acme.com", "content": "google-site-verification=xyz"},
    ])
    rec = {"purpose": "ownership", "type": "TXT", "host": "acme.com",
           "value": "loftbox-verification=abc123"}
    results = _run_cloudflare_with_state(state, [rec])
    assert results[0][1] == "applied", results
    # PUT 이 없어야 한다(무관 레코드 변형 금지). POST 만.
    methods = [c[0] for c in state.calls]
    assert "PUT" not in methods, state.calls
    assert "POST" in methods, state.calls
    # 기존 두 레코드 content 가 그대로 보존되어야 한다.
    contents = sorted(r["content"] for r in state.records)
    assert "v=spf1 -all" in contents
    assert "google-site-verification=xyz" in contents
    assert "loftbox-verification=abc123" in contents
    assert len(state.records) == 3, state.records


def test_cloudflare_txt_skip_when_one_matches():
    # 기존 레코드 중 하나가 desired 와 동일 → skip, 쓰기 없음.
    state = _FakeCfState([
        {"id": "rec0", "type": "TXT", "name": "acme.com", "content": "v=spf1 -all"},
        {"id": "rec1", "type": "TXT", "name": "acme.com", "content": "loftbox-verification=abc123"},
    ])
    rec = {"purpose": "ownership", "type": "TXT", "host": "acme.com",
           "value": "loftbox-verification=abc123"}
    results = _run_cloudflare_with_state(state, [rec])
    assert results[0][1] == "skipped", results
    methods = [c[0] for c in state.calls]
    assert "POST" not in methods and "PUT" not in methods, state.calls


def test_cloudflare_mx_adds_new_preserving_backup():
    state = _FakeCfState([
        {"id": "rec0", "type": "MX", "name": "acme.com", "content": "backup.mail", "priority": 20},
    ])
    rec = {"purpose": "inbound", "type": "MX", "host": "acme.com",
           "value": "10 mail.loftbox.net"}
    results = _run_cloudflare_with_state(state, [rec])
    assert results[0][1] == "applied", results
    methods = [c[0] for c in state.calls]
    assert "PUT" not in methods, state.calls
    prio = sorted(r["priority"] for r in state.records)
    assert prio == [10, 20], state.records


def test_cloudflare_cname_skip_same():
    state = _FakeCfState([
        {"id": "rec0", "type": "CNAME", "name": "bounce.acme.com", "content": "rp.loftbox.net"},
    ])
    rec = {"purpose": "return-path", "type": "CNAME", "host": "bounce.acme.com",
           "value": "rp.loftbox.net"}
    results = _run_cloudflare_with_state(state, [rec])
    assert results[0][1] == "skipped", results
    methods = [c[0] for c in state.calls]
    assert "POST" not in methods and "PUT" not in methods, state.calls


def test_cloudflare_cname_overwrite_diff_puts_same_record():
    state = _FakeCfState([
        {"id": "rec0", "type": "CNAME", "name": "bounce.acme.com", "content": "old.example.net"},
    ])
    rec = {"purpose": "return-path", "type": "CNAME", "host": "bounce.acme.com",
           "value": "rp.loftbox.net"}
    results = _run_cloudflare_with_state(state, [rec], overwrite=True)
    assert results[0][1] == "applied", results
    methods = [c[0] for c in state.calls]
    assert "PUT" in methods, state.calls
    # PUT 은 동일한 기존 레코드(rec0)를 교체해야 한다(새 레코드 생성 아님).
    assert len(state.records) == 1, state.records
    assert state.records[0]["content"] == "rp.loftbox.net", state.records


def test_cloudflare_cname_conflict_without_overwrite():
    state = _FakeCfState([
        {"id": "rec0", "type": "CNAME", "name": "bounce.acme.com", "content": "old.example.net"},
    ])
    rec = {"purpose": "return-path", "type": "CNAME", "host": "bounce.acme.com",
           "value": "rp.loftbox.net"}
    results = _run_cloudflare_with_state(state, [rec], overwrite=False)
    assert results[0][1] == "conflict", results
    methods = [c[0] for c in state.calls]
    assert "POST" not in methods and "PUT" not in methods, state.calls


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")


if __name__ == "__main__":
    main()
