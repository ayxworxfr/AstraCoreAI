"""Tests for InputValidator (XSS patterns, length) and ContentFilter."""
import pytest

from astracore.runtime.security.validator import ContentFilter, InputValidator


@pytest.fixture
def validator():
    return InputValidator(max_input_length=100)


# ---------- InputValidator.validate_user_input ----------

def test_validate_accepts_normal_input(validator):
    ok, err = validator.validate_user_input("Hello, how are you?")
    assert ok is True
    assert err is None


def test_validate_rejects_input_exceeding_max_length(validator):
    ok, err = validator.validate_user_input("x" * 101)
    assert ok is False
    assert "maximum length" in err


def test_validate_rejects_script_tag(validator):
    ok, err = validator.validate_user_input("<script>alert(1)</script>")
    assert ok is False
    assert "suspicious patterns" in err


def test_validate_rejects_script_tag_case_insensitive(validator):
    ok, _ = validator.validate_user_input("<SCRIPT>evil()</SCRIPT>")
    assert ok is False


def test_validate_rejects_javascript_protocol(validator):
    ok, _ = validator.validate_user_input("javascript:void(0)")
    assert ok is False


def test_validate_rejects_onerror_attribute(validator):
    ok, _ = validator.validate_user_input('<img onerror=alert(1)>')
    assert ok is False


def test_validate_rejects_onclick_attribute(validator):
    ok, _ = validator.validate_user_input('<button onclick=evil()>')
    assert ok is False


def test_validate_accepts_input_at_exactly_max_length(validator):
    ok, _ = validator.validate_user_input("a" * 100)
    assert ok is True


# ---------- InputValidator.sanitize_metadata ----------

def test_sanitize_redacts_password():
    v = InputValidator()
    result = v.sanitize_metadata({"password": "s3cr3t", "name": "alice"})
    assert result["password"] == "***REDACTED***"
    assert result["name"] == "alice"


def test_sanitize_redacts_api_key():
    v = InputValidator()
    result = v.sanitize_metadata({"api_key": "sk-xxx"})
    assert result["api_key"] == "***REDACTED***"


def test_sanitize_redacts_token_field():
    v = InputValidator()
    result = v.sanitize_metadata({"auth_token": "abc"})
    assert result["auth_token"] == "***REDACTED***"


def test_sanitize_preserves_nonsensitive_fields():
    v = InputValidator()
    result = v.sanitize_metadata({"user_id": "123", "region": "us-east"})
    assert result == {"user_id": "123", "region": "us-east"}


# ---------- ContentFilter ----------

def test_content_filter_blocks_added_term():
    f = ContentFilter()
    f.add_blocked_term("spam")
    ok, msg = f.filter_content("This is spam content")
    assert ok is False
    assert "spam" in msg


def test_content_filter_is_case_insensitive():
    f = ContentFilter()
    f.add_blocked_term("spam")
    ok, _ = f.filter_content("This is SPAM")
    assert ok is False


def test_content_filter_allows_clean_content():
    f = ContentFilter()
    f.add_blocked_term("blocked")
    ok, text = f.filter_content("perfectly clean content")
    assert ok is True
    assert text == "perfectly clean content"


def test_content_filter_allows_before_term_added():
    f = ContentFilter()
    ok, _ = f.filter_content("anything goes without blocked terms")
    assert ok is True
