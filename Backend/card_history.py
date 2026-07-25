"""
Parses ProTrack365 "Card History" exports.

These files carry a .xls extension but are actually saved HTML tables
(ProTrack365's export format), not real Excel binaries. This module reads
them with pandas.read_html and normalizes the result into clean JSON-ready
dicts.

Expected columns (in order):
    #, Datetime, Type, Operation detail, Income, Expenditure, Card Balance,
    IMEI, Remark
"""

import math
import os

import pandas as pd

CARD_HISTORY_DIR = os.environ.get("CARD_HISTORY_DIR", os.path.join(os.path.dirname(__file__), "data"))


def _clean(value):
    """Turn NaN/'-'/empty into None, otherwise return the stripped string."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    s = str(value).strip()
    if s in ("", "-", "nan", "NaN"):
        return None
    return s


def _to_number(value):
    s = _clean(value)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_card_history_file(filepath):
    """
    Parses a single ProTrack365 card-history export file and returns a list
    of transaction dicts, most-recent first (matches the file's own order).
    """
    tables = pd.read_html(filepath)
    if not tables:
        return []

    df = tables[0]
    # First row holds the real header labels; promote it and drop it.
    df.columns = [str(c).strip() for c in df.iloc[0]]
    df = df[1:]

    records = []
    for _, row in df.iterrows():
        records.append({
            "seq": _clean(row.get("#")),
            "datetime": _clean(row.get("Datetime")),
            "type": _clean(row.get("Type")),
            "operation": _clean(row.get("Operation detail")),
            "income": _to_number(row.get("Income")),
            "expenditure": _to_number(row.get("Expenditure")),
            "balance": _to_number(row.get("Card Balance")),
            "imei": _clean(row.get("IMEI")),
            "remark": _clean(row.get("Remark")),
        })
    return records


def load_all(directory=None):
    """
    Parses every .xls/.xlsx/.html card-history export found in `directory`
    and returns a combined, de-duplicated, newest-first list.
    """
    directory = directory or CARD_HISTORY_DIR
    if not os.path.isdir(directory):
        return []

    all_records = []
    for fname in sorted(os.listdir(directory)):
        if fname.lower().endswith((".xls", ".xlsx", ".html", ".htm")):
            fpath = os.path.join(directory, fname)
            try:
                all_records.extend(parse_card_history_file(fpath))
            except Exception:
                # Skip files that don't parse as ProTrack card-history exports
                continue

    # De-duplicate identical rows (e.g. overlapping export date ranges)
    seen = set()
    deduped = []
    for r in all_records:
        key = (r["datetime"], r["imei"], r["expenditure"], r["income"], r["balance"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    deduped.sort(key=lambda r: r["datetime"] or "", reverse=True)
    return deduped


def summarize(records):
    total_expenditure = sum(r["expenditure"] or 0 for r in records)
    total_income = sum(r["income"] or 0 for r in records)
    new_devices_added = sum(1 for r in records if r["type"] == "New card")
    imeis = {r["imei"] for r in records if r["imei"]}

    latest_balance_by_imei = {}
    for r in records:  # records is newest-first, so first hit per IMEI wins
        if r["imei"] and r["imei"] not in latest_balance_by_imei and r["balance"] is not None:
            latest_balance_by_imei[r["imei"]] = r["balance"]

    return {
        "totalTransactions": len(records),
        "totalExpenditure": round(total_expenditure, 2),
        "totalIncome": round(total_income, 2),
        "newDevicesAdded": new_devices_added,
        "uniqueDevices": len(imeis),
    }