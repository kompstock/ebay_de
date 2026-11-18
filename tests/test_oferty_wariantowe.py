"""Testy skladania oferty wariantowej eBaya (wiersz oferty + wiersze wariantow)."""
import os
import csv
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# Testy nie czytaja przelacznika z config/warianty.json (plik wlasciciela) - patrz
# ustawienia_wariantow() w generate.py. Kto chce nowej sciezki, podaje --warianty.
os.environ.setdefault("EBAY_BEZ_WARIANTOW", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import generate as g            # noqa: E402
import oferty_wariantowe as ow  # noqa: E402
import warianty as w            # noqa: E402

csv.field_size_limit(10 ** 7)
FEED = ROOT / "tests" / "fixtures" / "sample.xml"
FUNKCJE = {"norm": g.norm, "brand_name": g.brand_name, "cpu_candidates": g.cpu_candidates,
           "zrodlo_procesora": g.zrodlo_procesora, "clean_capacity": g.clean_capacity,
           "marki": g.MARKI_ZNANE, "condition_class": g.condition_class,
           "nazwa_modelu": g.nazwa_modelu}
# Zamrozony cennik - jak w test_warianty.py, testy nie czytaja pliku wlasciciela.
CENNIK = {
    "ram_doplaty": {"4>8": 100, "4>16": 300, "8>12": 100, "8>16": 200, "8>24": 400, "8>32": 550,
                    "16>24": 200, "16>32": 400, "16>64": 1200, "32>64": 800},
    "ram_standard_ddr4": [8, 16, 32],
    "max_ram_wg_typu": {"DDR4": 32, "INNY": 0},
    "max_ram_domyslnie": 16,
    "dysk_cele_gb": [240, 480, 960, 2000],
    "dysk_grupy": {"128": [120, 128], "256": [240, 256], "512": [480, 500, 512],
                   "1000": [960, 1000, 1024], "2000": [2000, 2048]},
    "dysk_doplaty": {"128>256": 100, "128>512": 250, "128>1000": 600, "128>2000": 950,
                     "256>512": 200, "256>1000": 550, "256>2000": 850,
                     "512>1000": 350, "512>2000": 700},
    "max_dysk_przy_ram": {"8": 960},
    "marki_bez_rozbudowy": ["Apple"],
    "min_stan_bazy": 10,
    "max_sztuk_na_wariant": 2,
}


class _Bez:
    def add(self, *_a, **_k):
        pass


def srodowisko(kraj="de"):
    cfg = ow.wczytaj_cfg(g, ROOT, kraj)
    cfg["rate"] = 4.26
    produkty = g.zbierz_produkty(ET.fromstring(FEED.read_bytes()), cfg, _Bez(), Counter())
    laptopy = [(o, a) for o, a in produkty
               if g.typ_produktu(g.text(o.find("./cat")), cfg["settings"], a) == "Notebook"]
    rodziny = w.zbuduj_rodziny(laptopy, CENNIK, [], FUNKCJE)["rodziny"]
    return cfg, {r["baza"]["sku"]: r for r in rodziny}


class NapisyTests(unittest.TestCase):
    def test_lista_do_opisu_i_zakres_do_tytulu(self):
        konf = [{"ram": r, "dysk": d} for r in (8, 16, 32) for d in (256, 480, 960, 2000)]
        self.assertEqual(ow.napisy_pamieci(konf, g.clean_capacity),
                         {"ram": "8 / 16 / 32 GB", "ram_tytul": "8-32 GB RAM",
                          "disk": "256 GB / 480 GB / 960 GB / 2 TB", "dysk_tytul": "256 GB-2 TB"})

    def test_cecha_o_jednej_wartosci_nie_dostaje_napisu(self):
        # Model z jednym rozmiarem RAM-u: tytul i opis mowia o nim po staremu.
        konf = [{"ram": 32, "dysk": d} for d in (256, 480)]
        self.assertEqual(ow.napisy_pamieci(konf, g.clean_capacity),
                         {"disk": "256 / 480 GB", "dysk_tytul": "256-480 GB"})

    def test_jednostki_rynku(self):
        po_francusku = lambda t: g.clean_capacity(t, {"GB": "Go", "TB": "To"})
        napisy = ow.napisy_pamieci([{"ram": 8, "dysk": 256}, {"ram": 16, "dysk": 2000}], po_francusku)
        self.assertEqual((napisy["ram_tytul"], napisy["dysk_tytul"]), ("8-16 Go RAM", "256 Go-2 To"))


class OfertaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.rodziny = srodowisko()
        cls.headers = cls.cfg["headers"]
        cls.rodzina = cls.rodziny["4220"]                    # 51 sztuk w feedzie testowym
        cls.wiersze, cls.blad = ow.zbuduj_oferte(cls.rodzina, cls.cfg, cls.headers, g, _Bez())

    def test_oferta_powstaje(self):
        self.assertEqual(self.blad, "")
        self.assertEqual(len(self.wiersze), 1 + len(self.rodzina["warianty"]))
        self.assertEqual(ow.sprawdz_oferte(self.wiersze), [])

    def test_wiersz_oferty_nie_niesie_ceny_ilosci_ani_cech_wariantu(self):
        oferta = self.wiersze[0]
        for kolumna in ("*StartPrice", "*Quantity", "Relationship", "C:Arbeitsspeichergröße",
                        "C:SSD-Festplattenkapazität", "C:Festplattenkapazität"):
            self.assertEqual(oferta[kolumna], "", kolumna)
        self.assertEqual(oferta["CustomLabel"], f"SHOPER-ID:{self.rodzina['id_bazy']}:warianty")
        zestawy = dict(c.split("=") for c in oferta["RelationshipDetails"].split("|"))
        self.assertEqual(set(zestawy), {"Arbeitsspeichergröße", "SSD-Festplattenkapazität"})
        self.assertNotRegex(oferta["RelationshipDetails"], r"\s[=;|]|[=;|]\s")

    def test_tytul_ma_zakres_i_klase_a_opis_wszystkie_opcje(self):
        oferta = self.wiersze[0]
        ramy = sorted({v["ram"] for v in self.rodzina["warianty"]})
        self.assertIn(f"{ramy[0]}-{ramy[-1]} GB", oferta["*Title"])
        self.assertLessEqual(len(oferta["*Title"]), 80)
        klasa = g.condition_class(self.rodzina["baza"]["attrs"]["Kondycja sprzętu"])
        self.assertTrue(oferta["*Title"].endswith(klasa), oferta["*Title"])
        self.assertIn(" / ".join(str(r) for r in ramy) + " GB", oferta["*Description"])
        # Opis jest wspolny, wiec nie moze twierdzic, ze laptop MA konfiguracje bazy.
        zwykly, _ = g.build_row(self.rodzina["baza"]["offer"], self.rodzina["baza"]["attrs"],
                                self.cfg, self.headers, _Bez())
        baza_ram = g.clean_capacity(self.rodzina["baza"]["attrs"]["Ilość pamięci RAM"])
        self.assertIn(f"<b>{baza_ram}</b>", zwykly["*Description"])
        self.assertNotIn(f"<b>{baza_ram}</b>", oferta["*Description"])
        self.assertNotIn(f"{baza_ram} Arbeitsspeicher", oferta["*Description"])

    def test_wariant_kosztuje_tyle_co_wystawiony_osobno(self):
        # Wariant bazowy = ten sam produkt co zwykla oferta, wiec ta sama cena w euro.
        zwykly, _ = g.build_row(self.rodzina["baza"]["offer"], self.rodzina["baza"]["attrs"],
                                self.cfg, self.headers, _Bez())
        dzieci = self.wiersze[1:]
        self.assertEqual(dzieci[0]["*StartPrice"], zwykly["*StartPrice"])
        self.assertEqual(float(dzieci[0]["*StartPrice"]), min(float(d["*StartPrice"]) for d in dzieci))
        self.assertEqual({d["*Quantity"] for d in dzieci}, {"2"})
        # ...a budowanie wariantow nie zmienilo ceny ani stanu wpisu w feedzie.
        self.assertEqual(self.rodzina["baza"]["offer"].get("stock"), "51")

    def test_cena_kazdego_wariantu_idzie_przez_wzor_generatora(self):
        # Cena wariantu w zlotowkach (baza + doplaty) przeliczona tym samym wzorem,
        # ktorym generator liczy kazda zwykla oferte: kurs, VAT, oplaty, wysylka.
        baza = self.rodzina["baza"]
        profil = g.profil_produktu(g.text(baza["offer"].find("./cat")), self.cfg["settings"], baza["attrs"])
        for dziecko, v in zip(self.wiersze[1:], self.rodzina["warianty"]):
            wpis = ET.Element("o", {"price": str(v["cena"])})
            self.assertEqual(dziecko["*StartPrice"], f"{g.cena_eur(wpis, self.cfg, profil):.2f}",
                             (v["ram"], v["dysk"]))
        ceny = [float(d["*StartPrice"]) for d in self.wiersze[1:]]
        self.assertGreater(max(ceny), min(ceny))

    def test_wiersze_wariantow(self):
        id_bazy = self.rodzina["id_bazy"]
        for dziecko, v in zip(self.wiersze[1:], self.rodzina["warianty"]):
            self.assertEqual(dziecko["Relationship"], "Variation")
            self.assertEqual(dziecko["CustomLabel"], w.sku_wariantu(id_bazy, v["ram"], v["dysk"]))
            self.assertEqual(w.rozloz_sku(dziecko["CustomLabel"])["id"], id_bazy)
            wypelnione = {k for k, wartosc in dziecko.items() if wartosc}
            self.assertEqual(wypelnione, {"Relationship", "RelationshipDetails", "CustomLabel",
                                          "*StartPrice", "*Quantity"})

    def test_dopiski_rynku_trafiaja_do_kazdego_sku(self):
        wiersze, _ = ow.zbuduj_oferte(self.rodzina, self.cfg, self.headers, g, _Bez(), ["", "ITT"])
        self.assertTrue(all(x["CustomLabel"].endswith("-ITT") for x in wiersze))

    def test_baza_ponizej_progu_nie_dostaje_oferty(self):
        wiersze, powod = ow.zbuduj_oferte(self.rodziny["3809"], self.cfg, self.headers, g, _Bez())
        self.assertIsNone(wiersze)                            # 6 sztuk, prog 10
        self.assertEqual(powod, "baza ponizej progu stanu")

    def test_baza_z_hdd_nie_dostaje_oferty(self):
        rodzina = dict(self.rodzina, baza=dict(self.rodzina["baza"],
                                              attrs=dict(self.rodzina["baza"]["attrs"], **{"Typ dysku": "HDD"})))
        self.assertEqual(ow.zbuduj_oferte(rodzina, self.cfg, self.headers, g, _Bez()),
                         (None, "baza ma dysk inny niz SSD"))

    def test_kontrola_lapie_zepsuta_oferte(self):
        def zepsuj(zmiana):
            kopia = [dict(x) for x in self.wiersze]
            zmiana(kopia)
            return " | ".join(ow.sprawdz_oferte(kopia))
        self.assertIn("tytul ma", zepsuj(lambda k: k[0].update({"*Title": "x" * 81})))
        self.assertIn("niesie cene", zepsuj(lambda k: k[0].update({"*StartPrice": "100.00"})))
        self.assertIn("powtarza sie", zepsuj(lambda k: k[2].update(
            {"RelationshipDetails": k[1]["RelationshipDetails"]})))
        self.assertIn("nie jest dodatnia", zepsuj(lambda k: k[1].update({"*Quantity": "0"})))
        self.assertIn("powtorzone SKU", zepsuj(lambda k: k[2].update({"CustomLabel": k[1]["CustomLabel"]})))
        self.assertIn("jednoczesnie wariantem", zepsuj(lambda k: k[0].update({"C:Arbeitsspeichergröße": "8 GB"})))
        self.assertIn("Action", zepsuj(lambda k: k[1].update({self.headers[0]: "Add"})))

    def test_plik_ma_akcje_tylko_w_wierszu_oferty(self):
        # Wiersz wariantu z wpisana akcja eBay czyta jako osobna oferte (test z 15.11.2026).
        with tempfile.TemporaryDirectory() as katalog:
            plik = Path(katalog) / "ebay-add-warianty.csv"
            ow.zapisz_plik(plik, self.headers, [self.wiersze, self.wiersze], "VerifyAdd", "DE")
            with plik.open(encoding="utf-8-sig", newline="") as uchwyt:
                wiersze = list(csv.reader(uchwyt, delimiter=";"))
        self.assertEqual(wiersze[0], ["Info", "Version=1.0.0", "Template=fx_category_template_EBAY_DE"])
        self.assertEqual(wiersze[1], self.headers)
        akcje = [x[0] for x in wiersze[2:]]
        na_oferte = len(self.wiersze)
        self.assertEqual(akcje, (["VerifyAdd"] + [""] * (na_oferte - 1)) * 2)
        self.assertTrue(all(len(x) == len(self.headers) for x in wiersze[2:]))

    def test_wartosc_oferty_to_cena_razy_ilosc_wariantow(self):
        oczekiwana = sum(float(d["*StartPrice"]) * 2 for d in self.wiersze[1:])
        self.assertEqual(ow.wartosc_oferty(self.wiersze), oczekiwana)


def oferty_wariantowe_uklad(wiersze):
    return ow.sprawdz_uklad_revise(wiersze)


class TrzySchematyTests(unittest.TestCase):
    """Zwykle oferty, oferty wariantowe i dubel bazy w jednym raporcie eBaya - kazda sciezka
    ma widziec tylko swoje wiersze."""
    NAGLOWEK = ["Item number", "Title", "Variation details", "Custom label (SKU)", "Available quantity",
                "Currency", "Start price", "End date", "Listing site"]

    def raport(self, katalog):
        plik = Path(katalog) / "aktywne.csv"
        with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
            zapis = csv.writer(uchwyt)
            zapis.writerow(self.NAGLOWEK)
            zapis.writerows([
                # zwykla oferta konfiguracji podstawowej (stara sciezka, SKU Shopera)
                ["305000000001", "Zwykla", "", "4220", "5", "EUR", "299.0", "", "DE"],
                # oferta wariantowa tego samego laptopa (nowa sciezka, sygnatura SHOPER-ID)
                ["307000000002", "Wariantowa", "RAM=8 GB;16 GB|SSD=256 GB", "SHOPER-ID:7681:warianty", "4", "EUR", "299.0", "", "DE"],
                ["307000000002", "Wariantowa", "RAM=8 GB|SSD=256 GB", "SHOPER-ID:7681-8GB-256GB", "2", "EUR", "299.0", "", "DE"],
                ["307000000002", "Wariantowa", "RAM=16 GB|SSD=256 GB", "SHOPER-ID:7681-16GB-256GB", "2", "EUR", "350.0", "", "DE"],
                # ta sama rodzina na rynku wloskim - nie moze zablokowac niemieckiej
                ["308000000003", "Wariantowa IT", "RAM=8 GB|SSD=256 GB", "SHOPER-ID:7452-8GB-256GB-ITT", "2", "EUR", "305.0", "", "IT"],
                # reczna oferta wariantowa w obcym zapisie SKU (jak testowa z 14.11)
                ["307282655424", "Testowa", "RAM=8 GB|SSD=480GB", "10207:ram8:disk480", "1", "EUR", "369.0", "", "DE"],
            ])
        return plik

    def test_kazda_sciezka_widzi_tylko_swoje(self):
        with tempfile.TemporaryDirectory() as katalog:
            plik = self.raport(katalog)
            zwykle, _ = g.wczytaj_raport(plik, "DE")
            wariantowe_de = ow.przypiete_z_raportu(plik, "DE")
            wariantowe_it = ow.przypiete_z_raportu(plik, "IT")
        self.assertEqual(set(zwykle), {"4220"})                           # stara sciezka: sama zwykla oferta
        self.assertEqual(wariantowe_de, {"7681": "307000000002"})         # nowa: sama oferta z sygnatura
        self.assertEqual(wariantowe_it, {"7452": "308000000003"})

    def test_rodzina_z_oferta_na_ebayu_zostaje_przypieta_do_swojego_id(self):
        cfg, _ = srodowisko()
        produkty = g.zbierz_produkty(ET.fromstring(FEED.read_bytes()), cfg, _Bez(), Counter())
        laptopy = [(o, a) for o, a in produkty
                   if g.typ_produktu(g.text(o.find("./cat")), cfg["settings"], a) == "Notebook"]
        rodziny = w.zbuduj_rodziny(laptopy, CENNIK, [], FUNKCJE, przypiete={"7681"})["rodziny"]
        przypieta = next(r for r in rodziny if r["id_bazy"] == "7681")
        self.assertTrue(przypieta["przypieta"])
        self.assertEqual(przypieta["baza"]["sku"], "4220")


class PoczekalniaStronaTests(unittest.TestCase):
    """Strona poczekalni: czytelna dla czlowieka i odporna na to, co ktos wpisze w Shoperze."""

    @staticmethod
    def wpis(powod, id_, **inne):
        return dict({"rodzina": "W1", "opis": "Lenovo ThinkPad E15 | i5 | klasa A", "id_wystawionego": "10",
                     "sku_wystawionego": "S10", "ram_wystawionego": 8, "dysk_wystawionego": 256,
                     "stan_wystawionego": 40, "url_wystawionego": "https://kompre.pl/pl/p/x/10",
                     "id": id_, "sku": "S" + id_, "ram": 8, "dysk": 256, "stan": 3, "cena_xml": 999,
                     "cena_wystawionego": 1199, "cena_ze_wzoru": None, "powod": powod, "roznice": [],
                     "url": "https://kompre.pl/pl/p/y/" + id_}, **inne)

    def test_duble_na_gorze_roznice_obok_siebie_i_bez_wstrzykiwania(self):
        strona = ow.strona_poczekalni([
            self.wpis(w.POWOD_WARIANT, "12", ram=16, cena_ze_wzoru=1399),
            self.wpis(w.POWOD_DUBEL, "11", roznice=[{"pole": "Stan obudowy", "wystawiony": "",
                                                    "ten": "<script>alert(1)</script>"}],
                      url="javascript:alert(1)"),
        ], "FR", "2026-11-17 12:00 UTC")
        self.assertLess(strona.index('id="dubel"'), strona.index('id="wariant"'))
        self.assertNotIn("<script>", strona)
        self.assertIn("&lt;script&gt;", strona)
        self.assertNotIn('href="javascript:', strona)
        self.assertIn('href="https://kompre.pl/pl/p/x/10"', strona)
        self.assertIn("(-400.00)", strona)                 # 999 w Shoperze kontra 1399 z cennika
        self.assertIn("Stan obudowy", strona)
        ampersand = ow.strona_poczekalni([self.wpis(w.POWOD_DUBEL, "13", opis="Dell & HP <b>")])
        self.assertIn("Dell &amp; HP &lt;b&gt;", ampersand)

    def test_pusta_poczekalnia_tez_daje_strone(self):
        strona = ow.strona_poczekalni([], "FR")
        self.assertIn("<title>Poczekalnia · FR</title>", strona)
        self.assertEqual(ow.poczekalnia_wg_powodu([])["Dubel"], 0)

    def test_csv_ma_wszystkie_kolumny(self):
        wiersz = ow.wiersze_poczekalni([self.wpis(w.POWOD_DUBEL, "11")])[0]
        self.assertEqual(set(wiersz), set(ow.KOLUMNY_POCZEKALNI))
        self.assertEqual((wiersz["link"], wiersz["sku_wystawionego"]), ("https://kompre.pl/pl/p/y/11", "S10"))


class AktualizacjaTests(unittest.TestCase):
    """Codzienna aktualizacja wariantow: raport eBaya + dzisiejszy feed -> wiersze Revise."""
    NAGLOWEK = ["Item number", "Title", "Variation details", "Custom label (SKU)", "Available quantity",
                "Currency", "Start price", "End date", "Listing site"]
    NUMER = "307285168505"

    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.rodziny = srodowisko()
        cls.headers = cls.cfg["headers"]
        cls.rodzina = cls.rodziny["4220"]
        cls.wiersze, _ = ow.zbuduj_oferte(cls.rodzina, cls.cfg, cls.headers, g, _Bez())

    def raport(self, katalog, zmiany=None, dodatkowe=()):
        """Raport taki, jaki eBay oddalby dzien po wystawieniu naszej oferty; 'zmiany' podmieniaja
        (cena, ilosc) wybranych wariantow, czyli udaja to, co stoi na eBayu inaczej niz w feedzie."""
        oferta, dzieci = self.wiersze[0], self.wiersze[1:]
        wiersze = [[self.NUMER, oferta["*Title"], oferta["RelationshipDetails"], oferta["CustomLabel"],
                    "22", "EUR", dzieci[0]["*StartPrice"], "", "DE"]]
        for d in dzieci:
            cena, ilosc = (zmiany or {}).get(d["CustomLabel"], (d["*StartPrice"], d["*Quantity"]))
            wiersze.append([self.NUMER, oferta["*Title"], d["RelationshipDetails"], d["CustomLabel"],
                            ilosc, "EUR", cena, "", "DE"])
        wiersze += list(dodatkowe)
        plik = Path(katalog) / "aktywne.csv"
        with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
            zapis = csv.writer(uchwyt)
            zapis.writerow(self.NAGLOWEK)
            zapis.writerows(wiersze)
        return plik

    def aktualizacja(self, rodziny=None, **raport):
        with tempfile.TemporaryDirectory() as katalog:
            na_ebay = ow.oferty_z_raportu(self.raport(katalog, **raport), "DE")
        return ow.wiersze_aktualizacji(list((rodziny or self.rodziny).values()), na_ebay, self.cfg,
                                       self.headers, g, _Bez())

    def test_nic_sie_nie_zmienilo_wiec_plik_jest_pusty(self):
        wiersze, licznik = self.aktualizacja()
        self.assertEqual(wiersze, [])
        self.assertEqual((licznik["ofert"], licznik["bez_zmian"]), (1, len(self.wiersze) - 1))

    def test_wiersz_dostaje_tylko_wariant_ktory_sie_rozni(self):
        # Tak wygladal test na zywej ofercie 16.11.2026: jeden wariant mial 612 EUR i 3 sztuki.
        ostatni = self.wiersze[-1]
        wiersze, licznik = self.aktualizacja(zmiany={ostatni["CustomLabel"]: ("9999.0", "3")})
        self.assertEqual(len(wiersze), 2)
        oferta, w0 = wiersze
        # wiersz oferty: numer i SKU oferty, bez ceny, ilosci i Relationship
        self.assertEqual((oferta["Action"], oferta["Item number"], oferta["Relationship"]), ("Revise", self.NUMER, ""))
        self.assertEqual(oferta["Custom label (SKU)"], self.wiersze[0]["CustomLabel"])
        self.assertEqual((oferta["Start price"], oferta["Available quantity"]), ("", ""))
        # wiersz wariantu: pusta akcja i numer, pod swoja oferta
        self.assertEqual((w0["Action"], w0["Item number"], w0["Relationship"]), ("", "", "Variation"))
        self.assertEqual(w0["Relationship details"], ostatni["RelationshipDetails"])
        self.assertEqual(ow.sprawdz_uklad_revise(wiersze), "")
        self.assertEqual(w0["Custom label (SKU)"], ostatni["CustomLabel"])
        self.assertEqual(float(w0["Start price"]), float(ostatni["*StartPrice"]))
        self.assertEqual(w0["Available quantity"], "2")
        self.assertEqual((licznik["zmiana_ceny"], licznik["zmiana_ilosci"], licznik["wyzerowane"]), (1, 1, 0))
        self.assertEqual(set(w0), set(g.KOLUMNY_REVISE))

    def test_baza_ponizej_progu_zeruje_warianty_ale_nie_rusza_cen(self):
        ponizej = dict(self.rodziny, **{"4220": dict(self.rodzina, aktywna=False)})
        wiersze, licznik = self.aktualizacja(rodziny=ponizej)
        dzieci = [x for x in wiersze if x["Relationship"] == "Variation"]
        self.assertEqual((len(wiersze), len(dzieci)), (len(self.wiersze), len(self.wiersze) - 1))
        self.assertEqual({x["Available quantity"] for x in dzieci}, {"0"})
        wg_sku = {d["CustomLabel"]: d for d in self.wiersze[1:]}
        for x in dzieci:
            self.assertEqual(float(x["Start price"]), float(wg_sku[x["Custom label (SKU)"]]["*StartPrice"]))
        self.assertEqual(licznik["wyzerowane"], len(dzieci))

    def test_wpis_bazowy_zniknal_z_feedu(self):
        bez = {k: v for k, v in self.rodziny.items() if k != "4220"}
        wiersze, licznik = self.aktualizacja(rodziny=bez)
        self.assertEqual({x["Available quantity"] for x in wiersze if x["Relationship"]}, {"0"})
        self.assertEqual(licznik["oferty_bez_wpisu_bazowego"],
                         [f"{self.NUMER} (ID Shopera {self.rodzina['id_bazy']})"])

    def test_wariant_spoza_dzisiejszej_siatki_dostaje_zero(self):
        obcy = [self.NUMER, "Oferta", "Arbeitsspeichergröße=64 GB|SSD-Festplattenkapazität=2 TB",
                f"SHOPER-ID:{self.rodzina['id_bazy']}-64GB-2TB", "2", "EUR", "999.0", "", "DE"]
        wiersze, _ = self.aktualizacja(dodatkowe=[obcy])
        self.assertEqual([(x["Custom label (SKU)"], x["Available quantity"], x["Start price"]) for x in wiersze],
                         [(self.wiersze[0]["CustomLabel"], "", ""), (obcy[3], "0", "999.0")])

    def test_wyzerowany_wariant_nie_wraca_do_pliku_codziennie(self):
        ponizej = dict(self.rodziny, **{"4220": dict(self.rodzina, aktywna=False)})
        zera = {d["CustomLabel"]: (d["*StartPrice"], "0") for d in self.wiersze[1:]}
        wiersze, _ = self.aktualizacja(rodziny=ponizej, zmiany=zera)
        self.assertEqual(wiersze, [])

    def test_kontrola_ukladu_lapie_blad_z_17_listopada(self):
        # Dokladnie to, co poszlo na eBay.fr 17.11.2026: zwykla aukcja, a pod nia wiersze
        # wariantow cudzych ofert z wlasnym numerem i akcja.
        zly = [{"Action": "Revise", "Item number": "307286571727", "Relationship": "",
                "Custom label (SKU)": "SHOPER-ID:7451-FRA"},
               {"Action": "Revise", "Item number": "307286571890", "Relationship": "Variation",
                "Custom label (SKU)": "SHOPER-ID:10423-32GB-960GB-FRA"}]
        self.assertIn("ma akcje albo numer", ow.sprawdz_uklad_revise(zly))
        bez_rodzica = [dict(zly[0]), dict(zly[1], Action="", **{"Item number": ""})]
        self.assertIn("nie stoi pod wierszem swojej oferty", ow.sprawdz_uklad_revise(bez_rodzica))
        cudzy = [{"Action": "Revise", "Item number": "1", "Relationship": "", "Custom label (SKU)": "SHOPER-ID:7451:warianty-FRA"},
                 {"Action": "", "Item number": "", "Relationship": "Variation", "Custom label (SKU)": "SHOPER-ID:10423-8GB-256GB-FRA"}]
        self.assertIn("to nie jego oferta", ow.sprawdz_uklad_revise(cudzy))

    def test_kilka_ofert_kazda_w_swojej_grupie(self):
        # Dwie oferty wariantowe ze zmianami: kazdy wariant stoi pod wierszem swojej oferty.
        druga = self.rodziny["3809"]
        wiersze_drugiej, _ = ow.zbuduj_oferte(dict(druga, aktywna=True), self.cfg, self.headers, g, _Bez())
        if not wiersze_drugiej:
            self.skipTest("feed testowy nie ma drugiej rodziny z wariantami")
        dodatkowe = [["307000000099", "Druga", wiersze_drugiej[0]["RelationshipDetails"], wiersze_drugiej[0]["CustomLabel"],
                      "22", "EUR", "1.0", "", "DE"]]
        dodatkowe += [["307000000099", "Druga", d["RelationshipDetails"], d["CustomLabel"], "9", "EUR", "1.0", "", "DE"]
                      for d in wiersze_drugiej[1:]]
        ostatni = self.wiersze[-1]
        wiersze, licznik = self.aktualizacja(zmiany={ostatni["CustomLabel"]: ("9999.0", "3")}, dodatkowe=dodatkowe)
        self.assertEqual(ow.sprawdz_uklad_revise(wiersze), "")
        self.assertEqual([x["Item number"] for x in wiersze if x["Action"]], sorted([self.NUMER, "307000000099"]))
        self.assertEqual(licznik["ofert_zmienionych"], 2)

    def test_drobna_zmiana_ceny_wariantu_ponizej_progu_nie_idzie_do_pliku(self):
        # 17.11.2026: kurs NBP drgnal i 126 wariantow na FR dostalo +1 EUR, a zwykle oferty
        # tych samych baz staly - bo maja prog 'prog_zmiany_ceny_eur'. Warianty maja ten sam.
        prog = float(self.cfg["settings"]["prog_zmiany_ceny_eur"])
        ostatni = self.wiersze[-1]
        tuz_ponizej = f"{float(ostatni['*StartPrice']) + prog - 0.5:.1f}"
        wiersze, licznik = self.aktualizacja(zmiany={ostatni["CustomLabel"]: (tuz_ponizej, ostatni["*Quantity"])})
        self.assertEqual((wiersze, licznik["zmiana_ceny"]), ([], 0))
        # Gdy zmienia sie ilosc, wiersz idzie - ale z cena, ktora stoi na eBayu.
        wiersze, licznik = self.aktualizacja(zmiany={ostatni["CustomLabel"]: (tuz_ponizej, "7")})
        self.assertEqual([(x["Start price"], x["Available quantity"]) for x in wiersze if x["Relationship"]],
                         [(tuz_ponizej, ostatni["*Quantity"])])
        self.assertEqual(licznik["zmiana_ceny"], 0)
        # Na progu i powyzej - cena z dzisiejszego wzoru.
        na_progu = f"{float(ostatni['*StartPrice']) + prog:.1f}"
        wiersze, licznik = self.aktualizacja(zmiany={ostatni["CustomLabel"]: (na_progu, ostatni["*Quantity"])})
        self.assertEqual([float(x["Start price"]) for x in wiersze if x["Relationship"]],
                         [float(ostatni["*StartPrice"])])
        self.assertEqual(licznik["zmiana_ceny"], 1)

    def test_obce_oferty_i_inny_rynek_sa_pomijane(self):
        dodatkowe = [["305000000001", "Zwykla", "", "4220", "5", "EUR", "299.0", "", "DE"],
                     ["307282655424", "Reczna", "RAM=8 GB|SSD=480GB", "10207:ram8:disk480", "1", "EUR", "369.0", "", "DE"],
                     ["308000000003", "Wloska", "RAM=8 GB|SSD=256 GB", "SHOPER-ID:7452-8GB-256GB-ITT", "2", "EUR", "305.0", "", "IT"]]
        wiersze, licznik = self.aktualizacja(dodatkowe=dodatkowe)
        self.assertEqual((wiersze, licznik["ofert"]), ([], 1))


class PrzelacznikTests(unittest.TestCase):
    """Caly generator z wlaczonymi ofertami wariantowymi ('wlaczone': true), na feedzie testowym:
    laptop 4220 (ID 7681, 51 szt.) i 3809 (ID 7452, 6 szt.) oraz komputery."""

    @classmethod
    def setUpClass(cls):
        cls.katalog = Path(tempfile.mkdtemp())
        limity = cls.katalog / "limity.csv"
        limity.write_text("model;procesor;max_ram;max_dysk;uwagi\n", encoding="utf-8")
        cls.ustawienia = cls.katalog / "warianty.json"
        cls.ustawienia.write_text(json.dumps(dict(CENNIK, wlaczone=True, limity_plik=str(limity))),
                                  encoding="utf-8")
        cls.dodanie, cls.raport_dodania = cls.przebieg("pierwsze")
        cls.bez, _ = cls.przebieg("pierwsze", wlaczone=False)

    @classmethod
    def przebieg(cls, tryb, raport=None, wlaczone=True, feed=FEED, kraj="de", ustawienia=None, reczne=None):
        wynik = Path(tempfile.mkdtemp())
        polecenie = [sys.executable, str(ROOT / "src" / "generate.py"), "--kraj", kraj, "--tryb", tryb,
                     "--feed-file", str(feed), "--raport", str(raport or ROOT / "tests" / "fixtures" / "aktywne.csv"),
                     "--nbp-rate", "4.26", "--min-produktow", "1", "--output-dir", str(wynik)]
        if wlaczone:
            polecenie += ["--warianty", str(ustawienia or cls.ustawienia)]
        plik_reczny = Path(tempfile.mkdtemp()) / "sku-reczne.csv"
        plik_reczny.write_text("SKU;ilosc;cena\n" + (reczne or ""), encoding="utf-8")
        polecenie += ["--sku-reczne", str(plik_reczny)]
        kod = subprocess.run(polecenie, check=False, capture_output=True)
        assert kod.returncode in (0, 2), kod.stderr.decode("utf-8", "replace")[-2000:]
        return wynik, json.loads((wynik / "generation-report.json").read_text(encoding="utf-8"))

    @staticmethod
    def add(katalog):
        with (katalog / "ebay-add.csv").open(encoding="utf-8-sig", newline="") as uchwyt:
            wiersze = list(csv.reader(uchwyt, delimiter=";"))
        return wiersze[1], [dict(zip(wiersze[1], x)) for x in wiersze[2:]]

    @staticmethod
    def revise(katalog, nazwa="ebay-revise.csv"):
        with (katalog / nazwa).open(encoding="utf-8-sig", newline="") as uchwyt:
            wiersze = list(csv.reader(uchwyt))
        return [dict(zip(wiersze[1], x)) for x in wiersze[2:]]

    # Zwykla oferta bazy: RAM i dysk jak wariant, ale z "W" z przodu - inny SKU niz
    # wariant bazowy tej samej konfiguracji (decyzja z 17.11.2026).
    BAZA = "WSHOPER-ID:7681-8GB-256GB"
    WARIANT_BAZOWY = "SHOPER-ID:7681-8GB-256GB"

    def raport_ebay(self, zmiany=None, dodatkowe=()):
        """Raport eBaya dzien po wgraniu pliku z przebiegu 'pierwsze': zwykla oferta bazy,
        oferta wariantowa i jeden komputer. 'zmiany': SKU -> (cena, ilosc) inne niz w pliku."""
        naglowek, wiersze = self.add(self.dodanie)
        akcja = naglowek[0]
        pola = ["Item number", "Title", "Variation details", "Custom label (SKU)", "Available quantity",
                "Currency", "Start price", "End date", "Listing site"]
        wyjscie, numer_wariantowej = [], "307000000002"
        for w_ in wiersze:
            sku = w_["CustomLabel"]
            if not (sku.startswith(("SHOPER-ID:7681", "WSHOPER-ID:7681")) or sku == "3959"):
                continue
            cena, ilosc = w_["*StartPrice"], w_["*Quantity"]
            if sku.endswith(":warianty"):
                cena, ilosc = "1.0", "22"
            elif sku == "3959":
                ilosc = "3"                      # komputer: 47 sztuk po progach stanow starej sciezki
            wariantowa = sku.endswith(":warianty") or w_["Relationship"] == "Variation"
            cena, ilosc = (zmiany or {}).get(sku, (cena, ilosc))
            wyjscie.append([numer_wariantowej if wariantowa else f"30500000{len(wyjscie):04d}", "Oferta",
                            w_["RelationshipDetails"] if wariantowa else "", sku, ilosc, "EUR", cena, "", "DE"])
            self.assertIn(w_[akcja], ("Add", ""))
        wyjscie += list(dodatkowe)
        plik = Path(tempfile.mkdtemp()) / "aktywne.csv"
        with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
            zapis = csv.writer(uchwyt)
            zapis.writerow(pola)
            zapis.writerows(wyjscie)
        return plik

    def test_poczekalnia_w_kazdym_przebiegu_z_nowa_sciezka(self):
        # Wlasciciel przeglada duble recznie - lista ma byc w przebiegu, nie tylko w narzedziu.
        for plik in (ow.PLIK_POCZEKALNI_CSV, ow.PLIK_POCZEKALNI_HTML):
            self.assertTrue((self.dodanie / plik).exists(), plik)
            self.assertFalse((self.bez / plik).exists(), plik)
        with (self.dodanie / ow.PLIK_POCZEKALNI_CSV).open(encoding="utf-8-sig", newline="") as uchwyt:
            self.assertEqual(next(csv.reader(uchwyt, delimiter=";")), ow.KOLUMNY_POCZEKALNI)
        wr = self.raport_dodania["warianty"]
        self.assertEqual(sum(wr["poczekalnia_wg_powodu"].values()), wr["w_poczekalni"])
        self.assertEqual(wr["poczekalnia_pliki"], [ow.PLIK_POCZEKALNI_HTML, ow.PLIK_POCZEKALNI_CSV])
        json.dumps(wr)                                   # raport musi dac sie zapisac (Decimal!)

    def test_wylaczony_przelacznik_nic_nie_zmienia(self):
        domyslnie, raport = self.przebieg("pierwsze", wlaczone=False)
        self.assertEqual(raport["warianty"], {"wlaczone": False})
        self.assertEqual((domyslnie / "ebay-add.csv").read_bytes(), (self.bez / "ebay-add.csv").read_bytes())
        self.assertFalse(any(x["CustomLabel"].startswith("SHOPER-ID") for x in self.add(self.bez)[1]))

    def test_przelacznik_tylko_dla_wybranego_rynku(self):
        # Decyzja wlasciciela 17.11.2026: nowy system najpierw tylko na Francji.
        tylko_fr = self.katalog / "warianty-fr.json"
        dane = json.loads(self.ustawienia.read_text(encoding="utf-8"))
        tylko_fr.write_text(json.dumps(dict(dane, wlaczone=["fr"])), encoding="utf-8")
        niemcy, raport_de = self.przebieg("pierwsze", ustawienia=tylko_fr)
        francja, raport_fr = self.przebieg("pierwsze", kraj="fr", ustawienia=tylko_fr)
        self.assertEqual(raport_de["warianty"], {"wlaczone": False})
        self.assertEqual((niemcy / "ebay-add.csv").read_bytes(), (self.bez / "ebay-add.csv").read_bytes())
        self.assertTrue(raport_fr["warianty"]["wlaczone"])
        self.assertEqual(raport_fr["blokady"], [])
        sku = [x["CustomLabel"] for x in self.add(francja)[1]]
        self.assertIn(self.BAZA + "-FRA", sku)
        self.assertIn("SHOPER-ID:7681:warianty-FRA", sku)
        self.assertTrue(any(s_.startswith("SHOPER-ID:7681-") and s_.endswith("GB-FRA") or
                            s_.endswith("TB-FRA") for s_ in sku), sku)
        self.assertFalse([s_ for s_ in sku if s_.startswith("4220")], sku)
        # pusta lista albo rynek spoza listy = wylaczone
        nigdzie = self.katalog / "warianty-nigdzie.json"
        nigdzie.write_text(json.dumps(dict(dane, wlaczone=[])), encoding="utf-8")
        self.assertEqual(self.przebieg("pierwsze", kraj="fr", ustawienia=nigdzie)[1]["warianty"],
                         {"wlaczone": False})

    def test_budzet_ogranicza_oferty_wariantowe(self):
        # Limit sprzedazy eBaya: komplet ofert wariantowych go przekracza, wiec wlasciciel
        # podaje budzet w zlotowkach. Oferta, ktora sie nie miesci, czeka - zwykla zostaje.
        dane = json.loads(self.ustawienia.read_text(encoding="utf-8"))
        ciasny = self.katalog / "warianty-budzet.json"
        ciasny.write_text(json.dumps(dict(dane, budzet_pln=1000)), encoding="utf-8")
        wynik, raport = self.przebieg("pierwsze", ustawienia=ciasny)
        sku = [x["CustomLabel"] for x in self.add(wynik)[1]]
        self.assertIn(self.BAZA, sku)
        self.assertNotIn("SHOPER-ID:7681:warianty", sku)
        self.assertEqual(raport["pominieto"].get("wariantowa: nie miesci sie w budzecie"), 1)
        self.assertEqual(raport["warianty"]["ofert_wariantowych_w_pliku"], 0)
        luzny = self.katalog / "warianty-budzet-duzy.json"
        luzny.write_text(json.dumps(dict(dane, budzet_pln=10_000_000)), encoding="utf-8")
        wynik, raport = self.przebieg("pierwsze", ustawienia=luzny)
        self.assertIn("SHOPER-ID:7681:warianty", [x["CustomLabel"] for x in self.add(wynik)[1]])
        self.assertGreater(raport["warianty"]["wartosc_ofert_wariantowych_pln"], 1000)
        # w trybie 'nowe' do budzetu liczy sie tez to, co juz stoi na eBayu
        dopelniony = self.katalog / "warianty-budzet-pelny.json"
        dopelniony.write_text(json.dumps(dict(dane, budzet_pln=raport["warianty"]["wartosc_ofert_wariantowych_pln"])),
                              encoding="utf-8")
        obca = [["307999999999", "Inna wariantowa", "RAM=8 GB|SSD=256 GB", "SHOPER-ID:55555-8GB-256GB", "2", "EUR", "500.0", "", "DE"],
                ["305999999998", "Zwykla", "", "3959", "5", "EUR", "200.0", "", "DE"]]
        plik = Path(tempfile.mkdtemp()) / "aktywne.csv"
        with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
            zapis = csv.writer(uchwyt)
            zapis.writerow(["Item number", "Title", "Variation details", "Custom label (SKU)", "Available quantity",
                            "Currency", "Start price", "End date", "Listing site"])
            zapis.writerows(obca)
        wynik, raport = self.przebieg("nowe", raport=plik, ustawienia=dopelniony)
        self.assertNotIn("SHOPER-ID:7681:warianty", [x["CustomLabel"] for x in self.add(wynik)[1]])
        self.assertEqual(raport["pominieto"].get("wariantowa: nie miesci sie w budzecie"), 1)

    def test_laptop_dostaje_zwykla_oferte_i_oferte_wariantowa(self):
        naglowek, wiersze = self.add(self.dodanie)
        akcja = naglowek[0]
        sku = [x["CustomLabel"] for x in wiersze]
        self.assertEqual(self.raport_dodania["blokady"], [])
        # zwykla oferta konfiguracji podstawowej - z sygnatura ID Shopera, ze zwykla cena i iloscia
        zwykla = next(x for x in wiersze if x["CustomLabel"] == self.BAZA and x["Relationship"] == "")
        # SKU zwyklej oferty niesie RAM i dysk, tak jak wariant (dla magazynu)
        self.assertEqual(zwykla[akcja], "Add")
        self.assertEqual((zwykla[akcja], zwykla["Relationship"]), ("Add", ""))
        # 51 sztuk w magazynie -> 2 na eBayu: w nowej sciezce wszystko po 2 sztuki
        self.assertTrue(float(zwykla["*StartPrice"]) > 0)
        self.assertEqual(zwykla["*Quantity"], "2")
        # oferta wariantowa tego samego wpisu: wiersz oferty + warianty z pusta akcja
        oferta = next(x for x in wiersze if x["CustomLabel"] == "SHOPER-ID:7681:warianty")
        warianty_ = [x for x in wiersze if x["Relationship"] == "Variation"]
        self.assertEqual((oferta[akcja], oferta["*StartPrice"], oferta["*Quantity"]), ("Add", "", ""))
        self.assertTrue(warianty_ and all(x[akcja] == "" for x in warianty_))
        self.assertTrue(all(x["CustomLabel"].startswith("SHOPER-ID:7681-") for x in warianty_))
        # zwykla oferta i wariant tej samej konfiguracji: ta sama tresc dla magazynu, rozne SKU
        self.assertIn(self.WARIANT_BAZOWY, [x["CustomLabel"] for x in warianty_])
        self.assertEqual(len(sku), len(set(sku)), "dwa wiersze z tym samym SKU")
        # wariant bazowy kosztuje tyle samo co zwykla oferta (dubel z decyzji wlasciciela)
        self.assertEqual(warianty_[0]["*StartPrice"], zwykla["*StartPrice"])
        # wiersze wariantow stoja bezposrednio pod swoja oferta
        i = sku.index("SHOPER-ID:7681:warianty")
        self.assertEqual(sku[i + 1:i + 1 + len(warianty_)], [x["CustomLabel"] for x in warianty_])
        # stare SKU Shopera laptopow nie ida juz na eBay
        self.assertNotIn("4220", sku)
        self.assertNotIn("3809", sku)
        # laptop z 6 sztukami: ponizej progow stanow, wiec na razie nie wystawiamy ani zwyklej
        # oferty (ilosc bylaby 0), ani wariantowej (prog 10)
        self.assertFalse([s_ for s_ in sku if s_.startswith(("SHOPER-ID:7452", "WSHOPER-ID:7452"))], sku)
        self.assertEqual(self.raport_dodania["pominieto"].get("baza ponizej progu stanu"), 2)
        self.assertEqual(self.raport_dodania["warianty"]["ofert_wariantowych_w_pliku"], 1)

    def test_komputery_ida_po_staremu_tylko_z_iloscia_wedlug_progow(self):
        # Komputery nie maja wariantow: te same wiersze co przy wylaczonym przelaczniku,
        # rozni sie wylacznie ilosc - wedlug progow stanow zamiast pelnego stanu.
        _, wlaczone = self.add(self.dodanie)
        _, wylaczone = self.add(self.bez)
        wg_sku = lambda wiersze: {x["CustomLabel"]: x for x in wiersze if x["CustomLabel"] in ("3959", "4967", "10362")}
        nowe, stare = wg_sku(wlaczone), wg_sku(wylaczone)
        self.assertEqual({k: v["*Quantity"] for k, v in stare.items()}, {"3959": "47", "10362": "16"})
        self.assertEqual({k: v["*Quantity"] for k, v in nowe.items()}, {"3959": "3", "10362": "2"})
        for sku, wiersz in nowe.items():
            self.assertEqual(dict(wiersz, **{"*Quantity": ""}), dict(stare[sku], **{"*Quantity": ""}), sku)

    def test_nowe_nie_wystawia_drugi_raz(self):
        wynik, raport = self.przebieg("nowe", raport=self.raport_ebay())
        sku = [x["CustomLabel"] for x in self.add(wynik)[1]]
        self.assertFalse([s for s in sku if s.startswith(("SHOPER-ID:7681", "WSHOPER-ID:7681"))], sku)
        self.assertEqual(raport["pominieto"].get("wariantowa: juz wystawiona"), 1)

    def test_aktualizacja_bez_zmian_nie_rusza_wariantow(self):
        wynik, raport = self.przebieg("aktualizacja", raport=self.raport_ebay())
        self.assertEqual(raport["blokady"], [])
        self.assertEqual([x for x in self.revise(wynik) if x["Relationship"] == "Variation"], [])
        self.assertEqual(raport["warianty"]["aktualizacja"]["ofert"], 1)

    def test_aktualizacja_poprawia_wariant_i_zwykla_oferte_w_jednym_pliku(self):
        wariant = next(x for x in self.add(self.dodanie)[1] if x["Relationship"] == "Variation")
        self.assertEqual(wariant["CustomLabel"], self.WARIANT_BAZOWY)
        zmiany = {wariant["CustomLabel"]: ("9999.0", "7"), self.BAZA: ("9999.0", "5")}
        stara = ["305999999999", "Stara aukcja po SKU Shopera", "", "4220", "5", "EUR", "300.0", "", "DE"]
        wynik, raport = self.przebieg("aktualizacja", raport=self.raport_ebay(zmiany, [stara]))
        self.assertEqual(raport["blokady"], [])
        for nazwa in ("ebay-revise.csv", "ebay-revise-stany.csv"):
            wiersze = {x["Custom label (SKU)"]: x for x in self.revise(wynik, nazwa) if x["Relationship"] == ""}
            w_ = next(x for x in self.revise(wynik, nazwa)
                      if x["Relationship"] == "Variation" and x["Custom label (SKU)"] == wariant["CustomLabel"])
            self.assertEqual((w_["Action"], w_["Relationship"], w_["Item number"]), ("", "Variation", ""), nazwa)
            oferta = wiersze["SHOPER-ID:7681:warianty"]
            self.assertEqual((oferta["Action"], oferta["Item number"], oferta["Relationship"]), ("Revise", "307000000002", ""), nazwa)
            lista = self.revise(wynik, nazwa)
            self.assertEqual(oferty_wariantowe_uklad(lista), "", nazwa)
            self.assertEqual((float(w_["Start price"]), w_["Available quantity"]), (float(wariant["*StartPrice"]), "2"), nazwa)
            self.assertEqual(w_["Relationship details"], wariant["RelationshipDetails"])
            zwykla = wiersze[self.BAZA]
            self.assertEqual((zwykla["Relationship"], float(zwykla["Start price"])), ("", float(wariant["*StartPrice"])), nazwa)
            # zwykla oferta bazy: pelny plik mowi prawde o magazynie, plik stanow daje 2 sztuki
            self.assertEqual(zwykla["Available quantity"], "51" if nazwa == "ebay-revise.csv" else "2", nazwa)
            # dotychczasowa aukcja tego laptopa po SKU Shopera wypada z obiegu - dostaje zero
            self.assertEqual(wiersze["4220"]["Available quantity"], "0", nazwa)


    def test_reczna_ilosc_po_starym_sku_trafia_w_nowa_oferte(self):
        # Wpis '4220;5' z czasow starej sciezki (4220 = SKU Shopera wpisu ID 7681) dziala
        # bez przepisywania: zwykla oferta bazy i kazdy wariant pokazuja 5 sztuk.
        wynik, raport = self.przebieg("pierwsze", reczne="4220;5;\n")
        wiersze = self.add(wynik)[1]
        zwykla = next(x for x in wiersze if x["CustomLabel"] == self.BAZA and x["Relationship"] == "")
        warianty_ = [x for x in wiersze if x["Relationship"] == "Variation"]
        self.assertEqual(zwykla["*Quantity"], "5")
        self.assertEqual({x["*Quantity"] for x in warianty_}, {"5"})
        self.assertEqual(raport["sku_reczne"]["przepisane_na_oferty_shoper_id"], {self.BAZA: "4220"})
        # ...i to samo pod nowym zapisem; 0 = nie wystawiaj wcale
        wynik, _ = self.przebieg("pierwsze", reczne="SHOPER-ID:7681;0;\n")
        self.assertFalse([x for x in self.add(wynik)[1] if x["CustomLabel"].startswith(("SHOPER-ID:7681", "WSHOPER-ID:7681"))])
        # Inny rynek: '4220' to aukcja niemiecka, na Francji stary SKU brzmial '4220FRA'.
        tylko_fr = self.katalog / "warianty-fr-reczne.json"
        tylko_fr.write_text(json.dumps(dict(json.loads(self.ustawienia.read_text(encoding="utf-8")),
                                            wlaczone=["fr"])), encoding="utf-8")
        francja, _ = self.przebieg("pierwsze", kraj="fr", ustawienia=tylko_fr, reczne="4220;5;\n")
        zwykla_fr = next(x for x in self.add(francja)[1] if x["CustomLabel"] == self.BAZA + "-FRA"
                         and x["Relationship"] == "")
        self.assertEqual(zwykla_fr["*Quantity"], "2")

    def test_przepisany_wpis_nie_chroni_starej_aukcji(self):
        # Stara aukcja '4220' miala wpis na liscie recznej. Laptop stoi teraz pod SKU
        # SHOPER-ID, wiec stara aukcja ma dostac zero, a nie dalej sprzedawac obok.
        stara = ["305999999999", "Stara aukcja", "", "4220", "7", "EUR", "300.0", "", "DE"]
        wynik, raport = self.przebieg("aktualizacja", raport=self.raport_ebay(dodatkowe=[stara]),
                                      reczne="4220;5;\n")
        self.assertEqual(raport["blokady"], [])
        for nazwa in ("ebay-revise.csv", "ebay-revise-stany.csv"):
            wiersze = {x["Custom label (SKU)"]: x for x in self.revise(wynik, nazwa) if x["Relationship"] == ""}
            self.assertEqual(wiersze["4220"]["Available quantity"], "0", nazwa)
            # jeden wiersz, nie "ustaw 5" i zaraz potem "ustaw 0"
            self.assertEqual([x["Custom label (SKU)"] for x in self.revise(wynik, nazwa)].count("4220"), 1, nazwa)
        stany = {x["Custom label (SKU)"]: x for x in self.revise(wynik, "ebay-revise-stany.csv")
                 if x["Relationship"] == ""}
        self.assertEqual(stany[self.BAZA]["Available quantity"], "5")

    def feed_z_dublem(self):
        """Feed testowy + drugi wpis o tych samych szesciu cechach co laptop 4220 (ID 7681):
        ID 99001, SKU 'KLON', TANSZY - czyli "taki sam model pomalowany na zolto"."""
        root = ET.fromstring(FEED.read_bytes())
        wzor = next(o for o in root.findall("./o") if o.get("id") == "7681")
        klon = ET.fromstring(ET.tostring(wzor))
        klon.set("id", "99001")
        klon.set("price", str(float(wzor.get("price")) - 300))
        klon.set("stock", "60")
        for a in klon.findall("./attrs/a"):
            if a.get("name") == "SKU":
                a.text = "KLON"
        root.append(klon)
        plik = Path(tempfile.mkdtemp()) / "feed.xml"
        plik.write_bytes(ET.tostring(root, encoding="utf-8"))
        return plik

    def test_dubel_idzie_do_poczekalni_a_nie_na_ebay(self):
        # Pierwsze wystawienie: z dwoch takich samych wpisow wystawiamy tanszy, drugi czeka.
        wynik, raport = self.przebieg("pierwsze", feed=self.feed_z_dublem())
        sku = [x["CustomLabel"] for x in self.add(wynik)[1]]
        self.assertIn("WSHOPER-ID:99001-8GB-256GB", sku)
        self.assertIn("SHOPER-ID:99001:warianty", sku)
        self.assertFalse([s_ for s_ in sku if s_.startswith(("SHOPER-ID:7681", "WSHOPER-ID:7681"))], sku)
        self.assertNotIn("4220", sku)                         # wpis z poczekalni nie jest wystawiany wcale
        self.assertNotIn("KLON", sku)
        self.assertEqual(raport["warianty"]["w_poczekalni"], 1)
        self.assertEqual(raport["pominieto"].get("laptop w poczekalni"), 1)

    def test_tanszy_dubel_nie_przejmuje_oferty_ktora_juz_stoi(self):
        # Na eBayu stoja juz oferty wpisu 7681. Tanszy wpis 99001 dochodzi pozniej: zostaje
        # w poczekalni, a ceny istniejacych ofert dalej licza sie od 7681.
        feed = self.feed_z_dublem()
        nowe, raport = self.przebieg("nowe", raport=self.raport_ebay(), feed=feed)
        sku = [x["CustomLabel"] for x in self.add(nowe)[1]]
        self.assertFalse([s_ for s_ in sku if s_.startswith(("SHOPER-ID:99001", "SHOPER-ID:7681", "WSHOPER-ID:99001", "WSHOPER-ID:7681"))], sku)
        self.assertEqual(raport["warianty"]["w_poczekalni"], 1)
        akt, raport_akt = self.przebieg("aktualizacja", raport=self.raport_ebay(), feed=feed)
        self.assertEqual(raport_akt["blokady"], [])
        ruszone = [x["Custom label (SKU)"] for x in self.revise(akt)
                   if x["Relationship"] == "Variation" and x["Custom label (SKU)"].startswith("SHOPER-ID:7681-")]
        self.assertEqual(ruszone, [])                         # warianty bez zmian: ta sama baza, te same ceny


class RynkiTests(unittest.TestCase):
    def test_kazdy_rynek_sklada_oferte_w_swoim_jezyku(self):
        nazwy = {}
        for kraj in ("de", "it", "es", "fr"):
            cfg, rodziny = srodowisko(kraj)
            wiersze, blad = ow.zbuduj_oferte(rodziny["4220"], cfg, cfg["headers"], g, _Bez(),
                                             [cfg["kraj"].get("sufiks_sku", "")])
            self.assertEqual(blad, "", kraj)
            self.assertEqual(ow.sprawdz_oferte(wiersze), [], kraj)
            nazwy[kraj] = wiersze[0]["RelationshipDetails"].split("=")[0]
        self.assertEqual(nazwy["de"], "Arbeitsspeichergröße")
        self.assertEqual(len(set(nazwy.values())), 4, nazwy)


if __name__ == "__main__":
    unittest.main()
