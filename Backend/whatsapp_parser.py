"""
Parses exported WhatsApp chat logs (.txt) to pull out device recharge
events.

Handles both WhatsApp export layouts:
    iOS:     [HH:MM, DD/MM/YYYY] Sender: message
    Android: DD/MM/YYYY, HH:MM - Sender: message

Recognized message shapes (device token = plate/equipment name):
    "PLATE///AMOUNT"                 e.g. "KDR652Z///50"
    "PLATE///AMOUNT REMARK"          e.g. "KMFY701D///50 PILOT"
    "PLATE AMOUNT REMARK"            e.g. "KAH491Y 20 Mettax"
    "PLATE///DATAmb REMARK"          e.g. "KDH050Q///70mb Pilot"
    "PLATE DATAmb REMARK"            e.g. "KDH050Q 70mbs Pilot"
    "PLATE///DATAGB @PRICE REMARK"   e.g. "KCU744H///10GB @1000 mettax"

Anything else (chit-chat, unrelated messages) is returned separately as
"unparsed" so nothing is silently dropped.
"""

import os
import re
from datetime import datetime

RECHARGE_LOG_DIR = os.environ.get(
    "RECHARGE_LOG_DIR", os.path.join(os.path.dirname(__file__), "data", "whatsapp")
)

# WhatsApp iOS export: [HH:MM, DD/MM/YYYY] Sender: message
LINE_RE_IOS = re.compile(
    r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\s*,\s*(\d{1,2}/\d{1,2}/\d{4})\]\s*([^:]+):\s*(.*)$'
)
# WhatsApp Android export: DD/MM/YYYY, HH:MM - Sender: message
LINE_RE_ANDROID = re.compile(
    r'^(\d{1,2}/\d{1,2}/\d{4})\s*,\s*(\d{1,2}:\d{2}(?:\s?[APap][Mm])?)\s*-\s*([^:]+):\s*(.*)$'
)

EDITED_SUFFIX_RE = re.compile(r'\s*<This message was edited>\s*$', re.IGNORECASE)
INVISIBLE_CHARS_RE = re.compile(r'[\u200e\u200f\u2068\u2069\ufeff]')

PURE_NUMBER_RE = re.compile(r'^(\d+(?:\.\d+)?)$')
DATA_BUNDLE_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*(mb|mbs|gb)$', re.IGNORECASE)
GLUED_AMOUNT_RE = re.compile(r'^(\d+(?:\.\d+)?)([A-Za-z].*)$')  # e.g. "50Pilot", "40METTA"


def _classify_category(remark):
    """
    Classifies a recharge event into 'mettax', 'pilot', or 'protrack' based
    on its remark text. These are real recurring tags used in the chat
    logs to mark which billing/program a device's recharge belongs to.
    'protrack' is the default/base program (no special remark tag).
    """
    if not remark:
        return "protrack"
    r = remark.lower()
    if "mettax" in r or "metta" in r:
        return "mettax"
    if "pilot" in r:
        return "pilot"
    return "protrack"


def _looks_like_device_token(tok):
    if len(tok) < 3:
        return False
    letters = [c for c in tok if c.isalpha()]
    if not letters:
        return False
    all_upper = all(c.isupper() for c in letters)
    has_digit = any(c.isdigit() for c in tok)
    return all_upper and (has_digit or len(tok) >= 4)


def _parse_date(date_s):
    """WhatsApp exports typically use DD/MM/YYYY."""
    for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _clean_message(msg):
    msg = INVISIBLE_CHARS_RE.sub('', msg)
    msg = EDITED_SUFFIX_RE.sub('', msg)
    return msg.strip()


def _match_line(line):
    """Tries both known WhatsApp export layouts. Returns (time_s, date_s, sender, msg) or None."""
    m = LINE_RE_IOS.match(line)
    if m:
        time_s, date_s, sender, msg = m.groups()
        return time_s, date_s, sender.strip(), msg

    m = LINE_RE_ANDROID.match(line)
    if m:
        date_s, time_s, sender, msg = m.groups()
        return time_s, date_s, sender.strip(), msg

    return None


def _parse_amount_segment(rest):
    """
    Parses the portion of a message after the device token (whether split
    by '///' or by whitespace). Returns (amount, dataBundle, remark).
    `amount` is the monetary/recharge value (float or None if only a data
    bundle was mentioned with no price). `dataBundle` is the raw data
    token (e.g. "70mb", "10GB") if present.
    """
    tokens = rest.split()
    if not tokens:
        return None, None, None

    first = tokens[0]
    amount, data_bundle = None, None
    remaining = tokens[1:]

    m_num = PURE_NUMBER_RE.match(first)
    m_data = DATA_BUNDLE_RE.match(first)
    m_glued = GLUED_AMOUNT_RE.match(first)

    if m_num:
        amount = float(m_num.group(1))
    elif m_data:
        data_bundle = first
        if remaining and remaining[0].startswith('@'):
            price_tok = remaining[0][1:]
            m_price = PURE_NUMBER_RE.match(price_tok)
            if m_price:
                amount = float(m_price.group(1))
                remaining = remaining[1:]
    elif m_glued:
        # e.g. "50Pilot" -> amount=50, "Pilot" folded back into the remark
        amount = float(m_glued.group(1))
        remaining = [m_glued.group(2)] + remaining
    else:
        # First token isn't a recognizable amount - check if a *later*
        # token is a plain number (handles reversed order like
        # "Mettax 20"). Otherwise this isn't a parseable recharge message.
        amount_idx = next((i for i, t in enumerate(tokens) if PURE_NUMBER_RE.match(t)), None)
        if amount_idx is None:
            return None, None, None
        amount = float(tokens[amount_idx])
        remaining = tokens[:amount_idx] + tokens[amount_idx + 1:]

    remark = " ".join(remaining).strip() or None
    return amount, data_bundle, remark


def _extract_recharge(msg):
    """
    Returns (device, amount, dataBundle, remark) or (None, None, None, None)
    if the message doesn't look like a recharge entry.
    """
    if "///" in msg:
        left, right = msg.split("///", 1)
        device = left.strip()
        if not device:
            return None, None, None, None
        amount, data_bundle, remark = _parse_amount_segment(right.strip())
        if amount is None and data_bundle is None:
            return None, None, None, None
        return device, amount, data_bundle, remark

    tokens = msg.split()
    if len(tokens) < 2:
        return None, None, None, None

    # Device may be multiple words (e.g. "BAOLI KBE30-003 ELECTRIC FORKLIFT").
    # Find the first token that looks like an amount/data-bundle token; if
    # the very first token doesn't look like a device at all, bail out
    # (filters out chit-chat like "Sent 200 airtime to ...").
    if not _looks_like_device_token(tokens[0]):
        return None, None, None, None

    amount_idx = None
    for i in range(1, len(tokens)):
        if PURE_NUMBER_RE.match(tokens[i]) or DATA_BUNDLE_RE.match(tokens[i]) or GLUED_AMOUNT_RE.match(tokens[i]):
            amount_idx = i  # keep scanning - take the LAST match, not the first.
            # Equipment names (e.g. "SCHWING STETTER SP1807 - 657") can embed
            # numbers of their own before the real trailing recharge amount.

    if amount_idx is None:
        return None, None, None, None

    device = " ".join(tokens[:amount_idx])
    rest = " ".join(tokens[amount_idx:])
    amount, data_bundle, remark = _parse_amount_segment(rest)
    if amount is None and data_bundle is None:
        return None, None, None, None
    return device, amount, data_bundle, remark


def parse_chat_file(filepath):
    with open(filepath, encoding="utf-8", errors="ignore") as f:
        text = f.read()

    events = []
    unparsed = []

    for raw_line in text.splitlines():
        line = INVISIBLE_CHARS_RE.sub('', raw_line.strip())
        if not line:
            continue

        matched = _match_line(line)
        if not matched:
            continue

        time_s, date_s, sender, msg = matched
        msg = _clean_message(msg)
        date_iso = _parse_date(date_s)

        device, amount, data_bundle, remark = _extract_recharge(msg)

        if device:
            events.append({
                "date": date_iso,
                "time": time_s,
                "sender": sender,
                "device": device,
                "amount": amount,
                "dataBundle": data_bundle,
                "remark": remark,
                "category": _classify_category(remark),
                "raw": msg,
            })
        else:
            unparsed.append({
                "date": date_iso,
                "time": time_s,
                "sender": sender,
                "raw": msg,
            })

    return events, unparsed


def load_all(directory=None):
    """
    Parses every .txt WhatsApp export in `directory` and returns combined,
    de-duplicated (date+time+device+amount) recharge events plus unparsed
    lines, both sorted newest first.
    """
    directory = directory or RECHARGE_LOG_DIR
    if not os.path.isdir(directory):
        return [], []

    all_events, all_unparsed = [], []
    for fname in sorted(os.listdir(directory)):
        if fname.lower().endswith(".txt"):
            fpath = os.path.join(directory, fname)
            try:
                events, unparsed = parse_chat_file(fpath)
                all_events.extend(events)
                all_unparsed.extend(unparsed)
            except Exception:
                continue

    seen = set()
    deduped = []
    for e in all_events:
        key = (e["date"], e["time"], e["device"], e["amount"], e["dataBundle"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    deduped.sort(key=lambda e: (e["date"] or "", e["time"] or ""), reverse=True)
    all_unparsed.sort(key=lambda e: (e["date"] or "", e["time"] or ""), reverse=True)
    return deduped, all_unparsed