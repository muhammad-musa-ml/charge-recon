"""Run a synthetic world through the pipeline and score it against its labels."""

import random
from collections import Counter

from .ach import SETTLED, Payment, replay
from .dialects import FileError, parse
from .reconcile import reconcile, reconcile_store
from .store import Store
from .synth import build


def ingest_all(world, order_seed):
    store = Store()
    deliveries = list(world.deliveries)
    random.Random(order_seed).shuffle(deliveries)
    for d in deliveries:
        store.ingest(d.raw, d.property_id, d.name, d.as_of)
    return store


def score(world, report):
    t = world.truth
    got = report.kinds_by_key()
    keys = set(t.expected) | set(t.labels) | set(got)
    c = Counter()
    for key in keys:
        want = t.labels.get(key, frozenset())
        have = frozenset(got.get(key, ()))
        if want == have:
            c["exact"] += 1
        elif want and not have:
            c["missed"] += 1
        elif have and not want:
            c["spurious"] += 1
        else:
            c["wrong_kinds"] += 1
    return {
        "keys": len(keys),
        "faulty_keys": len(t.labels),
        "exact": c["exact"],
        "missed": c["missed"],
        "spurious": c["spurious"],
        "wrong_kinds": c["wrong_kinds"],
        "ach_violations_expected": t.violations,
        "ach_violations_seen": len(report.ach_violations),
        "ach_violation_ids_match": {v.payment_id for v in report.ach_violations} == t.violation_payments,
        "quarantined_expected": t.quarantined_files,
        "quarantined_seen": len(report.quarantined),
        "pending_expected": len(t.pending),
        "pending_match": set(report.pending) == t.pending,
    }


def naive_posted(world):
    """Add up every readable row of every delivery: no de-duplication, no superseding, no quarantine."""
    totals = {}
    for d in world.deliveries:
        try:
            parsed = parse(d.raw)
        except FileError:
            continue
        for r in parsed.rows:
            key = (d.property_id,) + r.key
            totals[key] = totals.get(key, 0) + r.amount_cents
    return totals


def naive_payments(world):
    """Count every settled event as money kept, whatever happens to the payment afterwards."""
    created = {e.payment_id: (e.amount_cents, e.key) for e in world.events if e.kind == "created"}
    out = {}
    for i, e in enumerate(world.events):
        if e.kind == "settled" and e.payment_id in created:
            cents, key = created[e.payment_id]
            out["%s#%d" % (e.event_id, i)] = Payment(e.event_id, key, cents, SETTLED, 1, e.on, "")
    return out


def run_seed(seed, units=40):
    world = build(seed, units)
    t = world.truth
    replayed = replay(world.events)
    store = ingest_all(world, order_seed=1)
    report = reconcile_store(t.expected, store, replayed)

    other = ingest_all(world, order_seed=2)
    other_report = reconcile_store(t.expected, other, replay(list(reversed(world.events))))
    stable = store.digest() == other.digest() and report == other_report

    posted_naive = naive_posted(world)
    base_ingest = reconcile(t.expected, posted_naive, replayed.payments, ach_violations=replayed.violations)
    posted_real = {p.key: p.amount_cents for p in store.current_charges()}
    base_ach = reconcile(t.expected, posted_real, naive_payments(world), conflicts=store.conflicts())

    out = {
        "seed": seed,
        "scenarios": dict(sorted(t.injected.items())),
        "recon": score(world, report),
        "order_stable": stable,
        "naive_ingest": score(world, base_ingest),
        "naive_ach": score(world, base_ach),
        "files_delivered": len(world.deliveries),
        "events_delivered": len(world.events),
        "findings": len(report.findings),
    }
    store.close()
    other.close()
    return out, world, report
