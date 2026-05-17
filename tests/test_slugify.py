"""Slugify rules."""

import pytest

from lib.slugify import MAX_LEN, SlugifyError, slugify


def test_basic_lowercase():
    assert slugify("Hello World") == "hello_world"


def test_strip_accents():
    assert slugify("Systeme") == "systeme"
    assert slugify("Système") == "systeme"
    assert slugify("Café crème") == "cafe_creme"


def test_collapse_special_chars():
    assert slugify("TTL too short!") == "ttl_too_short"
    assert slugify("a — b/c") == "a_b_c"
    assert slugify("hello___world") == "hello_world"


def test_trim_underscores():
    assert slugify("__hello__") == "hello"
    assert slugify("   spaces   ") == "spaces"


def test_idempotent():
    s = slugify("Complex Label with spécial chars!!!")
    assert slugify(s) == s


def test_max_length_truncation():
    long_label = "word " * 60  # 300 chars
    result = slugify(long_label)
    assert len(result) <= MAX_LEN
    assert not result.endswith("_")


def test_max_length_cut_on_underscore_boundary():
    # build a label where the cut would land mid-word: ensure boundary trimming
    label = "abcdefghij_" * 10  # underscores every 10 chars, total 110
    result = slugify(label)
    assert len(result) <= MAX_LEN
    assert result.startswith("abcdefghij")


def test_empty_label_raises():
    with pytest.raises(SlugifyError):
        slugify("")
    with pytest.raises(SlugifyError):
        slugify("   ")
    with pytest.raises(SlugifyError):
        slugify("---!!!")


def test_none_label_raises():
    with pytest.raises(SlugifyError):
        slugify(None)


def test_numbers_kept():
    assert slugify("Version 1.2.3") == "version_1_2_3"


def test_unicode_normalization():
    # NFKD decomposition handles ligatures and composed characters
    assert slugify("naïve") == "naive"
    assert slugify("résumé") == "resume"
