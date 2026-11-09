"""A seeded synthetic billing quarter: what should be charged, the messy files
and payment events that report it, and the findings each injected fault must
cause.

Three properties, each with a system of record that exports one layout. Every
(property, month) gets one full charge file. Some rows are first sent with a
stale amount and corrected by a later file; one file in some seeds arrives
with a corrupt row and is replaced by a corrected one; files and events are
delivered more than once and out of order.

Everything here is synthetic. Nothing is read from a real property manager.
"""

import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import reconcile as rc
from .ach import Event
from .money import format_cents

PROPERTIES = {"P1": "csv_a", "P2": "pipe_b", "P3": "fixed_c"}
PERIODS = ["2026-07", "2026-08", "2026-09"]

# scenario -> the exact set of findings it must produce
LABELS = {
    "clean": frozenset(),
    "r01_retry_ok": frozenset(),
    "in_flight": frozenset(),
    "return_after_window": frozenset(),
    "r01_exhausted": frozenset({rc.NOT_COLLECTED}),
    "r02_final": frozenset({rc.NOT_COLLECTED}),
    "late_r10": frozenset({rc.NOT_COLLECTED}),
    "drop_posting": frozenset({rc.NOT_POSTED}),
    "wrong_up": frozenset({rc.AMOUNT_MISMATCH, rc.UNDERCOLLECTED}),
    "wrong_down": frozenset({rc.AMOUNT_MISMATCH, rc.OVERCOLLECTED}),
    "unexpected_posting": frozenset({rc.UNEXPECTED_POSTING, rc.NOT_COLLECTED}),
}
# (scenario, cumulative probability) for a key that has an expected charge
MIX = [
    ("drop_posting", 0.02),
    ("wrong_up", 0.035),
    ("wrong_down", 0.05),
    ("r01_exhausted", 0.07),
    ("r02_final", 0.09),
    ("late_r10", 0.11),
    ("return_after_window", 0.12),
    ("r01_retry_ok", 0.16),
    ("in_flight", 0.18),
]


@dataclass(frozen=True)
class Row:
    unit_no: int
    ref: str
    code: str
    cents: int
    post_date: date


@dataclass(frozen=True)
class Delivery:
    property_id: str
    name: str
    raw: bytes
    as_of: str


@dataclass
class Truth:
    expected: dict
    labels: dict  # key -> frozenset of finding kinds, only for keys that have one
    pending: set
    scenarios: dict
    violations: int
    quarantined_files: int
    injected: Counter = field(default_factory=Counter)


@dataclass
class World:
    seed: int
    truth: Truth
    deliveries: list
    events: list


# --- rendering ---------------------------------------------------------------


def _commas(cents):
    return "{:,}.{:02d}".format(cents // 100, cents % 100)


def _csv_amount(cents, rng):
    if cents >= 100000:
        return '"%s"' % _commas(cents)
    style = rng.choice(["plain", "dollar", "quoted"])
    text = format_cents(cents)
    return {"plain": text, "dollar": "$" + text, "quoted": '"%s"' % text}[style]


def _unit_text(unit_no, rng):
    return rng.choice(["%04d" % unit_no, str(unit_no), " %04d " % unit_no])


def _us_date(d, rng):
    return ("%02d/%02d/%d" if rng.random() < 0.5 else "%d/%d/%d") % (d.month, d.day, d.year)


def render_csv(rows, rng, corrupt=None):
    eol = rng.choice(["\r\n", "\n"])
    head = "Unit,Resident ID,Charge Code,Amount,Post Date"
    out = [head]
    for i, r in enumerate(rows):
        if i and i % 25 == 0:
            out.append(head)
        if rng.random() < 0.02:
            out.append("")
        amount = _csv_amount(r.cents, rng)
        if i == corrupt:
            amount = format_cents(r.cents) + "5"
        ref = r.ref.lower() if rng.random() < 0.2 else r.ref
        out.append(",".join([_unit_text(r.unit_no, rng), ref, r.code, amount, _us_date(r.post_date, rng)]))
    out.append('TOTAL,,,"%s",' % _commas(sum(r.cents for r in rows)))
    raw = (eol.join(out) + eol).encode("utf-8")
    return (b"\xef\xbb\xbf" + raw) if rng.random() < 0.5 else raw


def render_pipe(rows, rng, corrupt=None):
    head = "UnitNo|TenantRef|Code|Amt|PostedOn"
    out = [head]
    for i, r in enumerate(rows):
        if i and i % 25 == 0:
            out += ["", head.lower() if rng.random() < 0.5 else head]
        amount = format_cents(r.cents) + ("5" if i == corrupt else "")
        out.append("|".join([_unit_text(r.unit_no, rng), r.ref, r.code, amount, r.post_date.strftime("%Y%m%d")]))
    out.append("T|%d|%s" % (len(rows), format_cents(sum(r.cents for r in rows))))
    return ("\r\n".join(out) + "\r\n").encode("ascii")


def render_fixed(rows, rng, corrupt=None):
    out = ["HDR20260731"]
    for i, r in enumerate(rows):
        when = "20260231" if i == corrupt else r.post_date.strftime("%Y%m%d")
        out.append(str(r.unit_no).ljust(6) + r.ref.ljust(8) + r.code.ljust(4) + ("%010d" % r.cents) + when)
    out.append("TRL%06d%012d" % (len(rows), sum(r.cents for r in rows)))
    return ("\r\n".join(out) + "\r\n").encode("ascii")


RENDER = {"csv_a": render_csv, "pipe_b": render_pipe, "fixed_c": render_fixed}


# --- the world ---------------------------------------------------------------


def _first_of(period):
    y, m = period.split("-")
    return date(int(y), int(m), 1)


def _export_time(period, day_offset, hh, mm):
    d = (_first_of(period) + timedelta(days=31)).replace(day=3) + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hh, mm).isoformat(timespec="seconds")


def _pick(rng):
    x = rng.random()
    for name, upto in MIX:
        if x < upto:
            return name
    return "clean"


def build(seed, units=40):
    rng = random.Random(seed)
    expected, labels, scenarios, pending = {}, {}, {}, set()
    posted = {}  # key -> (cents as posted, stale cents or None)
    events = []
    counter = [0]
    violations = 0
    injected = Counter()

    def add_events(pid, key, amount, d0, steps):
        n = [0]

        def e(kind, day, code=""):
            n[0] += 1
            return Event(
                "%s:%d" % (pid, n[0]), pid, kind, d0 + timedelta(days=day), code,
                amount if kind == "created" else 0, key if kind == "created" else (),
            )

        events.append(e("created", 0))
        for kind, day, code in steps:
            events.append(e(kind, day, code))

    for pi, prop in enumerate(PROPERTIES):
        for u in range(units):
            unit_no, ref = 101 + u, "R%d%03d" % (pi + 1, u + 1)
            enrolled = rng.random() < 0.7
            codes = []
            if enrolled:
                annual = rng.random() < 0.05
                codes.append(("ANN", 100000 + 500 * rng.randint(0, 60)) if annual else ("INS", 1200 + 50 * rng.randint(0, 25)))
                if rng.random() < 0.25:
                    codes.append(("TLL", 600 + 50 * rng.randint(0, 10)))
                if rng.random() < 0.10:
                    codes.append(("PET", 1000 + 50 * rng.randint(0, 50)))
            for pdx, period in enumerate(PERIODS):
                last = pdx == len(PERIODS) - 1
                for code, cents in codes:
                    if code == "ANN" and pdx > 0:
                        continue
                    key = (prop, str(unit_no), ref, code, period)
                    expected[key] = cents
                    name = _pick(rng)
                    if name == "in_flight" and not last:
                        name = "clean"
                    scenarios[key] = name
                    d0 = _first_of(period) + timedelta(days=1 + rng.randint(0, 3))
                    pid = "pay%05d" % (counter[0] + 1)
                    pay = name not in ("drop_posting",)
                    if pay:
                        counter[0] += 1
                    steps = {
                        "clean": [("submitted", 0, ""), ("settled", 2, "")],
                        "wrong_up": [("submitted", 0, ""), ("settled", 2, "")],
                        "wrong_down": [("submitted", 0, ""), ("settled", 2, "")],
                        "r01_retry_ok": [("submitted", 0, ""), ("returned", 2, "R01"), ("submitted", 6, ""), ("settled", 8, "")],
                        "r01_exhausted": [
                            ("submitted", 0, ""), ("returned", 2, "R01"), ("submitted", 6, ""),
                            ("returned", 8, "R01"), ("submitted", 12, ""), ("returned", 14, "R01"),
                        ],
                        "r02_final": [("submitted", 0, ""), ("returned", 2, "R02")],
                        "late_r10": [("submitted", 0, ""), ("settled", 2, ""), ("returned", 32, "R10")],
                        "return_after_window": [("submitted", 0, ""), ("settled", 2, ""), ("returned", 9, "R01")],
                        "in_flight": [("submitted", 0, "")],
                        "drop_posting": [],
                    }[name]
                    if pay:
                        add_events(pid, key, cents, d0, steps)
                    if name == "return_after_window":
                        violations += 1
                    if name == "in_flight":
                        pending.add(key)
                    if name != "drop_posting":
                        shown = cents
                        if name == "wrong_up":
                            shown = cents + 50 * rng.randint(1, 6)
                        elif name == "wrong_down":
                            shown = cents - 50 * rng.randint(1, 6)
                        stale = None
                        if name not in ("wrong_up", "wrong_down") and rng.random() < 0.05:
                            stale = cents + 50 * rng.randint(1, 6)
                            injected["resend_corrected"] += 1
                        posted[key] = (shown, stale, d0)
                    if LABELS[name]:
                        labels[key] = LABELS[name]
                    injected[name] += 1
                if not enrolled and rng.random() < 0.02:
                    key = (prop, str(unit_no), ref, "INS", period)
                    cents = 1200 + 50 * rng.randint(0, 25)
                    posted[key] = (cents, None, _first_of(period) + timedelta(days=1))
                    scenarios[key] = "unexpected_posting"
                    labels[key] = LABELS["unexpected_posting"]
                    injected["unexpected_posting"] += 1

    # files: one full file per (property, period), a correcting file when any row was stale
    deliveries, quarantined = [], 0
    bad_group = (rng.choice(list(PROPERTIES)), rng.choice(PERIODS)) if rng.random() < 0.8 else None
    for prop, layout in PROPERTIES.items():
        for pdx, period in enumerate(PERIODS):
            group = sorted((k for k in posted if k[0] == prop and k[4] == period), key=lambda k: (int(k[1]), k[3]))
            if not group:
                continue
            full = [Row(int(k[1]), k[2], k[3], posted[k][1] if posted[k][1] is not None else posted[k][0], posted[k][2]) for k in group]
            fix = [Row(int(k[1]), k[2], k[3], posted[k][0], posted[k][2]) for k in group if posted[k][1] is not None]
            render = RENDER[layout]
            deliveries.append(Delivery(prop, "%s-%s-full" % (prop, period), render(full, rng), _export_time(period, 0, 8, 0)))
            if fix:
                deliveries.append(Delivery(prop, "%s-%s-fix" % (prop, period), render(fix, rng), _export_time(period, 1, 8, 0)))
            if (prop, period) == bad_group:
                bad = render(full, rng, corrupt=rng.randrange(len(full)))
                deliveries.append(Delivery(prop, "%s-%s-bad" % (prop, period), bad, _export_time(period, 0, 7, 30)))
                quarantined += 1

    # delivery noise: repeats and out-of-order arrival
    extra = [d for d in deliveries if rng.random() < 0.3]
    deliveries = deliveries + extra
    rng.shuffle(deliveries)
    events = events + [ev for ev in events if rng.random() < 0.05]
    rng.shuffle(events)

    return World(seed, Truth(expected, labels, pending, scenarios, violations, quarantined, injected), deliveries, events)
