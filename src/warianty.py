#!/usr/bin/env python3
"""Warianty RAM/dysk: jedna oferta na rodzine, konfiguracje liczone ze wzoru.

PO CO
Sklep trzyma laptop w jednej, podstawowej konfiguracji (np. 8 GB / 256 GB),
a sprzedaje go tez po rozbudowie. Zamiast brac te konfiguracje z ofert
Allegro, liczymy je sami: cena bazy + doplata za RAM + doplata za dysk.

RODZINA (decyzja wlasciciela, 15.11.2026)
Model + klasa + procesor + rozdzielczosc + dotyk + grafika. Wpisy o tych
samych szesciu cechach to jedna oferta. Inna klasa, matryca, dotyk albo karta
graficzna to osobna rodzina, czyli osobna oferta. Innych pol NIE porownujemy -
wlasciciel nie chce, zeby opis baterii czy obudowy mnozyl oferty.

WYSTAWIONY WPIS (BAZA) I JEGO ID
Z rodziny wystawiamy jeden wpis: o najmniejszej konfiguracji, a przy tej samej
konfiguracji najtanszy. Od niego liczymy wszystkie warianty. Oferta jest
przypieta do ID Shopera tego wpisu (atrybut 'id' w XML) - ID, nie SKU, bo SKU
to pole, ktore ktos moze zmienic przez przypadek. ID siedzi w SKU wariantow
na eBayu ('10207-16GB-960GB'), wiec wraca w raporcie aktywnych ofert.
Dopoki przypiety wpis ma stan, zostaje baza - nawet gdy w feedzie pojawi sie
tanszy albo mniejszy wpis o tych samych cechach. Gdy sie wyprzeda, baza
zostaje nastepny wpis rodziny: oferta zostaje, zmienia sie ID.

POCZEKALNIA
Kazdy inny wpis rodziny trafia do poczekalni: nie jest wystawiany, niczego nie
blokuje, a raport pokazuje, czym rozni sie od wystawionego (pole po polu).
Wlasciciel przeglada ja recznie, na spokojnie.

CZEGO TU NIE MA
Modul niczego nie wystawia i nie zna eBaya. Zwraca rodziny z wariantami,
a co z nimi zrobic (jedna oferta wariantowa, tytul, plik CSV) - to osobny krok.

Reguly siatki i cennik sa przeniesione 1:1 z repo shoper-allegro-sync
(variants.py, limits.py), zeby eBay i Allegro liczyly to samo. Test
'test_ceny_jak_na_allegro' przypina wynik do cen zywych ofert.
"""

from __future__ import annotations

import csv
import hashlib
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import duplikaty

POLE_RAM = "Ilość pamięci RAM"
POLE_DYSKU = "Dysk"
POLE_TYPU_DYSKU = "Typ dysku"
POLE_TYPU_RAM = "Typ pamięci RAM"
POLE_KLASY = "Kondycja sprzętu"
POLE_ROZDZIELCZOSCI = "Rozdzielczość ekranu"
POLE_DOTYKU = "Ekran dotykowy"
POLE_SYSTEMU = "Zainstalowany system"
POLE_GRAFIKI = "Model karty graficznej"

# Pola, ktore z definicji roznia wpisy tej samej rodziny - nie raportujemy ich
# jako "roznic", bo wlasnie po nich wpis jest pomijany albo wybierany na baze.
POLA_KONFIGURACJI = {"SKU", POLE_RAM, POLE_DYSKU}

POWOD_DUBEL = "dubel - ta sama konfiguracja co wystawiona"
POWOD_WARIANT = "ta konfiguracja jest w ofercie jako wariant"
POWOD_MNIEJSZA = "mniejsza konfiguracja niz wystawiona - wejdzie po jej wyprzedaniu"
POWOD_SPOZA = "takiej konfiguracji nie ma w ofercie"

KOLUMNY_LIMITOW = ["model", "procesor", "max_ram", "max_dysk", "uwagi"]

_POJEMNOSC = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(GB|TB)\s*$", re.I)
_WIDELKI = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*(GB|TB)\s*$", re.I)
_PRZEJSCIE = re.compile(r"^(\d+)>(\d+)$")
_GENERACJA = re.compile(r"\bgen\.?\s*(\d+)")
_WOKOL_MYSLNIKA = re.compile(r"\s*-\s*")
PRZEDROSTEK_SKU = "SHOPER-ID:"
KONCOWKA_OFERTY = ":warianty"
_SKU_WARIANTOWE = re.compile(
    r"^SHOPER-ID:(?P<id>\d+)(?:(?P<oferta>:warianty)|-(?P<ram>\d+)GB-(?P<dysk>\d+)(?P<jedn>GB|TB))?"
    r"(?P<dopiski>(?:-[A-Za-z0-9]+)*)$")
_GRAFIKA_NAWIAS = re.compile(r"\([^)]*\)")
_GRAFIKA_DOPISEK = re.compile(r"\bfor\b.*$")
_GRAFIKA_PAMIEC = re.compile(r"\b\d+\s*gb\b")
_GRAFIKA_SZUM = re.compile(r"\b(graphics|grafika)\b")


# --------------------------------------------------------------------------- #
# pojemnosci
# --------------------------------------------------------------------------- #

def pojemnosc_gb(value: str) -> int | None:
    """'8GB', '8 GB', '1TB', '120/128 GB' -> liczba GB. None = nie umiemy odczytac.

    1 TB liczymy jako 1000 GB, tak jak cennik (grupa '1000' zawiera 960, 1000
    i 1024). Widelki '120/128 GB' to zapis sprzedawcy - bierzemy wieksza
    wartosc, tak samo jak clean_capacity() w generatorze.
    """
    tekst = str(value or "")
    widelki = _WIDELKI.match(tekst)
    if widelki:
        liczba = Decimal(max(int(widelki.group(1)), int(widelki.group(2))))
        jednostka = widelki.group(3)
    else:
        trafienie = _POJEMNOSC.match(tekst)
        if not trafienie:
            return None
        liczba = Decimal(trafienie.group(1).replace(",", "."))
        jednostka = trafienie.group(2)
    if jednostka.upper() == "TB":
        liczba *= 1000
    if liczba <= 0 or liczba != liczba.to_integral_value():
        return None
    return int(liczba)


def etykieta_feedu(gb: int) -> str:
    """Pojemnosc w zapisie feedu: '960GB', '2TB'. Dalej czyta ja generator."""
    return f"{gb // 1000}TB" if gb >= 1000 and gb % 1000 == 0 else f"{gb}GB"


# --------------------------------------------------------------------------- #
# cennik
# --------------------------------------------------------------------------- #

def grupa_dysku(gb: int, cennik: dict) -> int:
    """Grupa cenowa dysku: 480, 500 i 512 kosztuja tyle samo. Brak grupy = sam rozmiar."""
    for nazwa, czlonkowie in (cennik.get("dysk_grupy") or {}).items():
        if gb in czlonkowie:
            return int(nazwa)
    return gb


def sprawdz_cennik(cennik: dict) -> list[str]:
    """Bledy w cenniku slowami. Pusta lista = mozna liczyc.

    Sprawdzamy to, na czym naprawde mozna sie pomylic przy recznej edycji:
    zapis przejscia, ujemna albo nieliczbowa doplate, rozmiar w dwoch grupach.
    """
    bledy: list[str] = []
    for sekcja in ("ram_doplaty", "dysk_doplaty"):
        for klucz, wartosc in (cennik.get(sekcja) or {}).items():
            przejscie = _PRZEJSCIE.match(str(klucz))
            if not przejscie or int(przejscie.group(1)) >= int(przejscie.group(2)):
                bledy.append(f"{sekcja}: '{klucz}' - oczekiwany zapis 'mniej>wiecej', np. '8>16'")
                continue
            try:
                doplata = Decimal(str(wartosc).replace(",", "."))
            except InvalidOperation:
                bledy.append(f"{sekcja}: '{klucz}' - doplata '{wartosc}' nie jest liczba")
                continue
            if not doplata.is_finite() or doplata < 0:
                bledy.append(f"{sekcja}: '{klucz}' - doplata nie moze byc ujemna")
    widziane: dict[int, str] = {}
    for nazwa, czlonkowie in (cennik.get("dysk_grupy") or {}).items():
        if not str(nazwa).isdigit():
            bledy.append(f"dysk_grupy: nazwa grupy '{nazwa}' musi byc liczba GB")
        for rozmiar in czlonkowie:
            if rozmiar in widziane:
                bledy.append(f"dysk_grupy: {rozmiar} GB jest w dwoch grupach "
                             f"('{widziane[rozmiar]}' i '{nazwa}')")
            widziane[rozmiar] = str(nazwa)
    for cel in cennik.get("dysk_cele_gb") or []:
        if not isinstance(cel, int) or cel <= 0:
            bledy.append(f"dysk_cele_gb: '{cel}' musi byc dodatnia liczba GB")
    return bledy


def _doplata(tabela: dict, przed: int, po: int) -> Decimal | None:
    """Doplata za przejscie. 0 gdy nic sie nie zmienia, None gdy cennik jej nie zna."""
    if przed == po:
        return Decimal("0")
    surowa = (tabela or {}).get(f"{przed}>{po}")
    if surowa is None or str(surowa).strip() == "":
        return None
    return Decimal(str(surowa).replace(",", "."))


# --------------------------------------------------------------------------- #
# klucze: model, procesor, rodzina
# --------------------------------------------------------------------------- #

def _zapis_modelu(tekst: str, funkcje: dict) -> str:
    """Ten sam zapis co w odcisku duplikatow: 'Gen1' = 'Gen. 1', bez spacji przy '-'."""
    model = funkcje["norm"](tekst)
    model = _GENERACJA.sub(r"gen \1", model)
    model = _WOKOL_MYSLNIKA.sub("-", model)
    return model.strip().rstrip(".").strip()


def klucz_modelu(attrs: dict, funkcje: dict) -> str:
    """'lenovo thinkpad e15 gen 2' - marka i model w zapisie niezaleznym od feedu."""
    marka = duplikaty._marka(attrs, funkcje)          # noqa: SLF001 - jedna normalizacja na caly projekt
    model = duplikaty._model(attrs, funkcje)          # noqa: SLF001
    return f"{marka} {model}".strip() if marka and model else ""


def klucz_procesora(attrs: dict, funkcje: dict) -> str:
    kandydaci = funkcje["cpu_candidates"](funkcje["zrodlo_procesora"](attrs))
    return funkcje["norm"](kandydaci[0]) if kandydaci else ""


def klucz_grafiki(attrs: dict, funkcje: dict) -> str:
    """'Intel HD Graphics 530 + NVIDIA GeForce 940MX 2GB' -> 'intel hd 530 + nvidia geforce 940mx'.

    Feed zapisuje te sama karte na kilka sposobow ('Intel HD 530' i 'Intel HD
    Graphics 530', z iloscia pamieci albo bez, z dopiskiem 'for 10th Gen Intel
    Processors'). Te dopiski zdejmujemy, zeby literowka nie rozbijala rodziny.
    Karta dedykowana ZOSTAJE w kluczu - generatorowe gpu_clean() ja gubi, wiec
    RTX 3000 i RTX 5000 wyszlyby jako ta sama grafika.
    """
    czesci = []
    for czesc in funkcje["norm"](attrs.get(POLE_GRAFIKI, "")).split("+"):
        czesc = _GRAFIKA_NAWIAS.sub("", czesc)
        czesc = _GRAFIKA_DOPISEK.sub("", czesc)
        czesc = _GRAFIKA_PAMIEC.sub("", czesc)
        czesc = _GRAFIKA_SZUM.sub("", czesc)
        czesc = " ".join(czesc.split())
        if czesc:
            czesci.append(czesc)
    return " + ".join(czesci)


def klucz_rodziny(attrs: dict, funkcje: dict) -> tuple[str, ...] | None:
    """(model, procesor, klasa, rozdzielczosc, dotyk, grafika) albo None, gdy brakuje danych.

    Bez modelu, procesora albo klasy nie skladamy rodziny: zlepilibysmy ze soba
    rozne laptopy, a klasa decyduje o stanie towaru w ofercie. Taki wpis idzie
    dalej po staremu, jako zwykla oferta bez wariantow.
    """
    norm = funkcje["norm"]
    model = klucz_modelu(attrs, funkcje)
    procesor = klucz_procesora(attrs, funkcje)
    klasa = funkcje["condition_class"](attrs.get(POLE_KLASY, ""))
    if not (model and procesor and klasa):
        return None
    rozdzielczosc = norm(attrs.get(POLE_ROZDZIELCZOSCI, "")).replace(" ", "")
    dotyk = "dotyk" if norm(attrs.get(POLE_DOTYKU, "")) == "tak" else "bez dotyku"
    return (model, procesor, klasa, rozdzielczosc, dotyk, klucz_grafiki(attrs, funkcje))


def id_rodziny(klucz: tuple[str, ...]) -> str:
    """Krotki identyfikator rodziny do raportow i podgladu.

    Liczony z klucza, a nie z bazy: gdy baza sie wyprzeda i zastapi ja inny
    wpis, identyfikator zostaje ten sam.
    """
    return "W" + hashlib.sha1("|".join(klucz).encode("utf-8")).hexdigest()[:10]


def _z_dopiskami(sku: str, dopiski) -> str:
    """Dopiski (GW24, znacznik rynku: ITT, SPAIN, FRA) doklejamy po myslniku, zeby
    'SHOPER-ID:10207-8GB-480GB-ITT' dalo sie przeczytac - sklejone bez odstepu
    dawaly '480GBITT'."""
    return "-".join([sku, *[d for d in dopiski if d]])


def sku_bazy(id_shopera: str, dopiski=()) -> str:
    """SKU zwyklej oferty konfiguracji podstawowej: 'SHOPER-ID:10207'. Ta sama sygnatura
    co w ofercie wariantowej - obie stoja na tym samym wpisie Shopera."""
    return _z_dopiskami(f"{PRZEDROSTEK_SKU}{id_shopera}", dopiski)


def sku_oferty(id_shopera: str, dopiski=()) -> str:
    """SKU calej oferty wariantowej: 'SHOPER-ID:10207:warianty'."""
    return _z_dopiskami(f"{PRZEDROSTEK_SKU}{id_shopera}{KONCOWKA_OFERTY}", dopiski)


def sku_wariantu(id_shopera: str, ram: int, dysk: int, dopiski=()) -> str:
    """SKU wariantu na eBayu: sygnatura + ID Shopera wystawionego wpisu + konfiguracja.

    'SHOPER-ID:10207-16GB-960GB' = wez produkt o ID 10207, wloz 16 GB i dysk 960 GB.
    Sygnature 'SHOPER-ID:' wybral wlasciciel: w zamowieniu od razu widac, ze liczba
    to ID ze Shopera, a nie SKU. Koncowke z konfiguracja ma KAZDY wariant: magazyn
    widzi ja zawsze w tym samym miejscu, a zaden wariant nie ma SKU zwyklej oferty,
    wiec stara sciezka generatora go nie pomyli. Calosc z GW24 i znacznikiem kraju
    ma najwyzej ok. 37 znakow - eBay daje na SKU 50 (SKU Shopera bywa 39-znakowe).
    """
    return _z_dopiskami(f"{PRZEDROSTEK_SKU}{id_shopera}-{ram}GB-{etykieta_feedu(dysk)}", dopiski)


def rozloz_sku(sku: str) -> dict | None:
    """Odczyt SKU z raportu eBaya: {'id', 'oferta', 'ram', 'dysk', 'dopiski'} albo None,
    gdy to nie jest SKU nowej sciezki. Stad generator wie, na ktorym ID Shopera stoi
    oferta ('przypiete'), bez osobnego pliku z powiazaniami."""
    trafienie = _SKU_WARIANTOWE.match((sku or "").strip())
    if not trafienie:
        return None
    oferta = bool(trafienie["oferta"])
    zwykla = not oferta and trafienie["ram"] is None        # 'SHOPER-ID:10207' - zwykla oferta bazy
    return {"id": trafienie["id"], "oferta": oferta, "zwykla": zwykla,
            "ram": None if oferta or zwykla else int(trafienie["ram"]),
            "dysk": (None if oferta or zwykla
                     else int(trafienie["dysk"]) * (1000 if trafienie["jedn"] == "TB" else 1)),
            "dopiski": [d for d in trafienie["dopiski"].split("-") if d]}


def czy_chromebook(attrs: dict, nazwa: str, kategoria: str, funkcje: dict) -> bool:
    norm = funkcje["norm"]
    pola = (nazwa, kategoria, attrs.get("Model", ""), attrs.get(POLE_SYSTEMU, ""))
    return any(slowo in norm(pole) for pole in pola
               for slowo in ("chromebook", "chrome os", "chromeos"))


# --------------------------------------------------------------------------- #
# lista ograniczen rozbudowy
# --------------------------------------------------------------------------- #

def wczytaj_limity(sciezka: Path) -> tuple[list[dict], list[str]]:
    """Lista modeli z ograniczona rozbudowa: (wiersze, bledy). Brak pliku = pusta lista.

    Plik wpisuje sie recznie, wiec czytamy go odpornie, tak jak sku-reczne.csv:
    separator z linii naglowka, wiersze puste i zaczynajace sie od '#' pomijane.
    Kolumny: model;procesor;max_ram;max_dysk;uwagi
      model     - 'Lenovo ThinkPad E15 Gen 2' (marka moze byc albo nie)
      procesor  - puste = caly model; 'i5-1135G7' = tylko ten procesor
      max_ram   - puste = regula domyslna, liczba = najwyzej tyle GB, 0 = RAM-u nie ruszamy
      max_dysk  - puste = regula domyslna, liczba = najwyzej tyle GB, 0 = dysku nie ruszamy
    '0;0' wylacza rozbudowe modelu calkowicie.
    """
    if not sciezka.is_file():
        return [], []
    tekst = sciezka.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    linie = [l for l in tekst.split("\n") if l.strip() and not l.lstrip().startswith("#")]
    if not linie:
        return [], []
    sep = max(";,\t", key=linie[0].count)
    if linie[0].count(sep) == 0:
        sep = ";"
    naglowek = [k.strip().lower() for k in next(csv.reader([linie[0]], delimiter=sep))]
    brak = [k for k in ("model", "max_ram", "max_dysk") if k not in naglowek]
    if brak:
        return [], [f"{sciezka.name}: brak kolumn {', '.join(brak)} w naglowku"]
    wiersze, bledy = [], []
    for numer, linia in enumerate(linie[1:], start=2):
        pola = [p.strip() for p in next(csv.reader([linia], delimiter=sep))]
        wpis = {k: (pola[i] if i < len(pola) else "") for i, k in enumerate(naglowek)}
        if not wpis.get("model"):
            bledy.append(f"{sciezka.name}, wiersz {numer}: pusta kolumna 'model'")
            continue
        limit = {}
        for kolumna in ("max_ram", "max_dysk"):
            surowa = wpis.get(kolumna, "")
            if surowa == "":
                limit[kolumna] = None
            elif surowa.isdigit():
                limit[kolumna] = int(surowa)
            else:
                bledy.append(f"{sciezka.name}, wiersz {numer}: {kolumna} = '{surowa}' "
                             f"- wpisz liczbe GB, 0 albo zostaw puste")
                break
        else:
            # Wiersz z dwiema pustymi kolumnami niczego nie ustala. Pomijamy go bez
            # bledu: lista modeli z podgladu (warianty-modele.csv) ma takie wiersze
            # dla kazdego modelu i mozna ja zapisac jako ten plik w calosci,
            # wypelniajac tylko te, ktore maja miec ograniczenie.
            if limit["max_ram"] is None and limit["max_dysk"] is None:
                continue
            wiersze.append({"model": wpis["model"], "procesor": wpis.get("procesor", ""),
                            "max_ram": limit["max_ram"], "max_dysk": limit["max_dysk"],
                            "uwagi": wpis.get("uwagi", ""), "wiersz": numer})
    return wiersze, bledy


def _pasuje_model(wpisany: str, attrs: dict, funkcje: dict) -> bool:
    """Wpis z listy pasuje z marka albo bez: 'Lenovo ThinkPad E15 Gen 2' i 'ThinkPad E15 Gen 2'."""
    wpis = _zapis_modelu(wpisany, funkcje)
    pelny = klucz_modelu(attrs, funkcje)
    sam_model = duplikaty._model(attrs, funkcje)     # noqa: SLF001
    return bool(wpis) and wpis in (pelny, sam_model)


def _waga_procesora(wpisany: str, attrs: dict, funkcje: dict) -> int:
    """Jak dobrze wpis z listy pasuje do procesora: 3 = dokladnie, 2 = zawiera sie, 0 = wcale.

    'i5-1135G7' pasuje do 'Intel Core i5-1135G7' po zawieraniu, bo nikt nie wpisze
    pelnej nazwy, a w obrebie JEDNEGO modelu to wystarcza. Dokladne trafienie
    wazy wiecej: gdy lista ma wiersz 'Intel Core i5' i wiersz 'Intel Core
    i5-8365U', ten drugi ma wygrac, a nie skonczyc sie bledem "dwa wiersze".
    """
    wpis = funkcje["norm"](wpisany)
    procesor = klucz_procesora(attrs, funkcje)
    if not wpis or not procesor:
        return 0
    return 3 if wpis == procesor else (2 if wpis in procesor else 0)


def limit_rozbudowy(attrs: dict, nazwa: str, kategoria: str, cennik: dict,
                    limity: list[dict], funkcje: dict) -> dict:
    """Ile wolno rozbudowac te sztuke: {'max_ram', 'max_dysk', 'zrodlo', 'jawny_ram'}.

    Pierwszenstwo: wiersz 'model + procesor', potem wiersz samego modelu, potem
    reguly domyslne. 'max_dysk' None znaczy "bez ograniczenia" (do najwiekszego
    dysku z cennika). Dwa pasujace wiersze o tym samym pierwszenstwie to blad
    listy - wtedy NIE rozbudowujemy wcale, bo nie wiadomo, ktory mial wygrac,
    a wariant wystawiony wbrew ograniczeniu to obietnica nie do spelnienia.
    """
    norm = funkcje["norm"]
    typ_ram = (attrs.get(POLE_TYPU_RAM, "") or "").strip().upper()
    wg_typu = {str(k).upper(): v for k, v in (cennik.get("max_ram_wg_typu") or {}).items()}
    domyslny_ram = int(wg_typu.get(typ_ram, cennik.get("max_ram_domyslnie", 16)))
    limit = {"max_ram": domyslny_ram, "max_dysk": None, "zrodlo": "domyslny", "jawny_ram": False,
             "wiersz_listy": None}

    marki_bez = {norm(m) for m in cennik.get("marki_bez_rozbudowy") or []}
    if norm(funkcje["brand_name"](attrs.get("Producent", ""))) in marki_bez:
        limit.update(max_ram=0, max_dysk=0, zrodlo="marka bez rozbudowy")
    elif czy_chromebook(attrs, nazwa, kategoria, funkcje):
        limit.update(max_ram=0, max_dysk=0, zrodlo="Chromebook")

    pasujace = []
    for wiersz in limity:
        if not _pasuje_model(wiersz["model"], attrs, funkcje):
            continue
        waga = _waga_procesora(wiersz["procesor"], attrs, funkcje) if wiersz["procesor"] else 1
        if waga:
            pasujace.append((waga, wiersz))
    if not pasujace:
        return limit
    najwyzsze = max(waga for waga, _ in pasujace)
    wygrane = [w for waga, w in pasujace if waga == najwyzsze]
    if len(wygrane) > 1:
        numery = ", ".join(str(w["wiersz"]) for w in wygrane)
        return {"max_ram": 0, "max_dysk": 0, "jawny_ram": False, "wiersz_listy": None,
                "zrodlo": f"BLAD LISTY: wiersze {numery} dotycza tego samego modelu"}
    wiersz = wygrane[0]
    if wiersz["max_ram"] is not None:
        limit.update(max_ram=wiersz["max_ram"], jawny_ram=True)
    if wiersz["max_dysk"] is not None:
        limit["max_dysk"] = wiersz["max_dysk"]
    limit.update(zrodlo=f"lista, wiersz {wiersz['wiersz']}", wiersz_listy=wiersz)
    return limit


# --------------------------------------------------------------------------- #
# siatka konfiguracji
# --------------------------------------------------------------------------- #

def siatka(ram: int, dysk: int, typ_ram: str, cennik: dict, limit: dict) -> tuple[list[dict], list[str]]:
    """Konfiguracje do wystawienia z bazy (ram, dysk): (lista, braki w cenniku).

    Pierwsza na liscie jest zawsze sama baza, z doplata 0. Siatka tylko
    ROZBUDOWUJE - mniejszego RAM-u ani dysku z bazy nie zrobimy.

    RAM: przejscia z cennika zaczynajace sie od RAM-u bazy, do limitu. Dla DDR4
    tylko rozmiary standardowe (8/16/32), chyba ze limit z listy wskazuje wprost
    inny rozmiar (np. 24) - wtedy ten jeden dochodzi.
    Dysk: cele z cennika wieksze od bazy i z WYZSZEJ grupy cenowej (z 480 nie
    robimy 512, bo to ta sama polka).
    """
    typ_ram = (typ_ram or "").strip().upper()
    standard = cennik.get("ram_standard_ddr4")
    ram_cele = {ram}
    for klucz in cennik.get("ram_doplaty") or {}:
        przejscie = _PRZEJSCIE.match(str(klucz))
        if not przejscie:
            continue
        przed, po = int(przejscie.group(1)), int(przejscie.group(2))
        if przed != ram or not ram < po <= limit["max_ram"]:
            continue
        wskazany = limit.get("jawny_ram") and po == limit["max_ram"]
        if typ_ram != "DDR4" or not standard or po in standard or wskazany:
            ram_cele.add(po)

    dysk_cele = {dysk}
    for cel in cennik.get("dysk_cele_gb") or []:
        if (cel > dysk and grupa_dysku(cel, cennik) > grupa_dysku(dysk, cennik)
                and (limit["max_dysk"] is None or cel <= limit["max_dysk"])):
            dysk_cele.add(cel)

    max_dysk_przy_ram = cennik.get("max_dysk_przy_ram") or {}
    konfiguracje, braki = [], []
    for cel_ram in sorted(ram_cele):
        for cel_dysk in sorted(dysk_cele):
            prog = max_dysk_przy_ram.get(str(cel_ram))
            if prog is not None and cel_dysk > int(prog) and cel_dysk != dysk:
                continue
            doplata_ram = _doplata(cennik.get("ram_doplaty"), ram, cel_ram)
            doplata_dysk = _doplata(cennik.get("dysk_doplaty"),
                                    grupa_dysku(dysk, cennik), grupa_dysku(cel_dysk, cennik)) \
                if cel_dysk != dysk else Decimal("0")
            if doplata_ram is None:
                braki.append(f"ram_doplaty: brak '{ram}>{cel_ram}'")
                continue
            if doplata_dysk is None:
                braki.append(f"dysk_doplaty: brak "
                             f"'{grupa_dysku(dysk, cennik)}>{grupa_dysku(cel_dysk, cennik)}'")
                continue
            konfiguracje.append({"ram": cel_ram, "dysk": cel_dysk,
                                 "doplata_ram": doplata_ram, "doplata_dysk": doplata_dysk,
                                 "baza": (cel_ram, cel_dysk) == (ram, dysk)})
    return konfiguracje, sorted(set(braki))


def ilosc_na_ebay(stan_bazy: int, cennik: dict) -> int:
    """Ile sztuk pokazuje na eBayu oferta nowej sciezki przy danym stanie wpisu bazowego.

    Jedna zasada dla zwyklej oferty bazy i dla kazdego wariantu (decyzja wlasciciela
    z 17.11.2026: "na nowym wszystko na 2 sztuki"): ponizej 'min_stan_bazy' zero,
    od progu w gore 'max_sztuk_na_wariant' (albo pelny stan, gdy to ustawienie jest 0).
    """
    if stan_bazy < int(cennik.get("min_stan_bazy", 0) or 0):
        return 0
    maks = int(cennik.get("max_sztuk_na_wariant", 0) or 0)
    return min(stan_bazy, maks) if maks else stan_bazy


def atrybuty_wariantu(attrs: dict, ram: int, dysk: int) -> dict:
    """Atrybuty konfiguracji dorobionej z bazy. Nowy dysk montujemy zawsze jako SSD -
    takze gdy baza miala HDD albo SSD+HDD (tak samo jak na Allegro)."""
    nowe = dict(attrs)
    stary_dysk = pojemnosc_gb(attrs.get(POLE_DYSKU, ""))
    nowe[POLE_RAM] = f"{ram}GB"
    nowe[POLE_DYSKU] = etykieta_feedu(dysk)
    if stary_dysk != dysk:
        nowe[POLE_TYPU_DYSKU] = "SSD"
    return nowe


# --------------------------------------------------------------------------- #
# rodziny
# --------------------------------------------------------------------------- #

def _cena(offer) -> Decimal | None:
    try:
        cena = Decimal(str(offer.get("price") or "").replace(",", "."))
    except InvalidOperation:
        return None
    return cena if cena.is_finite() and cena > 0 else None


def _wpis(offer, attrs: dict) -> dict:
    return {"offer": offer, "attrs": attrs, "sku": attrs.get("SKU", ""), "id": offer.get("id", ""),
            "ram": pojemnosc_gb(attrs.get(POLE_RAM, "")), "dysk": pojemnosc_gb(attrs.get(POLE_DYSKU, "")),
            "cena": _cena(offer), "stan": int(float(offer.get("stock", "0") or 0)),
            "nazwa": (offer.findtext("name") or "").strip(),
            "kategoria": (offer.findtext("cat") or "").strip()}


def _roznice(baza: dict, inny: dict, funkcje: dict) -> list[dict]:
    """Czym wpis z poczekalni rozni sie od wystawionego, poza RAM-em i dyskiem:
    [{'pole', 'wystawiony', 'ten'}]. Wlasciciel sprawdza duble recznie, wiec raport
    ma pokazac obie wartosci obok siebie, a nie sama nazwe pola."""
    norm = funkcje["norm"]
    pola = (set(baza["attrs"]) | set(inny["attrs"])) - POLA_KONFIGURACJI
    return [{"pole": p, "wystawiony": baza["attrs"].get(p, "") or "", "ten": inny["attrs"].get(p, "") or ""}
            for p in sorted(pola) if norm(baza["attrs"].get(p, "")) != norm(inny["attrs"].get(p, ""))]


def zbuduj_rodziny(produkty, cennik: dict, limity: list[dict], funkcje: dict,
                   przypiete=()) -> dict:
    """Rodziny z wariantami + poczekalnia. Niczego nie usuwa z feedu.

    'produkty' to pary (offer, attrs) laptopow ze stanem > 0, w tej samej postaci,
    w jakiej zwraca je zbierz_produkty() generatora. 'przypiete' to ID Shopera
    wpisow, na ktorych stoja juz oferty na eBayu (odczytane z raportu aktywnych
    ofert); puste przy pierwszym wystawieniu. Zwraca:
      'rodziny'      - jedna na klucz, z baza i lista wariantow (baza jest pierwsza),
      'poczekalnia'  - pozostale wpisy rodziny: nie wystawiamy ich, pokazujemy roznice,
      'bez_rodziny'  - wpisy, dla ktorych nie da sie zlozyc klucza albo odczytac
                       RAM/dysku/ceny; ida po staremu, jako oferty bez wariantow.
    """
    min_stan = int(cennik.get("min_stan_bazy", 0) or 0)
    przypiete = {str(x) for x in przypiete}
    grupy: dict[tuple, list[dict]] = {}
    bez_rodziny = []
    for offer, attrs in produkty:
        wpis = _wpis(offer, attrs)
        klucz = klucz_rodziny(attrs, funkcje)
        if klucz is None:
            powod = "brak modelu, procesora albo klasy"
        elif not wpis["id"]:
            powod = "brak ID Shopera"
        elif wpis["ram"] is None or wpis["dysk"] is None:
            powod = "nieczytelny RAM albo dysk"
        elif wpis["cena"] is None:
            powod = "niepoprawna cena"
        else:
            grupy.setdefault(klucz, []).append(wpis)
            continue
        bez_rodziny.append({"sku": wpis["sku"], "id": wpis["id"], "nazwa": wpis["nazwa"], "powod": powod})

    rodziny, poczekalnia = [], []
    for klucz in sorted(grupy):
        # Najmniejsza konfiguracja, a przy tej samej - najtanszy. To wybor przy
        # PIERWSZYM wystawieniu. Gdy oferta juz stoi na ktoryms wpisie (przypiete)
        # i ten wpis ma stan, zostaje on baza: nowy, tanszy dubel nie przejmuje
        # oferty sam, tylko czeka w poczekalni na reczne sprawdzenie.
        wpisy = sorted(grupy[klucz], key=lambda w: (w["ram"], w["dysk"], w["cena"], w["id"]))
        juz_wystawione = [x for x in wpisy if x["id"] in przypiete]
        baza = juz_wystawione[0] if juz_wystawione else wpisy[0]
        reszta = [x for x in wpisy if x is not baza]
        limit = limit_rozbudowy(baza["attrs"], baza["nazwa"], baza["kategoria"], cennik, limity, funkcje)
        konfiguracje, braki = siatka(baza["ram"], baza["dysk"], baza["attrs"].get(POLE_TYPU_RAM, ""),
                                     cennik, limit)
        # Wszystkie warianty i zwykla oferta bazy biora z TYCH SAMYCH sztuk, a eBay nie
        # ma wspolnej puli na oferte. Dlatego ponizej progu ('min_stan_bazy') warianty
        # dostaja 0 - tak samo jak zwykla aukcja przy malym stanie - a powyzej stala,
        # mala liczbe sztuk. Decyzja wlasciciela z 16.11.2026: od 10 sztuk, po 2.
        aktywna = baza["stan"] >= min_stan
        stan = ilosc_na_ebay(baza["stan"], cennik)
        warianty = []
        for k in konfiguracje:
            doplata = k["doplata_ram"] + k["doplata_dysk"]
            warianty.append({
                "ram": k["ram"], "dysk": k["dysk"], "baza": k["baza"],
                "sku": sku_wariantu(baza["id"], k["ram"], k["dysk"]),
                "doplata": doplata, "cena": baza["cena"] + doplata, "stan": stan,
            })
        wg_slotu = {(w["ram"], grupa_dysku(w["dysk"], cennik)): w for w in warianty}
        identyfikator = id_rodziny(klucz)
        rodziny.append({
            "id": identyfikator, "klucz": klucz, "klasa": klucz[2],
            "opis": f"{funkcje['brand_name'](baza['attrs'].get('Producent', ''))} "
                    f"{baza['attrs'].get('Model', '')} | {klucz[1]} | klasa {klucz[2]} | "
                    f"{klucz[3] or 'rozdzielczosc ?'} | {klucz[4]} | {klucz[5] or 'grafika ?'}",
            "baza": baza, "id_bazy": baza["id"], "sku_oferty": sku_oferty(baza["id"]),
            "przypieta": bool(juz_wystawione), "aktywna": aktywna,
            "warianty": warianty, "limit": limit, "braki_cennika": braki,
        })
        for inny in reszta:
            wzor = wg_slotu.get((inny["ram"], grupa_dysku(inny["dysk"], cennik)))
            if (inny["ram"], inny["dysk"]) == (baza["ram"], baza["dysk"]):
                powod = POWOD_DUBEL
            elif wzor:
                powod = POWOD_WARIANT
            elif (inny["ram"], inny["dysk"]) < (baza["ram"], baza["dysk"]):
                powod = POWOD_MNIEJSZA
            else:
                powod = POWOD_SPOZA
            poczekalnia.append({
                "rodzina": identyfikator, "opis": rodziny[-1]["opis"], "id_wystawionego": baza["id"],
                "sku": inny["sku"], "id": inny["id"], "ram": inny["ram"], "dysk": inny["dysk"],
                "cena_xml": inny["cena"], "stan": inny["stan"],
                "cena_wystawionego": baza["cena"],
                "cena_ze_wzoru": wzor["cena"] if wzor else None,
                "powod": powod, "roznice": _roznice(baza, inny, funkcje),
            })
    return {"rodziny": rodziny, "poczekalnia": poczekalnia, "bez_rodziny": bez_rodziny}


# --------------------------------------------------------------------------- #
# lista modeli do ustawiania ograniczen
# --------------------------------------------------------------------------- #

KOLUMNY_LISTY_MODELI = KOLUMNY_LIMITOW + ["teraz_max_ram", "teraz_max_dysk", "skad_limit", "typ_ram",
                                           "klasy", "wpisow", "sztuk", "ram_w_feedzie",
                                           "dysk_w_feedzie", "dorobionych_wariantow"]


def lista_modeli(produkty, rodziny: list[dict], cennik: dict, limity: list[dict], funkcje: dict) -> list[dict]:
    """Wszystkie modele z feedu, po jednym wierszu na model + procesor.

    Pierwsze piec kolumn to dokladnie kolumny warianty-limity.csv, wiec te liste
    mozna otworzyc w Excelu, wpisac ograniczenia przy wybranych modelach i zapisac
    W CALOSCI jako config/warianty-limity.csv - wiersze bez wpisanych limitow
    niczego nie ustalaja. Kolumny dalej sa informacyjne (czytnik je pomija).

    Zeby zapis w calosci niczego nie zgubil, na koncu doklejamy wiersze, ktore juz
    sa na liscie, a nie wynikaja z feedu: reguly dla calego modelu (bez procesora)
    i modele wpisane zawczasu, ktorych w feedzie jeszcze nie ma.
    """
    ile_wariantow: dict[tuple, int] = {}
    for rodzina in rodziny:
        klucz = rodzina["klucz"][:2]
        ile_wariantow[klucz] = ile_wariantow.get(klucz, 0) + sum(1 for v in rodzina["warianty"] if not v["baza"])

    grupy: dict[tuple, list] = {}
    for offer, attrs in produkty:
        klucz = (klucz_modelu(attrs, funkcje), klucz_procesora(attrs, funkcje))
        if all(klucz):
            grupy.setdefault(klucz, []).append((offer, attrs))

    def rozmiary(wpisy, pole):
        wartosci = sorted({pojemnosc_gb(a.get(pole, "")) for _, a in wpisy} - {None})
        return " / ".join(etykieta_feedu(x) for x in wartosci)

    wiersze = []
    for klucz in sorted(grupy):
        wpisy = grupy[klucz]
        offer, attrs = wpisy[0]
        limit = limit_rozbudowy(attrs, (offer.findtext("name") or ""), (offer.findtext("cat") or ""),
                                cennik, limity, funkcje)
        # Limit wpisujemy w wiersz tylko wtedy, gdy wiersz listy wskazuje DOKLADNIE ten
        # procesor. Regula szersza - caly model albo fragment nazwy ('Ryzen 5', 'Intel')
        # - jedzie nizej jako wlasny wiersz, tak jak wpisal ja wlasciciel: przepisana
        # tutaj zamienilaby sie w regule jednego procesora i nie objela nastepnego.
        z_listy = limit["wiersz_listy"] or {}
        wlasny = z_listy if (z_listy.get("procesor")
                             and _waga_procesora(z_listy["procesor"], attrs, funkcje) == 3) else None
        kandydaci = funkcje["cpu_candidates"](funkcje["zrodlo_procesora"](attrs))
        wiersze.append({
            "model": funkcje["nazwa_modelu"](attrs), "procesor": kandydaci[0],
            "max_ram": "" if not wlasny or wlasny["max_ram"] is None else wlasny["max_ram"],
            "max_dysk": "" if not wlasny or wlasny["max_dysk"] is None else wlasny["max_dysk"],
            "uwagi": wlasny["uwagi"] if wlasny else "",
            "teraz_max_ram": limit["max_ram"],
            "teraz_max_dysk": "bez limitu" if limit["max_dysk"] is None else limit["max_dysk"],
            "skad_limit": limit["zrodlo"],
            "typ_ram": " / ".join(sorted({(a.get(POLE_TYPU_RAM, "") or "?") for _, a in wpisy})),
            "klasy": " / ".join(sorted({funkcje["condition_class"](a.get(POLE_KLASY, "")) or "?" for _, a in wpisy})),
            "wpisow": len(wpisy),
            "sztuk": sum(int(float(o.get("stock", "0") or 0)) for o, _ in wpisy),
            "ram_w_feedzie": rozmiary(wpisy, POLE_RAM), "dysk_w_feedzie": rozmiary(wpisy, POLE_DYSKU),
            "dorobionych_wariantow": ile_wariantow.get(klucz, 0),
        })

    wszystkie = [attrs for wpisy in grupy.values() for _, attrs in wpisy]
    for wiersz in limity:
        trafia = any(_pasuje_model(wiersz["model"], a, funkcje)
                     and (not wiersz["procesor"] or _waga_procesora(wiersz["procesor"], a, funkcje))
                     for a in wszystkie)
        dokladnie = wiersz["procesor"] and any(
            _pasuje_model(wiersz["model"], a, funkcje)
            and _waga_procesora(wiersz["procesor"], a, funkcje) == 3 for a in wszystkie)
        if dokladnie:
            continue                       # juz przepisany wyzej, przy swoim procesorze
        if not trafia:
            skad = "wpisany zawczasu - nie ma go w feedzie"
        elif wiersz["procesor"]:
            skad = f"regula dla procesorow z '{wiersz['procesor']}' w nazwie"
        else:
            skad = "regula dla calego modelu"
        pusty = dict.fromkeys(KOLUMNY_LISTY_MODELI, "")
        pusty.update(model=wiersz["model"], procesor=wiersz["procesor"], uwagi=wiersz["uwagi"],
                     max_ram="" if wiersz["max_ram"] is None else wiersz["max_ram"],
                     max_dysk="" if wiersz["max_dysk"] is None else wiersz["max_dysk"],
                     skad_limit=skad)
        wiersze.append(pusty)
    return wiersze
