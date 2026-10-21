"""
Tests for the admin panel authentication (backend/admin_auth.py).

The admin endpoints are served by Flask-RESTful, so the auth decorator has to
return a plain dict (not a flask Response) - otherwise the 401 body cannot be
JSON-serialised and the client receives a 500 instead.

Run with:
    cd backend && .venv/bin/python -m pytest tests -v
"""

import os
import sys

import pytest

# The backend modules import each other by bare name (e.g. "import app"), so the
# backend directory has to be on sys.path.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


@pytest.fixture()
def client(monkeypatch):
    """A Flask test client with a known admin key."""
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")

    import admin_auth

    admin_auth.ADMIN_API_KEY = "test-admin-key"

    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


AUTH = {"X-Admin-API-Key": "test-admin-key"}


def test_missing_key_is_unauthorized(client):
    response = client.get("/api/admin/health")
    assert response.status_code == 401
    assert "error" in response.get_json()


def test_wrong_key_is_unauthorized(client):
    response = client.get(
        "/api/admin/health", headers={"X-Admin-API-Key": "not-the-key"}
    )
    assert response.status_code == 401
    assert "error" in response.get_json()


def test_valid_key_is_authorized(client):
    response = client.get("/api/admin/health", headers=AUTH)
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_key_can_be_passed_as_query_param(client):
    response = client.get("/api/admin/health?admin_key=test-admin-key")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_admin_endpoints_all_require_the_key(client):
    """Every admin endpoint must reject an unauthenticated request."""
    endpoints = [
        ("get", "/api/admin/health"),
        ("get", "/api/admin/stats"),
        ("get", "/api/admin/files"),
        ("get", "/api/admin/config"),
        ("get", "/api/admin/logs"),
    ]

    for method, url in endpoints:
        response = getattr(client, method)(url)
        assert response.status_code == 401, f"{url} should require the admin key"
