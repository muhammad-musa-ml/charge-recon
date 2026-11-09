import importlib.util
import json
import os
import unittest

from chargerecon.harness import run_seed

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def load_runner():
    spec = importlib.util.spec_from_file_location("run_results", os.path.join(ROOT, "scripts", "run_results.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StoredResults(unittest.TestCase):
    def test_the_committed_results_are_what_the_code_produces_now(self):
        runner = load_runner()
        runs = [run_seed(seed)[0] for seed in range(1, 21)]
        totals = runner.summarize(runs)
        with open(os.path.join(ROOT, "results", "results.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().replace("\r\n", "\n"), runner.markdown(runs, totals))
        with open(os.path.join(ROOT, "results", "results.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), json.loads(json.dumps({"totals": totals, "seeds": runs})))


class SummaryTable(unittest.TestCase):
    def test_every_cell_is_a_sum_of_the_per_seed_rows(self):
        with open(os.path.join(ROOT, "results", "results.json"), encoding="utf-8") as f:
            seeds = json.load(f)["seeds"]
        with open(os.path.join(ROOT, "results", "results.md"), encoding="utf-8") as f:
            md = f.read()

        def tot(approach, field):
            return sum(s[approach][field] for s in seeds)

        n = tot("recon", "keys")
        labels = {
            "recon": "this pipeline",
            "naive_ingest": "add up every row of every file, then the same ACH replay",
            "naive_ach": "this ingest, then count every settled event as collected",
        }
        for approach, label in labels.items():
            exact, missed, spurious, wrong = (tot(approach, k) for k in ("exact", "missed", "spurious", "wrong_kinds"))
            with self.subTest(approach=approach):
                self.assertEqual(exact + missed + spurious + wrong, n)
                self.assertIn("| %s | %d of %d | %d | %d | %d |" % (label, exact, n, missed, spurious, wrong), md)


if __name__ == "__main__":
    unittest.main()
