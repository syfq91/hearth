"""Unit tests for unified application errors."""

from app.core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)


def test_app_error_defaults() -> None:
    """Verify base AppError attributes and dictionary serialization."""
    err = AppError(
        code="test_code",
        message="A test error occurred",
        status_code=400,
        details={"field": "test"},
    )
    assert err.code == "test_code"
    assert err.message == "A test error occurred"
    assert err.status_code == 400
    assert err.details == {"field": "test"}

    payload = err.to_dict()
    assert payload == {
        "error": {
            "code": "test_code",
            "message": "A test error occurred",
            "details": {"field": "test"},
        }
    }


def test_derived_errors() -> None:
    """Verify status codes and default codes for standard errors."""
    not_found = NotFoundError("Item not found", details={"id": "123"})
    assert not_found.status_code == 404
    assert not_found.code == "not_found"
    assert not_found.details == {"id": "123"}

    val_err = ValidationError("Field invalid")
    assert val_err.status_code == 422
    assert val_err.code == "validation_error"

    conflict = ConflictError("Resource exists")
    assert conflict.status_code == 409
    assert conflict.code == "conflict"

    unauth = UnauthorizedError("Invalid token")
    assert unauth.status_code == 401
    assert unauth.code == "unauthorized"

    forbidden = ForbiddenError("Access denied")
    assert forbidden.status_code == 403
    assert forbidden.code == "forbidden"
