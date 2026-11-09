"""Run seeds 1..N through the pipeline and write results/results.json and results/results.md.

    python scripts/run_results.py              # 20 seeds, writes results/
    python scripts/run_results.py --seeds 5 --out /tmp/try
    python scripts/run_results.py --show 3     # print one seed's reconciliation report

The output has no timestamps and no machine data, so a second run on the same
code writes byte-identical files.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from chargerecon.harness import run_seed  # noqa: E402
from chargerecon.reconcile import render  # noqa: E402


def summarize(runs):
    keys = sum(r["recon"]["keys"] for r in runs)
    faulty = sum(r["recon"]["faulty_keys"] for r in runs)
    exact = sum(r["recon"]["exact"] for r in runs)
    scenarios = {}
    for r in runs:
        for name, n in r["scenarios"].items():
            scenarios[name] = scenarios.get(name, 0) + n
    return {
        "seeds": len(runs),
        "keys": keys,
        "faulty_keys": faulty,
        "exact": exact,
        "missed": sum(r["recon"]["missed"] for r in runs),
        "spurious": sum(r["recon"]["spurious"] for r in runs),
        "wrong_kinds": sum(r["recon"]["wrong_kinds"] for r in runs),
        "ach_violations_expected": sum(r["recon"]["ach_violations_expected"] for r in runs),
        "ach_violations_seen": sum(r["recon"]["ach_violations_seen"] for r in runs),
        "quarantined_expected": sum(r["recon"]["quarantined_expected"] for r in runs),
        "quarantined_seen": sum(r["recon"]["quarantined_seen"] for r in runs),
        "pending_matched_seeds": sum(1 for r in runs if r["recon"]["pending_match"]),
        "order_stable_seeds": sum(1 for r in runs if r["order_stable"]),
        "naive_ingest_exact": sum(r["naive_ingest"]["exact"] for r in runs),
        "naive_ingest_spurious": sum(r["naive_ingest"]["spurious"] for r in runs),
        "naive_ingest_wrong_kinds": sum(r["naive_ingest"]["wrong_kinds"] for r in runs),
        "naive_ach_exact": sum(r["naive_ach"]["exact"] for r in runs),
        "naive_ach_missed": sum(r["naive_ach"]["missed"] for r in runs),
        "naive_ach_spurious": sum(r["naive_ach"]["spurious"] for r in runs),
        "scenario_totals": dict(sorted(scenarios.items())),
        "keys_per_seed_min": min(r["recon"]["keys"] for r in runs),
        "keys_per_seed_max": max(r["recon"]["keys"] for r in runs),
        "faults_per_seed_min": min(r["recon"]["faulty_keys"] for r in runs),
        "faults_per_seed_max": max(r["recon"]["faulty_keys"] for r in runs),
        "naive_ingest_exact_min_pct": round(min(100.0 * r["naive_ingest"]["exact"] / r["recon"]["keys"] for r in runs), 1),
        "naive_ingest_exact_max_pct": round(max(100.0 * r["naive_ingest"]["exact"] / r["recon"]["keys"] for r in runs), 1),
        "naive_ach_exact_min_pct": round(min(100.0 * r["naive_ach"]["exact"] / r["recon"]["keys"] for r in runs), 1),
        "naive_ach_exact_max_pct": round(max(100.0 * r["naive_ach"]["exact"] / r["recon"]["keys"] for r in runs), 1),
    }


def markdown(runs, t):
    n = t["keys"]
    lines = [
        "# Results",
        "",
        "Synthetic data only. %d seeds, %d charge keys in all (%d to %d per seed), %d of them with an injected fault"
        " (%d to %d per seed)." % (
            t["seeds"], n, t["keys_per_seed_min"], t["keys_per_seed_max"], t["faulty_keys"],
            t["faults_per_seed_min"], t["faults_per_seed_max"]),
        "",
        "| approach | keys matching their label exactly | missed | spurious | wrong kinds |",
        "|---|---|---|---|---|",
        "| this pipeline | %d of %d | %d | %d | %d |" % (t["exact"], n, t["missed"], t["spurious"], t["wrong_kinds"]),
        "| add up every row of every file, then the same ACH replay | %d of %d | 0 | %d | %d |" % (
            t["naive_ingest_exact"], n, t["naive_ingest_spurious"], t["naive_ingest_wrong_kinds"]),
        "| this ingest, then count every settled event as collected | %d of %d | %d | %d | 0 |" % (
            t["naive_ach_exact"], n, t["naive_ach_missed"], t["naive_ach_spurious"]),
        "",
        "Per seed, the plain row-adding approach matched %.1f%% to %.1f%% of keys and the settled-event count %.1f%% to %.1f%%."
        % (t["naive_ingest_exact_min_pct"], t["naive_ingest_exact_max_pct"],
           t["naive_ach_exact_min_pct"], t["naive_ach_exact_max_pct"]),
        "",
        "Side channels over all seeds: ACH violations seen %d of %d expected; quarantined files seen %d of %d expected;"
        " pending payments matched in %d of %d seeds; store and report identical under a second arrival order in %d of %d seeds."
        % (t["ach_violations_seen"], t["ach_violations_expected"], t["quarantined_seen"], t["quarantined_expected"],
           t["pending_matched_seeds"], t["seeds"], t["order_stable_seeds"], t["seeds"]),
        "",
        "## Scenarios injected, all seeds",
        "",
        "| scenario | count |",
        "|---|---|",
    ]
    lines += ["| %s | %d |" % (k, v) for k, v in t["scenario_totals"].items()]
    lines += [
        "",
        "## Per seed",
        "",
        "| seed | keys | faults | exact | missed | spurious | ACH viol. | quarantined | order stable | row-adding exact | settled-count exact |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        s = r["recon"]
        lines.append(
            "| %d | %d | %d | %d | %d | %d | %d/%d | %d/%d | %s | %d | %d |" % (
                r["seed"], s["keys"], s["faulty_keys"], s["exact"], s["missed"], s["spurious"],
                s["ach_violations_seen"], s["ach_violations_expected"],
                s["quarantined_seen"], s["quarantined_expected"],
                "yes" if r["order_stable"] else "NO", r["naive_ingest"]["exact"], r["naive_ach"]["exact"]))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
    ap.add_argument("--show", type=int, help="print the reconciliation report for one seed and exit")
    args = ap.parse_args()
    if args.show is not None:
        _, _, report = run_seed(args.show)
        print(render(report))
        return
    runs = [run_seed(seed)[0] for seed in range(1, args.seeds + 1)]
    totals = summarize(runs)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"totals": totals, "seeds": runs}, f, indent=1, sort_keys=True)
        f.write("\n")
    with open(os.path.join(args.out, "results.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(markdown(runs, totals))
    print("wrote %s: %d keys, %d exact, %d missed, %d spurious"
          % (os.path.abspath(args.out), totals["keys"], totals["exact"], totals["missed"], totals["spurious"]))


if __name__ == "__main__":
    main()
