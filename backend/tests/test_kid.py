import pytest

from app.domain.kid import build_kid


def test_regular_domain():
    assert build_kid("faq", "fo", 86) == "faq-fo-0086"
    assert build_kid("policy", "fo", 3) == "pol-fo-0003"
    assert build_kid("product", "cp", 7) == "prd-cp-0007"


def test_common_domain_two_segments():
    # common 域 short_code 为空串，kid 退化为两段（技术设计文档 5.1）
    assert build_kid("term", "", 2) == "term-0002"


def test_seq_overflow_expands():
    assert build_kid("faq", "fo", 12345) == "faq-fo-12345"


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        build_kid("wiki", "fo", 1)


def test_non_positive_seq_rejected():
    with pytest.raises(ValueError):
        build_kid("faq", "fo", 0)
