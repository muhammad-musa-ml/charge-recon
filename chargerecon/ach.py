"""ACH payment lifecycle as a state machine, replayed from processor events.

created -> submitted -> settled -> returned, with a returned payment allowed
back to submitted only when its return code allows another try and the attempt
budget is not spent.

replay() is a fold over the whole event list, sorted by (date, lifecycle step,
event id) and de-duplicated by event id, so the order events arrive in cannot
change the outcome. An event that does not fit the machine is skipped and
reported as a Violation; it never changes a payment.

The return rules below are settings of this model, not a statement of the Nacha
rules: R01 may be presented again until three attempts are spent, R02, R03 and
R10 are final, and the windows count weekdays only (no bank holidays).
"""

from dataclasses import dataclass
from datetime import date, timedelta

CREATED, SUBMITTED, SETTLED, RETURNED = "created", "submitted", "settled", "returned"
STEP = {"created": 0, "submitted": 1, "settled": 2, "returned": 3}
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ReturnRule:
    window_kind: str  # "banking" or "calendar"
    window_days: int
    retriable: bool


RETURN_RULES = {
    "R01": ReturnRule("banking", 2, True),
    "R02": ReturnRule("banking", 2, False),
    "R03": ReturnRule("banking", 2, False),
    "R10": ReturnRule("calendar", 60, False),
}
UNKNOWN_RETURN = ReturnRule("banking", 2, False)


def rule_for(code):
    return RETURN_RULES.get(code, UNKNOWN_RETURN)


def banking_days_between(start, end):
    """Weekdays after `start` up to and including `end`."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def within_window(rule, settled_on, returned_on):
    if rule.window_kind == "calendar":
        return (returned_on - settled_on).days <= rule.window_days
    return banking_days_between(settled_on, returned_on) <= rule.window_days


@dataclass(frozen=True)
class Event:
    event_id: str
    payment_id: str
    kind: str
    on: date
    code: str = ""  # return code, on returned events
    amount_cents: int = 0  # on created events
    key: tuple = ()  # the charge this payment collects, on created events


@dataclass(frozen=True)
class Payment:
    payment_id: str
    key: tuple
    amount_cents: int
    state: str
    attempt: int
    settled_on: object
    return_code: str

    @property
    def collected_cents(self):
        return self.amount_cents if self.state == SETTLED else 0

    @property
    def final_failure(self):
        if self.state != RETURNED:
            return False
        return not rule_for(self.return_code).retriable or self.attempt >= MAX_ATTEMPTS

    @property
    def pending(self):
        """Not settled and not finally failed: still moving, or waiting for another try."""
        return self.state != SETTLED and not self.final_failure


@dataclass(frozen=True)
class Violation:
    payment_id: str
    event_id: str
    reason: str


@dataclass(frozen=True)
class Replay:
    payments: dict
    violations: tuple


def _step(payment, event):
    """Return the next Payment, or a string saying why the event does not fit."""
    kind = event.kind
    if payment is None:
        if kind != "created":
            return "payment has no created event"
        if event.amount_cents <= 0 or not event.key:
            return "created event needs a positive amount and a charge key"
        return Payment(event.payment_id, event.key, event.amount_cents, CREATED, 0, None, "")
    state = payment.state
    if kind == "created":
        return "payment already created"
    if kind == "submitted":
        if state == CREATED:
            return Payment(payment.payment_id, payment.key, payment.amount_cents, SUBMITTED, 1, None, "")
        if state == RETURNED:
            rule = rule_for(payment.return_code)
            if not rule.retriable:
                return "%s is final, no new attempt" % (payment.return_code or "return")
            if payment.attempt >= MAX_ATTEMPTS:
                return "attempt budget of %d is spent" % MAX_ATTEMPTS
            return Payment(
                payment.payment_id, payment.key, payment.amount_cents, SUBMITTED, payment.attempt + 1, None, ""
            )
        return "cannot submit from %s" % state
    if kind == "settled":
        if state != SUBMITTED:
            return "cannot settle from %s" % state
        return Payment(
            payment.payment_id, payment.key, payment.amount_cents, SETTLED, payment.attempt, event.on, ""
        )
    if kind == "returned":
        if not event.code:
            return "returned event has no return code"
        if state == SUBMITTED:
            return Payment(
                payment.payment_id, payment.key, payment.amount_cents, RETURNED, payment.attempt, None, event.code
            )
        if state == SETTLED:
            rule = rule_for(event.code)
            if not within_window(rule, payment.settled_on, event.on):
                return "%s arrived after its %d-%s-day window" % (event.code, rule.window_days, rule.window_kind)
            return Payment(
                payment.payment_id, payment.key, payment.amount_cents, RETURNED, payment.attempt, None, event.code
            )
        return "cannot return from %s" % state
    return "unknown event kind %r" % kind


def replay(events):
    by_id = {}
    for e in events:
        by_id.setdefault(e.event_id, set()).add(e)
    violations = []
    usable = []
    for event_id, versions in by_id.items():
        if len(versions) > 1:
            violations.append(
                Violation(sorted(v.payment_id for v in versions)[0], event_id, "event id reused with different content")
            )
        else:
            usable.append(next(iter(versions)))
    usable.sort(key=lambda e: (e.on, STEP.get(e.kind, 9), e.event_id))
    payments = {}
    for e in usable:
        result = _step(payments.get(e.payment_id), e)
        if isinstance(result, str):
            violations.append(Violation(e.payment_id, e.event_id, result))
        else:
            payments[e.payment_id] = result
    violations.sort(key=lambda v: (v.payment_id, v.event_id, v.reason))
    return Replay(payments, tuple(violations))
