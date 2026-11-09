import hashlib
import itertools
import sqlite3
import unittest

from chargerecon.money import format_cents, parse_amount
from chargerecon.store import Store, normalize_as_of

P1, P2 = "P1", "P2"


def make_csv(rows):
    """rows: (unit, resident, code, amount text, MM/DD/YYYY)"""
    total = sum(parse_amount(r[3]) for r in rows)
    lines = ["Unit,Resident ID,Charge Code,Amount,Post Date"]
    lines += [",".join([r[0], r[1], r[2], '"%s"' % r[3], r[4]]) for r in rows]
    lines.append('TOTAL,,,"%s",' % format_cents(total))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


JUL = [("0101", "R-1", "INS", "14.50", "07/01/2026"), ("0102", "R-2", "INS", "14.50", "07/01/2026")]
JUL_FIXED = [("0101", "R-1", "INS", "15.00", "07/01/2026"), ("0102", "R-2", "INS", "14.50", "07/01/2026")]
T1, T2, T3 = "2026-07-02T08:00:00", "2026-07-03T08:00:00", "2026-07-04T08:00:00"


def amounts(store):
    return {(p.unit, p.code, p.period): p.amount_cents for p in store.current_charges()}


class StoreCase(unittest.TestCase):
    def new_store(self, cls=Store):
        s = cls()
        self.addCleanup(s.close)
        return s


class Ingest(StoreCase):
    def test_accepted_file_posts_its_rows(self):
        s = self.new_store()
        r = s.ingest(make_csv(JUL), P1, "jul.csv", T1)
        self.assertEqual((r.status, r.row_count, r.problems), ("accepted", 2, ()))
        self.assertEqual(amounts(s), {("101", "INS", "2026-07"): 1450, ("102", "INS", "2026-07"): 1450})

    def test_same_file_again_changes_nothing(self):
        s = self.new_store()
        s.ingest(make_csv(JUL), P1, "jul.csv", T1)
        before = s.digest()
        r = s.ingest(make_csv(JUL), P1, "jul-copy.csv", T1)
        self.assertEqual(r.status, "duplicate")
        self.assertEqual(s.digest(), before)
        self.assertEqual(s.db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0], 2)

    def test_same_bytes_later_is_the_property_manager_saying_it_again(self):
        s = self.new_store()
        s.ingest(make_csv(JUL), P1, "a.csv", T1)
        s.ingest(make_csv(JUL_FIXED), P1, "b.csv", T2)
        self.assertEqual(amounts(s)[("101", "INS", "2026-07")], 1500)
        s.ingest(make_csv(JUL), P1, "a-again.csv", T3)
        self.assertEqual(amounts(s)[("101", "INS", "2026-07")], 1450)

    def test_same_unit_in_another_property_is_another_charge(self):
        s = self.new_store()
        s.ingest(make_csv(JUL), P1, "a.csv", T1)
        s.ingest(make_csv(JUL_FIXED), P2, "b.csv", T1)
        by_prop = {(p.property_id, p.unit): p.amount_cents for p in s.current_charges()}
        self.assertEqual(by_prop[(P1, "101")], 1450)
        self.assertEqual(by_prop[(P2, "101")], 1500)

    def test_corrected_file_wins_and_history_is_kept(self):
        s = self.new_store()
        s.ingest(make_csv(JUL), P1, "a.csv", T1)
        s.ingest(make_csv(JUL_FIXED), P1, "b.csv", T2)
        self.assertEqual(amounts(s)[("101", "INS", "2026-07")], 1500)
        seen = s.db.execute("SELECT COUNT(*) FROM sightings WHERE unit = '101'").fetchone()[0]
        self.assertEqual(seen, 2)

    def test_a_late_older_file_does_not_overwrite_a_newer_one(self):
        s = self.new_store()
        s.ingest(make_csv(JUL_FIXED), P1, "b.csv", T2)
        s.ingest(make_csv(JUL), P1, "a.csv", T1)
        self.assertEqual(amounts(s)[("101", "INS", "2026-07")], 1500)

    def test_tie_on_as_of_is_decided_by_hash_and_reported(self):
        a, b = make_csv(JUL), make_csv(JUL_FIXED)
        s1, s2 = self.new_store(), self.new_store()
        s1.ingest(a, P1, "a", T1)
        s1.ingest(b, P1, "b", T1)
        s2.ingest(b, P1, "b", T1)
        s2.ingest(a, P1, "a", T1)
        self.assertEqual(amounts(s1), amounts(s2))
        self.assertEqual(s1.conflicts(), [(P1, "101", "R-1", "INS", "2026-07")])
        self.assertEqual(s1.conflicts(), s2.conflicts())

    def test_the_tie_goes_to_the_file_with_the_larger_hash(self):
        a, b = make_csv(JUL), make_csv(JUL_FIXED)
        winner = a if hashlib.sha256(a).hexdigest() > hashlib.sha256(b).hexdigest() else b
        want = 1450 if winner is a else 1500
        for first, second in [(a, b), (b, a)]:
            s = self.new_store()
            s.ingest(first, P1, "x", T1)
            s.ingest(second, P1, "y", T1)
            self.assertEqual(amounts(s)[("101", "INS", "2026-07")], want)

    def test_an_amount_sqlite_cannot_hold_quarantines_the_file(self):
        s = self.new_store()
        raw = (
            "Unit,Resident ID,Charge Code,Amount,Post Date\r\n"
            "0101,R-1,INS,99999999999999999999.00,07/01/2026\r\n"
            "TOTAL,,,99999999999999999999.00,\r\n"
        ).encode("ascii")
        r = s.ingest(raw, P1, "big.csv", T1)
        self.assertEqual(r.status, "quarantined")
        self.assertTrue(any("too large" in p for p in r.problems))
        self.assertEqual(s.current_charges(), [])

    def test_no_conflict_when_the_tied_files_agree(self):
        s = self.new_store()
        s.ingest(make_csv(JUL), P1, "a", T1)
        s.ingest(make_csv(list(reversed(JUL))), P1, "b", T1)  # different bytes, same amounts
        self.assertEqual(s.conflicts(), [])


class Order(StoreCase):
    def test_every_arrival_order_gives_the_same_store(self):
        bad = make_csv(JUL[:1]).replace(b"14.50", b"14.505")
        files = [
            (make_csv(JUL), P1, "a", T1),
            (make_csv(JUL_FIXED), P1, "b", T2),
            (make_csv(JUL), P1, "a-again", T3),
            (bad, P1, "bad", T2),
            (make_csv(JUL), P1, "a", T1),  # replay of the first
        ]
        digests, currents = set(), set()
        for perm in itertools.permutations(files):
            s = self.new_store()
            for raw, prop, name, as_of in perm:
                s.ingest(raw, prop, name, as_of)
            digests.add(s.digest())
            currents.add(tuple(s.current_charges()))
        self.assertEqual(len(digests), 1)
        self.assertEqual(len(currents), 1)


class Quarantine(StoreCase):
    def test_one_bad_row_posts_nothing_from_the_file(self):
        bad = make_csv(JUL).replace(b'"14.50",07/01/2026\r\nTOTAL', b'"14.505",07/01/2026\r\nTOTAL')
        s = self.new_store()
        r = s.ingest(bad, P1, "bad.csv", T1)
        self.assertEqual((r.status, r.row_count), ("quarantined", 0))
        self.assertTrue(any("third" in p or "not an amount" in p for p in r.problems))
        self.assertEqual(s.current_charges(), [])
        self.assertEqual(len(s.quarantined()), 1)

    def test_a_corrected_file_replaces_the_quarantined_one(self):
        bad = make_csv(JUL).replace(b"14.50", b"14.5x", 1)
        s = self.new_store()
        s.ingest(bad, P1, "bad.csv", T1)
        s.ingest(make_csv(JUL), P1, "good.csv", T2)
        self.assertEqual(len(s.current_charges()), 2)
        self.assertEqual(len(s.quarantined()), 1)

    def test_junk_and_cut_off_files_are_quarantined_with_a_reason(self):
        s = self.new_store()
        r = s.ingest(b"this is not a charge file\n", P1, "junk", T1)
        self.assertEqual(r.status, "quarantined")
        self.assertIn("no known layout", r.problems[0])
        cut = make_csv(JUL).split(b"TOTAL")[0]
        r = s.ingest(cut, P1, "cut", T1)
        self.assertEqual(r.status, "quarantined")
        self.assertIn("cut short", r.problems[0])

    def test_the_same_bad_file_twice_is_a_duplicate(self):
        s = self.new_store()
        s.ingest(b"junk\n", P1, "junk", T1)
        self.assertEqual(s.ingest(b"junk\n", P1, "junk", T1).status, "duplicate")


class Atomic(StoreCase):
    def test_a_failure_while_posting_rows_leaves_nothing_behind(self):
        class Failing(Store):
            def _insert_sightings(self, property_id, sha, as_of, rows):
                super()._insert_sightings(property_id, sha, as_of, rows[:1])
                raise sqlite3.OperationalError("disk full")

        s = self.new_store(Failing)
        with self.assertRaises(sqlite3.OperationalError):
            s.ingest(make_csv(JUL), P1, "a", T1)
        for table in ("files", "sightings", "file_problems"):
            self.assertEqual(s.db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0], 0, table)


class AsOf(StoreCase):
    def test_zones_are_normalised_to_utc(self):
        self.assertEqual(normalize_as_of("2026-07-02T08:00:00"), "2026-07-02T08:00:00")
        self.assertEqual(normalize_as_of("2026-07-02T08:00:00-05:00"), "2026-07-02T13:00:00")
        self.assertEqual(normalize_as_of("2026-07-02"), "2026-07-02T00:00:00")

    def test_garbage_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_as_of("last tuesday")
        with self.assertRaises(ValueError):
            self.new_store().ingest(make_csv(JUL), P1, "a", "yesterday")


if __name__ == "__main__":
    unittest.main()
