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
        powod = (f"\n  blokady: {self.raport.get('blokady')}"
                 f"\n  pominieto: {self.raport.get('pominieto')}"
                 f"\n  review: {(self.out / 'review.csv').read_text(encoding='utf-8-sig')[:600]}")
        self.assertTrue(self.raport["ok"], powod)
        self.assertGreater(self.raport["do_wystawienia"], 0, powod)

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

    def test_kategoria_wynika_z_typu_towaru(self):
        """Apple laptop -> 111422, pecet -> 179, reszta laptopow -> 177."""
        for w in self.wiersze:
            if w["C:Produktart"] == "Desktop":
                self.assertEqual(w["*Category"], "179")
            elif w["*C:Marke"] == "Apple":
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


class KomputeryOsobnoOdLaptopow(unittest.TestCase):
    """Pecet i laptop nie moga sie gryzc: inny szablon, inne pola, inne aspekty.

    Fixture ma dwa prawdziwe komputery z feedu sklepowego - zwykly (SKU 3959)
    i All-in-One (SKU 4967), ktory ma zostac pominiety.
    """
    PECET = "3959"
    AIO = "4967"

    @classmethod
    def setUpClass(cls):
        cls.wiersze, cls.raport, cls.out = uruchom()
        cls.wg_sku = {w["CustomLabel"]: w for w in cls.wiersze}

    def pecet(self):
        self.assertIn(self.PECET, self.wg_sku,
                      f"pecet wypadl z CSV; pominieto: {self.raport.get('pominieto')}")
        return self.wg_sku[self.PECET]

    def opis(self, wiersz):
        tekst = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", wiersz["*Description"])
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tekst)))

    def test_pecet_trafia_do_csv(self):
        self.assertEqual(self.pecet()["*Category"], "179")

    def test_all_in_one_pominiety(self):
        self.assertNotIn(self.AIO, self.wg_sku, "All-in-One nie ma byc wystawiany")

    def test_pecet_nie_obiecuje_ekranu_ani_baterii(self):
        """Najdrozszy blad starego szablonu: 'Das Display misst  bei .' u kazdego peceta."""
        wiersz = self.pecet()
        tekst = self.opis(wiersz)
        self.assertNotIn("Das Display misst", tekst)
        self.assertNotRegex(tekst, r"\bAkku\b")
        self.assertEqual(wiersz["*C:Bildschirmgröße"], "")
        self.assertEqual(wiersz["C:Maximale Auflösung"], "")

    def test_pecet_nie_zaklada_wifi_i_mikrofonu(self):
        """Dla laptopa to zalozenia bezpieczne, dla peceta twierdzenia nieprawdziwe."""
        for cecha in ("Wi-Fi", "Bluetooth", "Eingebautes Mikrofon"):
            self.assertNotIn(cecha, self.pecet()["C:Besonderheiten"])

    def test_pecet_dostaje_wlasne_aspekty(self):
        wiersz = self.pecet()
        self.assertEqual(wiersz["C:Produktart"], "Desktop")
        self.assertTrue(wiersz["C:Formfaktor"], "pecet musi miec Formfaktor")

    def test_aliasy_pol_z_feedu_dzialaja(self):
        """Feed nazywa pola peceta inaczej. Puste tu = aliasy przestaly dzialac."""
        wiersz = self.pecet()
        self.assertTrue(wiersz["C:Betriebssystem"], "alias 'Zainstalowany System'")
        self.assertTrue(wiersz["C:Konnektivität"], "scalanie 'Złącza z tyłu'")
        self.assertIn("Win", wiersz["*Title"], "sufiks tytulu wymaga aliasu systemu")

    def test_ladegeraet_wynika_z_tresci_pola(self):
        """SKU 3959 ma 'Zasilacz z przewodem', wiec Ja. Liczy sie tresc, nie samo pole."""
        self.assertEqual(self.pecet()["C:Inklusive Ladegerät"], "Ja")

    def test_opis_peceta_nie_mowi_o_notebooku(self):
        """Wspolny slownik tlumaczy 'Zasilacz z przewodem' na 'Notebook, Netzteil mit
        Kabel'. W opisie peceta profil ma to nadpisac."""
        tekst = self.opis(self.pecet())
        self.assertIn("Business-Desktop-PC", tekst)
        self.assertNotIn("Notebook, Netzteil", tekst)

    def test_laptop_zostal_przy_swoim_szablonie(self):
        laptopy = [w for w in self.wiersze if w["C:Produktart"] != "Desktop"
                   and w["*C:Marke"] != "Apple"]
        self.assertTrue(laptopy)
        for w in laptopy:
            self.assertIn("Business-Notebook", self.opis(w))
            self.assertTrue(w["*C:Bildschirmgröße"], "laptop musi miec przekatna")


class NowyKomputer(unittest.TestCase):
    """Nowy zestaw skladany ma wlasny profil: inne ConditionID, opis i marka.

    Fixture ma prawdziwa oferte z feedu (SKU 10362, Logic, RTX 3050) - w tej samej
    kategorii XML co poleasingowe, wiec rozpoznaje ja wariant po polu kondycji.
    """
    NOWY = "10362"
    POLEASINGOWY = "3959"

    @classmethod
    def setUpClass(cls):
        cls.wiersze, cls.raport, cls.out = uruchom()
        cls.wg_sku = {w["CustomLabel"]: w for w in cls.wiersze}

    def nowy(self):
        self.assertIn(self.NOWY, self.wg_sku,
                      f"nowy komputer wypadl z CSV; pominieto: {self.raport.get('pominieto')}")
        return self.wg_sku[self.NOWY]

    def opis(self, wiersz):
        tekst = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", wiersz["*Description"])
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tekst)))

    def test_wariant_rozpoznany_po_kondycji(self):
        """Ta sama kategoria XML co poleasingowy, wiec kategoria go nie odrozni."""
        self.assertEqual(self.raport["produktow_z_zapasem_wg_typu"].get("Desktop-PC nowy"), 1)

    def test_nowy_ma_condition_neu(self):
        """Bez wlasnego ConditionID leciala by wartosc _brak = 3000 'Gebraucht'."""
        self.assertEqual(self.nowy()["*ConditionID"], "1000")
        self.assertEqual(self.wg_sku[self.POLEASINGOWY]["*ConditionID"], "3000",
                         "poleasingowy ma zostac na 3000")

    def test_opis_nowego_nie_mowi_o_poleasingowym(self):
        tekst = self.opis(self.nowy())
        self.assertNotIn("Leasingrückläufer aus", tekst)
        self.assertNotIn("Sichere Datenlöschung", tekst)
        self.assertNotIn("Vorbesitzer", tekst)
        self.assertIn("Neuware", tekst)

    def test_gpsr_wskazuje_na_producenta_zestawu(self):
        """Feed podaje 'Niezdefiniowany'. Kto sklada, ten jest producentem."""
        wiersz = self.nowy()
        self.assertIn("LOGIC CONCEPT", wiersz["Manufacturer Name"].upper())
        self.assertTrue(wiersz["Responsible Person 1"], "wymagana osoba odpowiedzialna w UE")
        self.assertEqual(wiersz["Manufacturer Country"], "PL")

    def test_marka_jest_ze_slownika_ebay(self):
        vocab = json.loads((ROOT / "config" / "ebay-vocab.json").read_text(encoding="utf-8"))
        marki = vocab["kategorie"]["179"]["aspekty"]["Marke"]
        self.assertIn(self.nowy()["*C:Marke"], marki)

    def test_gwarancja_z_feedu(self):
        """Feed mowi '24 miesiace' - w ofercie ma byc '2 Jahre', nie stala z sufitu."""
        self.assertEqual(self.nowy()["C:Herstellergarantie"], "2 Jahre")

    def test_zdanie_wiodace_nie_jest_biurowe(self):
        tekst = self.opis(self.nowy())
        for biurowe in ("Online-Unterricht", "Word und Excel", "Videokonferenzen"):
            self.assertNotIn(biurowe, tekst)


class NowyTowar(unittest.TestCase):
    """Bramka dla typow, ktore wlasnego profilu dla nowego towaru jeszcze nie maja."""

    def generate(self):
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        return generate

    def test_nowy_jest_blokowany_z_nazwanym_powodem(self):
        generate = self.generate()
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        blad = generate.blokada_nowego_towaru({"Kondycja sprzętu": "Nowy"}, settings)
        self.assertIn("nowy towar", blad)
        self.assertIn("Gebraucht", blad, "powod ma tlumaczyc, czym grozi wystawienie")

    def test_poleasingowy_przechodzi(self):
        generate = self.generate()
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        poleasingowy = {"Kondycja sprzętu": "[Klasa A] komputer poleasingowy, "
                                            "w 100% sprawny, przetestowany"}
        self.assertEqual(generate.blokada_nowego_towaru(poleasingowy, settings), "")

    def test_bramke_da_sie_wylaczyc(self):
        generate = self.generate()
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        settings["towar_nowy"]["blokuj"] = False
        self.assertEqual(generate.blokada_nowego_towaru({"Kondycja sprzętu": "Nowy"}, settings), "")


class FormatyAspektow(unittest.TestCase):
    """Aspekty przepadaly po cichu na formacie, nie na braku danych."""

    def generate(self):
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        return generate

    def vocab(self, aspekt, kategoria="177"):
        vocab = json.loads((ROOT / "config" / "ebay-vocab.json").read_text(encoding="utf-8"))
        return vocab["kategorie"][kategoria]["aspekty"][aspekt]

    def test_taktowanie_ma_dwa_miejsca_po_przecinku(self):
        """Feed Allegro podaje '1.6', eBay zna tylko '1,60 GHz'. 716 ofert traci
        ten aspekt, jesli nie wyrownamy formatu."""
        generate = self.generate()
        dozwolone = self.vocab("Prozessorgeschwindigkeit")
        for surowe, oczekiwane in [("1.6", "1,60 GHz"), ("2.4", "2,40 GHz"),
                                   ("1.60", "1,60 GHz"), ("2,30", "2,30 GHz")]:
            wynik = generate.base_clock(surowe)
            self.assertEqual(wynik, oczekiwane)
            self.assertIn(wynik, dozwolone, f"{wynik} musi byc w slowniku eBaya")

    def test_pojemnosc_w_zapisie_ebaya(self):
        generate = self.generate()
        dozwolone = self.vocab("Festplattenkapazität")
        self.assertEqual(generate.clean_capacity("1000GB"), "1 TB")
        self.assertEqual(generate.clean_capacity("2000 GB"), "2 TB")
        self.assertEqual(generate.clean_capacity("120/128 GB"), "128 GB")
        self.assertEqual(generate.clean_capacity("256GB"), "256 GB")
        for wynik in ("1 TB", "128 GB", "256 GB"):
            self.assertIn(wynik, dozwolone)

    def test_slownik_nie_zalezy_od_wielkosci_liter(self):
        """Config pisze 'Brak systemu', feed 'brak systemu' - to ta sama wartosc."""
        generate = self.generate()
        mapa = {"Brak systemu": "Nicht enthalten", "_komentarz": {"zagniezdzone": "x"}}
        self.assertEqual(generate.wpis_bez_wzgledu_na_wielkosc(mapa, "brak systemu"),
                         "Nicht enthalten")
        self.assertEqual(generate.wpis_bez_wzgledu_na_wielkosc(mapa, "BRAK SYSTEMU"),
                         "Nicht enthalten")
        self.assertEqual(generate.wpis_bez_wzgledu_na_wielkosc(mapa, "co innego"), "")


class Klawiatura(unittest.TestCase):
    """Naklejki niemieckie ida na kazda klawiature, wiec uklad z feedu nie moze
    trafiac do opisu - inaczej tabela mowila 'QWERTY US', a FAQ obiecywalo naklejki."""

    @classmethod
    def setUpClass(cls):
        cls.wiersze, cls.raport, cls.out = uruchom()

    def opis(self, wiersz):
        tekst = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", wiersz["*Description"])
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tekst)))

    def test_laptop_ma_staly_tekst_klawiatury(self):
        laptopy = [w for w in self.wiersze if w["C:Produktart"] != "Desktop"]
        self.assertTrue(laptopy)
        for w in laptopy:
            tekst = self.opis(w)
            self.assertIn("Die Tastatur ist mit deutschen Tastaturaufklebern angepasst", tekst)
            self.assertIn("QWERTY, QWERTZ oder AZERTY", tekst)

    def test_uklad_z_feedu_nie_wychodzi_do_opisu(self):
        """Zaden laptop nie moze obiecywac ukladu, ktorego kupujacy nie dostanie."""
        for w in self.wiersze:
            tekst = self.opis(w)
            for uklad in ("QWERTY US", "QWERTY Nordic", "QWERTY/QWERTZ"):
                self.assertNotIn(uklad, tekst, f"{w['CustomLabel']}: {uklad} w opisie")

    def test_pecet_nie_dostaje_wiersza_o_klawiaturze(self):
        """Do komputera klawiatura nie wchodzi w sklad zestawu."""
        pecety = [w for w in self.wiersze if w["C:Produktart"] == "Desktop"]
        self.assertTrue(pecety)
        for w in pecety:
            self.assertNotIn("Tastatur-Layout", self.opis(w))


class Zlacza(unittest.TestCase):
    """Jeden wpis ze zrodla = jeden kafelek. Bez laczenia i bez gubienia."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        self.generate = generate
        self.aspects = json.loads((ROOT / "config" / "aspects.json").read_text(encoding="utf-8"))

    def kafelki(self, zrodlo):
        return self.generate.kafelki_portow(zrodlo, self.aspects, self.generate.Review())

    def test_lista_po_przecinku_jeden_do_jednego(self):
        zrodlo = "HDMI, USB 3.0, USB 3.1 typ C, RJ-45, minijack 3.5 mm (audio)"
        self.assertEqual(len(self.kafelki(zrodlo)), 5, "piec wpisow = piec kafelkow")

    def test_type_c_nie_ginie(self):
        """Wczesniej 'USB 3.1 typ C' spadalo do 'USB 3.1' - kupujacy tracil Type-C."""
        self.assertEqual(self.kafelki("USB 3.1 typ C"), ["USB 3.1 Typ-C"])
        self.assertEqual(self.kafelki("USB 3.2 typ C Gen 2"), ["USB 3.2 Gen 2 Typ-C"])
        self.assertEqual(self.kafelki("USB 3.2 typ A Gen 2"), ["USB 3.2 Gen 2"])

    def test_rozne_porty_nie_lacza_sie_w_jeden(self):
        """'8x USB 3.1 Typ A' i '2x USB 3.1 Typ-C' to nie jest '10x USB 3.1'."""
        wynik = self.kafelki("2x USB 3.1 typ A, 2x USB 3.1 typ C")
        self.assertEqual(wynik, ["2x USB 3.1", "2x USB 3.1 Typ-C"])

    def test_brak_przecinka_ale_dwie_ilosci(self):
        """Jedyny wyjatek od 1:1 - sprzedawca zapomnial przecinka."""
        self.assertEqual(self.kafelki("4 x USB 3.0 2X USB 2.0"),
                         ["4x USB 3.0", "2x USB 2.0"])

    def test_zapis_ze_sztukami(self):
        zrodlo = "USB 3.2 Gen. 1 - 3 szt. HDMI - 1 szt. RJ-45 (LAN) - 1 szt"
        self.assertEqual(self.kafelki(zrodlo),
                         ["3x USB 3.2 Gen 1", "1x HDMI", "1x RJ-45 (LAN)"])

    def test_porty_bez_reguly_nie_znikaja(self):
        """PS/2, DVI i Serial wypadaly wczesniej z opisu zupelnie."""
        for zrodlo, oczekiwane in [("PS/2", "PS/2"), ("DVI", "DVI"),
                                   ("2x PS/2", "2x PS/2")]:
            self.assertEqual(self.kafelki(zrodlo), [oczekiwane])

    def test_inne_jest_pomijane(self):
        """'inne' nic nie mowi kupujacemu i nie jest brakiem do uzupelnienia."""
        self.assertEqual(self.kafelki("inne"), [])
        self.assertEqual(self.kafelki("HDMI, inne"), ["HDMI"])

    def test_polski_nie_wycieka_z_nieznanego_portu(self):
        """Nieznana etykieta idzie surowo tylko wtedy, gdy jest bezpieczna.

        Polskie slowo w gotowym opisie blokuje CALY produkt, wiec taki wpis
        wolimy pominac niz pokazac.
        """
        self.assertEqual(self.kafelki("wyjście specjalne producenta"), [])
        self.assertEqual(self.kafelki("HDMI, wyjście specjalne producenta"), ["HDMI"])
        self.assertEqual(self.kafelki("COM Express"), ["Serielle Schnittstelle (RS-232)"])

    def test_jedno_gniazdo_moze_dac_dwie_wartosci_aspektu(self):
        wynik = self.generate.connectivity_aspect(
            [(1, "USB 3.2 typ C Gen 2")], self.aspects, self.generate.Review())
        self.assertIn("USB-C", wynik)
        self.assertIn("USB 3.2", wynik)


class ProducentDoGPSR(unittest.TestCase):
    """GPSR musi wskazywac faktycznego producenta, nie wpis z listy sprzedawcy."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        self.generate = generate
        self.settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8"))

    def test_niepewna_marka_ustepuje_modelowi(self):
        """Feed mowi 'CLEVO', ale model i nazwa mowia Panasonic ToughBook."""
        attrs = {"Producent": "CLEVO", "Model": "ToughBook CF-31"}
        self.assertEqual(
            self.generate.producent_z_modelu(attrs, "Dotykowy Panasonic ToughBook CF-31",
                                             self.settings),
            "Panasonic")

    def test_zaslepka_chiny_tez_ustepuje(self):
        attrs = {"Producent": "Chiny/reszta", "Model": "Toughbook CF-31 MK5"}
        self.assertEqual(
            self.generate.producent_z_modelu(attrs, "Panasonic ToughBook", self.settings),
            "Panasonic")

    def test_prawdziwy_clevo_zostaje_clevo(self):
        """Model bez rozpoznawalnej marki nie jest nadpisywany - trafi na wpis CLEVO."""
        attrs = {"Producent": "CLEVO", "Model": "NH55 barebone"}
        self.assertEqual(
            self.generate.producent_z_modelu(attrs, "Laptop CLEVO NH55", self.settings), "")

    def test_pewnej_marki_nie_ruszamy(self):
        """Dell z modelem Latitude zostaje Dellem - nie wchodzimy w marki spoza listy."""
        attrs = {"Producent": "DELL", "Model": "Latitude 5300"}
        self.assertEqual(
            self.generate.producent_z_modelu(attrs, "Laptop Dell Latitude", self.settings), "")

    def test_clevo_ma_komplet_danych_gpsr(self):
        manufacturers = json.loads(
            (ROOT / "config" / "manufacturers.json").read_text(encoding="utf-8"))
        blok, blad = self.generate.gpsr_block("CLEVO", manufacturers)
        self.assertEqual(blad, "")
        self.assertEqual(blok["Manufacturer Country"], "TW")
        self.assertEqual(blok["Responsible Person 1 Country"], "DE",
                         "osoba odpowiedzialna musi byc w UE")


class BramkiKonfiguracji(unittest.TestCase):
    """Bledy konfiguracji maja byc glosna blokada, nie cichym zerem produktow."""

    def cfg(self):
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        return generate, {
            "settings": json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8")),
            "vocab": json.loads((ROOT / "config" / "ebay-vocab.json").read_text(encoding="utf-8")),
        }

    def test_komplet_profili_przechodzi(self):
        generate, cfg = self.cfg()
        self.assertEqual(generate.sprawdz_profile(cfg), [])

    def test_brak_kategorii_w_slowniku_to_blokada(self):
        """Wczesniej konczylo sie to KeyError zlapanym jako 'blad_konwersji' -
        komputery znikaly, a raport mowil "ok": true."""
        generate, cfg = self.cfg()
        cfg["vocab"]["kategorie"].pop("179")
        braki = generate.sprawdz_profile(cfg)
        self.assertTrue(any("179" in b for b in braki), braki)

    def test_brak_szablonu_to_blokada(self):
        generate, cfg = self.cfg()
        cfg["settings"]["profile_produktu"]["Desktop-PC"]["szablon"] = "nie-ma-takiego.html"
        self.assertTrue(generate.sprawdz_profile(cfg))

    def test_komputery_z_allegro_sa_blokowane(self):
        """Z Allegro biora sie wylacznie laptopy - empi.xml nie ma pola 'Obudowa'."""
        generate, cfg = self.cfg()
        self.assertEqual(generate.allegro.sprawdz_kategorie(cfg), [])
        cfg["settings"]["allegro"]["kategoria_docelowa"] = "Komputery"
        self.assertTrue(generate.allegro.sprawdz_kategorie(cfg))


class Tryby(unittest.TestCase):
    def test_nowe_pomija_juz_wystawione(self):
        wiersze, raport, _ = uruchom("nowe")
        self.assertEqual(raport["pominieto"].get("juz_wystawione"), 1)
        self.assertEqual(raport["aktywnych_na_ebay"], 1, "oferta US nie moze trafic do puli DE")

    def test_aktualizacja_daje_revise(self):
        _, raport, out = uruchom("aktualizacja")
        self.assertTrue((out / "ebay-revise.csv").exists())
        with (out / "ebay-revise.csv").open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt))
        self.assertEqual(rows[1][0], "Action")
        self.assertTrue(all(r[0] == "Revise" for r in rows[2:] if r))
        self.assertTrue(all(r[2] for r in rows[2:] if r), "kazdy wiersz musi miec Item number")

    def test_test_daje_verifyadd(self):
        out = uruchom("test")[2]
        with (out / "ebay-add.csv").open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt, delimiter=";"))
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
