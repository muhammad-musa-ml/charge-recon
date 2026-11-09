import unittest
from datetime import date

from chargerecon.dialects import (
    UndecodableFile,
    UnknownDialect,
    norm_unit,
    parse,
)

CSV_HEAD = "Unit,Resident ID,Charge Code,Amount,Post Date\r\n"


def csv_file(lines, total="1,205.00", bom=True, eol="\r\n"):
    body = CSV_HEAD.replace("\r\n", eol) + "".join(l + eol for l in lines)
    if total is not None:
        body += 'TOTAL,,,"%s",%s' % (total, eol)
    return (b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8")


GOOD_CSV_ROWS = [
    "0101,r-1001,INS,14.50,07/01/2026",
    '0102,R-1002,ANN,"1,188.00",07/01/2026',
    '0103,R-1003,INS,(2.50),07/02/2026',
]
GOOD_CSV_TOTAL = "1,200.00"  # 14.50 + 1188.00 - 2.50


class CsvA(unittest.TestCase):
    def test_clean_file(self):
        p = parse(csv_file(GOOD_CSV_ROWS, total=GOOD_CSV_TOTAL))
        self.assertEqual(p.dialect, "csv_a")
        self.assertEqual(p.encoding, "utf-8-sig")
        self.assertEqual(p.problems(), [])
        self.assertEqual(
            [(r.unit, r.resident_ref, r.code, r.amount_cents, r.post_date) for r in p.rows],
            [
                ("101", "R-1001", "INS", 1450, date(2026, 7, 1)),
                ("102", "R-1002", "ANN", 118800, date(2026, 7, 1)),
                ("103", "R-1003", "INS", -250, date(2026, 7, 2)),
            ],
        )
        self.assertEqual(p.sum_cents, 120000)
        self.assertEqual(p.rows[0].period, "2026-07")

    def test_line_endings_and_bom_do_not_matter(self):
        base = parse(csv_file(GOOD_CSV_ROWS, total=GOOD_CSV_TOTAL))
        for eol in ["\n", "\r", "\r\n"]:
            for bom in [True, False]:
                with self.subTest(eol=repr(eol), bom=bom):
                    p = parse(csv_file(GOOD_CSV_ROWS, total=GOOD_CSV_TOTAL, bom=bom, eol=eol))
                    self.assertEqual(p.problems(), [])
                    self.assertEqual([r.key for r in p.rows], [r.key for r in base.rows])

    def test_repeated_header_and_blank_lines_are_skipped(self):
        lines = [GOOD_CSV_ROWS[0], "", CSV_HEAD.strip(), GOOD_CSV_ROWS[1], ",,,,", GOOD_CSV_ROWS[2]]
        p = parse(csv_file(lines, total=GOOD_CSV_TOTAL))
        self.assertEqual(p.problems(), [])
        self.assertEqual(len(p.rows), 3)

    def test_each_bad_row_is_a_reject_with_its_line_number(self):
        lines = [
            "0101,R-1,INS,14.505,07/01/2026",   # line 2: third decimal
            "0102,R-2,INS,14.50,02/30/2026",    # line 3: no such date
            "0103,R-3,INS,14.50",               # line 4: short row
            "0104,R-4,I,14.50,07/01/2026",      # line 5: charge code too short
            "0105,R-5,INS,14.50,07/01/26",      # line 6: two-digit year
            ",R-6,INS,14.50,07/01/2026",        # line 7: no unit
        ]
        p = parse(csv_file(lines, total="0.00"))
        self.assertEqual([r.line_no for r in p.rejects], [2, 3, 4, 5, 6, 7])
        self.assertEqual(p.rows, [])
        self.assertTrue(p.problems())

    def test_control_total_mismatch_is_a_problem(self):
        p = parse(csv_file(GOOD_CSV_ROWS, total="1,200.01"))
        self.assertEqual(p.rejects, [])
        self.assertEqual(len(p.problems()), 1)
        self.assertIn("control total", p.problems()[0])

    def test_missing_total_means_possibly_cut_short(self):
        p = parse(csv_file(GOOD_CSV_ROWS, total=None))
        self.assertIn("cut short", p.problems()[0])

    def test_row_after_total_and_second_total(self):
        raw = csv_file(GOOD_CSV_ROWS, total=GOOD_CSV_TOTAL) + b"0104,R-4,INS,1.00,07/01/2026\r\nTOTAL,,,1.00,\r\n"
        p = parse(raw)
        self.assertEqual(len(p.rejects), 2)

    def test_same_key_twice_in_one_file(self):
        lines = GOOD_CSV_ROWS + ["101,R-1001,ins,14.50,07/15/2026"]
        p = parse(csv_file(lines, total=GOOD_CSV_TOTAL))
        self.assertEqual(len(p.rows), 3)
        self.assertEqual([r.line_no for r in p.rejects], [5])
        self.assertIn("duplicate key", p.rejects[0].reason)

    def test_unicode_line_separator_inside_a_field_does_not_split_the_row(self):
        raw = csv_file(['0101,"R-10 0",INS,14.50,07/01/2026'], total="14.50")
        p = parse(raw)
        self.assertEqual(p.problems(), [])
        self.assertEqual(len(p.rows), 1)

    def test_encodings(self):
        text = csv_file(['0101,"René-1",INS,14.50,07/01/2026'], total="14.50", bom=False).decode("utf-8")
        cp = parse(text.encode("cp1252"))
        self.assertEqual((cp.encoding, cp.rows[0].resident_ref), ("cp1252", "RENÉ-1"))
        u16 = parse(text.encode("utf-16"))
        self.assertEqual((u16.encoding, u16.problems()), ("utf-16", []))
        with self.assertRaises(UndecodableFile):
            parse(b"Unit,Resident ID,Charge Code,Amount,Post Date\r\n\x81\x8d\r\n")

    def test_broken_quoting_is_a_reject_not_a_crash(self):
        lines = ['0101,"R-1,INS,14.50,07/01/2026', GOOD_CSV_ROWS[0]]
        p = parse(csv_file(lines, total="14.50"))
        self.assertTrue(p.rejects)


def pipe_file(rows, trailer=None, extra_header=False):
    out = ["UnitNo|TenantRef|Code|Amt|PostedOn"]
    for i, r in enumerate(rows):
        out.append(r)
        if extra_header and i == 0:
            out += ["", "unitno|tenantref|code|amt|postedon"]
    if trailer is not None:
        out.append(trailer)
    return ("\n".join(out) + "\n").encode("ascii")


PIPE_ROWS = ["0101|R-1001|INS|14.50|20260701", "0102|R-1002|INS|2.50-|20260702"]


class PipeB(unittest.TestCase):
    def test_clean_with_credit_and_page_header(self):
        p = parse(pipe_file(PIPE_ROWS, "T|2|12.00", extra_header=True))
        self.assertEqual((p.dialect, p.problems()), ("pipe_b", []))
        self.assertEqual([r.amount_cents for r in p.rows], [1450, -250])
        self.assertEqual(p.control_count, 2)

    def test_count_and_total_must_agree(self):
        self.assertIn("control count", parse(pipe_file(PIPE_ROWS, "T|3|12.00")).problems()[0])
        self.assertIn("control total", parse(pipe_file(PIPE_ROWS, "T|2|12.01")).problems()[0])

    def test_missing_or_malformed_trailer(self):
        self.assertIn("cut short", parse(pipe_file(PIPE_ROWS)).problems()[0])
        p = parse(pipe_file(PIPE_ROWS, "T|two|12.00"))
        self.assertTrue(any("bad trailer" in r.reason for r in p.rejects))

    def test_row_after_trailer_and_wrong_field_count(self):
        p = parse(pipe_file(PIPE_ROWS + [], "T|2|12.00") + b"0103|R-3|INS|1.00|20260701\n")
        self.assertEqual(len(p.rejects), 1)
        p = parse(pipe_file(PIPE_ROWS + ["0103|R-3|INS|1.00"], "T|2|12.00"))
        self.assertIn("expected 5 fields", p.rejects[0].reason)

    def test_a_unit_named_T_is_data_not_a_trailer(self):
        p = parse(pipe_file(["T|R-1|INS|1.00|20260701"], "T|1|1.00"))
        self.assertEqual(p.problems(), [])
        self.assertEqual(p.rows[0].unit, "T")

    def test_pipe_file_must_start_with_header(self):
        with self.assertRaises(UnknownDialect):
            parse(b"0101|R-1|INS|1.00|20260701\nUnitNo|TenantRef|Code|Amt|PostedOn\n")


def fixed_row(unit, ref, code, cents, ymd):
    amt = ("-%09d" % -cents) if cents < 0 else "%010d" % cents
    return unit.ljust(6) + ref.ljust(8) + code.ljust(4) + amt + ymd


def fixed_file(rows, trailer=None):
    out = ["HDR20260731"] + rows
    if trailer is not None:
        out.append(trailer)
    return ("\r\n".join(out) + "\r\n").encode("ascii")


FIXED_ROWS = [fixed_row("0101", "R-1001", "INS", 1450, "20260701"), fixed_row("0102", "R-1002", "INS", -250, "20260702")]


class FixedC(unittest.TestCase):
    def test_clean(self):
        p = parse(fixed_file(FIXED_ROWS, "TRL" + "000002" + "000000001200"))
        self.assertEqual((p.dialect, p.problems()), ("fixed_c", []))
        self.assertEqual([(r.unit, r.amount_cents) for r in p.rows], [("101", 1450), ("102", -250)])

    def test_wrong_width_is_a_reject(self):
        p = parse(fixed_file(FIXED_ROWS + ["0103  R-1003  INS 000000145020260701X"], "TRL000003000000001200"))
        self.assertIn("expected 36 columns", p.rejects[0].reason)

    def test_trailer_mismatch(self):
        p = parse(fixed_file(FIXED_ROWS, "TRL000002000000001201"))
        self.assertIn("control total", p.problems()[0])
        p = parse(fixed_file(FIXED_ROWS, "TRL000009000000001200"))
        self.assertIn("control count", p.problems()[0])

    def test_no_trailer(self):
        self.assertIn("cut short", parse(fixed_file(FIXED_ROWS)).problems()[0])


class AcrossDialects(unittest.TestCase):
    def test_same_postings_read_the_same_from_every_layout(self):
        a = parse(csv_file(["0101,R-1001,INS,14.50,07/01/2026", "0102,R-1002,INS,(2.50),07/02/2026"], total="12.00"))
        b = parse(pipe_file(PIPE_ROWS, "T|2|12.00"))
        c = parse(fixed_file(FIXED_ROWS, "TRL000002000000001200"))
        shape = lambda p: [(r.key, r.amount_cents, r.post_date) for r in p.rows]
        self.assertEqual(shape(a), shape(b))
        self.assertEqual(shape(b), shape(c))


class Unknown(unittest.TestCase):
    def test_not_a_charge_file(self):
        for raw in [b"", b"\n\n", b"hello world\n", b"Unit,Name\r\n1,2\r\n", b"Unit,Resident ID,Charge Code\r\n"]:
            with self.subTest(raw=raw):
                with self.assertRaises(UnknownDialect):
                    parse(raw)


class UnitNames(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(norm_unit("0101"), "101")
        self.assertEqual(norm_unit(" 101 "), "101")
        self.assertEqual(norm_unit(" a-101 "), "A-101")
        self.assertEqual(norm_unit("A 101"), "A101")
        self.assertEqual(norm_unit("000"), "0")
        self.assertEqual(norm_unit("٣"), "٣")  # not an ASCII digit, left alone


if __name__ == "__main__":
    unittest.main()
