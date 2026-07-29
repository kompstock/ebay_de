"""Testy bramek jakosci. Kazdy z nich pilnuje bledu, ktory realnie wystapil."""
import csv
import html
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sample.xml"
POLISH_CHARS = set("ąćęłńóśźż")


def run(feed=FIXTURE, out=None):
    out = out or Path(tempfile.mkdtemp())
    subprocess.run(
        [sys.executable, str(ROOT / "src" / "generate.py"),
         "--feed-file", str(feed), "--nbp-rate", "4.26", "--output-dir", str(out)],
        check=False, capture_output=True)
    rows = list(csv.reader((out / "ebay-de-laptops.csv").open(encoding="utf-8-sig"), delimiter=";"))
    report = json.loads((out / "generation-report.json").read_text(encoding="utf-8"))
    data = [dict(zip(rows[1], r)) for r in rows[2:] if r]
    return data, report, out


class Gates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.report, cls.out = run()
        cls.row = cls.rows[0]

    def test_produkt_wyeksportowany(self):
        self.assertEqual(self.report["products_exported"], 1)

    def test_gpsr_kompletny(self):
        for field in ("Manufacturer Name", "Manufacturer AddressLine1", "Manufacturer City",
                      "Manufacturer Country", "Manufacturer PostalCode", "Manufacturer Email",
                      "Responsible Person 1", "Responsible Person 1 Type",
                      "Responsible Person 1 AddressLine1", "Responsible Person 1 City",
                      "Responsible Person 1 Country", "Responsible Person 1 Email"):
            self.assertTrue(self.row[field], f"puste pole GPSR: {field}")

    def test_brak_polskich_slow_w_opisie(self):
        d = self.row["*Description"]
        plain = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", d)
        plain = html.unescape(re.sub(r"<[^>]+>", " ", plain))
        found = [c for c in plain if c in POLISH_CHARS]
        self.assertFalse(found, f"polskie znaki w opisie: {set(found)}")
        self.assertIsNone(re.search(r"(?<![\w])(i|oraz|lub|jest|możliwy)(?![\w])", plain))

    def test_brak_klasy_stanu_w_opisie(self):
        self.assertNotIn("Klasse A", self.row["*Description"])
        self.assertNotIn("Klasa", self.row["*Description"])

    def test_brak_niepodmienionych_placeholderow(self):
        self.assertNotIn("{{", self.row["*Description"])

    def test_brak_podwojnego_escapowania(self):
        self.assertNotIn("&amp;uuml;", self.row["*Description"])
        self.assertNotIn("&amp;auml;", self.row["*Description"])

    def test_tytul_do_80_znakow(self):
        self.assertLessEqual(len(self.row["*Title"]), 80)
        self.assertGreater(len(self.row["*Title"]), 30)

    def test_taktowanie_bez_zakresu(self):
        self.assertNotIn("-", self.row["C:Prozessorgeschwindigkeit"])
        self.assertRegex(self.row["C:Prozessorgeschwindigkeit"], r"^\d+,\d+ GHz$")

    def test_aspekty_ze_slownika(self):
        allowed = json.loads((ROOT / "config" / "aspects.json").read_text(encoding="utf-8"))
        for value in self.row["C:Konnektivität"].split("|"):
            self.assertIn(value, allowed["konnektivitaet"]["_dozwolone"])
        for value in self.row["C:Besonderheiten"].split("|"):
            self.assertIn(value, allowed["besonderheiten"]["_dozwolone"])

    def test_sekcje_opisu_obecne(self):
        d = html.unescape(self.row["*Description"])
        for marker in ("So bereiten wir", "Sie gehen kein Risiko", "Häufige Fragen",
                       "Wer wir sind", "Firmenkunden", "Tastaturaufkleber", "Packstation"):
            self.assertIn(marker, d, f"brak sekcji: {marker}")

    def test_raport_json(self):
        self.assertIn("nbp", self.report)
        self.assertEqual(self.report["csv_columns"], 104)


class Blockades(unittest.TestCase):
    def test_nieznany_producent_blokuje(self):
        feed = FIXTURE.read_text(encoding="utf-8").replace(">LENOVO<", ">NIEZNANY<")
        tmp = Path(tempfile.mkdtemp()) / "f.xml"
        tmp.write_text(feed, encoding="utf-8")
        rows, report, _ = run(tmp)
        self.assertEqual(report["products_exported"], 0)
        self.assertEqual(rows, [])

    def test_nieznane_tlumaczenie_blokuje(self):
        feed = FIXTURE.read_text(encoding="utf-8").replace(
            "Normalne ślady użytkowania", "Wgniecenie na obudowie, brak nóżki")
        tmp = Path(tempfile.mkdtemp()) / "f.xml"
        tmp.write_text(feed, encoding="utf-8")
        rows, report, out = run(tmp)
        self.assertEqual(report["products_exported"], 0)
        review = (out / "review.csv").read_text(encoding="utf-8-sig")
        self.assertIn("Wgniecenie", review)


if __name__ == "__main__":
    unittest.main()
