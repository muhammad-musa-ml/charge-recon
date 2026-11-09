import itertools
import random
import unittest
from datetime import date

from chargerecon.ach import (
    MAX_ATTEMPTS,
    RETURNED,
    SETTLED,
    SUBMITTED,
    Event,
    banking_days_between,
    replay,
)

KEY = ("P1", "101", "R-1", "INS", "2026-07")
FRI = date(2026, 7, 3)  # a Friday
assert FRI.weekday() == 4


def d(n):
    return date(2026, 7, n)


def ev(kind, on, i, code="", pid="pay1"):
    return Event(
        "%s-%s-%d" % (pid, kind, i), pid, kind, on, code,
        1450 if kind == "created" else 0, KEY if kind == "created" else (),
    )


def run(*events):
    return replay(list(events))


class BankingDays(unittest.TestCase):
    def test_counts_weekdays_only(self):
        self.assertEqual(banking_days_between(FRI, FRI), 0)
        self.assertEqual(banking_days_between(FRI, d(4)), 0)  # Saturday
        self.assertEqual(banking_days_between(FRI, d(6)), 1)  # Monday
        self.assertEqual(banking_days_between(FRI, d(7)), 2)  # Tuesday
        self.assertEqual(banking_days_between(FRI, d(8)), 3)


class Happy(unittest.TestCase):
    def test_settled_payment_is_collected(self):
        r = run(ev("created", d(1), 0), ev("submitted", d(1), 0), ev("settled", d(2), 0))
        p = r.payments["pay1"]
        self.assertEqual((p.state, p.attempt, p.collected_cents, p.pending), (SETTLED, 1, 1450, False))
        self.assertEqual(r.violations, ())

    def test_in_flight_is_pending_and_collects_nothing(self):
        p = run(ev("created", d(1), 0), ev("submitted", d(1), 0)).payments["pay1"]
        self.assertEqual((p.state, p.collected_cents, p.pending), (SUBMITTED, 0, True))


class Returns(unittest.TestCase):
    def test_r01_then_a_second_attempt_settles(self):
        r = run(
            ev("created", d(1), 0), ev("submitted", d(1), 0), ev("returned", d(3), 0, "R01"),
            ev("submitted", d(6), 1), ev("settled", d(7), 1),
        )
        p = r.payments["pay1"]
        self.assertEqual((p.state, p.attempt, p.collected_cents), (SETTLED, 2, 1450))
        self.assertEqual(r.violations, ())

    def test_r01_waiting_for_another_try_is_pending_not_failed(self):
        p = run(ev("created", d(1), 0), ev("submitted", d(1), 0), ev("returned", d(3), 0, "R01")).payments["pay1"]
        self.assertEqual((p.state, p.final_failure, p.pending), (RETURNED, False, True))

    def test_the_attempt_budget_is_spent_after_three_returns(self):
        events = [ev("created", d(1), 0)]
        day = 1
        for attempt in range(MAX_ATTEMPTS):
            events += [ev("submitted", d(day), attempt), ev("returned", d(day + 1), attempt, "R01")]
            day += 2
        r = run(*events)
        p = r.payments["pay1"]
        self.assertEqual((p.attempt, p.final_failure, p.collected_cents), (3, True, 0))
        again = run(*events, ev("submitted", d(day + 1), 9))
        self.assertEqual(len(again.violations), 1)
        self.assertIn("budget", again.violations[0].reason)
        self.assertEqual(again.payments["pay1"], p)

    def test_final_codes_allow_no_new_attempt(self):
        for code in ["R02", "R03", "R10", "R99"]:
            with self.subTest(code=code):
                r = run(
                    ev("created", d(1), 0), ev("submitted", d(1), 0), ev("returned", d(2), 0, code),
                    ev("submitted", d(3), 1),
                )
                self.assertTrue(r.payments["pay1"].final_failure)
                self.assertEqual(len(r.violations), 1)
                self.assertIn("final", r.violations[0].reason)

    def test_a_return_after_settlement_inside_the_window_takes_the_money_back(self):
        # settled Friday, R01 on Tuesday: two weekdays later
        r = run(ev("created", FRI, 0), ev("submitted", FRI, 0), ev("settled", FRI, 0), ev("returned", d(7), 0, "R01"))
        p = r.payments["pay1"]
        self.assertEqual((p.state, p.collected_cents), (RETURNED, 0))
        self.assertEqual(r.violations, ())

    def test_a_return_after_the_window_is_refused_and_the_payment_stays_settled(self):
        r = run(ev("created", FRI, 0), ev("submitted", FRI, 0), ev("settled", FRI, 0), ev("returned", d(8), 0, "R01"))
        self.assertEqual(r.payments["pay1"].state, SETTLED)
        self.assertEqual(len(r.violations), 1)
        self.assertIn("window", r.violations[0].reason)

    def test_r10_has_a_sixty_day_window(self):
        base = [ev("created", d(1), 0), ev("submitted", d(1), 0), ev("settled", d(2), 0)]
        inside = run(*base, Event("late", "pay1", "returned", date(2026, 8, 31), "R10"))  # 60 days
        self.assertEqual(inside.payments["pay1"].state, RETURNED)
        outside = run(*base, Event("late", "pay1", "returned", date(2026, 9, 1), "R10"))  # 61 days
        self.assertEqual(outside.payments["pay1"].state, SETTLED)
        self.assertEqual(len(outside.violations), 1)


class Illegal(unittest.TestCase):
    def test_each_illegal_move_is_a_violation_and_changes_nothing(self):
        created = [ev("created", d(1), 0)]
        cases = {
            "settle before submit": created + [ev("settled", d(2), 0)],
            "return from created": created + [ev("returned", d(2), 0, "R01")],
            "second created": created + [ev("created", d(2), 1)],
            "double settle": created + [ev("submitted", d(1), 0), ev("settled", d(2), 0), ev("settled", d(3), 1)],
            "submit while submitted": created + [ev("submitted", d(1), 0), ev("submitted", d(2), 1)],
            "return without a code": created + [ev("submitted", d(1), 0), ev("returned", d(2), 0, "")],
            "event for a payment never created": [ev("submitted", d(1), 0, pid="ghost")],
            "unknown kind": created + [Event("x", "pay1", "refunded", d(2))],
        }
        for name, events in cases.items():
            with self.subTest(name):
                r = run(*events)
                self.assertEqual(len(r.violations), 1, r.violations)

    def test_created_needs_an_amount_and_a_key(self):
        r = run(Event("c", "pay1", "created", d(1), "", 0, KEY))
        self.assertEqual(len(r.violations), 1)
        r = run(Event("c", "pay1", "created", d(1), "", 1450, ()))
        self.assertEqual(len(r.violations), 1)


class Delivery(unittest.TestCase):
    EVENTS = [
        ev("created", d(1), 0), ev("submitted", d(1), 0), ev("returned", d(3), 0, "R01"),
        ev("submitted", d(6), 1), ev("settled", d(7), 1),
    ]

    def test_duplicates_are_ignored(self):
        once = run(*self.EVENTS)
        twice = run(*self.EVENTS, *self.EVENTS, self.EVENTS[2])
        self.assertEqual(once, twice)

    def test_every_arrival_order_gives_the_same_result(self):
        expected = run(*self.EVENTS)
        for perm in itertools.permutations(self.EVENTS):
            self.assertEqual(run(*perm), expected)

    def test_shuffles_with_duplicates_and_a_late_return(self):
        events = self.EVENTS + [ev("returned", d(9), 2, "R01", pid="pay1")]
        expected = run(*events)
        rng = random.Random(7)
        for _ in range(200):
            mixed = events + rng.sample(events, 3)
            rng.shuffle(mixed)
            self.assertEqual(run(*mixed), expected)

    def test_an_id_reused_with_different_content_is_dropped_whichever_arrives_first(self):
        a = Event("e1", "pay1", "returned", d(3), "R01")
        b = Event("e1", "pay1", "returned", d(3), "R02")
        base = [ev("created", d(1), 0), ev("submitted", d(1), 0)]
        r1, r2 = run(*base, a, b), run(*base, b, a)
        self.assertEqual(r1, r2)
        self.assertEqual(r1.payments["pay1"].state, SUBMITTED)
        self.assertEqual([v.reason for v in r1.violations], ["event id reused with different content"])


if __name__ == "__main__":
    unittest.main()
