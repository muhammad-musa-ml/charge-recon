# charge-recon

I wrote this to see what it takes to keep property-management charge files, ACH payments and a ledger agreeing with each other. It reads charge files from an accounting system, replays the ACH payments that collect those charges through a state machine, and lists every charge where what billing expected, what the ledger shows and what the bank kept disagree. Python standard library only, synthetic data only.

The problem comes from Corgi's posting for a full stack engineer on its Beagle renters-insurance platform, which lists parsing messy third-party formats, state machines where mistakes cost money, and ACH and billing reconciliation. I have not seen any of Corgi's or Beagle's files or code, so the file layouts, the return rules and the faults here are my own guesses. Not affiliated with Corgi or Beagle.

## What is in it

- `chargerecon/money.py`: amounts such as `$1,234.50`, `(45.00)` and `45.00-` become integer cents. This module creates no float. A third decimal, a misplaced comma, two sign marks, a non-ASCII digit or an amount that does not fit a signed 64-bit integer is refused.
- `chargerecon/dialects.py`: three charge-file layouts (comma separated, pipe separated, fixed width), each with a trailer that carries a control total. A row that cannot be read is a reject with its line number, and a file with no trailer is reported as possibly cut short.
- `chargerecon/store.py`: SQLite. Every row of every accepted file is kept, and the charge in force for a key is the one from the file with the latest `as_of`, ties broken by file hash. The result depends on the set of files, not on the order they arrived in.
- `chargerecon/ach.py`: an ACH payment as a state machine (created, submitted, settled, returned), replayed from events that may repeat or arrive late.
- `chargerecon/reconcile.py`: expected, posted and collected amounts per charge key, with eight kinds of finding.
- `chargerecon/synth.py` and `chargerecon/harness.py`: a seeded generator of messy files and payment events with labelled faults, and the scorer.

A sample of one report (7 of the 61 lines of `python scripts/run_results.py --show 1`):

```
expected 11210.50  posted 11143.00  collected 10800.00
54 finding(s), 2 key(s) pending, 4 ACH violation(s), 1 quarantined file(s)
  NOT_POSTED           P1/110/R1010/INS/2026-08  expected 15.00, no charge on the ledger
  AMOUNT_MISMATCH      P1/112/R1012/INS/2026-08  expected 15.00, posted 13.00
  OVERCOLLECTED        P1/112/R1012/INS/2026-08  collected 15.00 against a posted 13.00
  ACH pay00028 pay00028:4: R01 arrived after its 2-banking-day window
  QUARANTINED P2 4f4c5149f2f9 as_of 2026-09-03T07:30:00: line 14: not an amount: '11.505'
```

## Decisions I made

- A file posts whole or not at all. One unreadable row, a duplicate key inside the file, a missing trailer or a wrong control total quarantines it.
- A later file replaces an earlier one only for the keys it contains. A later file that leaves a row out does not delete it.
- The same bytes sent again with the same `as_of` change nothing. The same bytes with a later `as_of` count as a new file, because the sender has said the same thing again. `as_of` is trusted, so an old export stamped with a later `as_of` replaces a correction made before it.
- Dates carry no time of day, so payment events on one date are tried in lifecycle order, and one that does not fit yet is tried again after the rest of that date. A retry dated the day of its return is still a retry.
- A bank can return a settled debit for two weekdays (R01, R02, R03) or 60 calendar days (R10), and R01 can be presented again until three attempts are spent. The windows match Modern Treasury's return-code table; the retry rule is my own setting. Both are constants in `ach.py`, not a statement of the Nacha rules, and bank holidays are not modelled.

## Run

```
python -m unittest discover -s tests -t .
python scripts/run_results.py
python scripts/run_results.py --show 3
python scripts/mutation_check.py --out results/mutation.txt
```

There are 107 tests. `run_results.py` rewrites `results/` and writes no timestamps, and a test regenerates it and compares. `mutation_check.py` changes one place in one module at a time (a few changes touch two adjacent lines) in a copy of the repository, leaves `tests/test_results.py` out of the copy, and runs the tests: all 39 changes made at least one test fail, and the output of that run is `results/mutation.txt`. Developed on Python 3.13.5 and SQLite 3.49.1 on Windows; the store uses SQL window functions.

## Results

20 seeds, 6657 charge keys (287 to 372 per seed), 779 of them with a planted mismatch that must be reported (29 to 55 per seed). Each of those has the exact set of findings it should cause. Late returns, re-sent and corrected files, repeated deliveries and in-flight payments are injected too; they must raise no finding, so they are not in the 779.

| approach | keys matching their label exactly | missed | spurious | wrong kinds |
|---|---|---|---|---|
| this pipeline | 6657 of 6657 | 0 | 0 | 0 |
| add up every row of every file, then the same ACH replay | 4130 of 6657 | 0 | 2334 | 193 |
| this ingest, then count every settled event as collected | 6164 of 6657 | 124 | 360 | 9 |

Per seed, the row-adding approach matched 35.2% to 78.2% of keys and the settled-event count 90.1% to 94.1%. Over all seeds the pipeline saw all 78 late returns it should refuse, on the same payments in 20 of 20 seeds, 15 of 15 quarantined files, and gave the same store and report under a second arrival order in 20 of 20 seeds. Per-seed rows and the scenario counts are in `results/results.md`.

## What this does not show

- The first row is scored against labels written by the same person who wrote the reconciler. 6657 of 6657 says the parts agree with each other, not that real files behave this way. The other two rows are the measurement: what a plain approach gets wrong on the same data. I made those plain on purpose, with no de-duplication, no superseding, no quarantine and no return handling.
- No real charge file, bank file or payment processor was used, so none of these counts is a rate that exists anywhere.
- The payment events are made up. There is no Stripe, no network and no money moves.
- The faults are the ones I thought of. A layout, a rounding rule or a return code I did not model would not be caught. Two of the eight kinds of finding, COLLECTED_NOT_POSTED and CONFLICT, never occur in the seeded data and are covered by unit tests only.
- A payment that is created or submitted and then never heard of again stays pending for ever: there is no as-of date, so nothing ages into a finding. Neither does an R01 whose retry never comes.
- Only in-memory SQLite was tested, and nothing was run on another platform or Python version.
- No performance was measured.
