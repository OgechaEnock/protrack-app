"""
Authentication utilities for admin endpoints.
Simple API key-based authentication.

The key is read from the ADMIN_API_KEY environment variable. If it is not set,
a development default is used and a warning is logged - set the environment
variable before exposing the admin panel to anyone else.
"""

import functools
import os
from flask import request

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "admin123")  # Change this in production!
AUTH_HEADER = "X-Admin-API-Key"
QUERY_PARAM = "admin_key"

# Printed once at import time so a deployment without ADMIN_API_KEY is
# obvious in the logs.
if not os.environ.get("ADMIN_API_KEY"):
    print(
        "[admin] WARNING: ADMIN_API_KEY is not set. Using the development "
        "default key. Set ADMIN_API_KEY before exposing this API."
    )


def require_admin(f):
    """Decorator to protect admin endpoints with API key authentication."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get(AUTH_HEADER) or request.args.get(QUERY_PARAM)
        if not api_key or api_key != ADMIN_API_KEY:
            # A plain dict (not a flask Response) is returned because these
            # endpoints are served by Flask-RESTful, which JSON-serialises
            # whatever the resource returns.
            return {
                "error": "Unauthorized. Provide a valid admin API key."
            }, 401
        return f(*args, **kwargs)
    return wrapper


def get_admin_key():
    """Returns the configured admin API key (for frontend to use)."""
    return ADMIN_API_KEY