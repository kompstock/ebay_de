"""Testy bramek jakosci. Kazdy pilnuje bledu, ktory realnie wystapil."""
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
RAPORT = ROOT / "tests" / "fixtures" / "aktywne.csv"
POLSKIE = set("ąćęłńóśźż")
csv.field_size_limit(10 ** 7)


def uruchom(tryb="pierwsze", feed=FIXTURE, raport=RAPORT):
    out = Path(tempfile.mkdtemp())
    subprocess.run(
        [sys.executable, str(ROOT / "src" / "generate.py"), "--tryb", tryb,
         "--feed-file", str(feed), "--raport", str(raport), "--nbp-rate", "4.26",
         "--min-produktow", "1", "--output-dir", str(out)],
        check=False, capture_output=True)
    raport_json = json.loads((out / "generation-report.json").read_text(encoding="utf-8"))
    wiersze = []
    plik = out / "ebay-add.csv"
    if plik.exists():
        with plik.open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt, delimiter=";"))
        wiersze = [dict(zip(rows[1], r)) for r in rows[2:] if r]
    return wiersze, raport_json, out


class Wystawianie(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wiersze, cls.raport, cls.out = uruchom()

    def test_cos_wyszlo(self):
        self.assertGreater(self.raport["do_wystawienia"], 0)
        self.assertTrue(self.raport["ok"])

    def test_tytul_nie_klamie_o_systemie(self):
        """Sufiks tytulu musi wynikac z pola systemu, nie byc stala."""
        for w in self.wiersze:
            tytul, system = w["*Title"], w["C:Betriebssystem"]
            if "Win" in tytul:
                self.assertIn("Windows", system,
                              f"tytul obiecuje Windows, a system to {system!r}: {tytul}")
            if "macOS" in tytul:
                self.assertTrue(system.lower().startswith(("macos", "mac os")) or not system,
                                f"tytul obiecuje macOS, a system to {system!r}")

    def test_apple_ma_wlasna_kategorie(self):
        for w in self.wiersze:
            if w["*C:Marke"] == "Apple":
                self.assertEqual(w["*Category"], "111422")
                self.assertEqual(w["C:Produktart"], "", "kategoria Apple nie ma aspektu Produktart")
            else:
                self.assertEqual(w["*Category"], "177")

    def test_cena_zawiera_doplate(self):
        doplata = json.loads((ROOT / "config" / "settings.json").read_text(
            encoding="utf-8"))["doplata_wysylka_eur"]
        for w in self.wiersze:
            self.assertGreater(float(w["*StartPrice"]), doplata)

    def test_gpsr_kompletny(self):
        for w in self.wiersze:
            for pole in ("Manufacturer Name", "Manufacturer AddressLine1", "Manufacturer City",
                         "Manufacturer Country", "Manufacturer Email", "Responsible Person 1",
                         "Responsible Person 1 Type", "Responsible Person 1 City",
                         "Responsible Person 1 Country", "Responsible Person 1 Email"):
                self.assertTrue(w[pole], f"puste pole GPSR: {pole}")

    def test_opis_bez_polskiego(self):
        for w in self.wiersze:
            tekst = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", w["*Description"])
            tekst = html.unescape(re.sub(r"<[^>]+>", " ", tekst))
            self.assertFalse([c for c in tekst if c in POLSKIE])
            self.assertNotIn("{{", w["*Description"])
            self.assertNotIn("&amp;uuml;", w["*Description"])

    def test_klasa_stanu_nie_wycieka(self):
        for w in self.wiersze:
            self.assertNotIn("Klasse A", w["*Description"])
            self.assertNotIn("Klasa", w["*Description"])

    def test_tytul_do_80_znakow(self):
        for w in self.wiersze:
            self.assertLessEqual(len(w["*Title"]), 80)

    def test_aspekty_ze_slownika_kategorii(self):
        vocab = json.loads((ROOT / "config" / "ebay-vocab.json").read_text(encoding="utf-8"))
        scisle = {"Marke", "Bildschirmgröße", "Prozessor", "Serie", "Farbe", "Konnektivität",
                  "Besonderheiten", "Passend für", "Grafikprozessortyp", "Betriebssystem"}
        for w in self.wiersze:
            slownik = vocab["kategorie"][w["*Category"]]["aspekty"]
            for kolumna, wartosc in w.items():
                if not kolumna.startswith(("C:", "*C:")) or not wartosc:
                    continue
                aspekt = kolumna.split(":", 1)[1]
                if aspekt in scisle and aspekt in slownik:
                    for czesc in wartosc.split("|"):
                        self.assertIn(czesc, slownik[aspekt], f"{aspekt} = {czesc!r}")

    def test_pola_wymagane_niepuste(self):
        vocab = json.loads((ROOT / "config" / "ebay-vocab.json").read_text(encoding="utf-8"))
        for w in self.wiersze:
            for aspekt in vocab["kategorie"][w["*Category"]]["_wymagane"]:
                self.assertTrue(w.get(f"*C:{aspekt}") or w.get(f"C:{aspekt}"),
                                f"puste pole wymagane C:{aspekt}")


class Tryby(unittest.TestCase):
    def test_nowe_pomija_juz_wystawione(self):
        wiersze, raport, _ = uruchom("nowe")
        self.assertEqual(raport["pominieto"].get("juz_wystawione"), 1)
        self.assertEqual(raport["aktywnych_na_ebay"], 1, "oferta US nie moze trafic do puli DE")

    def test_aktualizacja_daje_revise(self):
        _, raport, out = uruchom("aktualizacja")
        self.assertTrue((out / "ebay-revise.csv").exists())
        rows = list(csv.reader((out / "ebay-revise.csv").open(encoding="utf-8-sig")))
        self.assertEqual(rows[1][0], "Action")
        self.assertTrue(all(r[0] == "Revise" for r in rows[2:] if r))
        self.assertTrue(all(r[2] for r in rows[2:] if r), "kazdy wiersz musi miec Item number")

    def test_test_daje_verifyadd(self):
        out = uruchom("test")[2]
        rows = list(csv.reader((out / "ebay-add.csv").open(encoding="utf-8-sig"), delimiter=";"))
        self.assertTrue(all(r[0] == "VerifyAdd" for r in rows[2:] if r))

    def test_bramka_malego_feedu(self):
        out = Path(tempfile.mkdtemp())
        subprocess.run(
            [sys.executable, str(ROOT / "src" / "generate.py"), "--tryb", "pierwsze",
             "--feed-file", str(FIXTURE), "--nbp-rate", "4.26",
             "--min-produktow", "999", "--output-dir", str(out)],
            check=False, capture_output=True)
        raport = json.loads((out / "generation-report.json").read_text(encoding="utf-8"))
        self.assertFalse(raport["ok"])
        self.assertTrue(raport["blokady"])

    def test_aktualizacja_bez_raportu_blokuje(self):
        _, raport, _ = uruchom("aktualizacja", raport=Path("/nie/ma/takiego.csv"))
        self.assertFalse(raport["ok"])


if __name__ == "__main__":
    unittest.main()
