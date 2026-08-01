"""
Authentication utilities for admin endpoints.
Simple API key-based authentication.
"""

import functools
import os
from flask import request, jsonify

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "admin123")  # Change this in production!
AUTH_HEADER = "X-Admin-API-Key"


def require_admin(f):
    """Decorator to protect admin endpoints with API key authentication."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get(AUTH_HEADER) or request.args.get("admin_key")
        if not api_key or api_key != ADMIN_API_KEY:
            return jsonify({"error": "Unauthorized. Provide a valid admin API key."}), 401
        return f(*args, **kwargs)
    return wrapper


def get_admin_key():
    """Returns the configured admin API key (for frontend to use)."""
    return ADMIN_API_KEY