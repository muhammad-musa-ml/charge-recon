"""Break the code on purpose and require the tests to notice.

    python scripts/mutation_check.py [--out results/mutation.txt]

Each mutant changes one place in one module (usually one line, a few change two
adjacent lines) in its own temporary copy of the repository, never in this
checkout, then runs the test suite there. tests/test_results.py is left out of
the copy because it fails for any change in behaviour. A mutant that no test
fails on is a survivor. A mutant whose anchor text does not occur exactly once,
whose bytes do not change, or whose tree no longer imports is not run, and the
script says so instead of printing a verdict. Exit code 0 only when every mutant
was run and killed.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

M = []


def mut(name, path, old, new):
    M.append((name, path, old, new))


mut("money: allow other-script digits", "chargerecon/money.py",
    'r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)?(?:\\.[0-9]{1,2})?"',
    'r"(?:\\d{1,3}(?:,\\d{3})+|\\d+)?(?:\\.\\d{1,2})?"')
mut("money: allow a third decimal", "chargerecon/money.py", '(?:\\.[0-9]{1,2})?")', '(?:\\.[0-9]{1,3})?")')
mut("money: allow two sign marks", "chargerecon/money.py",
    '    if marks > 1:\n        raise MoneyError("more than one sign mark in %r" % text)',
    '    if False:\n        raise MoneyError("more than one sign mark in %r" % text)')
mut("money: implied cents accept other scripts", "chargerecon/money.py",
    'if not s or not re.fullmatch(r"[0-9]+", s):', 'if not s or not re.fullmatch(r"\\d+", s):')
mut("dialects: splitlines instead of CR/LF", "chargerecon/dialects.py",
    'return re.split(r"\\r\\n|\\r|\\n", text)', 'return text.splitlines()')
mut("dialects: no trailer check", "chargerecon/dialects.py", "        if not self.trailer_seen:", "        if False:")
mut("dialects: no control total check", "chargerecon/dialects.py",
    "if self.control_total_cents is not None and self.control_total_cents != self.sum_cents:", "if False:")
mut("dialects: no control count check", "chargerecon/dialects.py",
    "if self.control_count is not None and self.control_count != len(self.rows):", "if False:")
mut("dialects: duplicate keys kept", "chargerecon/dialects.py",
    "    _drop_duplicate_keys(parsed)\n    return parsed", "    return parsed")
mut("dialects: unit zeros kept", "chargerecon/dialects.py", 's = s.lstrip("0") or "0"', "s = s")
mut("dialects: repeated csv header parsed as data", "chargerecon/dialects.py",
    "        if names == header:\n            continue  # header repeated on a new page",
    "        if False:\n            continue  # header repeated on a new page")
mut("dialects: no year range", "chargerecon/dialects.py", "if not (MIN_YEAR <= y <= MAX_YEAR):", "if False:")
mut("dialects: pipe unit T always a trailer", "chargerecon/dialects.py",
    'if cells[0] == "T" and len(cells) != 5:', 'if cells[0] == "T":')
mut("store: oldest file wins", "chargerecon/store.py", "ORDER BY s.as_of DESC, s.file_sha DESC", "ORDER BY s.as_of ASC, s.file_sha DESC")
mut("store: no hash tie-break", "chargerecon/store.py", "ORDER BY s.as_of DESC, s.file_sha DESC", "ORDER BY s.as_of DESC")
mut("store: no transaction around a file", "chargerecon/store.py",
    '        with self.db:\n            self.db.execute(\n                "INSERT INTO files',
    '        if True:\n            self.db.execute(\n                "INSERT INTO files')
mut("store: same bytes later are a duplicate", "chargerecon/store.py",
    '"SELECT 1 FROM files WHERE property_id = ? AND sha256 = ? AND as_of = ?",\n            (property_id, sha, as_of),',
    '"SELECT 1 FROM files WHERE property_id = ? AND sha256 = ?",\n            (property_id, sha),')
mut("store: a quarantined file keeps its rows", "chargerecon/store.py",
    "rows = [] if problems else parsed.rows", "rows = parsed.rows")
mut("store: any two files conflict", "chargerecon/store.py",
    "HAVING COUNT(DISTINCT r.amount_cents) > 1", "HAVING COUNT(DISTINCT r.amount_cents) > 0")
mut("ach: banking-day window not enforced", "chargerecon/ach.py",
    "return banking_days_between(settled_on, returned_on) <= rule.window_days", "return True")
mut("ach: calendar window not enforced", "chargerecon/ach.py",
    "return (returned_on - settled_on).days <= rule.window_days", "return True")
mut("ach: attempt budget not enforced", "chargerecon/ach.py",
    '            if payment.attempt >= MAX_ATTEMPTS:\n                return "attempt budget',
    '            if False:\n                return "attempt budget')
mut("ach: duplicate events not collapsed", "chargerecon/ach.py",
    "by_id.setdefault(e.event_id, set()).add(e)", "by_id.setdefault(e.event_id + str(id(e)), set()).add(e)")
mut("ach: events not sorted by date", "chargerecon/ach.py",
    "usable.sort(key=lambda e: (e.on, STEP.get(e.kind, 9), e.event_id))", "usable.sort(key=lambda e: e.event_id)")
mut("ach: R01 not retriable", "chargerecon/ach.py", '"R01": ReturnRule("banking", 2, True),', '"R01": ReturnRule("banking", 2, False),')
mut("ach: R02 retriable", "chargerecon/ach.py", '"R02": ReturnRule("banking", 2, False),', '"R02": ReturnRule("banking", 2, True),')
mut("reconcile: credit read as overcollection", "chargerecon/reconcile.py", "if c > max(p, 0):", "if c > p:")
mut("reconcile: pending payments are exceptions", "chargerecon/reconcile.py",
    "elif p > 0 and not is_pending:", "elif p > 0:")
mut("reconcile: conflicts dropped in reconcile_store", "chargerecon/reconcile.py",
    "conflicts=store.conflicts(), ach_violations", "ach_violations")
mut("reconcile: zero expected amount allowed", "chargerecon/reconcile.py", "        if cents <= 0:", "        if False:")
mut("reconcile: no NOT_POSTED", "chargerecon/reconcile.py",
    "if key in expected and key not in posted:\n            add(NOT_POSTED", "if False:\n            add(NOT_POSTED")
mut("money: no size limit", "chargerecon/money.py", "    if cents > MAX_CENTS:", "    if False:")
mut("dialects: BOM file with cp1252 not read", "chargerecon/dialects.py",
    'return body.decode("cp1252"), "cp1252"', 'return body.decode("utf-8"), "cp1252"')
mut("dialects: non-ASCII charge codes accepted", "chargerecon/dialects.py",
    'if not s.isascii() or not re.fullmatch(r"[A-Z0-9]{2,8}", s.upper()):', 'if not re.fullmatch(r"[A-Z0-9]{2,8}", s.upper()):')
mut("dialects: bad quoting reported at the end line", "chargerecon/dialects.py",
    'Reject(start, "bad quoting: %s" % exc)', 'Reject(reader.line_num, "bad quoting: %s" % exc)')
mut("ach: same-day events not tried again", "chargerecon/ach.py", "            day = deferred\n", "            break\n")
mut("ach: return codes matched exactly", "chargerecon/ach.py",
    "RETURN_RULES.get(code.strip().upper(), UNKNOWN_RETURN)", "RETURN_RULES.get(code, UNKNOWN_RETURN)")
mut("store: smaller hash wins a tie", "chargerecon/store.py",
    "ORDER BY s.as_of DESC, s.file_sha DESC", "ORDER BY s.as_of DESC, s.file_sha ASC")
mut("synth: CSV files never use LF", "chargerecon/synth.py",
    'eol = rng.choice(["\\r\\n", "\\n"])', 'eol = rng.choice(["\\r\\n", "\\r\\n"])')


def md5(b):
    return hashlib.md5(b).hexdigest()


def run(work, i, path, old, new):
    dst = os.path.join(work, "m%02d" % i)
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "results"))
    os.remove(os.path.join(dst, "tests", "test_results.py"))
    target = os.path.join(dst, path)
    with open(target, "rb") as f:
        before = f.read()
    text = before.decode("utf-8").replace("\r\n", "\n")
    if text.count(old) != 1:
        return "UNRUN (anchor occurs %d times)" % text.count(old)
    mutated = text.replace(old, new).encode("utf-8")
    with open(target, "wb") as f:
        f.write(mutated)
    if md5(before) == md5(mutated):
        return "UNRUN (bytes unchanged)"
    p = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=dst, capture_output=True, encoding="utf-8", errors="replace", timeout=300,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    out = p.stderr + p.stdout
    failed = re.findall(r"^(?:FAIL|ERROR): (\S+) \((\S+)\)", out, re.M)
    ran = re.search(r"^Ran (\d+) test", out, re.M)
    if any("_FailedTest" in f[1] for f in failed) or not ran or int(ran.group(1)) == 0:
        return "UNRUN (the mutated tree does not import or ran no tests)"
    if p.returncode == 0:
        return "SURVIVED"
    if not failed:
        return "UNRUN (non-zero exit with no failing test named)"
    return "killed by %d test(s)" % len(failed)


def main():
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    if out_path and os.path.exists(out_path):
        os.remove(out_path)  # a crashed run must not leave the last run's output behind
    work = tempfile.mkdtemp(prefix="chargerecon-mut-")
    results, lines = [], []
    try:
        for i, (name, path, old, new) in enumerate(M, start=1):
            verdict = run(work, i, path, old, new)
            results.append(verdict)
            lines.append("%02d %-50s %s" % (i, name, verdict))
            print(lines[-1], flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    killed = sum(1 for v in results if v.startswith("killed"))
    lines.append("mutants %d, killed %d, survived %d, not run %d" % (
        len(results), killed, sum(1 for v in results if v == "SURVIVED"),
        sum(1 for v in results if v.startswith("UNRUN"))))
    print(lines[-1])
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    sys.exit(0 if killed == len(results) else 1)


if __name__ == "__main__":
    main()
