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
