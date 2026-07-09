#!/usr/bin/env python3
"""No-network tests for check_loftbox_mail agent receipt behavior."""
import importlib.util
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_PATH = os.path.join(HERE, "..", "check-loftbox-mail", "scripts", "check_loftbox_mail.py")

spec = importlib.util.spec_from_file_location("check_loftbox_mail", MOD_PATH)
clm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clm)


def parse(argv):
    return clm.build_parser().parse_args(argv)


def run_main(argv, fake_http):
    old_argv = sys.argv[:]
    old_http = clm.http_text
    old_env = {k: os.environ.get(k) for k in ("LOFTBOX_API_KEY", "LOFTBOX_MAILBOX_ID", "LOFTBOX_MAILBOX_IDS")}
    try:
        os.environ["LOFTBOX_API_KEY"] = "test_key"
        os.environ.pop("LOFTBOX_MAILBOX_ID", None)
        os.environ.pop("LOFTBOX_MAILBOX_IDS", None)
        sys.argv = ["check_loftbox_mail.py"] + argv
        clm.http_text = fake_http
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            clm.main()
    finally:
        sys.argv = old_argv
        clm.http_text = old_http
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def expect_exit(fn):
    try:
        fn()
    except SystemExit:
        return True
    return False


def run():
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)

    payload = clm.build_receipt_payload(
        parse(
            [
                "ack",
                "--message-id",
                "msg_1",
                "--receipt-status",
                "FAILED",
                "--receipt-error",
                "parse failed",
                "--action-required",
            ]
        )
    )
    check("payload normalizes status", payload["status"] == "failed")
    check("payload includes error", payload["error"] == "parse failed")
    check("payload includes action_required", payload["action_required"] is True)

    calls = []

    def ok_http(method, url, api_key, payload=None):
        calls.append((method, url, api_key, payload))
        return json.dumps({"ok": True})

    run_main(["--mailbox-id", "mb_a", "ack", "--message-id", "msg_1", "--message-id", "msg_2"], ok_http)
    check("three calls", len(calls) == 3)
    check("receipt first", calls[0][1].endswith("/v1/messages/msg_1/agent-receipts"))
    check("receipt second", calls[1][1].endswith("/v1/messages/msg_2/agent-receipts"))
    check("ack last", calls[2][1].endswith("/v1/mailboxes/mb_a/inbox/ack"))
    check("default processed", calls[0][3] == {"status": "processed"})
    check("ack ids", calls[2][3] == {"message_ids": ["msg_1", "msg_2"]})

    failed_calls = []

    def failing_receipt(method, url, api_key, payload=None):
        failed_calls.append((method, url, payload))
        if url.endswith("/agent-receipts"):
            raise clm.RequestError(500, "receipt failed")
        return json.dumps({"ok": True})

    check(
        "receipt failure exits",
        expect_exit(lambda: run_main(["--mailbox-id", "mb_a", "ack", "--message-id", "msg_1"], failing_receipt)),
    )
    check("ack skipped on receipt failure", all("/inbox/ack" not in call[1] for call in failed_calls))

    if failures:
        print("FAIL:", ", ".join(failures))
        sys.exit(1)
    print("ok: all receipt tests passed")


if __name__ == "__main__":
    run()
