"""Testy siatki wariantow RAM/dysk. Kazdy pilnuje reguly, ktora ustalil wlasciciel."""
import os
import copy
import csv
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

# Testy nie czytaja przelacznika z config/warianty.json (plik wlasciciela) - patrz
# ustawienia_wariantow() w generate.py. Kto chce nowej sciezki, podaje --warianty.
os.environ.setdefault("EBAY_BEZ_WARIANTOW", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import generate as g  # noqa: E402
import warianty as w  # noqa: E402

FUNKCJE = {"norm": g.norm, "brand_name": g.brand_name, "cpu_candidates": g.cpu_candidates,
           "zrodlo_procesora": g.zrodlo_procesora, "clean_capacity": g.clean_capacity,
           "marki": g.MARKI_ZNANE, "condition_class": g.condition_class,
           "nazwa_modelu": g.nazwa_modelu}
# Cennik WZORCOWY, zamrozony w tescie - celowo NIE czytamy config/warianty.json.
# Tamten plik wlasciciel zmienia wedle uznania, a workflow uruchamia testy przed
# kazdym generowaniem: gdyby testy liczyly na cenach z repo, zmiana jednej doplaty
# zatrzymalaby caly przebieg eBaya. Testy pilnuja WZORU, nie kwot w cenniku.
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
    "max_sztuk_na_wariant": 1,
}
BEZ_LIMITU = {"max_ram": 32, "max_dysk": None, "zrodlo": "test", "jawny_ram": False}


def laptop(sku, ram="8GB", dysk="256GB", cena="1199", stan="6", nazwa="Laptop", kategoria="Laptopy",
           id_=None, **inne):
    """Para (offer, attrs) w tej samej postaci, w jakiej daje ja zbierz_produkty().
    'id_' to ID Shopera (atrybut id w XML); bez niego bierzemy 'id-<sku>'."""
    attrs = {"SKU": sku, "Producent": "Lenovo", "Model": "ThinkPad E15 Gen 2",
             "Procesor": "i5 - 1135G7, 8MB Cache, 11 gen.",
             "Kondycja sprzętu": "[Klasa A-] laptop poleasingowy, w 100% sprawny",
             "Rozdzielczość ekranu": "1920 x 1080", "Ekran dotykowy": "Nie",
             "Typ pamięci RAM": "DDR4", "Typ dysku": "SSD",
             "Model karty graficznej": "Intel Iris Xe Graphics",
             "Ilość pamięci RAM": ram, "Dysk": dysk}
    attrs.update(inne)
    offer = ET.Element("o", {"id": "id-" + sku if id_ is None else id_, "price": cena, "stock": stan,
                             "avail": "1"})
    ET.SubElement(offer, "name").text = nazwa
    ET.SubElement(offer, "cat").text = kategoria
    return offer, attrs


def rodziny(produkty, cennik=CENNIK, limity=(), przypiete=()):
    return w.zbuduj_rodziny(produkty, cennik, list(limity), FUNKCJE, przypiete=przypiete)


def wynik_sku():
    """SKU wszystkich wariantow rodziny wzorcowej o ID 10207 - do testu odczytu."""
    return [v["sku"] for v in rodziny([laptop("X", id_="10207")])["rodziny"][0]["warianty"]]


def ceny(rodzina):
    return {(v["ram"], v["dysk"]): v["cena"] for v in rodzina["warianty"]}


class PojemnoscTests(unittest.TestCase):
    def test_zapisy_z_feedu(self):
        for tekst, gb in (("8GB", 8), ("8 GB", 8), ("256gb", 256), ("1TB", 1000), ("2 TB", 2000),
                          ("120/128 GB", 128)):
            self.assertEqual(w.pojemnosc_gb(tekst), gb, tekst)

    def test_smieci_to_none_a_nie_zero(self):
        # None wyrzuca wpis z rodziny. Zero poszloby dalej jako najmniejsza
        # konfiguracja i zostaloby baza calej rodziny.
        for tekst in ("", "brak", "8", "GB", "8 MB", "1,5 TB i wiecej", None):
            self.assertIsNone(w.pojemnosc_gb(tekst), tekst)

    def test_etykieta_wraca_do_zapisu_feedu(self):
        self.assertEqual([w.etykieta_feedu(x) for x in (256, 960, 1000, 2000)],
                         ["256GB", "960GB", "1TB", "2TB"])


class CennikTests(unittest.TestCase):
    def test_wzorcowy_cennik_jest_poprawny(self):
        self.assertEqual(w.sprawdz_cennik(CENNIK), [])

    def test_pliki_wlasciciela_nie_zatrzymuja_testow(self):
        # config/warianty.json i config/warianty-limity.csv wypelnia wlasciciel. Blad
        # w nich ma wyjsc w raporcie generatora jako czytelna blokada, a nie jako
        # czerwony krok "Uruchom testy", po ktorym nie powstaje zaden raport.
        # Tu sprawdzamy tylko, ze ich czytanie niczego nie wywraca.
        sciezka = ROOT / "config" / "warianty.json"
        if sciezka.is_file():
            try:
                self.assertIsInstance(w.sprawdz_cennik(g.load_json(sciezka)), list)
            except ValueError:
                pass                                 # zepsuty JSON zglosi generator
        wiersze, bledy = w.wczytaj_limity(ROOT / "config" / "warianty-limity.csv")
        self.assertIsInstance(wiersze, list)
        self.assertIsInstance(bledy, list)

    def test_bledy_recznej_edycji_sa_nazwane(self):
        zly = copy.deepcopy(CENNIK)
        zly["ram_doplaty"]["16>8"] = 100            # odwrocone przejscie
        zly["ram_doplaty"]["8>16"] = -5             # ujemna doplata
        zly["dysk_doplaty"]["256>512"] = "duzo"     # nie liczba
        zly["dysk_grupy"]["256"].append(480)        # 480 w dwoch grupach
        bledy = " | ".join(w.sprawdz_cennik(zly))
        for fragment in ("'16>8'", "ujemna", "nie jest liczba", "480 GB jest w dwoch grupach"):
            self.assertIn(fragment, bledy)


class SiatkaTests(unittest.TestCase):
    def test_ceny_jak_na_allegro(self):
        # ThinkPad E15 Gen 2 klasa A-, baza 10207: 8/256 za 1199 zl. Siedem z tych
        # cen stoi dzis na zywych ofertach Allegro wystawionych przez automat
        # shoper-allegro-sync (np. 16/960 = 1949 zl, oferta 19113006208).
        wynik = rodziny([laptop("B")])["rodziny"][0]
        self.assertEqual(ceny(wynik), {
            (8, 256): 1199, (8, 480): 1399, (8, 960): 1749,
            (16, 256): 1399, (16, 480): 1599, (16, 960): 1949, (16, 2000): 2249,
            (32, 256): 1749, (32, 480): 1949, (32, 960): 2299, (32, 2000): 2599})

    def test_baza_jest_pierwsza_i_bez_doplaty(self):
        pierwszy = rodziny([laptop("B")])["rodziny"][0]["warianty"][0]
        self.assertTrue(pierwszy["baza"])
        self.assertEqual((pierwszy["sku"], pierwszy["doplata"]), ("SHOPER-ID:id-B-8GB-256GB", 0))

    def test_przy_8_gb_dysk_najwyzej_960(self):
        self.assertNotIn((8, 2000), ceny(rodziny([laptop("B")])["rodziny"][0]))

    def test_siatka_tylko_rozbudowuje(self):
        # Z 16/512 nie zrobimy 8 GB ani mniejszego dysku. 480 odpada tez dlatego,
        # ze to ta sama polka cenowa co 512.
        konf, _ = w.siatka(16, 512, "DDR4", CENNIK, BEZ_LIMITU)
        self.assertEqual({(k["ram"], k["dysk"]) for k in konf},
                         {(16, 512), (16, 960), (16, 2000), (32, 512), (32, 960), (32, 2000)})

    def test_dysk_z_tej_samej_polki_cenowej_nie_jest_wariantem(self):
        # Dzisiejszy cennik nie ma 512 wsrod celow, ale gdy ktos je dopisze, z 480
        # nie moze powstac "rozbudowa" do 512 - to ta sama grupa, bez doplaty.
        cennik = dict(CENNIK, dysk_cele_gb=[240, 480, 512, 960, 2000])
        konf, braki = w.siatka(16, 480, "DDR4", cennik, dict(BEZ_LIMITU, max_ram=16))
        self.assertEqual([k["dysk"] for k in konf], [480, 960, 2000])
        self.assertEqual(braki, [])

    def test_ddr4_bez_rozmiarow_niestandardowych(self):
        konf, _ = w.siatka(8, 256, "DDR4", CENNIK, BEZ_LIMITU)
        self.assertEqual({k["ram"] for k in konf}, {8, 16, 32})    # bez 12 i 24

    def test_niestandardowy_rozmiar_tylko_gdy_lista_go_wskazuje(self):
        limit = {"max_ram": 24, "max_dysk": 0, "zrodlo": "lista", "jawny_ram": True}
        konf, _ = w.siatka(8, 256, "DDR4", CENNIK, limit)
        self.assertEqual({k["ram"] for k in konf}, {8, 16, 24})

    def test_inny_typ_pamieci_niz_ddr4_dostaje_wszystkie_przejscia_do_limitu(self):
        limit = dict(BEZ_LIMITU, max_ram=16)
        konf, _ = w.siatka(8, 256, "DDR3", CENNIK, limit)
        self.assertEqual({k["ram"] for k in konf}, {8, 12, 16})

    def test_brak_doplaty_w_cenniku_zdejmuje_wariant_i_jest_zgloszony(self):
        # Dysk 32 GB nie nalezy do zadnej grupy, wiec cennik nie zna doplaty
        # '32>256'. Wariant bez ceny nie moze trafic do oferty - ale brak ma byc
        # widoczny, zeby dalo sie dopisac doplate.
        konf, braki = w.siatka(8, 32, "DDR4", CENNIK, dict(BEZ_LIMITU, max_ram=8))
        self.assertEqual([(k["ram"], k["dysk"]) for k in konf], [(8, 32)])
        self.assertIn("dysk_doplaty: brak '32>256'", braki)

    def test_nowy_dysk_to_zawsze_ssd(self):
        _, attrs = laptop("B", **{"Typ dysku": "SSD+HDD"})
        self.assertEqual(w.atrybuty_wariantu(attrs, 16, 960)["Typ dysku"], "SSD")
        self.assertEqual(w.atrybuty_wariantu(attrs, 16, 256)["Typ dysku"], "SSD+HDD")
        self.assertEqual(w.atrybuty_wariantu(attrs, 16, 2000)["Dysk"], "2TB")
        self.assertEqual(w.atrybuty_wariantu(attrs, 16, 256)["Ilość pamięci RAM"], "16GB")


class LimityTests(unittest.TestCase):
    def limit(self, wiersze, **inne):
        _, attrs = laptop("B", **inne)
        limity = [dict({"procesor": "", "max_ram": None, "max_dysk": None, "uwagi": "", "wiersz": i + 2}, **x)
                  for i, x in enumerate(wiersze)]
        return w.limit_rozbudowy(attrs, "Laptop", "Laptopy", CENNIK, limity, FUNKCJE)

    def test_domyslnie_wedlug_typu_pamieci(self):
        self.assertEqual(self.limit([])["max_ram"], 32)                                     # DDR4
        self.assertEqual(self.limit([], **{"Typ pamięci RAM": "Inny"})["max_ram"], 0)       # wlutowana
        self.assertEqual(self.limit([], **{"Typ pamięci RAM": "DDR5"})["max_ram"], 16)      # reszta
        self.assertEqual(self.limit([], **{"Typ pamięci RAM": ""})["max_ram"], 16)

    def test_wiersz_modelu_z_marka_i_bez(self):
        for wpis in ("Lenovo ThinkPad E15 Gen 2", "ThinkPad E15 Gen 2", "thinkpad e15 gen2", "ThinkPad E15 Gen. 2"):
            wynik = self.limit([{"model": wpis, "max_ram": 16}])
            self.assertEqual((wynik["max_ram"], wynik["jawny_ram"]), (16, True), wpis)

    def test_podobny_model_nie_pasuje(self):
        # Dopasowania po zawieraniu nie ma: ograniczenie E15 nie moze spasc na E15 Gen 3.
        for wpis in ("ThinkPad E15", "ThinkPad E15 Gen 3", "E15 Gen 2"):
            self.assertEqual(self.limit([{"model": wpis, "max_ram": 0}])["max_ram"], 32, wpis)

    def test_wiersz_z_procesorem_wygrywa_z_wierszem_modelu(self):
        # Wartosci wiersza z procesorem sa celowo INNE niz (0, 0): tyle samo zwraca
        # blad "dwa wiersze tego samego modelu", wiec zero niczego by nie dowodzilo.
        wiersze = [{"model": "ThinkPad E15 Gen 2", "max_ram": 16},
                   {"model": "ThinkPad E15 Gen 2", "procesor": "i5-1135G7", "max_ram": 0, "max_dysk": 960}]
        wynik = self.limit(wiersze)
        self.assertEqual((wynik["max_ram"], wynik["max_dysk"], wynik["zrodlo"]), (0, 960, "lista, wiersz 3"))
        inny = self.limit(wiersze, Procesor="i7 - 1165G7, 12MB Cache")
        self.assertEqual((inny["max_ram"], inny["max_dysk"]), (16, None))

    def test_dokladny_procesor_wygrywa_z_ogolnym(self):
        # 'Intel Core i5' zawiera sie w 'Intel Core i5-1135G7', wiec pasuja oba wiersze.
        # Ma wygrac dokladny - bez tego konczyloby sie bledem "dwa wiersze" i brakiem rozbudowy.
        wynik = self.limit([{"model": "ThinkPad E15 Gen 2", "procesor": "Intel Core i5", "max_ram": 8},
                            {"model": "ThinkPad E15 Gen 2", "procesor": "Intel Core i5-1135G7", "max_ram": 16}])
        self.assertEqual((wynik["max_ram"], wynik["zrodlo"]), (16, "lista, wiersz 3"))

    def test_dwa_wiersze_tego_samego_modelu_wylaczaja_rozbudowe(self):
        # Nie wiadomo, ktory mial wygrac. Wariant wystawiony wbrew ograniczeniu
        # to obietnica nie do spelnienia, wiec bezpieczniej nie wystawic zadnego.
        wynik = self.limit([{"model": "ThinkPad E15 Gen 2", "max_ram": 16},
                            {"model": "Lenovo ThinkPad E15 Gen 2", "max_ram": 32}])
        self.assertEqual((wynik["max_ram"], wynik["max_dysk"]), (0, 0))
        self.assertIn("BLAD LISTY", wynik["zrodlo"])

    def test_marka_bez_rozbudowy_i_wyjatek_z_listy(self):
        apple = {"Producent": "Apple", "Model": "MacBook Pro A1707"}
        bez = self.limit([], **apple)
        self.assertEqual((bez["max_ram"], bez["max_dysk"]), (0, 0))
        wyjatek = self.limit([{"model": "Apple MacBook Pro A1707", "max_dysk": 960}], **apple)
        self.assertEqual((wyjatek["max_ram"], wyjatek["max_dysk"]), (0, 960))

    def test_chromebook_bez_rozbudowy(self):
        _, attrs = laptop("C", **{"Zainstalowany system": "Chrome OS"})
        wynik = w.limit_rozbudowy(attrs, "Laptop", "Laptopy", CENNIK, [], FUNKCJE)
        self.assertEqual((wynik["max_ram"], wynik["max_dysk"]), (0, 0))

    def test_plik_listy_czytany_odpornie(self):
        with tempfile.TemporaryDirectory() as katalog:
            plik = Path(katalog) / "warianty-limity.csv"
            plik.write_text("# opis nad naglowkiem\n\nmodel;procesor;max_ram;max_dysk;uwagi\n"
                            "Dell Latitude 7400;;0;0;wlutowany RAM\n"
                            "# zakomentowany;;8;;\n"
                            "HP EliteBook 840 G6;i5-8365U;;960;\n"
                            "Lenovo ThinkPad T480;;;;wiersz bez limitow niczego nie ustala\n"
                            ";;16;;\n"
                            "Dell Latitude 5400;;duzo;;\n", encoding="utf-8-sig")
            wiersze, bledy = w.wczytaj_limity(plik)
        self.assertEqual([(x["model"], x["procesor"], x["max_ram"], x["max_dysk"]) for x in wiersze],
                         [("Dell Latitude 7400", "", 0, 0), ("HP EliteBook 840 G6", "i5-8365U", None, 960)])
        self.assertEqual(len(bledy), 2)
        self.assertIn("pusta kolumna 'model'", bledy[0])
        self.assertIn("'duzo'", bledy[1])

    def test_brak_pliku_to_pusta_lista(self):
        self.assertEqual(w.wczytaj_limity(ROOT / "config" / "nie-ma-takiego.csv"), ([], []))


class RodzinyTests(unittest.TestCase):
    def test_klucz_rozdziela_to_co_ustalil_wlasciciel(self):
        _, baza = laptop("B")
        klucz = w.klucz_rodziny(baza, FUNKCJE)
        self.assertEqual(klucz, ("lenovo thinkpad e15 gen 2", "intel core i5-1135g7", "A-", "1920x1080",
                                 "bez dotyku", "intel iris xe"))
        for zmiana in ({"Kondycja sprzętu": "[Klasa A] laptop poleasingowy"},
                       {"Rozdzielczość ekranu": "1366 x 768"}, {"Ekran dotykowy": "Tak"},
                       {"Procesor": "i7 - 1165G7, 12MB Cache"}, {"Model": "ThinkPad E15 Gen 3"},
                       {"Model karty graficznej": "Intel Iris Xe Graphics + NVIDIA GeForce MX450 2GB"},
                       {"Model karty graficznej": "Intel UHD Graphics"}):
            self.assertNotEqual(w.klucz_rodziny(laptop("X", **zmiana)[1], FUNKCJE), klucz, zmiana)

    def test_ram_dysk_i_reszta_pol_nie_rozdzielaja_rodziny(self):
        _, baza = laptop("B")
        # Decyzja wlasciciela: porownujemy TYLKO szesc cech. Opis obudowy, baterii czy
        # klawiatury nie rozdziela ofert - taki wpis idzie do poczekalni.
        for zmiana in ({"Ilość pamięci RAM": "32GB"}, {"Dysk": "1TB"}, {"Bateria": "inna"}, {"Model": "Thinkpad E15 Gen2"},
                       {"Stan obudowy": "Klapa matrycy odnowiona w kolorze rozowym!"},
                       {"Klawiatura (ISO lub ANSI)": "QWERTY PL, Numeryczna"},
                       {"Model karty graficznej": "Intel Iris Xe"},
                       {"Model karty graficznej": "intel iris xe graphics for 11th Gen Intel Processors"}):
            self.assertEqual(w.klucz_rodziny(laptop("X", **zmiana)[1], FUNKCJE),
                             w.klucz_rodziny(baza, FUNKCJE), zmiana)

    def test_baza_to_najmniejsza_konfiguracja_a_reszta_idzie_do_poczekalni(self):
        wynik = rodziny([laptop("DUZY", ram="16GB", dysk="480GB", cena="1599", stan="10"),
                         laptop("BAZA"),
                         laptop("WIELKI", ram="16GB", dysk="2TB", cena="2449", stan="10", Bateria="nowa")])
        self.assertEqual(len(wynik["rodziny"]), 1)
        self.assertEqual(wynik["rodziny"][0]["baza"]["sku"], "BAZA")
        pom = {p["sku"]: p for p in wynik["poczekalnia"]}
        self.assertEqual(set(pom), {"DUZY", "WIELKI"})
        self.assertEqual(pom["DUZY"]["powod"], w.POWOD_WARIANT)
        self.assertEqual(pom["DUZY"]["roznice"], [])
        # Raport ma pokazac, o ile cena z XML rozjezdza sie ze wzorem: tu 2449 kontra 2249.
        self.assertEqual((pom["WIELKI"]["cena_xml"], pom["WIELKI"]["cena_ze_wzoru"]), (2449, 2249))
        # ...i czym wpis rozni sie od wystawionego - obie wartosci, nie sama nazwa pola.
        self.assertEqual(pom["WIELKI"]["roznice"], [{"pole": "Bateria", "wystawiony": "", "ten": "nowa"}])
        self.assertEqual(pom["WIELKI"]["id_wystawionego"], "id-BAZA")

    def test_drugi_wpis_tej_samej_konfiguracji_jest_nazwany(self):
        wynik = rodziny([laptop("A1"), laptop("A2", cena="1299")])
        self.assertEqual(wynik["rodziny"][0]["baza"]["sku"], "A1")           # tanszy jest baza
        self.assertEqual(wynik["poczekalnia"][0]["powod"], w.POWOD_DUBEL)
        self.assertEqual(wynik["poczekalnia"][0]["cena_wystawionego"], 1199)

    def test_oferta_trzyma_sie_swojego_id_dopoki_ma_stan(self):
        # Obawa wlasciciela: ktos doda taki sam model "pomalowany na zolto" i tanszy.
        # Bez przypiecia tanszy dubel zostalby baza i przejal oferte. Z przypieciem
        # oferta zostaje na swoim ID, a nowy wpis czeka w poczekalni z wypisana roznica.
        stary = laptop("STARY", id_="10207")
        zolty = laptop("ZOLTY", id_="10999", cena="999", **{"Stan obudowy": "pomalowany na zolto"})
        bez = rodziny([stary, zolty])
        self.assertEqual(bez["rodziny"][0]["id_bazy"], "10999")           # pierwsze wystawienie: najtanszy
        self.assertFalse(bez["rodziny"][0]["przypieta"])

        wynik = rodziny([stary, zolty], przypiete={"10207"})
        rodzina = wynik["rodziny"][0]
        self.assertEqual((rodzina["id_bazy"], rodzina["przypieta"]), ("10207", True))
        self.assertEqual(ceny(rodzina)[(8, 256)], 1199)                   # cena nadal ze starego wpisu
        czeka = wynik["poczekalnia"][0]
        self.assertEqual((czeka["id"], czeka["powod"], czeka["cena_xml"], czeka["cena_wystawionego"]),
                         ("10999", w.POWOD_DUBEL, 999, 1199))
        self.assertEqual(czeka["roznice"],
                         [{"pole": "Stan obudowy", "wystawiony": "", "ten": "pomalowany na zolto"}])

    def test_po_wyprzedaniu_przypietego_wchodzi_nastepny_wpis(self):
        # Przypiety wpis zniknal z listy dostepnych (stan 0). Oferta nie zostaje pusta:
        # baza staje sie kolejny wpis rodziny, a rodzina ma ten sam identyfikator.
        stary = laptop("STARY", id_="10207")
        nowy = laptop("NOWY", id_="10355", ram="16GB", dysk="480GB", cena="1599")
        przed = rodziny([stary, nowy], przypiete={"10207"})["rodziny"][0]
        po = rodziny([nowy], przypiete={"10207"})["rodziny"][0]
        self.assertEqual((przed["id_bazy"], po["id_bazy"]), ("10207", "10355"))
        self.assertEqual(przed["id"], po["id"])
        self.assertFalse(po["przypieta"])                                 # sygnal: trzeba zmienic ID na eBayu

    def test_mniejszy_wpis_nie_przejmuje_przypietej_oferty(self):
        # Oferta stoi na 16/480. Dochodzi 8/256 o tych samych cechach: czeka, nazwany
        # po imieniu, bo warianty robimy tylko W GORE od wystawionego wpisu.
        duzy = laptop("DUZY", id_="10355", ram="16GB", dysk="480GB", cena="1599")
        maly = laptop("MALY", id_="10207")
        wynik = rodziny([duzy, maly], przypiete={"10355"})
        self.assertEqual(wynik["rodziny"][0]["id_bazy"], "10355")
        self.assertEqual(wynik["poczekalnia"][0]["powod"], w.POWOD_MNIEJSZA)
        self.assertEqual(min(ceny(wynik["rodziny"][0])), (16, 480))

    def test_rodzina_przezywa_wyprzedanie_bazy(self):
        # "Aukcja nalezy do rodziny, nie do bazy". Gdy 8/256 sie wyprzeda, zostaje
        # ten sam identyfikator (czyli ta sama oferta), a warianty licza sie od 16/480.
        maly, duzy = laptop("MALY"), laptop("DUZY", ram="16GB", dysk="480GB", cena="1599", stan="10")
        przed = rodziny([maly, duzy])["rodziny"][0]
        po = rodziny([duzy])["rodziny"][0]
        self.assertEqual(przed["id"], po["id"])
        self.assertEqual(po["baza"]["sku"], "DUZY")
        self.assertEqual(ceny(po), {(16, 480): 1599, (16, 960): 1949, (16, 2000): 2299,
                                    (32, 480): 1999, (32, 960): 2349, (32, 2000): 2699})

    def test_inna_klasa_to_osobna_rodzina(self):
        wynik = rodziny([laptop("A-"), laptop("A", cena="1399", **{"Kondycja sprzętu": "[Klasa A] laptop"})])
        self.assertEqual(sorted(r["klasa"] for r in wynik["rodziny"]), ["A", "A-"])
        self.assertEqual(wynik["poczekalnia"], [])
        self.assertEqual(len({r["id"] for r in wynik["rodziny"]}), 2)

    def test_inna_grafika_to_osobna_rodzina(self):
        # Przyklad z feedu: Precision 7750 z RTX 3000 (3999 zl) i z RTX 5000 (5289 zl).
        wynik = rodziny([laptop("RTX3000", **{"Model karty graficznej": "Intel UHD Graphics + RTX 3000 6GB"}),
                         laptop("RTX5000", cena="2489", **{"Model karty graficznej": "Intel UHD Graphics + RTX 5000 16GB"})])
        self.assertEqual(len(wynik["rodziny"]), 2)
        self.assertEqual(wynik["poczekalnia"], [])

    def test_zapis_grafiki_nie_rozbija_rodziny(self):
        # Feed pisze te sama karte na kilka sposobow. Zapisy z feedu z 14.11.2026.
        grafika = lambda tekst: w.klucz_grafiki({"Model karty graficznej": tekst}, FUNKCJE)
        self.assertEqual(grafika("Intel HD Graphics 530 + NVIDIA GeForce 940MX"), "intel hd 530 + nvidia geforce 940mx")
        self.assertEqual(grafika("Intel HD 530 + NVIDIA GeForce 940MX"), "intel hd 530 + nvidia geforce 940mx")
        self.assertEqual(grafika("Intel UHD Graphics 630 + Nvidia Quadro T1000 4GB"),
                         grafika("Intel UHD Graphics 630 + Nvidia Quadro T1000"))
        self.assertEqual(grafika("Intel UHD Graphics 630 + Nvidia Quadro T1000 4GB (GDDR5)"),
                         "intel uhd 630 + nvidia quadro t1000")
        self.assertEqual(grafika("Intel UHD Graphics for 10th Gen Intel Processors"), grafika("Intel UHD Graphics"))
        self.assertNotEqual(grafika("Intel UHD Graphics 630 + Nvidia Quadro T1000 4GB"),
                            grafika("Intel UHD Graphics 630 + Nvidia Quadro T2000"))
        self.assertEqual(grafika(""), "")

    def test_stan_wariantu_nie_przekracza_bazy_ani_sufitu(self):
        stany = lambda cennik, stan: {v["stan"] for v in rodziny([laptop("B", stan=stan)], cennik)["rodziny"][0]["warianty"]}
        self.assertEqual(stany(dict(CENNIK, max_sztuk_na_wariant=5), "6"), {5})
        self.assertEqual(stany(dict(CENNIK, max_sztuk_na_wariant=5), "3"), {3})
        self.assertEqual(stany(dict(CENNIK, max_sztuk_na_wariant=0), "6"), {6})

    def test_warianty_dopiero_od_progu_stanu_bazy(self):
        # Decyzja wlasciciela 16.11.2026: nowa metoda bierze od 10 sztuk i ustawia po 2.
        cennik = dict(CENNIK, min_stan_bazy=10, max_sztuk_na_wariant=2)
        def stany(stan):
            rodzina = rodziny([laptop("B", stan=stan)], cennik)["rodziny"][0]
            return rodzina["aktywna"], {v["stan"] for v in rodzina["warianty"]}
        self.assertEqual(stany("9"), (False, {0}))
        self.assertEqual(stany("10"), (True, {2}))
        self.assertEqual(stany("923"), (True, {2}))
        # Ponizej progu rodzina nie znika - oferta ma zostac i wrocic, gdy stan wzrosnie.
        self.assertEqual(len(rodziny([laptop("B", stan="3")], cennik)["rodziny"][0]["warianty"]), 11)

    def test_jedna_zasada_ilosci_dla_calej_nowej_sciezki(self):
        # Decyzja wlasciciela 17.11.2026: "na nowym wszystko na 2 sztuki" - zwykla oferta
        # bazy i kazdy wariant licza ilosc ta sama funkcja.
        cennik = dict(CENNIK, min_stan_bazy=10, max_sztuk_na_wariant=2)
        self.assertEqual([w.ilosc_na_ebay(s, cennik) for s in (0, 9, 10, 51, 923)], [0, 0, 2, 2, 2])
        self.assertEqual(w.ilosc_na_ebay(1, dict(cennik, min_stan_bazy=0)), 1)        # nigdy wiecej niz w magazynie
        self.assertEqual(w.ilosc_na_ebay(40, dict(cennik, max_sztuk_na_wariant=0)), 40)

    def test_sku_wariantu_to_id_shopera_i_konfiguracja(self):
        # ID, nie SKU Shopera: SKU ktos moze zmienic przez przypadek, ID nadaje Shoper.
        # Koncowke ma takze wariant bazowy - zaden wariant nie ma SKU zwyklej oferty.
        wynik = rodziny([laptop("4553_20260216084009", id_="10207")])["rodziny"][0]
        sku = {(v["ram"], v["dysk"]): v["sku"] for v in wynik["warianty"]}
        self.assertEqual(wynik["sku_oferty"], "SHOPER-ID:10207:warianty")
        self.assertEqual(sku[(8, 256)], "SHOPER-ID:10207-8GB-256GB")
        self.assertEqual(sku[(16, 960)], "SHOPER-ID:10207-16GB-960GB")
        self.assertEqual(sku[(32, 2000)], "SHOPER-ID:10207-32GB-2TB")
        self.assertEqual(len(set(sku.values())), len(sku))
        self.assertNotIn("4553_20260216084009", sku.values())

    def test_sygnatura_z_dopiskami_rynku_i_gwarancji(self):
        # Format ustalony przez wlasciciela 16.11.2026: SHOPER-ID:<ID>... + dopiski rynkow.
        self.assertEqual(w.sku_oferty("10207", ["ITT"]), "SHOPER-ID:10207:warianty-ITT")
        self.assertEqual(w.sku_wariantu("10207", 8, 480, ["", "ITT"]), "SHOPER-ID:10207-8GB-480GB-ITT")
        self.assertEqual(w.sku_wariantu("10207", 8, 480, ["GW24", "SPAIN"]), "SHOPER-ID:10207-8GB-480GB-GW24-SPAIN")
        # eBay daje na SKU 50 znakow - najdluzszy mozliwy przypadek musi sie zmiescic.
        self.assertLessEqual(len(w.sku_wariantu("99999", 64, 960, ["GW24", "SPAIN"])), 50)
        self.assertLessEqual(len(w.sku_oferty("99999", ["GW24", "SPAIN"])), 50)

    def test_z_sku_w_raporcie_da_sie_odczytac_id_shopera(self):
        # Tak generator dowie sie, na ktorym wpisie stoi oferta - z samego raportu eBaya.
        self.assertEqual(w.rozloz_sku("SHOPER-ID:10207:warianty"),
                         {"id": "10207", "oferta": True, "zwykla": False, "ram": None, "dysk": None,
                          "dopiski": []})
        self.assertEqual(w.rozloz_sku("SHOPER-ID:10207-16GB-2TB-GW24-ITT"),
                         {"id": "10207", "oferta": False, "zwykla": False, "ram": 16, "dysk": 2000,
                          "dopiski": ["GW24", "ITT"]})
        # Zwykla oferta konfiguracji podstawowej: sama sygnatura i ID.
        self.assertEqual(w.sku_bazy("10207"), "SHOPER-ID:10207")
        self.assertEqual(w.sku_bazy("10207", ["ITT"]), "SHOPER-ID:10207-ITT")
        self.assertEqual(w.rozloz_sku("SHOPER-ID:10207-ITT"),
                         {"id": "10207", "oferta": False, "zwykla": True, "ram": None, "dysk": None,
                          "dopiski": ["ITT"]})
        self.assertEqual(w.rozloz_sku("SHOPER-ID:10207-8GB-480GB")["dysk"], 480)
        for wariant in wynik_sku():
            self.assertEqual(w.rozloz_sku(wariant)["id"], "10207", wariant)
        # SKU zwyklych aukcji i oferty testowej ze starego formatu to nie nasza sygnatura.
        for obce in ("4553_20260216084009", "10207:warianty", "10207-8GB-480GB", "3888_20250724125241GW24ITT",
                     "SHOPER-ID:", "SHOPER-ID:abc-8GB-480GB", "", None):
            self.assertIsNone(w.rozloz_sku(obce), obce)

    def test_wpis_bez_id_shopera_nie_dostaje_oferty_wariantowej(self):
        wynik = rodziny([laptop("BEZ-ID", id_="")])
        self.assertEqual(wynik["rodziny"], [])
        self.assertEqual(wynik["bez_rodziny"][0]["powod"], "brak ID Shopera")

    def test_wpis_bez_klasy_albo_z_nieczytelna_pamiecia_idzie_po_staremu(self):
        wynik = rodziny([laptop("BEZ", **{"Kondycja sprzętu": "laptop poleasingowy"}),
                         laptop("ZLY", ram="osiem")])
        self.assertEqual(wynik["rodziny"], [])
        self.assertEqual({b["sku"]: b["powod"] for b in wynik["bez_rodziny"]},
                         {"BEZ": "brak modelu, procesora albo klasy", "ZLY": "nieczytelny RAM albo dysk"})

    def test_model_bez_rozbudowy_zostaje_sama_baza(self):
        limity = [{"model": "ThinkPad E15 Gen 2", "procesor": "", "max_ram": 0, "max_dysk": 0, "uwagi": "", "wiersz": 2}]
        wynik = rodziny([laptop("B")], limity=limity)["rodziny"][0]
        self.assertEqual([(v["ram"], v["dysk"], v["baza"]) for v in wynik["warianty"]], [(8, 256, True)])

    def test_nazwa_z_generatora_wklejona_do_listy_trafia_w_swoj_model(self):
        # Lista modeli do wyklikania bierze nazwe z nazwa_modelu(). Wklejona z powrotem
        # do warianty-limity.csv musi trafic dokladnie w ten model - inaczej
        # ograniczenie wpisane z listy po cichu by nie dzialalo.
        for inne in ({}, {"Producent": "HP", "Model": "EliteBook 840 G6"},
                     {"Producent": "Apple", "Model": "Apple MacBook Pro A1707"},
                     {"Producent": "Dell", "Model": "Latitude 5420."}):
            _, attrs = laptop("B", **inne)
            limity = [{"model": g.nazwa_modelu(attrs), "procesor": "", "max_ram": 0, "max_dysk": 0,
                       "uwagi": "", "wiersz": 2}]
            wynik = w.limit_rozbudowy(attrs, "Laptop", "Laptopy", CENNIK, limity, FUNKCJE)
            self.assertEqual(wynik["zrodlo"], "lista, wiersz 2", inne)


class ListaModeliTests(unittest.TestCase):
    PRODUKTY = [laptop("E15-i5"), laptop("E15-i5-duzy", ram="16GB", dysk="480GB", cena="1599"),
                laptop("E15-i7", Procesor="i7 - 1165G7, 12MB Cache"),
                laptop("HP", Producent="HP", Model="EliteBook 840 G6", Procesor="i5 - 8365U, 6MB Cache"),
                laptop("MAC", Producent="Apple", Model="Apple MacBook Pro A1707", Procesor="i7 - 7820HQ")]
    LIMITY = [{"model": "ThinkPad E15 Gen 2", "procesor": "", "max_ram": 16, "max_dysk": None, "uwagi": "caly model", "wiersz": 2},
              {"model": "HP EliteBook 840 G6", "procesor": "i5-8365U", "max_ram": None, "max_dysk": 960, "uwagi": "", "wiersz": 3},
              {"model": "Dell Latitude 9999", "procesor": "", "max_ram": 0, "max_dysk": 0, "uwagi": "zawczasu", "wiersz": 4}]

    def lista(self, limity):
        wynik = rodziny(self.PRODUKTY, limity=limity)
        return w.lista_modeli(self.PRODUKTY, wynik["rodziny"], CENNIK, list(limity), FUNKCJE)

    def test_jeden_wiersz_na_model_i_procesor(self):
        wiersze = self.lista([])
        self.assertEqual([(x["model"], x["procesor"], x["wpisow"]) for x in wiersze],
                         [("Apple MacBook Pro A1707", "Intel Core i7-7820HQ", 1),
                          ("HP EliteBook 840 G6", "Intel Core i5-8365U", 1),
                          ("Lenovo ThinkPad E15 Gen 2", "Intel Core i5-1135G7", 2),
                          ("Lenovo ThinkPad E15 Gen 2", "Intel Core i7-1165G7", 1)])
        mac = wiersze[0]
        self.assertEqual((mac["teraz_max_ram"], mac["skad_limit"], mac["dorobionych_wariantow"]),
                         (0, "marka bez rozbudowy", 0))
        self.assertEqual(wiersze[2]["dorobionych_wariantow"], 10)

    def test_lista_zapisana_w_calosci_jako_limity_niczego_nie_zmienia(self):
        # Wlasciciel otwiera liste w Excelu, wypelnia i zapisuje jako warianty-limity.csv.
        # Po takim zapisie kazdy laptop musi miec dokladnie ten sam limit co przedtem,
        # a reguly dla calego modelu i modele wpisane zawczasu nie moga zniknac.
        przed = {a["SKU"]: w.limit_rozbudowy(a, "", "", CENNIK, self.LIMITY, FUNKCJE) for _, a in self.PRODUKTY}
        with tempfile.TemporaryDirectory() as katalog:
            plik = Path(katalog) / "warianty-limity.csv"
            with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
                zapis = csv.DictWriter(uchwyt, fieldnames=w.KOLUMNY_LISTY_MODELI, delimiter=";")
                zapis.writeheader()
                zapis.writerows(self.lista(self.LIMITY))
            wczytane, bledy = w.wczytaj_limity(plik)
        self.assertEqual(bledy, [])
        # Wiersze wracaja tak, jak wpisal je wlasciciel - lista ich nie przerabia.
        self.assertEqual(sorted((x["model"], x["procesor"], x["max_ram"], x["max_dysk"]) for x in wczytane),
                         [("Dell Latitude 9999", "", 0, 0), ("HP EliteBook 840 G6", "i5-8365U", None, 960),
                          ("ThinkPad E15 Gen 2", "", 16, None)])
        for _, attrs in self.PRODUKTY:
            po = w.limit_rozbudowy(attrs, "", "", CENNIK, wczytane, FUNKCJE)
            self.assertEqual((po["max_ram"], po["max_dysk"], po["jawny_ram"]),
                             (przed[attrs["SKU"]]["max_ram"], przed[attrs["SKU"]]["max_dysk"],
                              przed[attrs["SKU"]]["jawny_ram"]), attrs["SKU"])

    def test_regula_na_rodzine_procesorow(self):
        # Ustalenie wlasciciela: T14 Gen 2 na Ryzen 5 i na Intelu do 24 GB (8 GB wlutowane
        # + jedna kosc 16 GB), na Ryzen 7 normalnie. 'Ryzen 5' i 'Intel' to fragmenty nazwy
        # procesora, wiec obejmuja takze procesor, ktory pojawi sie w feedzie pozniej.
        t14 = dict(Model="ThinkPad T14 Gen 2", ram="16GB")
        produkty = [laptop("R5", Procesor="Ryzen 5 PRO 5650U", **t14),
                    laptop("R7", Procesor="Ryzen 7 PRO 5850U", **t14),
                    laptop("I5", Procesor="i5 - 1135G7, 8MB Cache, 11 gen.", **t14)]
        limity = [{"model": "Lenovo ThinkPad T14 Gen 2", "procesor": "Ryzen 5", "max_ram": 24,
                   "max_dysk": None, "uwagi": "", "wiersz": 2},
                  {"model": "Lenovo ThinkPad T14 Gen 2", "procesor": "Intel", "max_ram": 24,
                   "max_dysk": None, "uwagi": "", "wiersz": 3}]

        def ram(lista_limitow, zestaw):
            return {r["baza"]["sku"]: sorted({v["ram"] for v in r["warianty"]})
                    for r in rodziny(zestaw, limity=lista_limitow)["rodziny"]}

        self.assertEqual(ram(limity, produkty), {"R5": [16, 24], "R7": [16, 32], "I5": [16, 24]})

        # Lista modeli zapisana w calosci jako plik ograniczen: reguly zostaja szerokie.
        wynik = rodziny(produkty, limity=limity)
        with tempfile.TemporaryDirectory() as katalog:
            plik = Path(katalog) / "warianty-limity.csv"
            with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
                zapis = csv.DictWriter(uchwyt, fieldnames=w.KOLUMNY_LISTY_MODELI, delimiter=";")
                zapis.writeheader()
                zapis.writerows(w.lista_modeli(produkty, wynik["rodziny"], CENNIK, limity, FUNKCJE))
            wczytane, bledy = w.wczytaj_limity(plik)
        self.assertEqual(bledy, [])
        self.assertEqual(sorted((x["procesor"], x["max_ram"]) for x in wczytane), [("Intel", 24), ("Ryzen 5", 24)])
        nowy = produkty + [laptop("R5-nowy", Procesor="Ryzen 5 PRO 5675U", **t14)]
        self.assertEqual(ram(wczytane, nowy)["R5-nowy"], [16, 24])


class StaraSciezkaOmijaOfertyWariantoweTests(unittest.TestCase):
    """Raport aktywnych ofert w ukladzie z 15.11.2026: oferta wariantowa to wiersz oferty
    i po jednym wierszu na wariant, wszystkie z tym samym numerem i z 'Variation details'."""
    NAGLOWEK = ["Item number", "Title", "Variation details", "Custom label (SKU)", "Available quantity",
                "Currency", "Start price", "End date", "Listing site"]
    WIERSZE = [
        ["305000000001", "Zwykla aukcja", "", "3888_20250724125241", "5", "EUR", "299.0", "", "DE"],
        ["307282655424", "E15 RAM/SSD waehlbar",
         "Arbeitsspeichergröße=8 GB;16 GB|SSD-Festplattenkapazität=256 GB;480GB", "10207:warianty", "3", "EUR", "318.0", "", "DE"],
        ["307282655424", "E15 RAM/SSD waehlbar",
         "Arbeitsspeichergröße=8 GB|SSD-Festplattenkapazität=256 GB", "4553_20260216084009", "1", "EUR", "318.0", "", "DE"],
        ["307282655424", "E15 RAM/SSD waehlbar",
         "Arbeitsspeichergröße=8 GB|SSD-Festplattenkapazität=480GB", "10207-8GB-480GB", "1", "EUR", "369.0", "", "DE"],
        ["307282655424", "E15 RAM/SSD waehlbar",
         "Arbeitsspeichergröße=16 GB|SSD-Festplattenkapazität=256 GB", "10207-16GB-256GB", "1", "EUR", "369.0", "", "DE"],
        ["306000000002", "Aukcja wloska", "", "3888_20250724125241ITT", "2", "EUR", "305.0", "", "IT"],
    ]

    def raport(self, katalog):
        plik = Path(katalog) / "raport.csv"
        with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
            zapis = csv.writer(uchwyt)
            zapis.writerow(self.NAGLOWEK)
            zapis.writerows(self.WIERSZE)
        return plik

    def test_wiersze_oferty_wariantowej_nie_trafiaja_do_aktywnych(self):
        with tempfile.TemporaryDirectory() as katalog:
            wariantowe = {}
            aktywne, zdublowane = g.wczytaj_raport(self.raport(katalog), "DE", wariantowe)
        # Zostaje tylko zwykla aukcja. Gdyby warianty weszly do mapy, tryb 'aktualizacja'
        # wyzerowalby te spoza feedu, a wariantowi o SKU z feedu zmienil cene na poziomie oferty.
        self.assertEqual(set(aktywne), {"3888_20250724125241"})
        self.assertEqual(zdublowane, [])
        self.assertEqual(wariantowe, {"307282655424": ["10207:warianty", "4553_20260216084009",
                                                       "10207-8GB-480GB", "10207-16GB-256GB"]})

    def przebieg(self, tryb):
        """Caly generator na feedzie testowym. W raporcie: zwykla aukcja SKU 3809 oraz oferta
        wariantowa, ktorej jeden wariant niesie SKU 4220 - towar, ktory jest tez w feedzie."""
        katalog = Path(tempfile.mkdtemp())
        wzor = ROOT / "tests" / "fixtures" / "aktywne.csv"
        with wzor.open(encoding="utf-8-sig", newline="") as uchwyt:
            naglowek = next(csv.reader(uchwyt))
        def wiersz(numer, szczegoly, sku, ilosc, cena):
            pola = dict.fromkeys(naglowek, "")
            pola.update({"Item number": numer, "Title": "Oferta", "Variation details": szczegoly,
                         "Custom label (SKU)": sku, "Available quantity": ilosc, "Currency": "EUR",
                         "Start price": cena, "Current price": cena, "Listing site": "DE"})
            return pola
        raport = katalog / "aktywne.csv"
        with raport.open("w", encoding="utf-8-sig", newline="") as uchwyt:
            zapis = csv.DictWriter(uchwyt, fieldnames=naglowek)
            zapis.writeheader()
            zapis.writerows([
                wiersz("305000000001", "", "3809", "2", "100.0"),
                wiersz("307282655424", "RAM=8 GB;16 GB|SSD=256 GB", "10207:warianty", "2", "318.0"),
                wiersz("307282655424", "RAM=8 GB|SSD=256 GB", "4220", "1", "318.0"),
                wiersz("307282655424", "RAM=16 GB|SSD=256 GB", "10207-16GB-256GB", "1", "369.0"),
            ])
        wynik = katalog / "out"
        kod = subprocess.run(
            [sys.executable, str(ROOT / "src" / "generate.py"), "--tryb", tryb,
             "--feed-file", str(ROOT / "tests" / "fixtures" / "sample.xml"), "--raport", str(raport),
             "--nbp-rate", "4.26", "--min-produktow", "1", "--output-dir", str(wynik)],
            check=False, capture_output=True)
        self.assertIn(kod.returncode, (0, 2), kod.stderr.decode("utf-8", "replace")[-1500:])
        return wynik, json.loads((wynik / "generation-report.json").read_text(encoding="utf-8"))

    def test_aktualizacja_nie_wysyla_niczego_do_oferty_wariantowej(self):
        wynik, raport = self.przebieg("aktualizacja")
        self.assertEqual(raport["blokady"], [])
        self.assertEqual(raport["aktywnych_na_ebay"], 1)
        self.assertEqual(raport["oferty_wariantowe_pominiete"],
                         {"ofert": 1, "wierszy_raportu": 3, "numery": ["307282655424"]})
        for nazwa in ("ebay-revise.csv", "ebay-revise-stany.csv"):
            tresc = (wynik / nazwa).read_text(encoding="utf-8-sig")
            self.assertNotIn("307282655424", tresc, nazwa)
        # Zwykla aukcja dalej jest prowadzona - poprawka nie wylaczyla aktualizacji.
        self.assertIn("305000000001", (wynik / "ebay-revise.csv").read_text(encoding="utf-8-sig"))

    def test_nowe_wystawia_zwykla_oferte_obok_wariantowej(self):
        # Decyzja wlasciciela 16.11.2026: konfiguracja podstawowa stoi i w ofercie wariantowej,
        # i jako zwykla aukcja. SKU 4220 jest wariantem oferty wariantowej, a mimo to idzie
        # do ebay-add.csv - oferta wariantowa nie blokuje zwyklej.
        wynik, raport = self.przebieg("nowe")
        with (wynik / "ebay-add.csv").open(encoding="utf-8-sig", newline="") as uchwyt:
            wiersze = list(csv.reader(uchwyt, delimiter=";"))
        sku = [w_[wiersze[1].index("CustomLabel")] for w_ in wiersze[2:]]
        self.assertIn("4220", sku)
        self.assertNotIn("3809", sku)                       # zwykla aktywna aukcja - juz wystawiona
        self.assertEqual(raport["pominieto"].get("juz_wystawione"), 1)
        self.assertEqual(raport["oferty_wariantowe_pominiete"]["ofert"], 1)

    def test_trzeci_argument_jest_opcjonalny(self):
        # Reszta generatora i jego testy wolaja wczytaj_raport(plik, rynek).
        with tempfile.TemporaryDirectory() as katalog:
            aktywne, _ = g.wczytaj_raport(self.raport(katalog), "DE")
        self.assertEqual(set(aktywne), {"3888_20250724125241"})

    def test_raport_bez_kolumny_wariantow_czyta_sie_jak_dotad(self):
        with tempfile.TemporaryDirectory() as katalog:
            plik = Path(katalog) / "stary.csv"
            bez = [n for n in self.NAGLOWEK if n != "Variation details"]
            with plik.open("w", encoding="utf-8-sig", newline="") as uchwyt:
                zapis = csv.writer(uchwyt)
                zapis.writerow(bez)
                zapis.writerow(["305000000001", "Zwykla aukcja", "3888_20250724125241", "5", "EUR", "299.0", "", "DE"])
            aktywne, _ = g.wczytaj_raport(plik, "DE", {})
        self.assertEqual(aktywne["3888_20250724125241"]["ilosc"], 5)


if __name__ == "__main__":
    unittest.main()
