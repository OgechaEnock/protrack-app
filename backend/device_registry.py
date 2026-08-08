"""
Parses the device registry spreadsheet (plate/SIM/IMEI/account master list).

Expected columns (header row, case-insensitive, some whitespace tolerated):
    NUMBERPLATE, SIMCARD, IMEI, ACCOUNT NAME, CLIENT'S MOBILE,
    CLIENT'S EMAIL, TELEPHONE

The sheet may have a title row above the real header (e.g. "PROTRACK"), so
we scan the first few rows for the one that contains "NUMBERPLATE".
"""

import math
import os

import pandas as pd

REGISTRY_DIR = os.environ.get(
    "REGISTRY_DIR", os.path.join(os.path.dirname(__file__), "data", "registry")
)

COLUMN_MAP = {
    "numberplate": "plate",
    "simcard": "sim",
    "sim card": "sim",
    "imei": "imei",
    "account name": "accountName",
    "clients mobile": "clientMobile",
    "client's mobile": "clientMobile",
    "clients email": "clientEmail",
    "client's email": "clientEmail",
    "telephone": "telephone",
}


def _clean_str(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return s if s else None


def _clean_id(v):
    """IMEI/phone numbers often come through as floats (e.g. 355139...0) -> strip trailing .0"""
    s = _clean_str(v)
    if s is None:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _find_header_row(raw_df):
    for i in range(min(5, len(raw_df))):
        row_vals = [str(v).strip().lower() for v in raw_df.iloc[i].tolist() if v is not None]
        if any("numberplate" in v for v in row_vals):
            return i
    return 0


def parse_registry_file(filepath):
    raw = pd.read_excel(filepath, header=None)
    header_row = _find_header_row(raw)

    df = pd.read_excel(filepath, header=header_row)
    df.columns = [str(c).strip() for c in df.columns]

    normalized_cols = {}
    for c in df.columns:
        key = c.lower().replace("’", "'").strip()
        key_norm = key.replace("'", "").replace("  ", " ").strip()
        mapped = COLUMN_MAP.get(key_norm) or COLUMN_MAP.get(key)
        if mapped:
            normalized_cols[c] = mapped

    df = df.rename(columns=normalized_cols)
    df = df.dropna(how="all")

    records = []
    for _, row in df.iterrows():
        plate = _clean_str(row.get("plate"))
        if not plate:
            continue
        records.append({
            "plate": plate,
            "sim": _clean_id(row.get("sim")),
            "imei": _clean_id(row.get("imei")),
            "accountName": _clean_str(row.get("accountName")),
            "clientMobile": _clean_id(row.get("clientMobile")),
            "clientEmail": _clean_str(row.get("clientEmail")),
            "telephone": _clean_id(row.get("telephone")),
        })
    return records


def load_registry(directory=None):
    """
    Loads the device registry from the most recently uploaded/modified
    spreadsheet in `directory` (there should typically be just one).
    """
    directory = directory or REGISTRY_DIR
    if not os.path.isdir(directory):
        return []

    files = [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.lower().endswith((".xlsx", ".xls"))
    ]
    if not files:
        return []

    latest = max(files, key=os.path.getmtime)
    try:
        return parse_registry_file(latest)
    except Exception:
        return []