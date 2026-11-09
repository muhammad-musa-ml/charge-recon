"""Money as integer cents. This module creates no float on the way in or out.

An amount must fit a SQLite INTEGER (a signed 64-bit value), because that is
where the store keeps it; a larger one is refused here, not at the insert.
"""

import re

MAX_CENTS = 2**63 - 1

_PLAIN = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)?(?:\.[0-9]{1,2})?")


class MoneyError(ValueError):
    pass


def parse_amount(text):
    """Read an amount written by a person or a billing system into integer cents.

    Accepts 12, 12.5, 12.50, 1,234.50, $12.50, -12.50, (12.50) and 12.50-.
    Refuses anything with more than two decimals, a misplaced comma, two sign
    marks, or a digit that is not 0 to 9 (str.isdigit accepts other scripts).
    """
    if not isinstance(text, str):
        raise MoneyError("amount must be text, got %s" % type(text).__name__)
    s = text.strip()
    if not s:
        raise MoneyError("empty amount")
    negative = False
    marks = 0
    if s.startswith("(") and s.endswith(")"):
        negative = True
        marks += 1
        s = s[1:-1].strip()
    if s.endswith("-"):
        negative = True
        marks += 1
        s = s[:-1].strip()
    if s and s[0] in "+-":
        if s[0] == "-":
            negative = True
        marks += 1
        s = s[1:].strip()
    if s.startswith("$"):
        s = s[1:].strip()
    if marks > 1:
        raise MoneyError("more than one sign mark in %r" % text)
    if not s or not _PLAIN.fullmatch(s):
        raise MoneyError("not an amount: %r" % text)
    whole, _, frac = s.partition(".")
    if not whole and not frac:
        raise MoneyError("not an amount: %r" % text)
    cents = int(whole.replace(",", "") or "0") * 100 + int((frac + "00")[:2] or "0")
    if cents > MAX_CENTS:
        raise MoneyError("amount too large: %r" % text)
    return -cents if negative else cents


def parse_implied_cents(text):
    """Fixed-width fields carry cents with no decimal point: '0000045000' is 450.00."""
    s = text.strip()
    negative = s.startswith("-")
    if negative:
        s = s[1:]
    if not s or not re.fullmatch(r"[0-9]+", s):
        raise MoneyError("not a cents field: %r" % text)
    value = int(s)
    if value > MAX_CENTS:
        raise MoneyError("amount too large: %r" % text)
    return -value if negative else value


def format_cents(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "%s%d.%02d" % (sign, cents // 100, cents % 100)
