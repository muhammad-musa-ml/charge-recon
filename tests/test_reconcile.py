import unittest
from datetime import date

from chargerecon import reconcile as rc
from chargerecon.ach import Event, replay
from chargerecon.store import Store
from tests.test_store import JUL, JUL_FIXED, T1, make_csv

KEY = ("P1", "101", "R-1", "INS", "2026-07")
KEY2 = ("P1", "102", "R-2", "INS", "2026-07")
AMOUNT = 1450


def events_for(key, outcome, amount=AMOUNT, pid="p"):
    def e(kind, day, code=""):
        return Event(
            "%s-%s-%s-%s" % (pid, kind, day, code), pid, kind, date(2026, 7, day), code,
            amount if kind == "created" else 0, key if kind == "created" else (),
        )

    created, sub = e("created", 1), e("submitted", 1)
    return {
        "settled": [created, sub, e("settled", 2)],
        "r02": [created, sub, e("returned", 3, "R02")],
        "late_r10": [created, sub, e("settled", 2), e("returned", 20, "R10")],
        "inflight": [created, sub],
        "r01_wait": [created, sub, e("returned", 3, "R01")],
    }[outcome]


def run(expected, posted, payment_outcomes=(), conflicts=()):
    events = []
    for i, (key, outcome) in enumerate(payment_outcomes):
        events += events_for(key, outcome, pid="p%d" % i)
    replayed = replay(events)
    return rc.reconcile(expected, posted, replayed.payments, conflicts=conflicts)


class Scenarios(unittest.TestCase):
    def kinds(self, report, key=KEY):
        return report.kinds_by_key().get(key, set())

    def test_everything_agrees(self):
        r = run({KEY: AMOUNT}, {KEY: AMOUNT}, [(KEY, "settled")])
        self.assertEqual((r.findings, r.pending), ((), ()))
        self.assertEqual((r.expected_total, r.posted_total, r.collected_total), (AMOUNT, AMOUNT, AMOUNT))

    def test_the_billing_side_expected_a_charge_that_never_reached_the_ledger(self):
        r = run({KEY: AMOUNT}, {})
        self.assertEqual(self.kinds(r), {rc.NOT_POSTED})

    def test_posted_with_nothing_expected_and_never_debited(self):
        r = run({}, {KEY: AMOUNT})
        self.assertEqual(self.kinds(r), {rc.UNEXPECTED_POSTING, rc.NOT_COLLECTED})

    def test_a_credit_with_nothing_expected_is_only_unexpected(self):
        r = run({}, {KEY: -250})
        self.assertEqual(self.kinds(r), {rc.UNEXPECTED_POSTING})

    def test_posted_higher_than_expected_while_we_collected_the_expected_amount(self):
        r = run({KEY: AMOUNT}, {KEY: AMOUNT + 50}, [(KEY, "settled")])
        self.assertEqual(self.kinds(r), {rc.AMOUNT_MISMATCH, rc.UNDERCOLLECTED})

    def test_posted_lower_than_expected_after_we_collected_the_expected_amount(self):
        r = run({KEY: AMOUNT}, {KEY: AMOUNT - 50}, [(KEY, "settled")])
        self.assertEqual(self.kinds(r), {rc.AMOUNT_MISMATCH, rc.OVERCOLLECTED})

    def test_a_final_return_means_not_collected(self):
        for outcome in ["r02", "late_r10"]:
            with self.subTest(outcome=outcome):
                r = run({KEY: AMOUNT}, {KEY: AMOUNT}, [(KEY, outcome)])
                self.assertEqual(self.kinds(r), {rc.NOT_COLLECTED})
                self.assertEqual(r.collected_total, 0)

    def test_a_payment_still_moving_is_pending_not_an_exception(self):
        for outcome in ["inflight", "r01_wait"]:
            with self.subTest(outcome=outcome):
                r = run({KEY: AMOUNT}, {KEY: AMOUNT}, [(KEY, outcome)])
                self.assertEqual((r.findings, r.pending), ((), (KEY,)))

    def test_money_taken_with_nothing_on_the_ledger(self):
        r = run({KEY: AMOUNT}, {}, [(KEY, "settled")])
        self.assertEqual(self.kinds(r), {rc.NOT_POSTED, rc.COLLECTED_NOT_POSTED})

    def test_money_taken_with_nothing_expected_or_posted(self):
        r = run({}, {}, [(KEY, "settled")])
        self.assertEqual(self.kinds(r), {rc.COLLECTED_NOT_POSTED})

    def test_a_conflict_is_reported_even_when_the_amounts_agree(self):
        r = run({KEY: AMOUNT}, {KEY: AMOUNT}, [(KEY, "settled")], conflicts=[KEY])
        self.assertEqual(self.kinds(r), {rc.CONFLICT})

    def test_two_payments_for_one_key_are_added_up(self):
        r = run({KEY: AMOUNT}, {KEY: AMOUNT}, [(KEY, "r02"), (KEY, "settled")])
        self.assertEqual((r.findings, r.collected_total), ((), AMOUNT))
        r = run({KEY: AMOUNT}, {KEY: AMOUNT}, [(KEY, "settled"), (KEY, "settled")])
        self.assertEqual(self.kinds(r), {rc.OVERCOLLECTED})

    def test_a_key_that_is_fine_is_not_touched_by_one_that_is_not(self):
        r = run(
            {KEY: AMOUNT, KEY2: AMOUNT}, {KEY: AMOUNT, KEY2: AMOUNT - 100},
            [(KEY, "settled"), (KEY2, "settled")],
        )
        self.assertEqual(self.kinds(r, KEY), set())
        self.assertEqual(self.kinds(r, KEY2), {rc.AMOUNT_MISMATCH, rc.OVERCOLLECTED})

    def test_expected_amounts_must_be_positive(self):
        with self.assertRaises(ValueError):
            run({KEY: 0}, {})


class TieOut(unittest.TestCase):
    def test_every_key_where_expected_and_collected_differ_is_explained(self):
        outcomes = ["settled", "r02", "late_r10", "inflight", "r01_wait", None]
        postings = [AMOUNT, AMOUNT + 50, AMOUNT - 50, None]
        n = 0
        for outcome in outcomes:
            for posted in postings:
                for expect in [AMOUNT, None]:
                    expected = {KEY: expect} if expect else {}
                    post = {KEY: posted} if posted else {}
                    r = run(expected, post, [(KEY, outcome)] if outcome else [])
                    e = expected.get(KEY, 0)
                    collected = r.collected_total
                    n += 1
                    if e != collected and KEY not in r.pending:
                        self.assertTrue(r.kinds_by_key().get(KEY), (outcome, posted, expect))
                    if e == posted == collected and e:
                        self.assertEqual(r.findings, (), (outcome, posted, expect))
        self.assertEqual(n, 48)


class WithTheStore(unittest.TestCase):
    def test_two_files_that_disagree_at_the_same_time_are_a_finding(self):
        store = Store()
        self.addCleanup(store.close)
        store.ingest(make_csv(JUL), "P1", "a.csv", T1)
        store.ingest(make_csv(JUL_FIXED), "P1", "b.csv", T1)
        report = rc.reconcile_store({KEY: AMOUNT}, store, replay([]))
        self.assertIn(rc.CONFLICT, report.kinds_by_key()[KEY])


    def test_files_and_events_to_a_report(self):
        store = Store()
        self.addCleanup(store.close)
        store.ingest(make_csv(JUL), "P1", "jul.csv", T1)
        store.ingest(b"junk\n", "P1", "junk.csv", T1)
        expected = {KEY: AMOUNT, KEY2: AMOUNT, ("P1", "103", "R-3", "INS", "2026-07"): AMOUNT}
        events = events_for(KEY, "settled", pid="a") + events_for(KEY2, "r02", pid="b")
        events.append(Event("orphan", "zzz", "settled", date(2026, 7, 9)))
        report = rc.reconcile_store(expected, store, replay(events))
        kinds = report.kinds_by_key()
        self.assertNotIn(KEY, kinds)
        self.assertEqual(kinds[KEY2], {rc.NOT_COLLECTED})
        self.assertEqual(kinds[("P1", "103", "R-3", "INS", "2026-07")], {rc.NOT_POSTED})
        self.assertEqual(len(report.ach_violations), 1)
        self.assertEqual(len(report.quarantined), 1)
        text = rc.render(report)
        self.assertIn("NOT_COLLECTED", text)
        self.assertIn("QUARANTINED", text)


if __name__ == "__main__":
    unittest.main()
