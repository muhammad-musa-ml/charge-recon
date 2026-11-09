"""Read three charge-file layouts into rows, and say exactly what could not be read.

csv_a   comma separated, header row, MM/DD/YYYY dates, a TOTAL footer row
pipe_b  pipe separated, header row that may repeat on every page, YYYYMMDD
        dates, a T|count|total trailer
fixed_c fixed width (36 columns), HDR line first, cents with no decimal point,
        a TRL trailer

A row that cannot be read is a Reject with its line number. Nothing is dropped
quietly, and a file with no trailer is reported as possibly cut short.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date

from .money import MoneyError, parse_amount, parse_implied_cents

MIN_YEAR, MAX_YEAR = 2000, 2100


class FileError(ValueError):
    """The file as a whole cannot be read."""


class UnknownDialect(FileError):
    pass


class UndecodableFile(FileError):
    pass


@dataclass(frozen=True)
class ChargeRow:
    line_no: int
    unit: str
    resident_ref: str
    code: str
    amount_cents: int
    post_date: date

    @property
    def period(self):
        return "%04d-%02d" % (self.post_date.year, self.post_date.month)

    @property
    def key(self):
        return (self.unit, self.resident_ref, self.code, self.period)


@dataclass(frozen=True)
class Reject:
    line_no: int
    reason: str


@dataclass
class ParsedFile:
    dialect: str
    encoding: str
    rows: list = field(default_factory=list)
    rejects: list = field(default_factory=list)
    control_total_cents: object = None
    control_count: object = None
    trailer_seen: bool = False

    @property
    def sum_cents(self):
        return sum(r.amount_cents for r in self.rows)

    def problems(self):
        """Reasons the file must not be posted. Empty means it can be."""
        out = ["line %d: %s" % (r.line_no, r.reason) for r in self.rejects]
        if not self.trailer_seen:
            out.append("no control total: the file may be cut short")
        elif not self.rejects:
            if self.control_total_cents is not None and self.control_total_cents != self.sum_cents:
                out.append(
                    "control total %d does not match the rows, which sum to %d"
                    % (self.control_total_cents, self.sum_cents)
                )
            if self.control_count is not None and self.control_count != len(self.rows):
                out.append(
                    "control count %d does not match the %d rows read"
                    % (self.control_count, len(self.rows))
                )
        return out


def decode(raw):
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as exc:
            raise UndecodableFile("not valid UTF-16: %s" % exc)
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252"), "cp1252"
    except UnicodeDecodeError as exc:
        raise UndecodableFile("not UTF-8 or Windows-1252: %s" % exc)


def split_lines(text):
    # str.splitlines also breaks on U+2028, U+0085 and form feeds; a name with
    # one of those in it must not turn one row into two.
    return re.split(r"\r\n|\r|\n", text)


def norm_unit(text):
    s = re.sub(r"\s+", "", text).upper()
    if s and s.isascii() and s.isdigit():
        s = s.lstrip("0") or "0"
    return s


def norm_ref(text):
    return re.sub(r"\s+", " ", text).strip().upper()


def norm_code(text):
    s = text.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,8}", s):
        raise ValueError("bad charge code %r" % text)
    return s


def _build_date(y, m, d):
    if not (MIN_YEAR <= y <= MAX_YEAR):
        raise ValueError("year %d out of range" % y)
    try:
        return date(y, m, d)
    except ValueError:
        raise ValueError("no such date %04d-%02d-%02d" % (y, m, d))


def parse_us_date(text):
    m = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})", text.strip())
    if not m:
        raise ValueError("not a MM/DD/YYYY date: %r" % text)
    return _build_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def parse_compact_date(text):
    m = re.fullmatch(r"([0-9]{4})([0-9]{2})([0-9]{2})", text.strip())
    if not m:
        raise ValueError("not a YYYYMMDD date: %r" % text)
    return _build_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _make_row(line_no, unit, ref, code, amount_cents, post_date):
    unit = norm_unit(unit)
    ref = norm_ref(ref)
    if not unit:
        raise ValueError("missing unit")
    if not ref:
        raise ValueError("missing resident reference")
    return ChargeRow(line_no, unit, ref, norm_code(code), amount_cents, post_date)


def detect(text):
    for line in split_lines(text):
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if s.startswith("HDR"):
            return "fixed_c"
        if "|" in s and low.startswith("unitno"):
            return "pipe_b"
        if "," in s and "charge code" in low:
            return "csv_a"
        raise UnknownDialect("first line matches no known layout: %r" % s[:60])
    raise UnknownDialect("file is empty")


def parse(raw):
    text, encoding = decode(raw)
    dialect = detect(text)
    parsed = {"csv_a": _parse_csv_a, "pipe_b": _parse_pipe_b, "fixed_c": _parse_fixed_c}[dialect](
        text, ParsedFile(dialect, encoding)
    )
    _drop_duplicate_keys(parsed)
    return parsed


def _drop_duplicate_keys(parsed):
    first = {}
    kept = []
    for row in parsed.rows:
        if row.key in first:
            parsed.rejects.append(
                Reject(row.line_no, "duplicate key in file, first seen at line %d" % first[row.key])
            )
        else:
            first[row.key] = row.line_no
            kept.append(row)
    parsed.rows = kept


CSV_COLUMNS = ("unit", "resident id", "charge code", "amount", "post date")


def _parse_csv_a(text, out):
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    index = None
    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            out.rejects.append(Reject(reader.line_num, "bad quoting: %s" % exc))
            continue
        line_no = reader.line_num
        if not any(c.strip() for c in cells):
            continue
        names = [c.strip().lower() for c in cells]
        if index is None:
            header = names
            index = {n: i for i, n in enumerate(names)}
            missing = [c for c in CSV_COLUMNS if c not in index]
            if missing:
                raise UnknownDialect("csv header lacks %s" % ", ".join(missing))
            width = len(names)
            continue
        if names == header:
            continue  # header repeated on a new page
        if names[0] == "total":
            if out.trailer_seen:
                out.rejects.append(Reject(line_no, "second TOTAL row"))
                continue
            out.trailer_seen = True
            try:
                if len(cells) != width:
                    raise ValueError("TOTAL row has %d columns, header has %d" % (len(cells), width))
                out.control_total_cents = parse_amount(cells[index["amount"]])
            except (MoneyError, ValueError) as exc:
                out.rejects.append(Reject(line_no, "bad TOTAL row: %s" % exc))
            continue
        if out.trailer_seen:
            out.rejects.append(Reject(line_no, "row after the TOTAL row"))
            continue
        if len(cells) != width:
            out.rejects.append(Reject(line_no, "expected %d columns, got %d" % (width, len(cells))))
            continue
        try:
            out.rows.append(
                _make_row(
                    line_no,
                    cells[index["unit"]],
                    cells[index["resident id"]],
                    cells[index["charge code"]],
                    parse_amount(cells[index["amount"]]),
                    parse_us_date(cells[index["post date"]]),
                )
            )
        except (MoneyError, ValueError) as exc:
            out.rejects.append(Reject(line_no, str(exc)))
    return out


PIPE_HEADER = ["unitno", "tenantref", "code", "amt", "postedon"]


def _parse_pipe_b(text, out):
    seen_header = False
    for line_no, line in enumerate(split_lines(text), start=1):
        s = line.strip()
        if not s:
            continue
        cells = [c.strip() for c in s.split("|")]
        if [c.lower() for c in cells] == PIPE_HEADER:
            seen_header = True
            continue
        if not seen_header:
            raise UnknownDialect("pipe file does not start with the UnitNo header")
        if cells[0] == "T" and len(cells) != 5:
            if out.trailer_seen:
                out.rejects.append(Reject(line_no, "second trailer"))
                continue
            out.trailer_seen = True
            try:
                if len(cells) != 3 or not re.fullmatch(r"[0-9]+", cells[1]):
                    raise ValueError("trailer must be T|count|total")
                out.control_count = int(cells[1])
                out.control_total_cents = parse_amount(cells[2])
            except (MoneyError, ValueError) as exc:
                out.rejects.append(Reject(line_no, "bad trailer: %s" % exc))
            continue
        if out.trailer_seen:
            out.rejects.append(Reject(line_no, "row after the trailer"))
            continue
        if len(cells) != 5:
            out.rejects.append(Reject(line_no, "expected 5 fields, got %d" % len(cells)))
            continue
        try:
            out.rows.append(
                _make_row(
                    line_no, cells[0], cells[1], cells[2],
                    parse_amount(cells[3]), parse_compact_date(cells[4]),
                )
            )
        except (MoneyError, ValueError) as exc:
            out.rejects.append(Reject(line_no, str(exc)))
    return out


FIXED_WIDTH = 36


def _parse_fixed_c(text, out):
    first = True
    for line_no, line in enumerate(split_lines(text), start=1):
        s = line.rstrip()
        if not s.strip():
            continue
        if first:
            first = False
            continue  # the HDR line
        if len(s) == 21 and s.startswith("TRL"):
            if out.trailer_seen:
                out.rejects.append(Reject(line_no, "second trailer"))
                continue
            out.trailer_seen = True
            try:
                out.control_count = parse_implied_cents(s[3:9])
                out.control_total_cents = parse_implied_cents(s[9:21])
            except MoneyError as exc:
                out.rejects.append(Reject(line_no, "bad trailer: %s" % exc))
            continue
        if out.trailer_seen:
            out.rejects.append(Reject(line_no, "row after the trailer"))
            continue
        if len(s) != FIXED_WIDTH:
            out.rejects.append(Reject(line_no, "expected %d columns, got %d" % (FIXED_WIDTH, len(s))))
            continue
        try:
            out.rows.append(
                _make_row(
                    line_no, s[0:6], s[6:14], s[14:18],
                    parse_implied_cents(s[18:28]), parse_compact_date(s[28:36]),
                )
            )
        except (MoneyError, ValueError) as exc:
            out.rejects.append(Reject(line_no, str(exc)))
    return out
