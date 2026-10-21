"""
Safaricom airtime USSD builder.

This module is the SINGLE source of truth for the USSD string used by the
API, the web UI and the test-suite, so the command can never drift between
surfaces.

Format (as specified by the project owner):

    *140*AMOUNT*PHONE_NUMBER#

Example (format only - the code has NOT been verified against Safaricom's
current airtime menu):

    *140*10*0712345678#

IMPORTANT CAVEATS
-----------------
1. This code has not been verified against Safaricom's live airtime menu.
   `*140*` may only work for certain accounts/products. Always confirm the
   real behaviour manually on the SIM before relying on it.
2. The builder NEVER touches M-PESA PINs, OTPs or any payment credential.
   It only formats a string that the user then dials themselves.
3. Opening a dialer with this string is NOT proof that a recharge happened.
   The caller must obtain an explicit confirmation from the user.
"""

import re
from urllib.parse import quote

# The Safaricom airtime USSD code used by this project.
USSD_CODE = "*140"

# Maximum airtime amount we are willing to build a USSD string for.
MAX_AMOUNT = 100000

# Minimum amount (in whole Kenyan shillings).
MIN_AMOUNT = 1

# Amount must be a positive whole number of shillings.
_AMOUNT_RE = re.compile(r"^\d{1,6}$")


class UssdError(ValueError):
    """Raised when the inputs cannot produce a valid USSD string."""


def normalize_amount(amount):
    """
    Coerces `amount` into a positive whole number of shillings.
    Accepts ints, floats that are whole numbers, and numeric strings.
    Raises UssdError for anything else.
    """
    if amount is None or amount == "":
        raise UssdError("An airtime amount is required.")

    if isinstance(amount, bool):
        raise UssdError("Invalid airtime amount.")

    if isinstance(amount, float):
        if not amount.is_integer():
            raise UssdError("Airtime amount must be a whole number of shillings.")
        amount = int(amount)

    if isinstance(amount, str):
        amount = amount.strip()
        if not amount:
            raise UssdError("An airtime amount is required.")
        if not _AMOUNT_RE.match(amount):
            raise UssdError("Invalid airtime amount. Use a whole number of shillings.")
        amount = int(amount)

    if not isinstance(amount, int):
        raise UssdError("Invalid airtime amount.")

    if amount < MIN_AMOUNT:
        raise UssdError(f"Airtime amount must be at least {MIN_AMOUNT}.")
    if amount > MAX_AMOUNT:
        raise UssdError(f"Airtime amount must not exceed {MAX_AMOUNT}.")

    return amount


def format_local_number(phone_e164):
    """
    254712345678 -> 0712345678 (the local Kenyan format Safaricom menus
    normally expect).
    """
    if not phone_e164:
        raise UssdError("A phone number is required.")
    phone = str(phone_e164).strip()
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("254"):
        rest = phone[3:]
        if rest.startswith("7") or rest.startswith("1"):
            return "0" + rest
        raise UssdError("Invalid Kenyan phone number.")
    raise UssdError("Invalid Kenyan phone number.")


def format_e164_number(phone_e164):
    """254712345678 -> +254712345678"""
    if not phone_e164:
        raise UssdError("A phone number is required.")
    phone = str(phone_e164).strip()
    if phone.startswith("+"):
        phone = phone[1:]
    if not phone.startswith("254"):
        raise UssdError("Invalid Kenyan phone number.")
    return "+" + phone


def build_ussd(amount, phone_e164, number_format="local"):
    """
    Builds the Safaricom airtime USSD string and its dialable URI.

    Returns a dict:
        {
          "ussd":  "*140*10*0712345678#",
          "uri":   "tel:*140*10*0712345678%23",
          "amount": 10,
          "phone": "0712345678",
          "numberFormat": "local",
        }

    `number_format` is "local" (0712345678) or "e164" (+254712345678).
    The URI percent-encodes '#' (which browsers would otherwise treat as a
    fragment) but leaves '*' intact, which is both valid in a tel: URI and
    universally accepted by Android dialers.
    """
    amt = normalize_amount(amount)

    if number_format == "e164":
        phone = format_e164_number(phone_e164)
    elif number_format == "local":
        phone = format_local_number(phone_e164)
    else:
        raise UssdError("Unknown phone number format.")

    ussd = f"{USSD_CODE}*{amt}*{phone}#"
    return {
        "ussd": ussd,
        "uri": "tel:" + quote(ussd, safe="*"),
        "amount": amt,
        "phone": phone,
        "numberFormat": number_format,
    }
