#!/usr/bin/env python3
"""해지(offboarding) --yes 머신 게이트 단위 테스트 (의존성 없음, python3 직접 실행).

각 delete 명령(domain `delete`, `delete-mailbox`, `delete-agent`)에 대해:
  - --yes 없이 호출하면 DELETE 를 절대 발행하지 않는다(HTTP 함수 모킹, 호출 0회).
  - --yes 와 함께 호출하면 정확히 그 id 로 DELETE 1회 발행한다.
또한 list-mailboxes 가 GET /v1/agents/{id}/mailboxes 경로를 구성하는지 단언한다.

실행: python3 tests/test_offboard_gate.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "setup-loftbox-domain", "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "register-loftbox-mail-agent", "scripts"))

import setup_loftbox_domain as dom  # noqa: E402
import offboard_loftbox_agent as off  # noqa: E402


class _Recorder:
    """_req 를 대체해 (method, path, body) 를 기록한다. 실제 네트워크 없음.

    responses: {(method, path_prefix_or_None): (code, body)} 매칭 우선,
    없으면 default 반환. GET status 류는 default 로 처리.
    """

    def __init__(self, default=(200, "{}")):
        self.calls = []
        self.default = default
        self.responses = {}

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        for (m, pfx), resp in self.responses.items():
            if method == m and (pfx is None or path.startswith(pfx)):
                return resp
        return self.default

    def deletes(self):
        return [c for c in self.calls if c[0] == "DELETE"]


def _patch_dom(recorder):
    dom._req = recorder


def _patch_off(recorder):
    off._req = recorder


def _restore():
    # 각 테스트 후 원래 _req 복구는 모듈 재할당으로 충분치 않으니
    # 테스트마다 새 recorder 를 붙이고, 마지막에 명시 복구한다.
    pass


# --- domain delete ----------------------------------------------------------


def test_domain_delete_without_yes_no_delete():
    rec = _Recorder()
    _patch_dom(rec)
    try:
        dom.delete_domain("dom_abc", yes=False)
    finally:
        pass
    assert rec.deletes() == [], rec.calls
    # 대상 표시를 위해 status GET 은 허용되지만 DELETE 는 없어야 한다.
    assert all(c[0] != "DELETE" for c in rec.calls), rec.calls


def test_domain_delete_with_yes_one_delete_correct_path():
    rec = _Recorder()
    _patch_dom(rec)
    dom.delete_domain("dom_abc", yes=True)
    dels = rec.deletes()
    assert len(dels) == 1, dels
    method, path, body = dels[0]
    assert path == "/v1/domains/dom_abc", path
    assert body is None, body


# --- mailbox delete ---------------------------------------------------------


def test_delete_mailbox_without_yes_no_delete():
    rec = _Recorder()
    _patch_off(rec)
    off.delete_mailbox("mbx_123", yes=False)
    assert rec.deletes() == [], rec.calls


def test_delete_mailbox_with_yes_one_delete_correct_path():
    rec = _Recorder()
    _patch_off(rec)
    off.delete_mailbox("mbx_123", yes=True)
    dels = rec.deletes()
    assert len(dels) == 1, dels
    assert dels[0][1] == "/v1/mailboxes/mbx_123", dels[0]


# --- agent delete -----------------------------------------------------------


def test_delete_agent_without_yes_no_delete():
    rec = _Recorder()
    _patch_off(rec)
    off.delete_agent("agt_999", yes=False)
    assert rec.deletes() == [], rec.calls


def test_delete_agent_with_yes_one_delete_correct_path():
    rec = _Recorder()
    _patch_off(rec)
    off.delete_agent("agt_999", yes=True)
    dels = rec.deletes()
    assert len(dels) == 1, dels
    assert dels[0][1] == "/v1/agents/agt_999", dels[0]


# --- list-mailboxes path ----------------------------------------------------


def test_list_mailboxes_builds_get_path():
    rec = _Recorder(default=(200, '{"data": []}'))
    _patch_off(rec)
    off.list_mailboxes("agt_999")
    gets = [c for c in rec.calls if c[0] == "GET"]
    assert len(gets) == 1, gets
    assert gets[0][1] == "/v1/agents/agt_999/mailboxes", gets[0]
    assert rec.deletes() == [], rec.calls


# --- 404 handling (존재하지 않는 id) ----------------------------------------


def test_delete_mailbox_404_clean():
    rec = _Recorder(default=(404, "not found"))
    _patch_off(rec)
    # 404 는 깨끗하게 처리되어야 한다(SystemExit 없음).
    off.delete_mailbox("mbx_missing", yes=True)
    dels = rec.deletes()
    assert len(dels) == 1, dels


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
        except SystemExit as e:
            failed += 1
            print(f"FAIL {t.__name__}: unexpected SystemExit({e})")
    if failed:
        print(f"\n{failed} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")


if __name__ == "__main__":
    main()
