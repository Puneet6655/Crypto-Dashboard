"""Shared test setup. Every test runs as a signed-in admin unless it asks otherwise.

Tests that check sign-in itself use the ``signed_out`` fixture, which removes that stand-in user.
"""
import pytest

from app.auth.accounts import CurrentUser
from app.auth.deps import current_user
from app.main import app

TEST_ADMIN = CurrentUser(id=1, email="admin@test.local", display_name="Test admin", role="admin")


@pytest.fixture(autouse=True)
def _signed_in_as_admin():
    app.dependency_overrides[current_user] = lambda: TEST_ADMIN
    yield
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
def signed_out():
    app.dependency_overrides.pop(current_user, None)
    yield


@pytest.fixture
def signed_in_as():
    """Call with a role to run the rest of the test as that kind of user."""
    def use(role: str) -> CurrentUser:
        user = CurrentUser(id=2, email=f"{role}@test.local", display_name=f"Test {role}", role=role)
        app.dependency_overrides[current_user] = lambda: user
        return user
    return use
