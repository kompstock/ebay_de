import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("generator", ROOT / "src" / "generate.py")
generator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(generator)


class GeneratorTests(unittest.TestCase):
    def test_reference_product(self):
        settings = generator.load_json(ROOT / "config" / "settings.json")
        translations = generator.load_json(ROOT / "config" / "translations.json")
        template = (ROOT / "templates" / "description.html").read_text(encoding="utf-8")
        nbp = {
            "currency": "euro", "code": "EUR", "table": "A",
            "number": "TEST", "effective_date": "2026-07-29", "rate": 4.3257,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            report = generator.generate(
                (ROOT / "tests" / "fixtures" / "sample.xml").read_bytes(),
                nbp, settings, translations, template, output,
            )
            self.assertEqual(report["products_exported"], 1)
            self.assertEqual(report["csv_columns"], 104)
            with (output / "ebay-de-laptops.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(len(rows[1]), 104)
            self.assertEqual(len(rows[2]), 104)
            record = dict(zip(rows[1], rows[2]))
            self.assertEqual(record["CustomLabel"], "3888_20250724125241")
            self.assertEqual(record["*ConditionID"], "3000")
            self.assertEqual(record["VAT%"], "19")
            self.assertEqual(record["*Quantity"], "985")
            self.assertEqual(record["*StartPrice"], "226.00")
            self.assertEqual(record["*C:Prozessor"], "AMD Ryzen 5 PRO 4000 Series")
            self.assertIn("für", record["*Description"])
            self.assertNotIn("fĂ", record["*Description"])

    def test_report_is_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            settings = generator.load_json(ROOT / "config" / "settings.json")
            translations = generator.load_json(ROOT / "config" / "translations.json")
            template = (ROOT / "templates" / "description.html").read_text(encoding="utf-8")
            generator.generate(
                (ROOT / "tests" / "fixtures" / "sample.xml").read_bytes(),
                {"currency": "euro", "code": "EUR", "table": "A", "number": "TEST",
                 "effective_date": "2026-07-29", "rate": 4.0},
                settings, translations, template, output,
            )
            report = json.loads((output / "generation-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["pricing"], "ceil(price_pln / nbp_eur_mid)")


if __name__ == "__main__":
    unittest.main()

