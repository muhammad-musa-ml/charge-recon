import unittest

from chargerecon.harness import run_seed

SEEDS = range(1, 9)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {seed: run_seed(seed)[0] for seed in SEEDS}

    def test_every_key_matches_its_label_exactly(self):
        for seed, r in self.runs.items():
            s = r["recon"]
            with self.subTest(seed=seed):
                self.assertEqual((s["missed"], s["spurious"], s["wrong_kinds"]), (0, 0, 0))
                self.assertEqual(s["exact"], s["keys"])
                self.assertGreater(s["faulty_keys"], 0)

    def test_side_channels_match(self):
        for seed, r in self.runs.items():
            s = r["recon"]
            with self.subTest(seed=seed):
                self.assertEqual(s["ach_violations_seen"], s["ach_violations_expected"])
                self.assertEqual(s["quarantined_seen"], s["quarantined_expected"])
                self.assertTrue(s["pending_match"])

    def test_arrival_order_changes_nothing(self):
        for seed, r in self.runs.items():
            with self.subTest(seed=seed):
                self.assertTrue(r["order_stable"])

    def test_the_plain_approaches_do_worse(self):
        for seed, r in self.runs.items():
            with self.subTest(seed=seed):
                self.assertLess(r["naive_ingest"]["exact"], r["recon"]["exact"])
                self.assertLess(r["naive_ach"]["exact"], r["recon"]["exact"])

    def test_every_scenario_is_exercised_somewhere(self):
        seen = set()
        for r in self.runs.values():
            seen |= set(r["scenarios"])
        wanted = {
            "clean", "drop_posting", "wrong_up", "wrong_down", "r01_exhausted", "r02_final", "late_r10",
            "return_after_window", "r01_retry_ok", "in_flight", "unexpected_posting", "resend_corrected",
        }
        self.assertEqual(wanted - seen, set())


if __name__ == "__main__":
    unittest.main()
