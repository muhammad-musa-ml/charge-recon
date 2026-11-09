import unittest

from chargerecon.dialects import parse
from chargerecon.store import Store
from chargerecon.synth import LABELS, PERIODS, PROPERTIES, build


class Generator(unittest.TestCase):
    def test_same_seed_same_world(self):
        a, b = build(3), build(3)
        self.assertEqual(a.deliveries, b.deliveries)
        self.assertEqual(a.events, b.events)
        self.assertEqual(a.truth.expected, b.truth.expected)
        self.assertEqual(a.truth.labels, b.truth.labels)

    def test_different_seeds_differ(self):
        self.assertNotEqual(build(1).truth.expected, build(2).truth.expected)

    def test_every_layout_and_period_is_present(self):
        w = build(1)
        layouts = {parse(d.raw).dialect for d in w.deliveries if d.name.endswith(("full", "fix"))}
        self.assertEqual(layouts, set(PROPERTIES.values()))
        for prop in PROPERTIES:
            for period in PERIODS:
                self.assertTrue(any(d.name.startswith("%s-%s-full" % (prop, period)) for d in w.deliveries))

    def test_the_files_really_are_messy(self):
        raws = [d.raw for seed in range(1, 6) for d in build(seed).deliveries]
        joined = b"\n".join(raws)
        self.assertTrue(any(r.startswith(b"\xef\xbb\xbf") for r in raws), "no BOM anywhere")
        self.assertIn(b"\r\n", joined)
        # the pipe and fixed files always use CRLF, so a file with bare LF is a CSV
        self.assertTrue(any(b"\r\n" not in r and b"\n" in r for r in raws), "no LF-only file")
        self.assertIn(b"$", joined)
        self.assertIn(b',"1,', joined)  # a thousands separator inside quotes
        self.assertIn(b"unitno|tenantref", joined)  # a page header in another case
        self.assertTrue(any(b"\nUnit,Resident ID" in r and r.count(b"Unit,Resident ID") > 1 for r in raws))
        self.assertTrue(any(b",r1" in r for r in raws), "no lower-case resident id")

    def test_labels_follow_the_scenario_table(self):
        w = build(4)
        for key, kinds in w.truth.labels.items():
            self.assertEqual(kinds, LABELS[w.truth.scenarios[key]])
        for key, name in w.truth.scenarios.items():
            if not LABELS[name]:
                self.assertNotIn(key, w.truth.labels)

    def test_the_store_quarantines_exactly_the_files_the_generator_corrupted(self):
        for seed in range(1, 9):
            w = build(seed)
            with Store() as s:
                for d in w.deliveries:
                    s.ingest(d.raw, d.property_id, d.name, d.as_of)
                self.assertEqual(len(s.quarantined()), w.truth.quarantined_files, seed)

    def test_every_expected_key_is_well_formed(self):
        w = build(5)
        for (prop, unit, ref, code, period), cents in w.truth.expected.items():
            self.assertIn(prop, PROPERTIES)
            self.assertIn(period, PERIODS)
            self.assertTrue(unit.isdigit() and not unit.startswith("0"))
            self.assertTrue(ref.startswith("R") and ref == ref.upper())
            self.assertGreater(cents, 0)


if __name__ == "__main__":
    unittest.main()
