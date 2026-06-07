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
