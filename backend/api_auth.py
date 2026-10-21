"""
API-key authentication for the airtime endpoints.

The airtime API handles phone numbers and money amounts, so unlike the
read-only dashboard endpoints it is protected by a key sent in the
`X-API-Key` header (a `api_key` query parameter is also accepted for
convenience, e.g. when testing from a phone browser).

The key is read from the AIRTIME_API_KEY environment variable. If it is not
set, a development default is used and a warning is logged - set the
environment variable before exposing this API to anyone else.
"""

import functools
import os

from flask import request

AIRTIME_API_KEY = os.environ.get("AIRTIME_API_KEY", "protrack-airtime-dev")
AUTH_HEADER = "X-API-Key"
QUERY_PARAM = "api_key"

# Printed once at import time so a deployment without AIRTIME_API_KEY is
# obvious in the logs.
if not os.environ.get("AIRTIME_API_KEY"):
    print(
        "[airtime] WARNING: AIRTIME_API_KEY is not set. Using the development "
        "default key. Set AIRTIME_API_KEY before exposing this API."
    )


def require_api_key(f):
    """Decorator that rejects requests without a valid API key."""

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get(AUTH_HEADER) or request.args.get(QUERY_PARAM)
        if not api_key or api_key != AIRTIME_API_KEY:
            return {
                "error": "Unauthorized. Provide a valid API key in the X-API-Key header."
            }, 401
        return f(*args, **kwargs)

    return wrapper
