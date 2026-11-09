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


if __name__ == "__main__":
    unittest.main()
