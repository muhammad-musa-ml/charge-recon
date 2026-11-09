import unittest

from chargerecon.money import MoneyError, format_cents, parse_amount, parse_implied_cents


class ParseAmount(unittest.TestCase):
    def test_accepted_spellings(self):
        cases = {
            "12": 1200,
            "12.5": 1250,
            "12.50": 1250,
            ".5": 50,
            "0.07": 7,
            "1,234.50": 123450,
            "$12.50": 1250,
            " $ 12.50 ": 1250,
            "-12.50": -1250,
            "+12.50": 1250,
            "(12.50)": -1250,
            "12.50-": -1250,
            "($1,000.00)": -100000,
            "0": 0,
            "0.00": 0,
        }
        for text, cents in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), cents)

    def test_refused_spellings(self):
        for text in [
            "",
            "   ",
            "$",
            ".",
            "12.345",
            "12.",
            "1,23.00",
            "1234,567.00",
            "1e3",
            "NaN",
            "12.50.00",
            "--5",
            "(12.50-)",
            "-(12.50)",
            "(12.50",
            "12.50)",
            "12 50",
            "twelve",
        ]:
            with self.subTest(text=text):
                with self.assertRaises(MoneyError):
                    parse_amount(text)

    def test_other_scripts_are_not_digits(self):
        # str.isdigit() and \d accept these; an amount made of them is not money.
        for text in ["\u0663.00", "12.\u0665\u0660", "\uff11\uff12.00"]:
            with self.subTest(text=text):
                with self.assertRaises(MoneyError):
                    parse_amount(text)

    def test_no_float_rounding(self):
        # 2**53 + 1 cents: a float cannot hold this value, an int can.
        self.assertEqual(parse_amount("90071992547409.93"), 2**53 + 1)
        self.assertEqual(parse_amount("0.10") + parse_amount("0.20"), parse_amount("0.30"))

    def test_amounts_a_sqlite_integer_cannot_hold_are_refused(self):
        self.assertEqual(parse_amount("92233720368547758.07"), 2**63 - 1)
        for text in ["92233720368547758.08", "99999999999999999999.00", "-92233720368547758.09"]:
            with self.subTest(text=text):
                with self.assertRaises(MoneyError):
                    parse_amount(text)
        with self.assertRaises(MoneyError):
            parse_implied_cents("9223372036854775808")

    def test_text_only(self):
        for value in [12.5, 12, None]:
            with self.subTest(value=value):
                with self.assertRaises(MoneyError):
                    parse_amount(value)


class ImpliedCents(unittest.TestCase):
    def test_fixed_width_field(self):
        self.assertEqual(parse_implied_cents("0000045000"), 45000)
        self.assertEqual(parse_implied_cents("-000001450"), -1450)
        self.assertEqual(parse_implied_cents("   1450  "), 1450)

    def test_refused(self):
        for text in ["", "12.50", "00004x000", "--1", "\u0663\u0663"]:
            with self.subTest(text=text):
                with self.assertRaises(MoneyError):
                    parse_implied_cents(text)


class Format(unittest.TestCase):
    def test_round_trip(self):
        for cents in [0, 7, 50, 1250, -1250, 123456789, -5]:
            with self.subTest(cents=cents):
                self.assertEqual(parse_amount(format_cents(cents)), cents)

    def test_shape(self):
        self.assertEqual(format_cents(-5), "-0.05")
        self.assertEqual(format_cents(100005), "1000.05")


if __name__ == "__main__":
    unittest.main()
