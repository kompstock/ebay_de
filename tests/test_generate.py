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


GW_LISTA = ROOT / "tests" / "fixtures" / "gwarancja-24.csv"


def uruchom(tryb="pierwsze", feed=FIXTURE, raport=RAPORT, gw_lista=None):
    out = Path(tempfile.mkdtemp())
    polecenie = [sys.executable, str(ROOT / "src" / "generate.py"), "--tryb", tryb,
                 "--feed-file", str(feed), "--raport", str(raport), "--nbp-rate", "4.26",
                 "--min-produktow", "1", "--output-dir", str(out)]
    if gw_lista:
        polecenie += ["--gw-lista", str(gw_lista)]
    wynik = subprocess.run(polecenie, check=False, capture_output=True)
    # 0 = ok, 2 = blokada. Cokolwiek innego to wywrotka, ktora bez tej asercji
    # przechodzila niezauwazona: pliki sa juz na dysku, wiec testy czytajace
    # tylko raport nie widzialy roznicy. Tak uciekl UnicodeEncodeError na
    # wydruku raportu w konsoli Windows.
    if wynik.returncode not in (0, 2):
        raise AssertionError(f"generate.py wywrocil sie (kod {wynik.returncode}):\n"
                             + wynik.stderr.decode("utf-8", "replace")[-2000:])
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

    def test_oferta_glowna_zawsze_12_miesiecy(self):
        """Feed mowi przy tych zestawach '24 miesiace', ale oferta glowna i tak
        dostaje 12. Inaczej blizniak GW24 nie mialby czym sie roznic."""
        tekst = self.opis(self.nowy())
        self.assertIn("12 Monate Garantie", tekst)
        self.assertNotIn("24 Monate Garantie", tekst)
        self.assertEqual(self.nowy()["C:Herstellergarantie"], "",
                         "pole eBaya dotyczy gwarancji producenta, nasza jest sprzedawcy")

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

    def test_identyczne_kafelki_sie_sumuja(self):
        """Dwa wpisy z feedu daly ten sam napis koncowy - kupujacy widzial
        '2x USB 3.1' dwa razy i nie wiedzial, czy to blad. Rozne etykiety
        dalej zostaja osobno, bo to chroni informacje o Type-C."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        self.assertEqual(generate.scal_identyczne_kafelki(["2x USB 3.1", "2x USB 3.1"]),
                         ["4x USB 3.1"])
        self.assertEqual(
            generate.scal_identyczne_kafelki(["8x USB 3.1 Typ A", "2x USB 3.1 Typ-C"]),
            ["8x USB 3.1 Typ A", "2x USB 3.1 Typ-C"])
        self.assertEqual(generate.scal_identyczne_kafelki(["1x HDMI", "PS/2", "1x HDMI"]),
                         ["2x HDMI", "PS/2"])

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


class WariantGwarancyjny(unittest.TestCase):
    """Blizniak z przedluzona gwarancja: ten sam sprzet, inne SKU, cena +20%.

    Fixture listy wskazuje 4220 (laptop - ma powstac), 3959 (pecet - ma zostac
    odrzucony) i 9999999 (nie ma go w feedzie - ma trafic do raportu).
    """
    ORYGINAL = "4220"
    BLIZNIAK = "4220GW24"

    @classmethod
    def setUpClass(cls):
        cls.wiersze, cls.raport, cls.out = uruchom(gw_lista=GW_LISTA)
        cls.wg_sku = {w["CustomLabel"]: w for w in cls.wiersze}

    def opis(self, wiersz):
        tekst = re.sub(r"(?is)<(style|script).*?</>", " ", wiersz["*Description"])
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tekst)))

    def test_blizniak_powstal(self):
        self.assertIn(self.BLIZNIAK, self.wg_sku,
                      f"pominieto: {self.raport.get('gw_pominieto')}")
        # laptop 4220, pecet 3959, nowy gaming 10362. 9999999 nie ma w feedzie.
        self.assertEqual(self.raport["gw_dodane"], 3)

    def test_nowy_gaming_tez_ma_blizniaka(self):
        """Nowy zestaw ma 24 miesiace juz w oryginale, wiec blizniak NIE dodaje
        gwarancji - nazywa ja w tytule i kosztuje 20% wiecej. Swiadoma decyzja
        handlowa. Pilnujemy tylko, zeby faktycznie sie czyms roznil."""
        self.assertIn("10362GW24", self.wg_sku,
                      f"pominieto: {self.raport.get('gw_pominieto')}")
        o, g = self.wg_sku["10362"], self.wg_sku["10362GW24"]
        self.assertNotEqual(o["*Title"], g["*Title"], "blizniak bez roznicy w tytule")
        self.assertIn("24 Monate Garantie", g["*Title"])
        self.assertAlmostEqual(float(g["*StartPrice"]) / float(o["*StartPrice"]), 1.20,
                               delta=0.01, msg="blizniak musi byc drozszy")
        self.assertEqual(g["*ConditionID"], "1000", "nowy zestaw zostaje nowy")

    def test_kazdy_blizniak_rozni_sie_od_oryginalu(self):
        """Bramka na cala rodzine wariantow: blizniak identyczny z oryginalem
        to czysty duplikat i nie ma prawa wyjsc."""
        for sku, wiersz in self.wg_sku.items():
            if not sku.endswith("GW24"):
                continue
            oryginal = self.wg_sku.get(sku[:-4])
            self.assertIsNotNone(oryginal, sku)
            self.assertNotEqual(oryginal["*Title"], wiersz["*Title"], sku)
            self.assertGreater(float(wiersz["*StartPrice"]),
                               float(oryginal["*StartPrice"]), sku)

    def test_stan_z_oryginalu(self):
        self.assertEqual(self.wg_sku[self.BLIZNIAK]["*Quantity"],
                         self.wg_sku[self.ORYGINAL]["*Quantity"])

    def test_opis_mowi_o_24_miesiacach(self):
        gw = self.opis(self.wg_sku[self.BLIZNIAK])
        org = self.opis(self.wg_sku[self.ORYGINAL])
        self.assertIn("24 Monate Garantie", gw)
        self.assertNotIn("12 Monate Garantie", gw)
        self.assertIn("12 Monate Garantie", org, "oryginal zostaje przy 12")

    def test_gwarancja_sprzedawcy_nie_udaje_producenta(self):
        """C:Herstellergarantie to gwarancja PRODUCENTA. Nasza jest sprzedawcy,
        wiec pole zostaje puste - inaczej twierdzilibysmy, ze HP daje 24 miesiace
        na laptopa poleasingowego."""
        self.assertEqual(self.wg_sku[self.BLIZNIAK]["C:Herstellergarantie"], "")
        self.assertEqual(self.wg_sku[self.ORYGINAL]["C:Herstellergarantie"], "")
        self.assertIn("24 Monate Garantie", self.opis(self.wg_sku[self.BLIZNIAK]))

    def test_tytul_rozni_sie_i_trzyma_system(self):
        """Gdyby dopisek wypadl przy obcinaniu, tytul bylby identyczny z oryginalem -
        czyli oferta bylaby prawdziwym duplikatem."""
        org = self.wg_sku[self.ORYGINAL]["*Title"]
        gw = self.wg_sku[self.BLIZNIAK]["*Title"]
        self.assertNotEqual(org, gw)
        self.assertIn("24 Monate Garantie", gw)
        self.assertLessEqual(len(gw), 80)
        self.assertRegex(gw, r"Win\d+ (Pro|Home)", "edycja systemu nie moze zostac ucieta")

    def test_pecet_tez_dostaje_blizniaka(self):
        """Komputer poleasingowy ma wlasny profil GW24 i wlasny szablon."""
        self.assertIn("3959GW24", self.wg_sku,
                      f"pominieto: {self.raport.get('gw_pominieto')}")
        blizniak = self.wg_sku["3959GW24"]
        self.assertEqual(blizniak["*Category"], "179")
        self.assertEqual(blizniak["C:Produktart"], "Desktop")
        tekst = self.opis(blizniak)
        self.assertIn("24 Monate Garantie", tekst)
        self.assertNotIn("12 Monate Garantie", tekst)
        self.assertNotIn("Tastatur-Layout", tekst, "pecet nadal bez klawiatury")

    def test_cena_rosnie_rowno_o_mnoznik(self):
        """Mnoznik dziala na cenie koncowej, wiec procent jest ten sam dla
        kazdej oferty - niezaleznie od tego, ile kosztuje sprzet."""
        for oryginal in (self.ORYGINAL, "3959"):
            o = float(self.wg_sku[oryginal]["*StartPrice"])
            g = float(self.wg_sku[oryginal + "GW24"]["*StartPrice"])
            self.assertAlmostEqual(g / o, 1.20, delta=0.01,
                                   msg=f"{oryginal}: {o} -> {g}")

    def test_sku_spoza_feedu_jest_raportowane(self):
        """Bez tego lista po cichu gnije - produkt wychodzi ze sprzedazy,
        wpis zostaje, nikt tego nie widzi."""
        self.assertIn("9999999", self.raport["gw_sku_poza_feedem"])

    def test_bez_listy_nie_ma_blizniakow(self):
        wiersze, raport, _ = uruchom()
        self.assertNotIn("4220GW24", {w["CustomLabel"] for w in wiersze})


class ListaSkuGwarancyjnych(unittest.TestCase):
    """Czytanie listy SKU z pliku - Excel zapisuje CSV raz z przecinkiem, raz ze srednikiem."""

    def modul(self):
        sys.path.insert(0, str(ROOT / "src"))
        import gwarancja
        return gwarancja

    def plik(self, tresc):
        sciezka = Path(tempfile.mkdtemp()) / "lista.csv"
        sciezka.write_text(tresc, encoding="utf-8")
        return sciezka

    def test_przecinek_i_srednik(self):
        gw = self.modul()
        for tresc in ("SKU,Uwagi\n3808,cokolwiek\n4220,\n",
                      "SKU;Uwagi\n3808;cokolwiek\n4220;\n"):
            lista, blad = gw.wczytaj_liste(self.plik(tresc), "SKU")
            self.assertEqual(blad, "")
            self.assertEqual(lista, ["3808", "4220"])

    def test_duplikaty_i_puste_wiersze_pomijane(self):
        gw = self.modul()
        lista, _ = gw.wczytaj_liste(self.plik("SKU\n3808\n3808\n\n4220\n"), "SKU")
        self.assertEqual(lista, ["3808", "4220"])

    def test_brak_kolumny_to_nazwany_blad(self):
        gw = self.modul()
        lista, blad = gw.wczytaj_liste(self.plik("Numer\n3808\n"), "SKU")
        self.assertEqual(lista, [])
        self.assertIn("SKU", blad)

    def test_pusta_lista_nie_jest_bledem(self):
        gw = self.modul()
        lista, blad = gw.wczytaj_liste(self.plik("SKU\n"), "SKU")
        self.assertEqual((lista, blad), ([], ""))

    def test_brak_pliku_to_nie_blad(self):
        """Lista jest opcjonalna. Brak pliku ma znaczyc to samo co pusta lista -
        funkcja spi. Inaczej raport wyglada na bledny, choc przebieg jest zdrowy."""
        gw = self.modul()
        self.assertEqual(gw.wczytaj_liste(Path("/nie/ma/takiego.csv"), "SKU"), ([], ""))


class OsKraju(unittest.TestCase):
    """Druga os konfiguracji: typ towaru mowi CO, kraj mowi GDZIE i po jakiemu."""

    def kraj_testowy(self, **zmiany) -> str:
        """Kopia kraju DE z podmieniona wartoscia. Sprzatana po tescie."""
        import json
        katalog = ROOT / "config" / "kraje"
        dane = json.loads((katalog / "de.json").read_text(encoding="utf-8"))
        dane.update(zmiany)
        kod = dane["kod"].lower()
        sciezka = katalog / f"{kod}.json"
        sciezka.write_text(json.dumps(dane, ensure_ascii=False), encoding="utf-8")
        self.addCleanup(sciezka.unlink)
        return kod

    def przebieg(self, kod, tryb="pierwsze", gw=True):
        out = Path(tempfile.mkdtemp())
        polecenie = [sys.executable, str(ROOT / "src" / "generate.py"), "--kraj", kod,
                     "--tryb", tryb, "--feed-file", str(FIXTURE), "--raport", str(RAPORT),
                     "--nbp-rate", "4.26", "--min-produktow", "1", "--output-dir", str(out)]
        if gw:
            polecenie += ["--gw-lista", str(ROOT / "tests/fixtures/gwarancja-24.csv")]
        wynik = subprocess.run(polecenie, check=False, capture_output=True, text=True)
        raport = json.loads((out / "generation-report.json").read_text(encoding="utf-8"))
        return raport, out, wynik

    def sku_z_pliku(self, out) -> list[str]:
        with (out / "ebay-add.csv").open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt, delimiter=";"))
        return [r[1] for r in rows[2:] if r]

    def test_niemcy_bez_sufiksu(self):
        """Dzisiejsze aukcje maja gole SKU - sufiks zerwalby z nimi powiazanie."""
        _, out, _ = self.przebieg("de")
        self.assertTrue(all("ITT" not in s for s in self.sku_z_pliku(out)))

    def test_sufiks_kraju_dochodzi_po_blizniaku_gwarancyjnym(self):
        """Kolejnosc nie jest kosmetyczna: gwarancja-24.csv trzyma GOLE SKU.
        Gdyby sufiks szedl wczesniej, lista przestalaby trafiac i warianty
        gwarancyjne przepadlyby bez zadnego komunikatu."""
        kod = self.kraj_testowy(kod="XT", sufiks_sku="ITT")
        _, out, _ = self.przebieg(kod)
        sku = self.sku_z_pliku(out)
        self.assertTrue(all(s.endswith("ITT") for s in sku), sku)
        self.assertIn("4220GW24ITT", sku, "blizniak ma zachowac oba sufiksy, w tej kolejnosci")

    def test_kraj_w_budowie_jest_blokada_a_nie_wysypka(self):
        """Brak tlumaczen ma zatrzymac przebieg z nazwanym powodem. Gdyby
        przeszedl, eBay przyjalby plik z pustymi aspektami i nikt by nie zauwazyl."""
        # Kraj SZTUCZNY, nie 'it': prawdziwe rynki z czasem sie uzupelniaja
        # i test przestalby cokolwiek sprawdzac.
        kod = self.kraj_testowy(kod="XB", pliki={
            "naglowek": "config/kraje/xb/ebay-header.csv",
            "slownik": "config/kraje/xb/ebay-vocab.json",
            "aspekty": "config/kraje/xb/aspects.json",
            "tlumaczenia": "config/kraje/xb/translations.json"})
        raport, out, _ = self.przebieg(kod)
        self.assertFalse(raport["ok"])
        powod = " ".join(raport["blokady"])
        self.assertIn("kraj nie jest gotowy", powod)
        self.assertIn("ebay-header.csv", powod, "komunikat ma nazwac brakujacy plik")
        self.assertFalse((out / "ebay-add.csv").exists(), "zaden CSV nie ma powstac")

    def test_wlochy_sa_kompletne(self):
        """Odwrotna strona bramki: kiedy kraj ma juz komplet, przebieg przechodzi
        i produkuje oferty. Inaczej 'nie wysypalo sie' znaczyloby tyle samo, co
        'nic nie powstalo'."""
        raport, out, _ = self.przebieg("it", tryb="test")
        self.assertEqual(raport["blokady"], [])
        self.assertTrue(raport["ok"])
        sku = self.sku_z_pliku(out)
        self.assertTrue(sku, "wloski przebieg ma wystawic oferty")
        self.assertTrue(all(s.endswith("ITT") for s in sku), sku)

    def test_naglowek_musi_pasowac_do_kraju(self):
        """Niemiecki naglowek przy wloskim rynku znaczylby porownywanie aukcji
        jednego rynku z plikiem na drugi - czyli zerowanie wszystkiego."""
        kod = self.kraj_testowy(kod="XN", naglowek_site_id="Italy")
        raport, _, _ = self.przebieg(kod)
        self.assertFalse(raport["ok"])
        self.assertIn("SiteID", " ".join(raport["blokady"]))

    def test_przekatna_w_zapisie_rynku(self):
        """Niemcy pisza '15,6 Zoll', Wlosi '15,6\"'. Aspekt jest WYMAGANY, wiec
        zly zapis to nie puste pole, tylko odrzucona oferta."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        self.assertEqual(generate.screen_size_de('15,6"', "{n} Zoll"), "15,6 Zoll")
        self.assertEqual(generate.screen_size_de("15.6 cali", '{n}"'), '15,6"')
        self.assertEqual(generate.screen_size_de("", '{n}"'), "")

    def test_nazwa_kolumny_i_nazwa_aspektu_ida_z_jednego_zrodla(self):
        """W pliku eBaya kolumna ma przedrostek i gwiazdke, a slownik dozwolonych
        wartosci indeksuje sie sama nazwa. Trzymamy jedno i wyprowadzamy drugie,
        zeby nie dalo sie ich rozjechac."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        kol, asp = generate.nazwy_kolumn(
            {"kraj": {"kolumny": {"marka": "*C:Marca", "seria": "C:Serie"}}})
        self.assertEqual((kol("marka"), asp("marka")), ("*C:Marca", "Marca"))
        self.assertEqual((kol("seria"), asp("seria")), ("C:Serie", "Serie"))

    def test_w_kodzie_nie_ma_juz_niemieckich_nazw_kolumn(self):
        """Bramka na przyszlosc: nowa kolumna ma isc do pliku kraju, nie do kodu.
        Jedna wpisana na sztywno i wloska oferta cicho traci ten aspekt.

        Patrzymy na sam KOD - docstringi i komentarze wolno podawac przyklady
        po niemiecku, bo one niczego nie wystawiaja.
        """
        import ast
        drzewo = ast.parse((ROOT / "src" / "generate.py").read_text(encoding="utf-8"))
        # Docstring to jeden duzy napis, wiec sam z siebie nie pasuje do wzorca
        # pojedynczej nazwy kolumny - przyklady w dokumentacji nie zaklocaja.
        zaszyte = sorted({
            w.value for w in ast.walk(drzewo)
            if isinstance(w, ast.Constant) and isinstance(w.value, str)
            and re.fullmatch(r"\*?C:[^{]+", w.value)
        })
        self.assertEqual(zaszyte, ["C:Prozessor"],
                         "jedyny dopuszczony wyjatek to etykieta w review.csv")

    def test_w_kodzie_nie_ma_niemieckich_nazw_aspektow(self):
        """Osobna bramka od poprzedniej: nazwa aspektu wystepuje tez BEZ
        przedrostka 'C:', jako klucz slownika dozwolonych wartosci. Taki zapis
        przeszedl poprzedni test i wywalil wloski przebieg przez KeyError."""
        import ast
        drzewo = ast.parse((ROOT / "src" / "generate.py").read_text(encoding="utf-8"))
        niemieckie = {"Marke", "Bildschirmgröße", "Prozessor", "Festplattentyp",
                      "Produktart", "Formfaktor", "Festplattenkapazität", "Grafikprozessor",
                      "Besonderheiten", "Erscheinungsjahr", "Farbe", "Modell",
                      "Betriebssystem", "Arbeitsspeichergröße", "Konnektivität",
                      "Herstellergarantie", "Serie", "Grafikprozessortyp"}
        zaszyte = sorted({
            w.value for w in ast.walk(drzewo)
            if isinstance(w, ast.Constant) and isinstance(w.value, str)
            and w.value in niemieckie
        })
        self.assertEqual(zaszyte, [], "nazwa aspektu ma isc wylacznie z pliku kraju")

    def test_kraj_nadpisuje_jezyk_w_profilu_nie_ruszajac_regul(self):
        """Profil lezy na przecieciu dwoch osi. Kraj podmienia to, co czyta
        kupujacy; reguly i ConditionID zostaja wspolne, zeby nowy typ towaru
        dodawalo sie raz, a nie raz na kazdy rynek."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        settings = {
            "profile_produktu": {"Notebook": {"gwarancja": "12 Monate Garantie",
                                              "condition_id": "3000", "szablon": "x.html"}},
            "profile_kraju": {"Notebook": {"gwarancja": "12 mesi di garanzia"}},
            "typ_produktu": {"_domyslnie": "Notebook"},
        }
        profil = generate.profil_produktu("Laptopy", settings)
        self.assertEqual(profil["gwarancja"], "12 mesi di garanzia")
        self.assertEqual(profil["condition_id"], "3000", "reguly zostaja wspolne")

    def test_generacja_procesora_w_zapisie_rynku(self):
        """Zapas, gdy dokladnego modelu nie ma w slowniku eBaya. Aspekt procesora
        jest WYMAGANY, wiec zla nazwa generacji to nie puste pole, tylko oferta
        odrzucona w calosci. Po niemiecku '8. Gen', po wlosku '8a generazione'."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        surowy = "i5-8265U, 6MB Cache, 8 gen."
        self.assertIn("Intel Core i5 8. Gen", generate.cpu_candidates(surowy, "{n}. Gen"))
        self.assertIn("Intel Core i5 8a generazione",
                      generate.cpu_candidates(surowy, "{n}a generazione"))

    def test_oba_rynki_wystawiaja_tyle_samo_z_tego_samego_feedu(self):
        """Bramka na cala os kraju: jesli Wlochy gubia oferty, ktore Niemcy
        wystawiaja, to znaczy, ze ktorys napis albo format zostal niemiecki."""
        de = self.przebieg("de", tryb="test")[0]
        it = self.przebieg("it", tryb="test")[0]
        self.assertEqual(it["do_wystawienia"], de["do_wystawienia"],
                         f"IT pominelo: {it['pominieto']}")

    def test_kazdy_blok_aspektow_jest_rozstrzygniety(self):
        """Bramka na dokladanie kolejnych rynkow.

        Wloskie oferty wyszly z niemieckimi wartosciami ('Eingebautes Mikrofon',
        'Arbeitsstation', 'microSD-Card-Slot') nie dlatego, ze ktos zle
        przetlumaczyl, tylko dlatego, ze tych blokow NIE BYLO w arkuszu do
        tlumaczenia. Skopiowaly sie z niemieckiego i nikt ich nie zobaczyl.

        Dlatego kazdy blok aspects.json musi byc albo eksportowany, albo jawnie
        wpisany jako nietlumaczony - z powodem. Nowy blok bez decyzji wywala ten
        test, zamiast po cichu wyjsc po niemiecku na obcym rynku.
        """
        sys.path.insert(0, str(ROOT / "tools"))
        import importlib
        eksport = importlib.import_module("eksport_do_tlumaczenia")
        aspekty = json.loads(
            (ROOT / "config" / "aspects.json").read_text(encoding="utf-8"))
        rozstrzygniete = (set(eksport.BLOK_DO_ASPEKTU) | set(eksport.ZAGNIEZDZONE)
                          | {b for b, *_ in eksport.LISTY} | set(eksport.NIE_TLUMACZYMY)
                          | {"porty_reguly"})
        bloki = {k for k in aspekty if not k.startswith("_")}
        self.assertEqual(bloki - rozstrzygniete, set(),
                         "blok bez decyzji: dopisz go do eksportu albo do NIE_TLUMACZYMY")

    def test_wloska_oferta_nie_ma_w_sobie_niemieckiego(self):
        """Bramka, ktorej brakowalo przez caly czas budowy rynku wloskiego.

        Niemieckie napisy wychodzily na eBay.it piec razy z rzedu, za kazdym
        razem z innego bloku konfiguracji: cechy domyslne, przeznaczenie,
        etykiety zlacz, zdania wiodace, dopisek tytulu. Za kazdym razem
        szukalem KONKRETNYCH slow, ktore akurat podejrzewalem - czyli czarnej
        listy, ktora z definicji znajduje tylko to, o czym juz sie wie.

        Ten test dziala odwrotnie: bierze CALE wyjscie wloskiego przebiegu
        i szuka cech jezyka niemieckiego. Nie trzeba wiedziec z gory, ktory
        blok sie wysypie.
        """
        import unicodedata
        NIEMIECKIE_ZNAKI = set("äöüßÄÖÜ")
        # Slowa funkcyjne i typowo niemieckie konstrukcje. Zadne z nich nie jest
        # poprawnym wloskim - 'die', 'das', 'und' po wlosku nie wystepuja.
        NIEMIECKIE_SLOWA = re.compile(
            r"(?<![\wàèéìòù])(der|die|das|und|mit|f[uü]r|ohne|nicht|wird|werden|sind|"
            r"ist|ein|eine|einen|einem|auch|oder|aber|sehr|nach|vor|bei|zum|zur|"
            r"Ger[aä]t|Monate|Monat|Garantie|Zoll|Festplatte|Arbeitsspeicher|"
            r"Gebrauchsspuren|Kratzer|Lieferumfang|Zustand|vorhanden|zutreffend)"
            r"(?![\wàèéìòù])", re.IGNORECASE)

        raport, out, _ = self.przebieg("it", tryb="test")
        self.assertTrue(raport["ok"], raport["blokady"])
        with (out / "ebay-add.csv").open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt, delimiter=";"))
        naglowek = rows[1]
        wiersze = [dict(zip(naglowek, r)) for r in rows[2:] if r]
        self.assertTrue(wiersze, "wloski przebieg nic nie wystawil")

        znalezione = []
        for wiersz in wiersze:
            widoczne = [wiersz.get("*Title", ""), wiersz.get("*Description", "")]
            widoczne += [v for k, v in wiersz.items() if k.startswith(("C:", "*C:"))]
            tekst = " ".join(widoczne)
            czysty = re.sub(r"<[^>]+>", " ", html.unescape(tekst))
            for znak in NIEMIECKIE_ZNAKI:
                if znak in czysty:
                    znalezione.append(f"{wiersz['CustomLabel']}: znak {znak!r}")
                    break
            else:
                trafienie = NIEMIECKIE_SLOWA.search(czysty)
                if trafienie:
                    kontekst = czysty[max(0, trafienie.start() - 40):trafienie.end() + 40]
                    znalezione.append(f"{wiersz['CustomLabel']}: {trafienie.group(0)!r}"
                                      f" w: ...{' '.join(kontekst.split())}...")
        self.assertEqual(znalezione[:5], [],
                         f"niemiecki w {len(znalezione)} wloskich ofertach")

    def test_nieznany_kraj_konczy_sie_zrozumialym_bledem(self):
        wynik = subprocess.run(
            [sys.executable, str(ROOT / "src" / "generate.py"), "--kraj", "nieistnieje",
             "--tryb", "test"], check=False, capture_output=True, text=True)
        self.assertNotEqual(wynik.returncode, 0)
        self.assertIn("nie znam kraju", wynik.stderr + wynik.stdout)


class RaportOdtwarzalny(unittest.TestCase):
    def test_do_uzupelnienia_ma_stala_kolejnosc(self):
        """Dwa przebiegi na tym samym feedzie maja dac ten sam raport.

        'top()' opieral sie na Counter.most_common(), ktore przy remisie zwraca
        kolejnosc wstawiania - a ta idzie za iteracja po zbiorze i zmienia sie
        miedzy procesami. Listy w 'do_uzupelnienia' to prawie same remisy po
        jednym produkcie, wiec raport wychodzil raz tak, raz tak i nie dalo sie
        porownac dwoch przebiegow.
        """
        pierwszy = uruchom()[1]["do_uzupelnienia"]
        drugi = uruchom()[1]["do_uzupelnienia"]
        self.assertEqual(pierwszy, drugi)


class DuplikatWAktualizacji(unittest.TestCase):
    """Najgrozniejszy scenariusz calego modulu duplikatow.

    Tryb 'aktualizacja' zeruje kazde aktywne SKU, ktorego nie ma w feedzie,
    a liste 'w_feedzie' buduje z produktow JUZ po odsianiu duplikatow. Gdyby
    odsianie objelo oferte aktywna, nastepny przebieg wystawilby jej Revise
    z iloscia 0 - czyli zdjalby zywa aukcje. Te testy ida cala droga przez
    generate.py i sprawdzaja plik wynikowy, nie sama funkcje.
    """

    NAGLOWEK = ("Item number,Title,Variation details,Custom label (SKU),Available quantity,"
                "Format,Currency,Start price,Auction Buy It Now price,Reserve price,"
                "Current price,Sold quantity,Watchers,Bids,Start date,End date,"
                "eBay category 1 name,eBay category 1 number,eBay category 2 name,"
                "eBay category 2 number,Condition,Listing site")

    def przygotuj(self, aktywne_sku):
        """Feed z blizniaczą parą Shoper/Allegro plus eksport aktywnych ofert."""
        import xml.etree.ElementTree as ET
        katalog = Path(tempfile.mkdtemp())
        drzewo = ET.parse(FIXTURE)
        root = drzewo.getroot()
        wzor = next(o for o in root.findall("./o")
                    if {a.get("name"): a.text for a in o.findall("./attrs/a")}.get("SKU") == "4220")
        blizniak = ET.fromstring(ET.tostring(wzor))
        for element in blizniak.findall("./attrs/a"):
            if element.get("name") == "SKU":
                element.text = "ALG1"
        ET.SubElement(blizniak.find("./attrs"), "a", {"name": "Źródło"}).text = "Allegro"
        root.append(blizniak)
        feed = katalog / "feed.xml"
        drzewo.write(feed, encoding="utf-8")

        wiersze = [self.NAGLOWEK]
        for numer, sku in enumerate(aktywne_sku, start=307000000000):
            wiersze.append(f"{numer},Testowa oferta,,{sku},5,FIXED_PRICE,EUR,100.0,,,"
                           f"100.0,0,,,,,PC Laptops,177,,,Used,DE")
        raport = katalog / "aktywne.csv"
        raport.write_text("\n".join(wiersze) + "\n", encoding="utf-8")
        return feed, raport

    def revise(self, out):
        with (out / "ebay-revise.csv").open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt))
        return [dict(zip(rows[1], r)) for r in rows[2:] if r]

    def test_obie_polowki_aktywne_nie_daja_zerowania(self):
        feed, raport = self.przygotuj(["4220", "ALG1"])
        _, wynik, out = uruchom("aktualizacja", feed=feed, raport=raport)
        self.assertEqual(wynik["duplikaty"]["kolizje_aktywnych"], 1)
        self.assertEqual(wynik["duplikaty"]["wstrzymanych"], 0)
        self.assertEqual(wynik["w_tym_zerowanych"], 0, "zadna z aukcji nie moze byc zerowana")
        zerowane = [w["Custom label (SKU)"] for w in self.revise(out)
                    if w["Available quantity"] == "0"]
        self.assertEqual(zerowane, [])

    def test_wstrzymana_polowka_nie_zeruje_aktywnej(self):
        """Allegro jeszcze nie na eBayu: wstrzymujemy je, a aktywny Shoper
        dostaje zwykla aktualizacje, nie zerowanie."""
        feed, raport = self.przygotuj(["4220"])
        _, wynik, out = uruchom("aktualizacja", feed=feed, raport=raport)
        self.assertEqual(wynik["duplikaty"]["wstrzymane_sku"], ["ALG1"])
        self.assertEqual(wynik["w_tym_zerowanych"], 0)
        zerowane = [w["Custom label (SKU)"] for w in self.revise(out)
                    if w["Available quantity"] == "0"]
        self.assertEqual(zerowane, [])


class StanyOgraniczone(unittest.TestCase):
    """Drugi plik trybu 'aktualizacja': te same aukcje, podmieniona ilosc.

    Sluzy do dawkowania zapasu na eBayu, wiec wgrywa sie ALBO jego, ALBO
    prawdziwy ebay-revise.csv. Ma wlasne wykrywanie zmian, bo ograniczenie
    potrafi byc inne nawet wtedy, gdy stan w feedzie wcale sie nie ruszyl.
    """

    NAGLOWEK = DuplikatWAktualizacji.NAGLOWEK

    def aktywne(self, wiersze):
        """wiersze: (sku, ilosc na eBayu, cena na eBayu)."""
        katalog = Path(tempfile.mkdtemp())
        linie = [self.NAGLOWEK]
        for numer, (sku, ilosc, cena) in enumerate(wiersze, start=307000000000):
            linie.append(f"{numer},Testowa oferta,,{sku},{ilosc},FIXED_PRICE,EUR,{cena},,,"
                         f"{cena},0,,,,,PC Laptops,177,,,Used,DE")
        plik = katalog / "aktywne.csv"
        plik.write_text("\n".join(linie) + "\n", encoding="utf-8")
        return plik

    def czytaj(self, sciezka):
        with sciezka.open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt))
        return [dict(zip(rows[1], r)) for r in rows[2:] if r]

    def ilosci(self, sciezka):
        return {w["Custom label (SKU)"]: w["Available quantity"] for w in self.czytaj(sciezka)}

    def mapuj(self, ile):
        """Czego oczekujemy wg aktualnej reguly z configu. Testy integracyjne
        pilnuja, czy generator w ogole stosuje ograniczenie i do wlasciwej
        kolumny - same wartosci pilnuje test_config_ma_progi_ktore_ustalilismy,
        zeby zmiana progow nie rozjezdzala polowy pliku."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        regula = generate.regula_stanow(json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")))
        return str(generate.ogranicz_stan(ile, regula))

    def test_progi_dzialaja_na_granicach(self):
        """Sama logika progow, na regule wpisanej tutaj - zeby zmiana wartosci
        w configu nie wymagala poprawiania tego testu."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        regula = {"progi": [[10, 0], [100, 7]], "powyzej": 20}
        oczekiwane = {0: 0, 1: 0, 9: 0, 10: 7, 11: 7, 99: 7, 100: 20, 5000: 20}
        self.assertEqual({ile: generate.ogranicz_stan(ile, regula) for ile in oczekiwane},
                         oczekiwane)

    def test_progi_dzialaja_bez_wzgledu_na_kolejnosc_w_configu(self):
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        pomieszana = {"progi": [[100, 7], [10, 0]], "powyzej": 20}
        self.assertEqual([generate.ogranicz_stan(i, pomieszana) for i in (5, 50, 500)],
                         [0, 7, 20])

    def test_config_ma_progi_ktore_ustalilismy(self):
        """Wartosci z config/settings.json. Ten test ma padac przy kazdej ich
        zmianie - progi sa decyzja handlowa, nie szczegolem technicznym."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        regula = generate.regula_stanow(json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")))
        oczekiwane = {0: 0, 9: 0, 10: 8, 99: 8, 100: 15, 5000: 15}
        self.assertEqual({ile: generate.ogranicz_stan(ile, regula) for ile in oczekiwane},
                         oczekiwane)
        # Kod i config musza mowic to samo - inaczej brak wpisu w configu
        # po cichu przestawia progi na inne niz te uzgodnione.
        self.assertEqual(generate.STANY_OGRANICZONE_DOMYSLNIE["progi"], regula["progi"])
        self.assertEqual(generate.STANY_OGRANICZONE_DOMYSLNIE["powyzej"], regula["powyzej"])

    def test_oba_pliki_powstaja_i_roznia_sie_tylko_iloscia(self):
        raport = self.aktywne([("4220", 5, "100.0")])
        _, wynik, out = uruchom("aktualizacja", raport=raport)
        prawdziwy = self.czytaj(out / "ebay-revise.csv")
        ograniczony = self.czytaj(out / "ebay-revise-stany.csv")
        self.assertEqual([w["Available quantity"] for w in prawdziwy], ["51"])
        self.assertEqual([w["Available quantity"] for w in ograniczony], [self.mapuj(51)])
        self.assertEqual(wynik["do_aktualizacji_ograniczone"], 1)
        # Poza iloscia wiersze musza byc identyczne - plik pomocniczy nie moze
        # przy okazji ruszac ceny, tytulu ani numeru aukcji.
        bez = [{k: v for k, v in w.items() if k != "Available quantity"}
               for w in (prawdziwy[0], ograniczony[0])]
        self.assertEqual(bez[0], bez[1])

    def test_brak_zmiany_stanu_i_tak_trafia_do_ograniczonego(self):
        """Najwazniejszy przypadek. Feed 51, aukcja 51, cena bez zmian: prawdziwy
        plik nie ma czego poprawiac, ale limit 10 dopiero trzeba ustawic. Gdyby
        plik pomocniczy dziedziczyl warunek 'bez_zmian', limit nigdy by nie
        zadzialal - plik wychodzilby pusty przy kazdym przebiegu."""
        raport = self.aktywne([("4220", 51, "279.0")])
        _, wynik, out = uruchom("aktualizacja", raport=raport)
        self.assertEqual(wynik["do_aktualizacji"], 0)
        self.assertEqual(self.czytaj(out / "ebay-revise.csv"), [])
        self.assertEqual(self.ilosci(out / "ebay-revise-stany.csv"), {"4220": self.mapuj(51)})

    def test_ponizej_dziesieciu_schodzi_do_zera_tylko_w_pomocniczym(self):
        """Prawdziwy plik ma zostac nietkniety: to on mowi prawde o magazynie."""
        raport = self.aktywne([("4220", 5, "100.0"), ("4242", 5, "100.0"),
                               ("3809", 5, "100.0")])
        _, _, out = uruchom("aktualizacja", raport=raport)
        # 4242 ma w feedzie 1 sztuke, 3809 - szesc.
        self.assertEqual(self.ilosci(out / "ebay-revise.csv"),
                         {"4220": "51", "4242": "1", "3809": "6"})
        self.assertEqual(self.ilosci(out / "ebay-revise-stany.csv"),
                         {"4220": self.mapuj(51), "4242": self.mapuj(1),
                          "3809": self.mapuj(6)})

    def test_zerowanie_jest_w_obu_plikach(self):
        """SKU zniknelo z feedu - to zdjecie aukcji, nie dawkowanie zapasu,
        wiec oba pliki musza je zerowac tak samo."""
        raport = self.aktywne([("4220", 5, "100.0"), ("4242", 5, "100.0"),
                               ("3809", 5, "100.0"), ("NIE-MA-W-FEEDZIE", 4, "100.0")])
        _, wynik, out = uruchom("aktualizacja", raport=raport)
        self.assertEqual(wynik["w_tym_zerowanych"], 1)
        for nazwa in ("ebay-revise.csv", "ebay-revise-stany.csv"):
            self.assertEqual(self.ilosci(out / nazwa).get("NIE-MA-W-FEEDZIE"), "0", nazwa)

    def test_blokada_zerowania_nie_zostawia_zadnego_pliku(self):
        """Bezpiecznik masowego zerowania musi zatrzymac oba pliki naraz.
        Gdyby zatrzymal tylko prawdziwy, wgralibysmy zdjecie aukcji tylnymi
        drzwiami - przez plik pomocniczy. Raport tez musi pokazac zero, bo
        inaczej strona obiecuje plik, ktorego nie ma."""
        raport = self.aktywne([("4220", 5, "100.0"), ("ZNIKNELO-1", 4, "100.0"),
                               ("ZNIKNELO-2", 4, "100.0")])
        _, wynik, out = uruchom("aktualizacja", raport=raport)
        self.assertFalse(wynik["ok"])
        self.assertFalse((out / "ebay-revise.csv").exists())
        self.assertFalse((out / "ebay-revise-stany.csv").exists())
        self.assertEqual(wynik["do_aktualizacji"], 0)
        self.assertEqual(wynik["do_aktualizacji_ograniczone"], 0)

    def test_pomocniczy_nie_powstaje_poza_aktualizacja(self):
        for tryb in ("pierwsze", "nowe", "test"):
            _, wynik, out = uruchom(tryb)
            self.assertFalse((out / "ebay-revise-stany.csv").exists(), tryb)
            self.assertIsNone(wynik["stany_ograniczone"], tryb)


class SkuReczne(unittest.TestCase):
    """Aukcje prowadzone poza generatorem.

    Tryb 'aktualizacja' zeruje kazde aktywne SKU nieobecne w feedzie. Oferta
    wystawiona recznie (tablet, ktorego kategorii generator nie obsluguje) nie
    ma jak trafic do feedu, wiec pierwszy przebieg po jej wystawieniu zdjalby
    ja z eBaya. Lista recznych SKU jest jedyna rzecza, ktora ja chroni.
    """

    NAGLOWEK = DuplikatWAktualizacji.NAGLOWEK

    def srodowisko(self, aktywne, lista=None):
        """aktywne: (sku, ilosc). lista: tresc pliku sku-reczne.csv albo None."""
        katalog = Path(tempfile.mkdtemp())
        linie = [self.NAGLOWEK]
        for numer, (sku, ilosc) in enumerate(aktywne, start=307000000000):
            linie.append(f"{numer},Testowa oferta,,{sku},{ilosc},FIXED_PRICE,EUR,390.0,,,"
                         f"390.0,0,,,,,PC Laptops,177,,,Used,DE")
        raport = katalog / "aktywne.csv"
        raport.write_text("\n".join(linie) + "\n", encoding="utf-8")
        plik = katalog / "sku-reczne.csv"
        plik.write_text(lista if lista is not None else "SKU;ilosc\n", encoding="utf-8")
        return raport, plik

    def uruchom(self, raport, plik):
        out = Path(tempfile.mkdtemp())
        wynik = subprocess.run(
            [sys.executable, str(ROOT / "src" / "generate.py"), "--tryb", "aktualizacja",
             "--feed-file", str(FIXTURE), "--raport", str(raport), "--nbp-rate", "4.26",
             "--min-produktow", "1", "--sku-reczne", str(plik), "--output-dir", str(out)],
            check=False, capture_output=True)
        if wynik.returncode not in (0, 2):
            raise AssertionError(wynik.stderr.decode("utf-8", "replace")[-1500:])
        raport_json = json.loads((out / "generation-report.json").read_text(encoding="utf-8"))
        return raport_json, out

    def ilosci(self, out, nazwa="ebay-revise.csv"):
        sciezka = out / nazwa
        if not sciezka.exists():
            return {}
        with sciezka.open(encoding="utf-8-sig") as uchwyt:
            rows = list(csv.reader(uchwyt))
        return {w[11]: (w[8], w[6]) for w in rows[2:] if w}

    # 4220, 4242 i 3809 sa w fixture; RECZNY-1 nie jest - i o to chodzi.
    AKTYWNE = [("4220", 5), ("4242", 5), ("3809", 5), ("RECZNY-1", 20)]

    def test_bez_listy_reczne_sku_jest_zerowane(self):
        """Bramka wyjsciowa: gdyby to przestalo byc prawda, cala lista
        stalaby sie zbedna i test ponizej nic by nie dowodzil."""
        raport, plik = self.srodowisko(self.AKTYWNE, lista="SKU;ilosc\n")
        wynik, out = self.uruchom(raport, plik)
        self.assertEqual(wynik["w_tym_zerowanych"], 1)
        self.assertEqual(self.ilosci(out).get("RECZNY-1", ("brak",))[0], "0")

    def test_z_lista_sku_nie_jest_zerowane(self):
        raport, plik = self.srodowisko(self.AKTYWNE, lista="SKU;ilosc\nRECZNY-1;20\n")
        wynik, out = self.uruchom(raport, plik)
        self.assertEqual(wynik["w_tym_zerowanych"], 0)
        self.assertEqual(wynik["sku_reczne"]["chronionych_przed_zerowaniem"], ["RECZNY-1"])
        self.assertNotIn("RECZNY-1", self.ilosci(out))

    def test_rozjechana_ilosc_jest_poprawiana_a_cena_nie(self):
        """Na eBayu 5 szt., w pliku 20 -> Revise z 20. Cena musi zostac ta
        z aukcji: generator nie zna ceny takiego towaru i nie moze zgadywac."""
        raport, plik = self.srodowisko(
            [("4220", 5), ("4242", 5), ("3809", 5), ("RECZNY-1", 5)],
            lista="SKU;ilosc\nRECZNY-1;20\n")
        wynik, out = self.uruchom(raport, plik)
        self.assertEqual(wynik["sku_reczne"]["poprawiono_ilosc"], 1)
        self.assertEqual(self.ilosci(out)["RECZNY-1"], ("20", "390.0"))

    def test_pusta_ilosc_znaczy_nie_ruszaj_wcale(self):
        raport, plik = self.srodowisko(
            [("4220", 5), ("4242", 5), ("3809", 5), ("RECZNY-1", 7)],
            lista="SKU;ilosc\nRECZNY-1;\n")
        wynik, out = self.uruchom(raport, plik)
        self.assertEqual(wynik["w_tym_zerowanych"], 0)
        self.assertEqual(wynik["sku_reczne"]["poprawiono_ilosc"], 0)
        self.assertNotIn("RECZNY-1", self.ilosci(out))

    def test_wpis_reczny_wygrywa_z_progami_stanow(self):
        """20 szt. wpadloby w przedzial 10-99 i zeszlo do 8. Wpis reczny to
        juz swiadoma decyzja - plik pomocniczy nie moze jej poprawiac."""
        raport, plik = self.srodowisko(
            [("4220", 5), ("4242", 5), ("3809", 5), ("RECZNY-1", 5)],
            lista="SKU;ilosc\nRECZNY-1;20\n")
        _, out = self.uruchom(raport, plik)
        self.assertEqual(self.ilosci(out, "ebay-revise-stany.csv")["RECZNY-1"][0], "20")

    def test_feed_ma_pierwszenstwo_przed_lista(self):
        """SKU z listy, ktore JEST w feedzie, nie moze zamrozic prawdziwego
        stanu - inaczej lista po cichu psulaby zwykly produkt."""
        raport, plik = self.srodowisko(self.AKTYWNE, lista="SKU;ilosc\n4220;20\nRECZNY-1;20\n")
        wynik, out = self.uruchom(raport, plik)
        self.assertEqual(wynik["sku_reczne"]["pominieto_bo_sa_w_feedzie"], ["4220"])
        # 4220 ma w fixture 51 sztuk i ma dostac 51, a nie 20 z listy.
        self.assertEqual(self.ilosci(out)["4220"][0], "51")

    def test_nieliczbowa_ilosc_zatrzymuje_przebieg(self):
        raport, plik = self.srodowisko(self.AKTYWNE, lista="SKU;ilosc\nRECZNY-1;dwadziescia\n")
        wynik, out = self.uruchom(raport, plik)
        self.assertFalse(wynik["ok"])
        self.assertTrue(any("nieliczbowa ilosc" in b for b in wynik["blokady"]), wynik["blokady"])
        self.assertFalse((out / "ebay-revise.csv").exists())

    def test_brak_pliku_nie_jest_bledem(self):
        raport, plik = self.srodowisko(self.AKTYWNE)
        wynik, _ = self.uruchom(raport, Path(plik.parent / "nie-ma-takiego.csv"))
        self.assertTrue(wynik["ok"])
        self.assertEqual(wynik["sku_reczne"], "lista pusta albo brak pliku")


class ZdaniaWiodace(unittest.TestCase):
    """Pecet nie moze o sobie mowic 'laptop'.

    Zdania wiodace sa wybierane z puli po hashu SKU. Pecety nie mialy wlasnej
    puli i brały laptopową, w ktorej 4 z 10 zdan mowia wprost 'Laptop' /
    'notebook'. Efekt: HP T630 Thin Client opisany jako 'Un notebook potente'.
    """

    LAPTOPOWE = ("notebook", "laptop", "portatile", "portatili")

    def pule_pecetow(self):
        """(nazwa_rynku, lista zdan) dla kazdej puli, z ktorej korzystaja pecety."""
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        wynik = []
        for kod in sorted(p.stem for p in (ROOT / "config" / "kraje").glob("*.json")):
            settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
            kraj = json.loads((ROOT / "config" / "kraje" / f"{kod}.json").read_text(encoding="utf-8"))
            settings.update({k: v for k, v in kraj.items()
                             if not k.startswith("_") and k not in ("pliki", "katalog_szablonow")})
            for typ, attrs in (("Desktop-PC", {}),
                               ("Desktop-PC GW24", {"Wariant gwarancyjny": "GW24"}),
                               ("Desktop-PC nowy", {"Kondycja sprzętu": "Nowy"})):
                profil = generate.profil_produktu("Komputery", settings, attrs)
                klucz = profil.get("zdania_wiodace", "zdania_wiodace")
                lista = settings.get(klucz, {}).get("lista", [])
                self.assertTrue(lista, f"{kod}/{typ}: pula '{klucz}' jest pusta")
                wynik.append((f"{kod}/{typ} ({klucz})", lista))
        return wynik

    def test_zadne_zdanie_dla_peceta_nie_mowi_o_laptopie(self):
        for skad, lista in self.pule_pecetow():
            for zdanie in lista:
                trafione = [w for w in self.LAPTOPOWE if w in zdanie.lower()]
                self.assertFalse(trafione,
                                 f"{skad}: zdanie mowi {trafione} -> {zdanie[:90]}")

    def test_pecety_nie_biora_puli_laptopowej(self):
        """Sam brak slowa 'laptop' nie wystarcza - pula laptopowa moze sie
        kiedys zmienic. Pecet ma miec wskazana WLASNA pule."""
        for skad, _ in self.pule_pecetow():
            self.assertNotIn("(zdania_wiodace)", skad,
                             f"{skad}: pecet siega po pule laptopowa")

    def test_w_gotowym_opisie_peceta_zdanie_wiodace_jest_z_puli_pecetow(self):
        """Droga do konca: przez generate.py, az do kolumny z opisem.

        Nie szukamy slowa 'notebook' w calym opisie - niemiecki szablon peceta
        slusznie wspomina notebooki w sekcji o firmie ("prüfen, reinigen und
        konfigurieren wir notebooks, desktop-computer..."). Sprawdzamy dokladnie
        to, co sie zepsulo: ze zdanie wiodace pochodzi z puli dla pecetow.
        """
        import html as _html
        sys.path.insert(0, str(ROOT / "src"))
        import generate
        for kraj in ("de", "it"):
            out = Path(tempfile.mkdtemp())
            subprocess.run([sys.executable, str(ROOT / "src" / "generate.py"),
                            "--tryb", "pierwsze", "--kraj", kraj, "--feed-file", str(FIXTURE),
                            "--raport", str(RAPORT), "--nbp-rate", "4.26",
                            "--min-produktow", "1", "--output-dir", str(out)],
                           check=False, capture_output=True)
            plik = out / "ebay-add.csv"
            self.assertTrue(plik.exists(), f"{kraj}: nie powstal ebay-add.csv")
            with plik.open(encoding="utf-8-sig") as uchwyt:
                rows = list(csv.reader(uchwyt, delimiter=";"))
            naglowek = rows[1]
            kol_kat = next(i for i, k in enumerate(naglowek) if k.strip().endswith("Category"))
            kol_op = next(i for i, k in enumerate(naglowek) if "Description" in k)

            settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
            kr = json.loads((ROOT / "config" / "kraje" / f"{kraj}.json").read_text(encoding="utf-8"))
            settings.update({k: v for k, v in kr.items()
                             if not k.startswith("_") and k not in ("pliki", "katalog_szablonow")})
            pula = settings["zdania_wiodace_pecety"]["lista"]
            laptopowe = settings["zdania_wiodace"]["lista"]

            pecety = [r for r in rows[2:] if r and r[kol_kat] == "179"]
            self.assertTrue(pecety, f"{kraj}: fixture nie dal ani jednego peceta")
            for r in pecety:
                opis = _html.unescape(r[kol_op])
                z_pecetow = [z for z in pula if z in opis]
                z_laptopow = [z for z in laptopowe if z in opis]
                self.assertTrue(z_pecetow or r[kol_kat] != "179" or z_laptopow == [],
                                f"{kraj}/{r[1]}: brak zdania z puli pecetow")
                self.assertEqual(z_laptopow, [],
                                 f"{kraj}/{r[1]}: opis peceta niesie zdanie z puli laptopowej")


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


class RaportDuplikatow(unittest.TestCase):
    """Duplikat = ta sama specyfikacja ORAZ ta sama cena. Sama specyfikacja nie
    wystarcza, bo ten sam model w innej cenie to inny produkt."""

    def modul(self):
        sys.path.insert(0, str(ROOT / "src"))
        import duplikaty
        import generate
        return duplikaty, {
            "norm": generate.norm, "brand_name": generate.brand_name,
            "cpu_candidates": generate.cpu_candidates,
            "zrodlo_procesora": generate.zrodlo_procesora,
            "clean_capacity": generate.clean_capacity,
            "screen_size_de": generate.screen_size_de,
        }

    def oferta(self, sku, cena, stan, zrodlo="Shoper", model="ThinkPad T490"):
        import xml.etree.ElementTree as ET
        el = ET.Element("o", {"price": str(cena), "stock": str(stan)})
        attrs = {"SKU": sku, "Producent": "Lenovo", "Model": model,
                 "Procesor": "i5-8265U, 6MB Cache, 8 gen.", "Ilość pamięci RAM": "8GB",
                 "Dysk": "256GB", "Typ dysku": "SSD", "Przekątna ekranu": '14"',
                 "Źródło": zrodlo}
        return (el, attrs)

    def znajdz(self, produkty, aktywne=(), odrzucaj=True):
        duplikaty, funkcje = self.modul()
        settings = {"duplikaty": {"enabled": True, "pole_zrodla": "Źródło",
                                  "domyslne_zrodlo": "Shoper", "preferowane_zrodlo": "Shoper",
                                  "odrzucaj": odrzucaj}}
        return duplikaty.znajdz(produkty, set(aktywne), settings, funkcje)

    def decyzje(self, wynik) -> dict:
        return {w["SKU"]: w["decyzja"] for w in wynik["wiersze"]}

    def test_ta_sama_cena_to_duplikat(self):
        wynik = self.znajdz([self.oferta("A", 1100, 10),
                             self.oferta("B", 1100, 20, "Allegro")])
        self.assertEqual(wynik["grup"], 1)
        self.assertEqual(wynik["ofert"], 2)

    def test_inna_cena_to_inny_produkt(self):
        """Sprzedawca ma po kilka ofert tego samego modelu w roznych cenach."""
        wynik = self.znajdz([self.oferta("A", 1100, 10),
                             self.oferta("B", 1049, 20, "Allegro")])
        self.assertEqual(wynik["grup"], 0)

    def test_inna_specyfikacja_to_inny_produkt(self):
        wynik = self.znajdz([self.oferta("A", 1100, 10),
                             self.oferta("B", 1100, 20, "Allegro", model="ThinkPad T480")])
        self.assertEqual(wynik["grup"], 0)

    def test_shoper_wygrywa_nad_allegro(self):
        """Gdy zadna nie jest jeszcze na eBayu: zostaje Shoper, bo ma prawdziwe
        dane o kondycji, Allegro wstawia domyslne."""
        wynik = self.znajdz([self.oferta("ALG", 1100, 99, "Allegro"),
                             self.oferta("SHP", 1100, 5, "Shoper")])
        self.assertEqual(self.decyzje(wynik), {"SHP": "zostaje", "ALG": "NIE WYSTAWIAMY"})
        self.assertEqual(wynik["wstrzymane_sku"], ["ALG"])

    def test_aktywnej_aukcji_nigdy_nie_zdejmujemy(self):
        """Nawet gdy aktywne jest Allegro, a czekajacy Shoper ma lepsze dane:
        aukcja z historia jest warta wiecej niz czystosc katalogu."""
        wynik = self.znajdz([self.oferta("ALG", 1100, 99, "Allegro"),
                             self.oferta("SHP", 1100, 5, "Shoper")], aktywne={"ALG"})
        self.assertEqual(self.decyzje(wynik), {"ALG": "zostaje", "SHP": "NIE WYSTAWIAMY"})

    def test_obie_aktywne_zostawiamy_do_decyzji(self):
        """Duplikat, ktory obiema polowami stoi juz na eBayu. Nic nie ruszamy,
        raport go pokazuje - reszte robi czlowiek."""
        wynik = self.znajdz([self.oferta("ALG", 1100, 99, "Allegro"),
                             self.oferta("SHP", 1100, 5, "Shoper")], aktywne={"ALG", "SHP"})
        self.assertEqual(wynik["wstrzymanych"], 0)
        self.assertEqual(wynik["kolizje_aktywnych"], 1)
        self.assertEqual(set(self.decyzje(wynik).values()), {"zostaje - kolizja aktywnych"})

    def test_raport_mowi_z_czym_kolidowala_wstrzymana(self):
        wynik = self.znajdz([self.oferta("ALG", 1100, 99, "Allegro"),
                             self.oferta("SHP", 1100, 5, "Shoper")])
        wstrzymana = [w for w in wynik["wiersze"] if w["decyzja"] == "NIE WYSTAWIAMY"][0]
        self.assertEqual(wstrzymana["koliduje_z"], "SHP")

    def test_bez_odrzucania_raport_nie_wskazuje_nikogo_do_odsiania(self):
        """Wylaczony przelacznik zostawia sam raport - feed idzie w calosci."""
        wynik = self.znajdz([self.oferta("ALG", 1100, 99, "Allegro"),
                             self.oferta("SHP", 1100, 5, "Shoper")], odrzucaj=False)
        self.assertEqual(wynik["wstrzymane_sku"], [])
        self.assertEqual(wynik["grup"], 1, "ale grupe nadal widac w raporcie")

    def test_dwie_oferty_z_tego_samego_zrodla_to_nie_duplikat(self):
        """Ten sam model w tej samej cenie dwa razy w Shoperze to dwie partie.
        Sklejenie ich zgubiloby towar, ktory naprawde stoi w magazynie."""
        wynik = self.znajdz([self.oferta("A", 1100, 10), self.oferta("B", 1100, 20)])
        self.assertEqual(wynik["grup"], 0)
        self.assertEqual(wynik["powtorzenia_w_jednym_zrodle"], 1, "ale liczymy je w raporcie")

    def test_dwie_oferty_z_allegro_tez_nie_sa_duplikatem(self):
        wynik = self.znajdz([self.oferta("A", 1100, 10, "Allegro"),
                             self.oferta("B", 1100, 20, "Allegro")])
        self.assertEqual(wynik["grup"], 0)

    def test_zostaje_cale_zrodlo_zwycieskie(self):
        """Trzy oferty w Shoperze to trzy partie. Dwie z Allegro sa ich
        odpowiednikami i nie ida na eBay, ale zadnej oferty Shopera nie ruszaja."""
        wynik = self.znajdz([self.oferta("S1", 1100, 10), self.oferta("S2", 1100, 11),
                             self.oferta("S3", 1100, 12),
                             self.oferta("A1", 1100, 10, "Allegro"),
                             self.oferta("A2", 1100, 11, "Allegro")])
        self.assertEqual(sorted(wynik["wstrzymane_sku"]), ["A1", "A2"])

    def test_nadwyzka_w_drugim_zrodle_zostaje(self):
        """Trzy oferty na Allegro przy jednej w Shoperze: tylko jedna ma tam
        odpowiednik, pozostale dwie to partie, ktorych w sklepie nie ma."""
        wynik = self.znajdz([self.oferta("SHP", 1100, 10),
                             self.oferta("A1", 1100, 10, "Allegro"),
                             self.oferta("A2", 1100, 11, "Allegro"),
                             self.oferta("A3", 1100, 12, "Allegro")])
        self.assertEqual(wynik["wstrzymanych"], 1)
        zostaje = {w["SKU"] for w in wynik["wiersze"] if w["decyzja"] == "zostaje"}
        self.assertIn("SHP", zostaje)
        self.assertEqual(len(zostaje), 3)

    def test_wstrzymujemy_tylko_to_czego_na_ebayu_jeszcze_nie_ma(self):
        """Gdy jedna oferta Allegro juz wisi, wstrzymujemy te druga."""
        wynik = self.znajdz([self.oferta("SHP", 1100, 10),
                             self.oferta("A1", 1100, 10, "Allegro"),
                             self.oferta("A2", 1100, 11, "Allegro")],
                            aktywne={"A1"})
        self.assertEqual(wynik["wstrzymane_sku"], ["A2"])

    def test_stan_zostaje_wlasny(self):
        """Nic nie jest scalane: kazda oferta, ktora zostaje, ma swoj stan."""
        wynik = self.znajdz([self.oferta("SHP", 1100, 5, "Shoper"),
                             self.oferta("ALG", 1100, 99, "Allegro")])
        zostaje = [w for w in wynik["wiersze"] if w["decyzja"] == "zostaje"][0]
        self.assertEqual(zostaje["SKU"], "SHP")
        self.assertEqual(zostaje["stan"], 5)
        self.assertNotIn("stan_po_scaleniu", zostaje)

    def test_blizniak_gwarancyjny_to_nie_duplikat_oryginalu(self):
        """W feedzie ma te sama cene - podnosi ja dopiero mnoznik profilu.
        Rozni go 12 miesiecy serwisu, wiec odcisk musi to widziec."""
        duplikaty, funkcje = self.modul()
        oryginal = {"Producent": "Lenovo", "Model": "ThinkPad T490",
                    "Procesor": "i5-8265U, 6MB Cache, 8 gen."}
        blizniak = dict(oryginal, **{"Wariant gwarancyjny": "GW24"})
        self.assertNotEqual(duplikaty.odcisk(oryginal, funkcje),
                            duplikaty.odcisk(blizniak, funkcje))

    def test_niepelna_specyfikacja_nie_jest_porownywana(self):
        """Bez marki, modelu i procesora odcisk zlepilby przypadkowe oferty."""
        duplikaty, funkcje = self.modul()
        self.assertEqual(duplikaty.odcisk({"Producent": "Lenovo"}, funkcje), "")
        self.assertEqual(duplikaty.odcisk({}, funkcje), "")

    def test_modul_sam_niczego_nie_usuwa(self):
        """Odsiewaniem zajmuje sie generate.py - modul tylko wskazuje SKU."""
        produkty = [self.oferta("A", 1100, 10), self.oferta("B", 1100, 20, "Allegro")]
        przed = len(produkty)
        self.znajdz(produkty)
        self.assertEqual(len(produkty), przed)

    def test_nigdy_nie_wstrzymujemy_sku_aktywnego_na_ebay(self):
        """Bramka na cala rodzine: cokolwiek wyjdzie z rozstrzygania, zadne
        aktywne SKU nie moze trafic na liste do odsiania - odsianie aktywnego
        znaczy wyzerowanie go w trybie 'aktualizacja', czyli zdjecie aukcji."""
        warianty = [
            ([self.oferta("S", 1100, 5), self.oferta("A", 1100, 9, "Allegro")], {"S"}),
            ([self.oferta("S", 1100, 5), self.oferta("A", 1100, 9, "Allegro")], {"A"}),
            ([self.oferta("S", 1100, 5), self.oferta("A", 1100, 9, "Allegro")], {"S", "A"}),
            ([self.oferta("S1", 1100, 5), self.oferta("S2", 1100, 6),
              self.oferta("A", 1100, 9, "Allegro")], {"S1", "A"}),
        ]
        for produkty, aktywne in warianty:
            with self.subTest(aktywne=sorted(aktywne)):
                wynik = self.znajdz(produkty, aktywne=aktywne)
                self.assertFalse(set(wynik["wstrzymane_sku"]) & aktywne)
