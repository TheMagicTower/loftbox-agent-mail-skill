#!/usr/bin/env python3
"""Standalone tests for check_loftbox_mail mailbox-id resolution.

Run: python3 tests/test_check_mailbox_resolution.py
No network. Drives the REAL argparse parser (build_parser) so the env-default
wiring is exercised the same way production runs it. Covers backward compat
(single LOFTBOX_MAILBOX_ID -> raw output), multi (LOFTBOX_MAILBOX_IDS), the
both-env-set interaction, and fail-closed ack routing."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_PATH = os.path.join(HERE, "..", "check-loftbox-mail", "scripts", "check_loftbox_mail.py")

spec = importlib.util.spec_from_file_location("check_loftbox_mail", MOD_PATH)
clm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clm)


def parse(argv):
    """Parse argv through the production parser (env defaults applied as in main)."""
    return clm.build_parser().parse_args(argv)


def clear_env():
    for k in ("LOFTBOX_MAILBOX_ID", "LOFTBOX_MAILBOX_IDS"):
        os.environ.pop(k, None)


def setenv(**kw):
    clear_env()
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = v


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

    # ---- resolve_mailboxes: precedence + count ----
    setenv()
    check("list: --mailbox-id", clm.resolve_mailboxes(parse(["--mailbox-id", "mb_a", "list"])) == ["mb_a"])

    setenv(LOFTBOX_MAILBOX_ID="mb_env")
    check("list: env single (legacy)", clm.resolve_mailboxes(parse(["list"])) == ["mb_env"])

    setenv()
    check("list: --mailbox-ids", clm.resolve_mailboxes(parse(["--mailbox-ids", "mb_a, mb_b ,mb_c", "list"])) == ["mb_a", "mb_b", "mb_c"])

    setenv(LOFTBOX_MAILBOX_IDS="mb_x,mb_y")
    check("list: env ids", clm.resolve_mailboxes(parse(["list"])) == ["mb_x", "mb_y"])

    # CRITICAL backward-compat: explicit --mailbox-id wins over a stray env IDS
    setenv(LOFTBOX_MAILBOX_IDS="mb_x,mb_y")
    check("list: explicit --mailbox-id beats env ids", clm.resolve_mailboxes(parse(["--mailbox-id", "mb_real", "list"])) == ["mb_real"])

    # both env set, no CLI flag: env IDS wins (single precedence for list AND ack)
    setenv(LOFTBOX_MAILBOX_ID="mb_single", LOFTBOX_MAILBOX_IDS="mb_1,mb_2")
    check("list: both env -> ids", clm.resolve_mailboxes(parse(["list"])) == ["mb_1", "mb_2"])

    # single id via IDS still counts as one -> raw-output path
    setenv(LOFTBOX_MAILBOX_IDS="mb_only")
    check("list: ids with one id -> len1", len(clm.resolve_mailboxes(parse(["list"]))) == 1)

    setenv()
    check("list: none -> exit", expect_exit(lambda: clm.resolve_mailboxes(parse(["list"]))))

    setenv()
    check("list: empty --mailbox-ids -> exit", expect_exit(lambda: clm.resolve_mailboxes(parse(["--mailbox-ids", " , ", "list"]))))

    # ---- resolve_ack_mailbox: must agree with list, fail-closed on ambiguity ----
    setenv()
    check("ack: --mailbox-id", clm.resolve_ack_mailbox(parse(["--mailbox-id", "mb_a", "ack", "--message-id", "m1"])) == "mb_a")

    setenv(LOFTBOX_MAILBOX_ID="mb_env")
    check("ack: env single (legacy)", clm.resolve_ack_mailbox(parse(["ack", "--message-id", "m1"])) == "mb_env")

    setenv(LOFTBOX_MAILBOX_IDS="mb_only")
    check("ack: ids single", clm.resolve_ack_mailbox(parse(["ack", "--message-id", "m1"])) == "mb_only")

    # the data-loss case the review caught: both env set, no explicit target -> FAIL CLOSED
    setenv(LOFTBOX_MAILBOX_ID="mb_single", LOFTBOX_MAILBOX_IDS="mb_1,mb_2")
    check("ack: both env multi -> exit (fail closed)", expect_exit(lambda: clm.resolve_ack_mailbox(parse(["ack", "--message-id", "m1"]))))

    # env IDS multi, explicit --mailbox-id targets one -> allowed
    setenv(LOFTBOX_MAILBOX_IDS="mb_1,mb_2")
    check("ack: explicit target in multi", clm.resolve_ack_mailbox(parse(["--mailbox-id", "mb_1", "ack", "--message-id", "m1"])) == "mb_1")

    setenv(LOFTBOX_MAILBOX_IDS="mb_1,mb_2")
    check("ack: multi no target -> exit", expect_exit(lambda: clm.resolve_ack_mailbox(parse(["ack", "--message-id", "m1"]))))

    setenv()
    check("ack: none -> exit", expect_exit(lambda: clm.resolve_ack_mailbox(parse(["ack", "--message-id", "m1"]))))

    clear_env()
    if failures:
        print("FAIL:", ", ".join(failures))
        sys.exit(1)
    print("ok: all mailbox-resolution tests passed")


if __name__ == "__main__":
    run()
