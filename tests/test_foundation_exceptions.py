"""Tests for the admedi typed exception hierarchy.

Covers:
- Base AdmediError construction and attribute storage
- All 5 subclass inheritance chains
- Specific attributes on ApiError and RateLimitError
- str() representation returns the message
- Catchability by both specific type and AdmediError base
- Default attribute values
"""

from __future__ import annotations

import pytest

from admedi.exceptions import (
    AdapterNotSupportedError,
    AdmediError,
    ApiError,
    AuthError,
    ConfigValidationError,
    RateLimitError,
)


class TestAdmediError:
    """Tests for the AdmediError base exception."""

    def test_message_stored(self) -> None:
        """AdmediError stores message attribute."""
        err = AdmediError("something failed")
        assert err.message == "something failed"

    def test_detail_stored(self) -> None:
        """AdmediError stores detail attribute when provided."""
        err = AdmediError("something failed", detail="extra info")
        assert err.detail == "extra info"

    def test_detail_defaults_to_none(self) -> None:
        """AdmediError.detail defaults to None when not provided."""
        err = AdmediError("something failed")
        assert err.detail is None

    def test_str_returns_message(self) -> None:
        """str(AdmediError) returns the message string."""
        err = AdmediError("test message")
        assert str(err) == "test message"

    def test_is_exception(self) -> None:
        """AdmediError is a subclass of Exception."""
        assert issubclass(AdmediError, Exception)

    def test_raise_and_catch(self) -> None:
        """AdmediError can be raised and caught."""
        with pytest.raises(AdmediError, match="catch me"):
            raise AdmediError("catch me")


class TestAuthError:
    """Tests for AuthError."""

    def test_inherits_from_admedi_error(self) -> None:
        """AuthError is a subclass of AdmediError."""
        assert issubclass(AuthError, AdmediError)

    def test_catchable_by_base(self) -> None:
        """AuthError is catchable by AdmediError."""
        with pytest.raises(AdmediError):
            raise AuthError("bad creds")

    def test_catchable_by_specific_type(self) -> None:
        """AuthError is catchable by its own type."""
        with pytest.raises(AuthError, match="bad creds"):
            raise AuthError("bad creds")

    def test_message_attribute(self) -> None:
        """AuthError stores message attribute from base."""
        err = AuthError("invalid token")
        assert err.message == "invalid token"

    def test_detail_attribute(self) -> None:
        """AuthError stores detail attribute from base."""
        err = AuthError("invalid token", detail="expired JWT")
        assert err.detail == "expired JWT"

    def test_str_returns_message(self) -> None:
        """str(AuthError) returns the message."""
        err = AuthError("auth failed")
        assert str(err) == "auth failed"


class TestRateLimitError:
    """Tests for RateLimitError."""

    def test_inherits_from_admedi_error(self) -> None:
        """RateLimitError is a subclass of AdmediError."""
        assert issubclass(RateLimitError, AdmediError)

    def test_catchable_by_base(self) -> None:
        """RateLimitError is catchable by AdmediError."""
        with pytest.raises(AdmediError):
            raise RateLimitError("slow down", retry_after=30.0)

    def test_catchable_by_specific_type(self) -> None:
        """RateLimitError is catchable by its own type."""
        with pytest.raises(RateLimitError):
            raise RateLimitError("slow down", retry_after=30.0)

    def test_retry_after_stored(self) -> None:
        """RateLimitError stores retry_after attribute."""
        err = RateLimitError("slow down", retry_after=30.0)
        assert err.retry_after == 30.0

    def test_retry_after_defaults_to_none(self) -> None:
        """RateLimitError.retry_after defaults to None."""
        err = RateLimitError("slow down")
        assert err.retry_after is None

    def test_retry_after_accessible_after_catch(self) -> None:
        """retry_after is accessible on the caught exception."""
        try:
            raise RateLimitError("slow down", retry_after=30.0)
        except RateLimitError as e:
            assert e.retry_after == 30.0

    def test_message_attribute(self) -> None:
        """RateLimitError stores message from base."""
        err = RateLimitError("limit hit", retry_after=60.0)
        assert err.message == "limit hit"

    def test_detail_attribute(self) -> None:
        """RateLimitError stores detail from base."""
        err = RateLimitError("limit hit", retry_after=60.0, detail="groups API")
        assert err.detail == "groups API"

    def test_str_returns_message(self) -> None:
        """str(RateLimitError) returns the message."""
        err = RateLimitError("rate limited")
        assert str(err) == "rate limited"


class TestApiError:
    """Tests for ApiError."""

    def test_inherits_from_admedi_error(self) -> None:
        """ApiError is a subclass of AdmediError."""
        assert issubclass(ApiError, AdmediError)

    def test_catchable_by_base(self) -> None:
        """ApiError is catchable by AdmediError."""
        with pytest.raises(AdmediError):
            raise ApiError("bad request", status_code=400)

    def test_catchable_by_specific_type(self) -> None:
        """ApiError is catchable by its own type."""
        with pytest.raises(ApiError):
            raise ApiError("bad request", status_code=400)

    def test_status_code_stored(self) -> None:
        """ApiError stores status_code attribute."""
        err = ApiError("bad request", status_code=400)
        assert err.status_code == 400

    def test_status_code_accessible_after_catch(self) -> None:
        """status_code is accessible on the caught exception."""
        try:
            raise ApiError("fail", status_code=400)
        except ApiError as e:
            assert e.status_code == 400

    def test_response_body_stored(self) -> None:
        """ApiError stores response_body attribute."""
        body = {"error": "invalid appKey"}
        err = ApiError("bad request", status_code=400, response_body=body)
        assert err.response_body == {"error": "invalid appKey"}

    def test_response_body_defaults_to_none(self) -> None:
        """ApiError.response_body defaults to None."""
        err = ApiError("server error", status_code=500)
        assert err.response_body is None

    def test_message_attribute(self) -> None:
        """ApiError stores message from base."""
        err = ApiError("not found", status_code=404)
        assert err.message == "not found"

    def test_detail_attribute(self) -> None:
        """ApiError stores detail from base."""
        err = ApiError("not found", status_code=404, detail="app does not exist")
        assert err.detail == "app does not exist"

    def test_all_attributes_together(self) -> None:
        """ApiError stores all attributes when all are provided."""
        body = {"error": "batch failed", "items": [1, 2, 3]}
        err = ApiError(
            "batch error",
            status_code=422,
            response_body=body,
            detail="3 items rejected",
        )
        assert err.message == "batch error"
        assert err.status_code == 422
        assert err.response_body == body
        assert err.detail == "3 items rejected"

    def test_str_returns_message(self) -> None:
        """str(ApiError) returns the message."""
        err = ApiError("server error", status_code=500)
        assert str(err) == "server error"


class TestConfigValidationError:
    """Tests for ConfigValidationError."""

    def test_inherits_from_admedi_error(self) -> None:
        """ConfigValidationError is a subclass of AdmediError."""
        assert issubclass(ConfigValidationError, AdmediError)

    def test_catchable_by_base(self) -> None:
        """ConfigValidationError is catchable by AdmediError."""
        with pytest.raises(AdmediError):
            raise ConfigValidationError("duplicate country")

    def test_catchable_by_specific_type(self) -> None:
        """ConfigValidationError is catchable by its own type."""
        with pytest.raises(ConfigValidationError, match="duplicate country"):
            raise ConfigValidationError("duplicate country")

    def test_message_attribute(self) -> None:
        """ConfigValidationError stores message from base."""
        err = ConfigValidationError("missing default tier")
        assert err.message == "missing default tier"

    def test_str_returns_message(self) -> None:
        """str(ConfigValidationError) returns the message."""
        err = ConfigValidationError("bad config")
        assert str(err) == "bad config"


class TestAdapterNotSupportedError:
    """Tests for AdapterNotSupportedError."""

    def test_inherits_from_admedi_error(self) -> None:
        """AdapterNotSupportedError is a subclass of AdmediError."""
        assert issubclass(AdapterNotSupportedError, AdmediError)

    def test_catchable_by_base(self) -> None:
        """AdapterNotSupportedError is catchable by AdmediError."""
        with pytest.raises(AdmediError):
            raise AdapterNotSupportedError("not supported")

    def test_catchable_by_specific_type(self) -> None:
        """AdapterNotSupportedError is catchable by its own type."""
        with pytest.raises(AdapterNotSupportedError, match="not supported"):
            raise AdapterNotSupportedError("not supported")

    def test_message_attribute(self) -> None:
        """AdapterNotSupportedError stores message from base."""
        err = AdapterNotSupportedError("bidding not supported")
        assert err.message == "bidding not supported"

    def test_str_returns_message(self) -> None:
        """str(AdapterNotSupportedError) returns the message."""
        err = AdapterNotSupportedError("no bidding")
        assert str(err) == "no bidding"


class TestExceptionDistinctTypes:
    """Verify all 5 subclasses are distinct types (not aliases)."""

    def test_all_subclasses_are_distinct(self) -> None:
        """All 5 exception subclasses are unique types."""
        subclasses = [
            AuthError,
            RateLimitError,
            ApiError,
            ConfigValidationError,
            AdapterNotSupportedError,
        ]
        # All pairs should be distinct
        for i, cls_a in enumerate(subclasses):
            for cls_b in subclasses[i + 1 :]:
                assert cls_a is not cls_b, f"{cls_a.__name__} is {cls_b.__name__}"

    def test_subclass_count(self) -> None:
        """There are exactly 5 direct subclasses of AdmediError."""
        direct_subclasses = AdmediError.__subclasses__()
        assert len(direct_subclasses) == 5

    def test_all_expected_subclasses_present(self) -> None:
        """All expected subclass names are present."""
        names = {cls.__name__ for cls in AdmediError.__subclasses__()}
        expected = {
            "AuthError",
            "RateLimitError",
            "ApiError",
            "ConfigValidationError",
            "AdapterNotSupportedError",
        }
        assert names == expected


class TestExceptionImportsFromPackage:
    """Verify exceptions are importable from the admedi package root."""

    def test_import_from_admedi(self) -> None:
        """All exceptions are importable from admedi top-level."""
        from admedi import (
            AdapterNotSupportedError,
            AdmediError,
            ApiError,
            AuthError,
            ConfigValidationError,
            RateLimitError,
        )

        # Verify they are the same classes (not copies)
        from admedi.exceptions import AdmediError as DirectAdmediError

        assert AdmediError is DirectAdmediError
        assert issubclass(AuthError, AdmediError)
        assert issubclass(RateLimitError, AdmediError)
        assert issubclass(ApiError, AdmediError)
        assert issubclass(ConfigValidationError, AdmediError)
        assert issubclass(AdapterNotSupportedError, AdmediError)
