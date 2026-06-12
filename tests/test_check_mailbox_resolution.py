#!/usr/bin/env python3
"""Standalone tests for check_loftbox_mail mailbox-id resolution.

Run: python3 tests/test_check_mailbox_resolution.py
No network. Covers backward compat (single LOFTBOX_MAILBOX_ID) and the new
multi-mailbox (LOFTBOX_MAILBOX_IDS) resolution + fail-closed ack routing."""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_PATH = os.path.join(HERE, "..", "check-loftbox-mail", "scripts", "check_loftbox_mail.py")

spec = importlib.util.spec_from_file_location("check_loftbox_mail", MOD_PATH)
clm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clm)


def args(mailbox_id=None, mailbox_ids=None):
    a = types.SimpleNamespace()
    a.mailbox_id = mailbox_id
    a.mailbox_ids = mailbox_ids
    return a


def clear_env():
    for k in ("LOFTBOX_MAILBOX_ID", "LOFTBOX_MAILBOX_IDS"):
        os.environ.pop(k, None)


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

    # --- resolve_mailbox_ids ---
    clear_env()
    check("single arg", clm.resolve_mailbox_ids(args(mailbox_id="mb_a")) == ["mb_a"])

    clear_env()
    os.environ["LOFTBOX_MAILBOX_ID"] = "mb_env"
    check("single env (legacy)", clm.resolve_mailbox_ids(args()) == ["mb_env"])

    clear_env()
    check("multi arg", clm.resolve_mailbox_ids(args(mailbox_ids="mb_a, mb_b ,mb_c")) == ["mb_a", "mb_b", "mb_c"])

    clear_env()
    os.environ["LOFTBOX_MAILBOX_IDS"] = "mb_x,mb_y"
    check("multi env", clm.resolve_mailbox_ids(args()) == ["mb_x", "mb_y"])

    # IDS precedence over single ID
    clear_env()
    os.environ["LOFTBOX_MAILBOX_ID"] = "mb_single"
    os.environ["LOFTBOX_MAILBOX_IDS"] = "mb_1,mb_2"
    check("ids over id", clm.resolve_mailbox_ids(args()) == ["mb_1", "mb_2"])

    clear_env()
    check("none -> exit", expect_exit(lambda: clm.resolve_mailbox_ids(args())))

    clear_env()
    check("empty ids -> exit", expect_exit(lambda: clm.resolve_mailbox_ids(args(mailbox_ids=" , "))))

    # --- resolve_ack_mailbox ---
    clear_env()
    check("ack explicit arg", clm.resolve_ack_mailbox(args(mailbox_id="mb_a")) == "mb_a")

    clear_env()
    os.environ["LOFTBOX_MAILBOX_ID"] = "mb_env"
    check("ack single env (legacy)", clm.resolve_ack_mailbox(args()) == "mb_env")

    clear_env()
    os.environ["LOFTBOX_MAILBOX_IDS"] = "mb_only"
    check("ack single-in-ids", clm.resolve_ack_mailbox(args()) == "mb_only")

    # fail-closed: multiple ids, no explicit target
    clear_env()
    os.environ["LOFTBOX_MAILBOX_IDS"] = "mb_1,mb_2"
    check("ack multi -> exit (fail closed)", expect_exit(lambda: clm.resolve_ack_mailbox(args())))

    # explicit --mailbox-id wins even with multi ids
    clear_env()
    os.environ["LOFTBOX_MAILBOX_IDS"] = "mb_1,mb_2"
    check("ack explicit beats multi", clm.resolve_ack_mailbox(args(mailbox_id="mb_2")) == "mb_2")

    clear_env()
    check("ack none -> exit", expect_exit(lambda: clm.resolve_ack_mailbox(args())))

    clear_env()
    if failures:
        print("FAIL:", ", ".join(failures))
        sys.exit(1)
    print("ok: all mailbox-resolution tests passed")


if __name__ == "__main__":
    run()
