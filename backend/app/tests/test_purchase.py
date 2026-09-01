from app.services.purchase_service import derive_invoice_status, validate_transition


def test_forward_transition_valid():
    assert validate_transition("pending_refine", "confirmed")
    assert validate_transition("confirmed", "jackyun_linked")
    assert validate_transition("arrived", "inbound")


def test_backward_or_skip_transition_invalid():
    assert not validate_transition("confirmed", "pending_refine")   # 回退
    assert not validate_transition("pending_refine", "shipped")     # 跳步
    assert not validate_transition("done", "done")                  # 原地
    assert not validate_transition("whatever", "confirmed")         # 非法态


def test_invoice_status_full_when_covered():
    assert derive_invoice_status("50000", "50000", "applied") == "full"
    assert derive_invoice_status("50000", "52000", "applied") == "full"


def test_invoice_status_partial():
    assert derive_invoice_status("50000", "20000", "applied") == "partial"


def test_invoice_status_none_kept_when_no_link():
    assert derive_invoice_status("50000", "0", "none") == "none"
    assert derive_invoice_status("50000", "0", "unverified") == "unverified"


def test_invoice_status_defaults_unverified():
    assert derive_invoice_status("50000", "0", "weird") == "unverified"


def test_zero_paid_with_links_is_partial():
    assert derive_invoice_status("0", "100", "none") == "partial"
