"""Value normalisation shared by the grounding checker, the scorer and the
offline mock provider.

Everything here exists because "is this the same value?" is not string equality
once a document can print the same date five ways and the same amount three.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "mrz": 3, "mär": 3,
    "apr": 4, "april": 4, "may": 5, "mai": 5, "jun": 6, "june": 6, "juni": 6,
    "jul": 7, "july": 7, "juli": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "okt": 10, "october": 10, "oktober": 10, "nov": 11, "november": 11,
    "dec": 12, "dez": 12, "december": 12, "dezember": 12,
}


def norm_text(value: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^\w\s]", " ", stripped.casefold())
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_number(value: Any) -> Optional[float]:
    """Read an amount written in any of the conventions the corpus uses.

    `1,234.56`, `1.234,56`, `1234.56`, `-1 234,56`, `€ 1.234,56` all land on the
    same float.  Ambiguous single-separator forms are resolved by the
    three-digits-after rule: `1.234` is a thousands separator, `1.23` is not.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    negative = text.startswith("-") or text.startswith("(") and text.endswith(")")
    digits = re.sub(r"[^\d.,]", "", text)
    if not re.search(r"\d", digits):
        return None

    if "." in digits and "," in digits:
        # The rightmost separator is the decimal point.
        dec_sep = "." if digits.rfind(".") > digits.rfind(",") else ","
        thou_sep = "," if dec_sep == "." else "."
        digits = digits.replace(thou_sep, "").replace(dec_sep, ".")
    elif "," in digits:
        tail = digits.rsplit(",", 1)[1]
        digits = digits.replace(",", "") if len(tail) == 3 else digits.replace(",", ".")
    elif "." in digits:
        tail = digits.rsplit(".", 1)[1]
        if len(tail) == 3 and digits.count(".") >= 1 and len(digits.split(".")[0]) <= 3:
            digits = digits.replace(".", "")
    try:
        number = float(digits)
    except ValueError:
        return None
    return -number if negative and number > 0 else number


def parse_date(value: Any) -> Optional[str]:
    """Normalise a date to YYYY-MM-DD, or return None if it isn't one."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _iso(y, mo, d)

    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if m:
        a, b, y = (int(g) for g in m.groups())
        # dd/mm vs mm/dd is genuinely ambiguous; disambiguate where we can.
        if a > 12:
            return _iso(y, b, a)
        if b > 12:
            return _iso(y, a, b)
        return _iso(y, b, a)  # default to day-first, matching most of the corpus

    m = re.fullmatch(r"([A-Za-zÄÖÜäöü]+)\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))

    m = re.fullmatch(r"(\d{1,2})\.?\s+([A-Za-zÄÖÜäöü]+)\.?\s+(\d{4})", text)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))

    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    if re.fullmatch(r"\d{4}", text):
        return text
    return None


def _iso(y: int, m: int, d: int) -> Optional[str]:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def find_dates(text: str) -> set[str]:
    """Every date-looking token in a document, normalised."""
    patterns = [
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b",
        r"\b[A-Za-zÄÖÜäöü]{3,9}\.?\s+\d{1,2},?\s+\d{4}\b",
        r"\b\d{1,2}\.?\s+[A-Za-zÄÖÜäöü]{3,9}\.?\s+\d{4}\b",
        r"\b(?:19|20)\d{2}\b",
    ]
    found: set[str] = set()
    for pattern in patterns:
        for match in re.findall(pattern, text):
            iso = parse_date(match if isinstance(match, str) else match[0])
            if iso:
                found.add(iso)
                found.add(iso[:7])
                found.add(iso[:4])
    return found


def find_numbers(text: str) -> set[float]:
    """Every number-looking token in a document, as floats."""
    out: set[float] = set()
    for token in re.findall(r"-?\d[\d.,\s]*\d|-?\d", text):
        parsed = parse_number(token)
        if parsed is not None:
            out.add(round(parsed, 2))
            out.add(round(abs(parsed), 2))
    return out
