"""Append-only store of charge-file rows.

A file is identified by (property, sha256 of its bytes, as_of). Sending the same
bytes with the same as_of again changes nothing. The same bytes with a later
as_of are a new file: the system of record has said the same thing again, and
that later statement is the one in force.

Every row of every accepted file is kept as a sighting. The charge in force for
a key is the sighting from the file with the latest as_of, ties broken by file
hash. That is a function of the set of files, not of the order they arrived
in, so a replayed, reordered or late file cannot change the result.

A file is accepted whole or quarantined whole: a file with one unreadable row
or a wrong control total posts nothing.
"""

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .dialects import FileError, parse

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    property_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    as_of TEXT NOT NULL,
    source_name TEXT NOT NULL,
    dialect TEXT,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'quarantined')),
    row_count INTEGER NOT NULL,
    PRIMARY KEY (property_id, sha256, as_of)
);
CREATE TABLE IF NOT EXISTS file_problems (
    property_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    as_of TEXT NOT NULL,
    seq INTEGER NOT NULL,
    problem TEXT NOT NULL,
    PRIMARY KEY (property_id, sha256, as_of, seq),
    FOREIGN KEY (property_id, sha256, as_of) REFERENCES files (property_id, sha256, as_of)
);
CREATE TABLE IF NOT EXISTS sightings (
    property_id TEXT NOT NULL,
    file_sha TEXT NOT NULL,
    as_of TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    unit TEXT NOT NULL,
    resident_ref TEXT NOT NULL,
    code TEXT NOT NULL,
    period TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    post_date TEXT NOT NULL,
    PRIMARY KEY (property_id, file_sha, as_of, line_no),
    FOREIGN KEY (property_id, file_sha, as_of) REFERENCES files (property_id, sha256, as_of)
);
CREATE INDEX IF NOT EXISTS sightings_key
    ON sightings (property_id, unit, resident_ref, code, period);
CREATE VIEW IF NOT EXISTS ranked AS
    SELECT s.property_id, s.unit, s.resident_ref, s.code, s.period,
           s.amount_cents, s.post_date, s.file_sha, s.as_of,
           ROW_NUMBER() OVER (
               PARTITION BY s.property_id, s.unit, s.resident_ref, s.code, s.period
               ORDER BY s.as_of DESC, s.file_sha DESC
           ) AS rn
    FROM sightings s
    JOIN files f ON f.property_id = s.property_id AND f.sha256 = s.file_sha AND f.as_of = s.as_of
    WHERE f.status = 'accepted';
"""


@dataclass(frozen=True)
class Posting:
    property_id: str
    unit: str
    resident_ref: str
    code: str
    period: str
    amount_cents: int
    file_sha: str
    as_of: str

    @property
    def key(self):
        return (self.property_id, self.unit, self.resident_ref, self.code, self.period)


@dataclass(frozen=True)
class IngestResult:
    status: str  # accepted, quarantined or duplicate
    sha256: str
    problems: tuple
    row_count: int


def normalize_as_of(value):
    """Export time as a sortable UTC string. Naive times are taken as UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds")


class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def ingest(self, raw, property_id, source_name, as_of):
        as_of = normalize_as_of(as_of)
        sha = hashlib.sha256(raw).hexdigest()
        seen = self.db.execute(
            "SELECT 1 FROM files WHERE property_id = ? AND sha256 = ? AND as_of = ?",
            (property_id, sha, as_of),
        ).fetchone()
        if seen:
            return IngestResult("duplicate", sha, (), 0)
        dialect, rows, problems = None, [], []
        try:
            parsed = parse(raw)
        except FileError as exc:
            problems = [str(exc)]
        else:
            dialect = parsed.dialect
            problems = parsed.problems()
            rows = [] if problems else parsed.rows
        status = "quarantined" if problems else "accepted"
        with self.db:
            self.db.execute(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                (property_id, sha, as_of, source_name, dialect, status, len(rows)),
            )
            self.db.executemany(
                "INSERT INTO file_problems VALUES (?, ?, ?, ?, ?)",
                [(property_id, sha, as_of, i, p) for i, p in enumerate(problems)],
            )
            self._insert_sightings(property_id, sha, as_of, rows)
        return IngestResult(status, sha, tuple(problems), len(rows))

    def _insert_sightings(self, property_id, sha, as_of, rows):
        self.db.executemany(
            "INSERT INTO sightings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (property_id, sha, as_of, r.line_no, r.unit, r.resident_ref, r.code, r.period,
                 r.amount_cents, r.post_date.isoformat())
                for r in rows
            ],
        )

    def current_charges(self):
        cur = self.db.execute(
            "SELECT property_id, unit, resident_ref, code, period, amount_cents, file_sha, as_of "
            "FROM ranked WHERE rn = 1 ORDER BY property_id, unit, resident_ref, code, period"
        )
        return [Posting(*row) for row in cur]

    def conflicts(self):
        """Keys where two files with the same latest as_of disagree on the amount."""
        cur = self.db.execute(
            "WITH tops AS (SELECT property_id, unit, resident_ref, code, period, MAX(as_of) AS m "
            "              FROM ranked GROUP BY property_id, unit, resident_ref, code, period) "
            "SELECT r.property_id, r.unit, r.resident_ref, r.code, r.period "
            "FROM ranked r JOIN tops t USING (property_id, unit, resident_ref, code, period) "
            "WHERE r.as_of = t.m "
            "GROUP BY r.property_id, r.unit, r.resident_ref, r.code, r.period "
            "HAVING COUNT(DISTINCT r.amount_cents) > 1 "
            "ORDER BY 1, 2, 3, 4, 5"
        )
        return [tuple(row) for row in cur]

    def quarantined(self):
        """{(property, sha256, as_of): [problem, ...]} for every quarantined file."""
        cur = self.db.execute(
            "SELECT property_id, sha256, as_of, problem FROM file_problems "
            "ORDER BY property_id, as_of, sha256, seq"
        )
        out = {}
        for prop, sha, as_of, problem in cur:
            out.setdefault((prop, sha, as_of), []).append(problem)
        return out

    def digest(self):
        """Hash of what the store means. The first file name seen for a file is
        bookkeeping that depends on arrival order, so it is left out."""
        h = hashlib.sha256()
        for table, columns, order in [
            ("files", "property_id, sha256, as_of, dialect, status, row_count", "property_id, sha256, as_of"),
            ("file_problems", "*", "property_id, sha256, as_of, seq"),
            ("sightings", "*", "property_id, file_sha, as_of, line_no"),
        ]:
            for row in self.db.execute("SELECT %s FROM %s ORDER BY %s" % (columns, table, order)):
                h.update(repr((table,) + tuple(row)).encode("utf-8"))
        return h.hexdigest()
