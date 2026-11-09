"""Three-way reconciliation per charge key: expected, posted and collected.

expected   what the billing side meant to charge, {key: cents}
posted     what the charge files put on the ledger, from the store
collected  what the bank kept, from the replayed ACH payments

A key is (property, unit, resident, charge code, period). Every key whose
expected and collected amounts differ is either listed as pending (a payment
is still moving) or carries at least one finding. A key whose three amounts
agree carries none, except a CONFLICT, which is raised even when the amount
in force happens to agree.
"""

from dataclasses import dataclass

from .money import format_cents

NOT_POSTED = "NOT_POSTED"
UNEXPECTED_POSTING = "UNEXPECTED_POSTING"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
NOT_COLLECTED = "NOT_COLLECTED"
UNDERCOLLECTED = "UNDERCOLLECTED"
OVERCOLLECTED = "OVERCOLLECTED"
COLLECTED_NOT_POSTED = "COLLECTED_NOT_POSTED"
CONFLICT = "CONFLICT"
ALL_KINDS = (
    NOT_POSTED, UNEXPECTED_POSTING, AMOUNT_MISMATCH, NOT_COLLECTED,
    UNDERCOLLECTED, OVERCOLLECTED, COLLECTED_NOT_POSTED, CONFLICT,
)


@dataclass(frozen=True)
class Finding:
    key: tuple
    kind: str
    expected_cents: int
    posted_cents: int
    collected_cents: int
    detail: str


@dataclass(frozen=True)
class Report:
    findings: tuple
    pending: tuple
    expected_total: int
    posted_total: int
    collected_total: int
    ach_violations: tuple
    quarantined: dict

    def kinds_by_key(self):
        out = {}
        for f in self.findings:
            out.setdefault(f.key, set()).add(f.kind)
        return out


def collected_by_key(payments):
    """{key: (collected cents, any payment pending, any payment finally failed, payment count)}"""
    out = {}
    for p in payments.values():
        c, pend, failed, n = out.get(p.key, (0, False, False, 0))
        out[p.key] = (c + p.collected_cents, pend or p.pending, failed or p.final_failure, n + 1)
    return out


def reconcile(expected, posted, payments, conflicts=(), ach_violations=(), quarantined=None):
    for key, cents in expected.items():
        if cents <= 0:
            raise ValueError("expected amount for %r must be positive, got %d" % (key, cents))
    paid = collected_by_key(payments)
    conflicts = set(conflicts)
    findings, pending = [], []
    for key in sorted(set(expected) | set(posted) | set(paid) | conflicts):
        e = expected.get(key, 0)
        p = posted.get(key, 0)
        c, is_pending, failed, n = paid.get(key, (0, False, False, 0))

        def add(kind, detail):
            findings.append(Finding(key, kind, e, p, c, detail))

        if key in conflicts:
            add(CONFLICT, "files with the same as_of disagree on the amount")
        if key in expected and key not in posted:
            add(NOT_POSTED, "expected %s, no charge on the ledger" % format_cents(e))
        elif key in posted and key not in expected:
            add(UNEXPECTED_POSTING, "posted %s, nothing was expected" % format_cents(p))
        elif key in posted and p != e:
            add(AMOUNT_MISMATCH, "expected %s, posted %s" % (format_cents(e), format_cents(p)))
        if key in posted:
            if c > max(p, 0):
                add(OVERCOLLECTED, "collected %s against a posted %s" % (format_cents(c), format_cents(p)))
            elif p > 0 and not is_pending:
                if c == 0:
                    add(NOT_COLLECTED, "no payment" if n == 0 else "payment finally returned")
                elif c < p:
                    add(UNDERCOLLECTED, "collected %s of a posted %s" % (format_cents(c), format_cents(p)))
        elif c > 0:
            add(COLLECTED_NOT_POSTED, "collected %s with nothing posted" % format_cents(c))
        if is_pending:
            pending.append(key)
    return Report(
        tuple(findings),
        tuple(pending),
        sum(expected.values()),
        sum(posted.values()),
        sum(v[0] for v in paid.values()),
        tuple(ach_violations),
        dict(quarantined or {}),
    )


def reconcile_store(expected, store, replayed):
    posted = {p.key: p.amount_cents for p in store.current_charges()}
    return reconcile(
        expected, posted, replayed.payments,
        conflicts=store.conflicts(), ach_violations=replayed.violations, quarantined=store.quarantined(),
    )


def render(report):
    lines = [
        "expected %s  posted %s  collected %s"
        % (format_cents(report.expected_total), format_cents(report.posted_total), format_cents(report.collected_total)),
        "%d finding(s), %d key(s) pending, %d ACH violation(s), %d quarantined file(s)"
        % (len(report.findings), len(report.pending), len(report.ach_violations), len(report.quarantined)),
    ]
    for f in report.findings:
        lines.append("  %-20s %s  %s" % (f.kind, "/".join(f.key), f.detail))
    for v in report.ach_violations:
        lines.append("  ACH %s %s: %s" % (v.payment_id, v.event_id, v.reason))
    for (prop, sha, as_of), problems in sorted(report.quarantined.items()):
        lines.append("  QUARANTINED %s %s as_of %s: %s" % (prop, sha[:12], as_of, "; ".join(problems)))
    return "\n".join(lines)
