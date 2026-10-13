import pytest

from exceptions import (
    APIError,
    ApplicationError,
    ConfigurationError,
    DataValidationError,
)


@pytest.mark.parametrize(
    ("error_type", "kwargs", "attribute", "expected"),
    [
        (APIError, {"status_code": 503}, "status_code", 503),
        (DataValidationError, {"field": "email"}, "field", "email"),
    ],
)
def test_custom_errors_keep_message_and_context(
    error_type, kwargs, attribute, expected
):
    error = error_type("Request failed", **kwargs)

    assert str(error) == "Request failed"
    assert getattr(error, attribute) == expected
    assert isinstance(error, ApplicationError)


def test_custom_errors_allow_missing_context():
    assert APIError("Request failed").status_code is None
    assert DataValidationError("Invalid data").field is None


def test_configuration_error_is_an_application_error():
    error = ConfigurationError("Missing required setting")

    assert str(error) == "Missing required setting"
    assert isinstance(error, ApplicationError)
