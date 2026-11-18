#!/usr/bin/env python3
"""Oferta wariantowa eBaya z rodziny policzonej w warianty.py.

Jedna oferta = jeden wiersz OFERTY (tytul, opis, zdjecia, cechy wspolne) i pod nim
po jednym wierszu na WARIANT (RAM + dysk, wlasna cena, ilosc i SKU). Uklad pliku
sprawdzony na zywym eBayu 14-15.11.2026 (VerifyAdd, Add i Revise oferty 307282655424):

  wiersz oferty    Action=Add, Relationship puste,
                   RelationshipDetails='Arbeitsspeichergröße=8 GB;16 GB|SSD-Festplattenkapazität=256 GB;480GB',
                   cena i ilosc PUSTE, cechy bedace wariantami PUSTE
  wiersz wariantu  Action PUSTE, Relationship=Variation,
                   RelationshipDetails='Arbeitsspeichergröße=8 GB|SSD-Festplattenkapazität=256 GB',
                   wlasne CustomLabel, *StartPrice, *Quantity

SKAD BIORA SIE WARTOSCI
Niczego nie liczymy tu drugi raz. Kazdy wariant przechodzi przez build_row()
generatora jako zwykly produkt o zmienionym RAM-ie, dysku i cenie - stamtad
bierzemy cene w euro i dokladne zapisy cech ('480GB', '2 TB'), ktore eBay ma
w swoim slowniku. Dzieki temu wariant kosztuje i nazywa sie tak samo, jak
kosztowalby i nazywal sie wystawiony osobno.

OPIS I TYTUL
Sa wspolne dla calej oferty, wiec nie moga twierdzic niczego o jednej
konfiguracji. Dostaja napisy z zakresem: w tytule '8-32 GB RAM 256 GB-2 TB',
w opisie '8 / 16 / 32 GB' i '256 GB / 480 GB / 960 GB / 2 TB'.
"""

from __future__ import annotations

import csv
from pathlib import Path

import warianty

LIMIT_TYTULU = 80
LIMIT_SKU = 50
ZNAKI_ZAKAZANE_W_CESZE = "=;|"


class _BezUwag:
    """Wiersze wariantow buduje build_row(), ktory zglasza braki do review.csv.
    Te same braki zglosi juz wiersz oferty - po co wpisywac je jedenascie razy."""
    def add(self, *_args, **_kwargs):
        pass


# --------------------------------------------------------------------------- #
# napisy z zakresem
# --------------------------------------------------------------------------- #

def _rozdziel(pojemnosc: str) -> tuple[str, str]:
    """'256 GB' -> ('256', 'GB'). Zapis bez jednostki zostaje w calosci jako liczba."""
    liczba, _, jednostka = pojemnosc.partition(" ")
    return liczba, jednostka


def _lista(wartosci: list[str]) -> str:
    """Do opisu: '8 / 16 / 32 GB', a przy roznych jednostkach '256 GB / 960 GB / 2 TB'."""
    jednostki = {_rozdziel(w)[1] for w in wartosci}
    if len(wartosci) > 1 and len(jednostki) == 1 and "" not in jednostki:
        return " / ".join(_rozdziel(w)[0] for w in wartosci) + " " + jednostki.pop()
    return " / ".join(wartosci)


def _zakres(wartosci: list[str]) -> str:
    """Do tytulu: '8-32 GB', a przy roznych jednostkach '256 GB-2 TB'."""
    if len(wartosci) == 1:
        return wartosci[0]
    pierwsza, ostatnia = wartosci[0], wartosci[-1]
    if _rozdziel(pierwsza)[1] == _rozdziel(ostatnia)[1]:
        return f"{_rozdziel(pierwsza)[0]}-{ostatnia}"
    return f"{pierwsza}-{ostatnia}"


def napisy_pamieci(konfiguracje: list[dict], zapis) -> dict:
    """Napisy 'pamiec' dla build_title() i render_description().

    Klucz powstaje tylko dla tej cechy, ktora w ofercie naprawde sie zmienia.
    Gdy rodzina ma jeden rozmiar RAM-u (np. model z wlutowana pamiecia), tytul
    i opis mowia o nim po staremu, a wyboru dotyczy sam dysk.
    """
    ramy = [zapis(f"{r}GB") for r in sorted({k["ram"] for k in konfiguracje})]
    dyski = [zapis(warianty.etykieta_feedu(d)) for d in sorted({k["dysk"] for k in konfiguracje})]
    napisy = {}
    if len(ramy) > 1:
        napisy.update(ram=_lista(ramy), ram_tytul=_zakres(ramy) + " RAM")
    if len(dyski) > 1:
        napisy.update(disk=_lista(dyski), dysk_tytul=_zakres(dyski))
    return napisy


# --------------------------------------------------------------------------- #
# skladanie oferty
# --------------------------------------------------------------------------- #

def _unikalne(wartosci) -> list[str]:
    wynik = []
    for wartosc in wartosci:
        if wartosc not in wynik:
            wynik.append(wartosc)
    return wynik


def zbuduj_oferte(rodzina: dict, cfg: dict, headers: list[str], g, review,
                  dopiski=()) -> tuple[list[dict] | None, str]:
    """Wiersze jednej oferty wariantowej: [oferta, wariant, wariant, ...] albo (None, powod).

    'g' to modul generate (przekazywany, zeby ten plik nie importowal generatora
    na sztywno - tak samo jak warianty.py dostaje 'funkcje'). 'dopiski' to
    koncowki SKU: GW24 i znacznik rynku.
    """
    for kolumna in ("Relationship", "RelationshipDetails"):
        if kolumna not in headers:
            return None, f"naglowek rynku nie ma kolumny {kolumna}"
    if not rodzina.get("aktywna", True):
        return None, "baza ponizej progu stanu"
    if len(rodzina["warianty"]) < 2:
        return None, "rodzina bez rozbudowy - zostaje sama zwykla oferta"
    baza = rodzina["baza"]
    offer, attrs = baza["offer"], baza["attrs"]
    if (attrs.get(warianty.POLE_TYPU_DYSKU) or "").strip() != "SSD":
        # Rozbudowany dysk to zawsze SSD. Baza z HDD dalaby w jednej ofercie dwa
        # typy dysku, a typ dysku jest cecha wspolna calej oferty.
        return None, "baza ma dysk inny niz SSD"
    kol, asp = g.nazwy_kolumn(cfg)
    settings = cfg["settings"]

    def zapis(tekst):
        return g.clean_capacity(tekst, settings.get("jednostki_pojemnosci"))

    warianty_wiersze = []
    for v in rodzina["warianty"]:
        # Wlasny slownik atrybutow: copy.copy() elementu XML dzieli go z oryginalem,
        # wiec cena wariantu nadpisywalaby cene wpisu w feedzie.
        offer_v = offer.makeelement(offer.tag, dict(offer.attrib))
        offer_v.extend(list(offer))
        offer_v.set("price", str(v["cena"]))
        offer_v.set("stock", str(v["stan"]))
        wiersz, blad = g.build_row(offer_v, warianty.atrybuty_wariantu(attrs, v["ram"], v["dysk"]),
                                   cfg, headers, _BezUwag())
        if not wiersz:
            return None, f"wariant {v['ram']}/{v['dysk']}: {blad}"
        ram_ebay, ssd_ebay = wiersz[kol("ram")], wiersz[kol("pojemnosc_ssd")]
        if not ram_ebay or not ssd_ebay:
            return None, f"wariant {v['ram']}/{v['dysk']}: eBay nie ma takiej wartosci RAM-u albo dysku"
        warianty_wiersze.append((v, wiersz, ram_ebay, ssd_ebay))

    # Wariantem jest tylko to, co sie zmienia. Cecha o jednej wartosci zostaje
    # zwykla cecha oferty - eBay nie pokazuje wtedy pustej listy wyboru.
    cechy = [(asp("ram"), kol("ram"), 2), (asp("pojemnosc_ssd"), kol("pojemnosc_ssd"), 3)]
    zmienne = [(nazwa, kolumna, i) for nazwa, kolumna, i in cechy
               if len({w[i] for w in warianty_wiersze}) > 1]
    if not zmienne:
        return None, "warianty nie roznia sie zadna cecha eBaya"

    napisy = napisy_pamieci([v for v, *_ in warianty_wiersze], zapis)
    oferta, blad = g.build_row(offer, attrs, cfg, headers, review, pamiec=napisy)
    if not oferta:
        return None, blad
    oferta["*Title"] = _tytul_z_typem_dysku(oferta["*Title"], napisy, offer, attrs, cfg, g)
    oferta["CustomLabel"] = warianty.sku_oferty(baza["id"], dopiski)
    oferta["Relationship"] = ""
    oferta["RelationshipDetails"] = "|".join(
        f"{nazwa}={';'.join(_unikalne(w[i] for w in warianty_wiersze))}" for nazwa, _, i in zmienne)
    # Cena i ilosc stoja wylacznie w wierszach wariantow, a cecha bedaca wariantem
    # nie moze stac jednoczesnie w cechach oferty.
    for kolumna in ["*StartPrice", "*Quantity"] + [kolumna for _, kolumna, _ in zmienne]:
        oferta[kolumna] = ""
    if any(i == 3 for _, _, i in zmienne):
        oferta[kol("pojemnosc_dysku")] = ""

    wiersze = [oferta]
    for v, wiersz, *_ in warianty_wiersze:
        para = (v, wiersz, wiersz[kol("ram")], wiersz[kol("pojemnosc_ssd")])
        dziecko = dict.fromkeys(headers, "")
        dziecko.update({
            "Relationship": "Variation",
            "RelationshipDetails": "|".join(f"{nazwa}={para[i]}" for nazwa, _, i in zmienne),
            "CustomLabel": warianty.sku_wariantu(baza["id"], v["ram"], v["dysk"], dopiski),
            "*StartPrice": wiersz["*StartPrice"],
            "*Quantity": wiersz["*Quantity"],
        })
        wiersze.append(dziecko)
    bledy = sprawdz_oferte(wiersze)
    return (None, "; ".join(bledy)) if bledy else (wiersze, "")


def _tytul_z_typem_dysku(tytul: str, napisy: dict, offer, attrs: dict, cfg: dict, g) -> str:
    """Tytul ma 80 znakow i generator skraca go od konca calymi czlonami. Przy dlugiej
    nazwie modelu zakres ze slowem 'RAM' wypychal z tytulu typ dysku: zostawalo
    '256 GB-2 TB' bez 'SSD'. Wtedy rezygnujemy ze slowa 'RAM' ('16-24 GB 256 GB-2 TB
    SSD') - cztery znaki, ktore zwykle wystarczaja, zeby 'SSD' wrocilo."""
    typ_dysku = (attrs.get(warianty.POLE_TYPU_DYSKU) or "").strip()
    if not typ_dysku or f" {typ_dysku} " in f" {tytul} " or not napisy.get("ram_tytul", "").endswith(" RAM"):
        return tytul
    settings = cfg["settings"]
    profil = g.profil_produktu(g.text(offer.find("./cat")), settings, attrs)
    krotszy = g.build_title(attrs, settings, cfg["aspects"], profil,
                            dict(napisy, ram_tytul=napisy["ram_tytul"][:-len(" RAM")]))
    return krotszy if f" {typ_dysku} " in f" {krotszy} " else tytul


def sprawdz_oferte(wiersze: list[dict]) -> list[str]:
    """Kontrola gotowych wierszy. To sa reguly, na ktorych eBay odrzuca plik albo -
    gorzej - przyjmuje go i wystawia cos innego, niz chcielismy."""
    oferta, dzieci = wiersze[0], wiersze[1:]
    bledy = []
    if len(oferta.get("*Title", "")) > LIMIT_TYTULU:
        bledy.append(f"tytul ma {len(oferta['*Title'])} znakow (limit {LIMIT_TYTULU})")
    if oferta.get("Relationship") or oferta.get("*StartPrice") or oferta.get("*Quantity"):
        bledy.append("wiersz oferty niesie cene, ilosc albo Relationship")
    szczegoly = oferta.get("RelationshipDetails", "")
    zestawy = {}
    for czesc in szczegoly.split("|"):
        nazwa, _, wartosci = czesc.partition("=")
        zestawy[nazwa] = wartosci.split(";")
        if nazwa != nazwa.strip() or any(w != w.strip() or not w for w in zestawy[nazwa]):
            bledy.append(f"spacja albo pusta wartosc w zestawie '{czesc}'")
        if any(z in w for w in zestawy[nazwa] for z in ZNAKI_ZAKAZANE_W_CESZE):
            bledy.append(f"wartosc cechy '{nazwa}' zawiera znak z '{ZNAKI_ZAKAZANE_W_CESZE}'")
        if oferta.get(f"C:{nazwa}") or oferta.get(f"*C:{nazwa}"):
            bledy.append(f"cecha '{nazwa}' jest jednoczesnie wariantem i cecha oferty")
    if len(dzieci) < 2:
        bledy.append("mniej niz dwa warianty")
    kombinacje, sku = set(), [oferta.get("CustomLabel", "")]
    for dziecko in dzieci:
        if dziecko.get(next(iter(dziecko))) or dziecko.get("Relationship") != "Variation":
            bledy.append("wiersz wariantu ma wypelniona kolumne Action albo zly Relationship")
        para = dict(c.partition("=")[::2] for c in dziecko.get("RelationshipDetails", "").split("|"))
        if set(para) != set(zestawy) or any(para[n] not in zestawy[n] for n in para if n in zestawy):
            bledy.append(f"wariant {para} nie miesci sie w zestawach oferty")
        klucz = tuple(sorted(para.items()))
        if klucz in kombinacje:
            bledy.append(f"wariant {para} powtarza sie")
        kombinacje.add(klucz)
        try:
            if float(dziecko.get("*StartPrice") or 0) <= 0 or int(dziecko.get("*Quantity") or 0) < 1:
                bledy.append(f"wariant {para}: cena albo ilosc nie jest dodatnia")
        except ValueError:
            bledy.append(f"wariant {para}: cena albo ilosc nie jest liczba")
        sku.append(dziecko.get("CustomLabel", ""))
    if len(set(sku)) != len(sku) or not all(sku):
        bledy.append("puste albo powtorzone SKU w ofercie")
    if any(len(s) > LIMIT_SKU for s in sku):
        bledy.append(f"SKU dluzsze niz {LIMIT_SKU} znakow")
    return bledy


def przypiete_z_raportu(sciezka: Path, site: str) -> dict[str, str]:
    """{ID Shopera: numer oferty eBay} dla ofert wariantowych, ktore juz stoja na rynku 'site'.

    Czytane z raportu aktywnych ofert: wiersze oferty wariantowej maja wypelniona
    kolumne 'Variation details', a ich SKU niosa sygnature 'SHOPER-ID:<ID>'. Stad
    wiadomo, ktorych rodzin NIE wystawiac drugi raz i na ktorym wpisie stoi oferta -
    bez osobnego pliku z powiazaniami. Oferty wariantowe o SKU w innym zapisie
    (reczne, testowe) nie sa nasze i tu nie trafiaja.
    """
    wynik: dict[str, str] = {}
    with sciezka.open(encoding="utf-8-sig", newline="") as uchwyt:
        for wiersz in csv.DictReader(uchwyt):
            if (wiersz.get("Listing site") or "").strip().upper() != site.upper():
                continue
            if not (wiersz.get("Variation details") or "").strip():
                continue
            rozlozone = warianty.rozloz_sku(wiersz.get("Custom label (SKU)") or "")
            if rozlozone:
                wynik[rozlozone["id"]] = (wiersz.get("Item number") or "").strip()
    return wynik


def oferty_z_raportu(sciezka: Path, site: str) -> dict[str, dict]:
    """Oferty wariantowe z sygnatura SHOPER-ID stojace na rynku 'site':
    {numer oferty: {'id', 'tytul', 'waluta', 'warianty': [wiersze wariantow z raportu]}}.

    Wiersz oferty i wiersze wariantow maja ten sam numer; wariant poznajemy po tym,
    ze jego SKU niesie konfiguracje ('...-16GB-480GB'), a nie koncowke ':warianty'.
    """
    oferty: dict[str, dict] = {}
    with sciezka.open(encoding="utf-8-sig", newline="") as uchwyt:
        for wiersz in csv.DictReader(uchwyt):
            if (wiersz.get("Listing site") or "").strip().upper() != site.upper():
                continue
            if not (wiersz.get("Variation details") or "").strip():
                continue
            sku = (wiersz.get("Custom label (SKU)") or "").strip()
            rozlozone = warianty.rozloz_sku(sku)
            if not rozlozone:
                continue
            numer = (wiersz.get("Item number") or "").strip()
            oferta = oferty.setdefault(numer, {"id": rozlozone["id"], "tytul": "", "waluta": "EUR",
                                               "sku_oferty": "", "warianty": []})
            oferta["tytul"] = (wiersz.get("Title") or "").strip() or oferta["tytul"]
            oferta["waluta"] = (wiersz.get("Currency") or "EUR").strip()
            if rozlozone["oferta"]:
                oferta["sku_oferty"] = sku
                continue
            oferta["warianty"].append({
                "sku": sku, "ram": rozlozone["ram"], "dysk": rozlozone["dysk"],
                "szczegoly": (wiersz.get("Variation details") or "").strip(),
                "ilosc": int(float(wiersz.get("Available quantity") or 0)),
                "cena": float((wiersz.get("Start price") or "0").replace(",", ".")),
            })
    return oferty


def wiersze_aktualizacji(rodziny: list[dict], na_ebay: dict[str, dict], cfg: dict,
                         headers: list[str], g, review, dopiski=()) -> tuple[list[dict], dict]:
    """Wiersze 'Revise' dla wariantow, w zwyklym szablonie pliku aktualizacji generatora.

    UKLAD: dla kazdej oferty, w ktorej cos sie zmienilo, najpierw WIERSZ OFERTY
    (Action=Revise, numer oferty, SKU oferty, bez ceny, ilosci i Relationship), a pod
    nim wiersze jej wariantow (Action i numer PUSTE, Relationship=Variation,
    'Relationship details' z raportu, nowa cena i ilosc). Tak jak w sprawdzonym
    tescie zmiany SKU z 15.11.2026.

    Dlaczego tak: eBay doczepia kazdy wiersz 'Variation' do NAJBLIZSZEGO wiersza
    oferty nad nim, a kolumne z numerem w wierszu wariantu ignoruje. 17.11.2026 plik
    FR mial jeden wiersz z numerem na wariant (bez wiersza oferty) i eBay uznal
    wszystkie 130 wariantow ze stu ofert za warianty pierwszej zwyklej aukcji w pliku
    - wynik: jeden blad 21916635 i nic nie zostalo zmienione. Pojedynczy wiersz
    wariantu z 16.11 przeszedl tylko dlatego, ze byl w pliku sam.

    Wiersz dostaje tylko wariant, w ktorym cos sie zmienilo - tak samo jak zwykle
    aukcje w ebay-revise.csv.

    Skad cena i ilosc:
      * rodzina ma oferte i dosc sztuk -> to, co wyszloby dzis z zbuduj_oferte(),
      * baza spadla ponizej progu, wyprzedala sie albo wariantu nie ma juz w siatce
        -> ilosc 0, cena bez zmian (oferta zostaje i wraca sama, gdy stan wroci).
    """
    wg_bazy = {r["id_bazy"]: r for r in rodziny}
    listing_site = cfg["settings"]["listing_site"]
    prog_ceny = float(cfg["settings"].get("prog_zmiany_ceny_eur", 0) or 0)
    wiersze = []
    licznik = {"ofert": len(na_ebay), "bez_zmian": 0, "zmiana_ceny": 0, "zmiana_ilosci": 0,
               "wyzerowane": 0, "oferty_bez_wpisu_bazowego": [], "brakujace_warianty": 0}
    for numer, oferta in sorted(na_ebay.items()):
        rodzina = wg_bazy.get(oferta["id"])
        dzis: dict[str, tuple[float, int]] = {}
        if rodzina is None:
            licznik["oferty_bez_wpisu_bazowego"].append(f"{numer} (ID Shopera {oferta['id']})")
        else:
            zbudowane, _ = zbuduj_oferte(rodzina, cfg, headers, g, review, dopiski)
            for dziecko in (zbudowane or [])[1:]:
                dzis[dziecko["CustomLabel"]] = (float(dziecko["*StartPrice"]), int(dziecko["*Quantity"]))
        licznik["brakujace_warianty"] += len(set(dzis) - {v["sku"] for v in oferta["warianty"]})
        zmienione = []
        for v in oferta["warianty"]:
            cena, ilosc = dzis.get(v["sku"], (v["cena"], 0))
            # Ten sam prog co przy zwyklych aukcjach ('prog_zmiany_ceny_eur'): bez niego
            # kazde drgniecie kursu NBP dawalo ~130 wierszy wariantow dziennie, a zwykla
            # oferta tej samej bazy stala bez zmian.
            if abs(cena - v["cena"]) < prog_ceny:
                cena = v["cena"]
            if cena == v["cena"] and ilosc == v["ilosc"]:
                licznik["bez_zmian"] += 1
                continue
            licznik["zmiana_ceny"] += cena != v["cena"]
            licznik["zmiana_ilosci"] += ilosc != v["ilosc"]
            licznik["wyzerowane"] += ilosc == 0 and v["ilosc"] != 0
            zmienione.append({
                "Action": "", "Category name": "", "Item number": "", "Title": "",
                "Listing site": "", "Currency": "",
                "Start price": f"{cena:.1f}", "Buy It Now price": "", "Available quantity": str(ilosc),
                "Relationship": "Variation", "Relationship details": v["szczegoly"],
                "Custom label (SKU)": v["sku"]})
        if not zmienione:
            continue
        licznik.setdefault("ofert_zmienionych", 0)
        licznik["ofert_zmienionych"] += 1
        wiersze.append({
            "Action": "Revise", "Category name": "", "Item number": numer, "Title": oferta["tytul"],
            "Listing site": listing_site, "Currency": oferta["waluta"],
            "Start price": "", "Buy It Now price": "", "Available quantity": "",
            "Relationship": "", "Relationship details": "",
            "Custom label (SKU)": oferta.get("sku_oferty") or warianty.sku_oferty(oferta["id"], dopiski)})
        wiersze.extend(zmienione)
    return wiersze, licznik


TYP_LAPTOPA = "Notebook"


def przygotuj(produkty: list, cfg: dict, g, cennik: dict, limity: list[dict],
              sku_na_ebay=(), sufiks_gw: str = "") -> dict:
    """Przestawia laptopy na nowa sciezke, zanim generator zbuduje wiersze.

    Zwraca plan: 'produkty' (lista dla dalszej czesci generatora), 'rodziny',
    'poczekalnia', 'dopiski'. Na liscie produktow:
      * wpis bazowy kazdej rodziny ZOSTAJE, ale dostaje SKU 'WSHOPER-ID:<ID>-<RAM>-<dysk>' -
        dzieki temu jego zwykla oferte wystawia, aktualizuje i zeruje ta sama,
        stara czesc generatora co kazda inna aukcje (cena, progi stanow, kupon);
      * wpisy z poczekalni ZNIKAJA - nie sa wystawiane;
      * reszta (komputery, laptopy bez rodziny, blizniaki GW24) zostaje bez zmian.
    'sku_na_ebay' to wszystkie SKU z raportu aktywnych ofert tego rynku: stad
    wiadomo, na ktorych wpisach stoja juz oferty (przypiecie po ID Shopera).
    """
    settings = cfg["settings"]
    sufiks_kraju = (cfg.get("kraj") or {}).get("sufiks_sku", "")
    dopiski = [sufiks_kraju]

    def blizniak_gw(attrs: dict) -> bool:
        sku = attrs.get("SKU", "")
        if sufiks_kraju and sku.endswith(sufiks_kraju):
            sku = sku[:-len(sufiks_kraju)]
        return bool(sufiks_gw) and sku.endswith(sufiks_gw)

    funkcje = {"norm": g.norm, "brand_name": g.brand_name, "cpu_candidates": g.cpu_candidates,
               "zrodlo_procesora": g.zrodlo_procesora, "clean_capacity": g.clean_capacity,
               "marki": g.MARKI_ZNANE, "condition_class": g.condition_class,
               "nazwa_modelu": g.nazwa_modelu}
    laptopy = [(o, a) for o, a in produkty
               if g.typ_produktu(g.text(o.find("./cat")), settings, a) == TYP_LAPTOPA
               and not blizniak_gw(a)]
    # Oferta GW24 niesie ID oryginalu, ale nie przypina rodziny - inaczej jej wpis
    # moglby przejac baze rodziny od wpisu, na ktorym stoja oferty.
    przypiete = {r["id"] for r in map(warianty.rozloz_sku, sku_na_ebay)
                 if r and not (sufiks_gw and sufiks_gw in r["dopiski"])}
    wynik = warianty.zbuduj_rodziny(laptopy, cennik, limity, funkcje, przypiete=przypiete)
    z_poczekalni = {(p["id"], p["sku"]) for p in wynik["poczekalnia"]}
    zostaja = [(o, a) for o, a in produkty if (o.get("id", ""), a.get("SKU", "")) not in z_poczekalni]
    for rodzina in wynik["rodziny"]:
        baza = rodzina["baza"]
        baza["attrs"]["SKU"] = warianty.sku_bazy(rodzina["id_bazy"], baza["ram"], baza["dysk"], dopiski)
    # Oferta GW24 laptopa (decyzja wlasciciela z 18.11.2026): ten sam zapis co zwykla
    # oferta bazy, z GW24 przed znacznikiem rynku - 'WSHOPER-ID:10207-8GB-256GB-GW24-FRA'.
    # Dalej zwykla oferta jednej konfiguracji, bez wyboru RAM i dysku.
    gw24: dict[str, str] = {}
    for offer, attrs in zostaja:
        # typ bazowy (bez attrs): z attrs blizniak to juz "Notebook GW24"
        if not blizniak_gw(attrs) or g.typ_produktu(g.text(offer.find("./cat")), settings) != TYP_LAPTOPA:
            continue
        id_ = (offer.get("id", "") or "").strip()
        ram = warianty.pojemnosc_gb(attrs.get(warianty.POLE_RAM, ""))
        dysk = warianty.pojemnosc_gb(attrs.get(warianty.POLE_DYSKU, ""))
        if not (id_.isdigit() and ram and dysk):
            continue                          # nieczytelna konfiguracja - zostaje stare SKU
        nowe = warianty.sku_bazy(id_, ram, dysk, [sufiks_gw, sufiks_kraju])
        gw24[nowe] = attrs.get("SKU", "")
        attrs["SKU"] = nowe
    return {"produkty": zostaja, "rodziny": wynik["rodziny"], "poczekalnia": wynik["poczekalnia"],
            "bez_rodziny": wynik["bez_rodziny"], "dopiski": dopiski, "ofert_w_pliku": 0,
            "aktualizacja": None, "gw24": gw24}


def reczne_ilosci(plan: dict, reczne: dict) -> dict[str, dict]:
    """Ilosci z config/sku-reczne.csv przepisane na oferty nowej sciezki.

    Wpis moze stac pod (pierwszy trafiony wygrywa):
      * pelnym SKU zwyklej oferty, np. 'WSHOPER-ID:10207-8GB-256GB-FRA',
      * samym ID z rynkiem, np. 'SHOPER-ID:10207-FRA',
      * STARYM SKU Shopera tak, jak stal na eBayu, np. '4771FRA' - dzieki temu
        istniejace wpisy dzialaja bez przepisywania listy.
    Rynek musi sie zgadzac, tak jak w starej sciezce: wpis '3888;5' (Niemcy) nie
    ustawia ilosci na Francji, bo to inna aukcja.

    Liczba obejmuje CALA rodzine: zwykla oferte bazy i kazdy wariant (najwyzej tyle,
    ile lezy w magazynie) - zastepuje prog 'min_stan_bazy' i 'max_sztuk_na_wariant',
    tak jak w starej sciezce zastepuje progi stanow. Pusta ilosc niczego nie zmienia.
    Zwraca {SKU zwyklej oferty bazy: {'ilosc', 'wpis'}} i poprawia rodziny w planie.
    """
    wynik: dict[str, dict] = {}
    for rodzina in plan["rodziny"]:
        baza = rodzina["baza"]
        nowe_sku = baza["attrs"]["SKU"]
        kandydaci = [nowe_sku, warianty._z_dopiskami(f"{warianty.PRZEDROSTEK_SKU}{rodzina['id_bazy']}",   # noqa: SLF001
                                                    plan["dopiski"]), baza["sku"]]
        wpis = next((k for k in kandydaci if k and reczne.get(k) is not None), None)
        if wpis is None:
            continue
        ilosc = min(int(reczne[wpis]), baza["stan"])
        wynik[nowe_sku] = {"ilosc": int(reczne[wpis]), "wpis": wpis}
        rodzina["aktywna"] = ilosc > 0
        for v in rodzina["warianty"]:
            v["stan"] = ilosc
    # Oferty GW24 w nowym zapisie: wpis po nowym SKU albo po starym ('4771GW24FRA').
    for nowe_sku, stare_sku in (plan.get("gw24") or {}).items():
        wpis = next((k for k in (nowe_sku, stare_sku) if k and reczne.get(k) is not None), None)
        if wpis is not None:
            wynik[nowe_sku] = {"ilosc": int(reczne[wpis]), "wpis": wpis}
    return wynik


def sprawdz_uklad_revise(wiersze: list[dict]) -> str:
    """'' gdy uklad pliku aktualizacji jest dobry, inaczej opis pierwszego bledu.

    Wiersz wariantu (Relationship=Variation) musi miec pusta akcje i numer i stac pod
    wierszem oferty wariantowej (SKU z koncowka ':warianty'), a nie pod zwykla aukcja."""
    rodzic = None
    for numer, w in enumerate(wiersze, start=1):
        if w.get("Relationship") == "Variation":
            if w.get("Action") or w.get("Item number"):
                return f"wiersz {numer}: wariant {w.get('Custom label (SKU)')} ma akcje albo numer oferty"
            if rodzic is None:
                return f"wiersz {numer}: wariant {w.get('Custom label (SKU)')} nie stoi pod wierszem swojej oferty"
            ten = warianty.rozloz_sku(w.get("Custom label (SKU)") or "")
            if not ten or ten["id"] != rodzic["id"]:
                return (f"wiersz {numer}: wariant {w.get('Custom label (SKU)')} stoi pod oferta "
                        f"{rodzic['sku']} - to nie jego oferta")
        else:
            r = warianty.rozloz_sku(w.get("Custom label (SKU)") or "")
            rodzic = dict(r, sku=w.get("Custom label (SKU)")) if r and r["oferta"] else None
    return ""


def wartosc_oferty(wiersze: list[dict]) -> float:
    """Cena razy ilosc wszystkich wariantow - tyle oferta zabiera z limitu sprzedazy eBaya."""
    return sum(float(d["*StartPrice"]) * int(d["*Quantity"]) for d in wiersze[1:])


# --------------------------------------------------------------------------- #
# poczekalnia: lista do recznego przegladu
# --------------------------------------------------------------------------- #

PLIK_POCZEKALNI_CSV = "warianty-poczekalnia.csv"
PLIK_POCZEKALNI_HTML = "warianty-poczekalnia.html"

KOLUMNY_POCZEKALNI = ["rodzina", "opis", "id_wystawionego", "sku_wystawionego", "id_shoper", "sku_shoper",
                      "ram_gb", "dysk_gb", "stan", "cena_xml", "cena_wystawionego", "cena_ze_wzoru",
                      "xml_minus_wzor", "co_to_jest", "ile_roznic", "roznice", "link", "link_wystawionego"]

# Kolejnosc na stronie: najpierw to, co naprawde trzeba obejrzec.
# POWOD_SPOZA nie wystepuje od 17.11.2026 - taki wpis dostaje wlasna rodzine (warianty.py).
KOLEJNOSC_POWODOW = [warianty.POWOD_DUBEL, warianty.POWOD_MNIEJSZA, warianty.POWOD_WARIANT]
OPIS_POWODU = {
    warianty.POWOD_DUBEL: ("Dubel", "dubel",
                           "Ten sam model, klasa, procesor, ekran i grafika, a do tego ten sam RAM i dysk "
                           "co wystawiony wpis. Sprawdź różnice - może to inny towar, który powinien "
                           "dostać inny opis w Shoperze."),
    warianty.POWOD_SPOZA: ("Poza ofertą", "spoza",
                           "Konfiguracja, której oferta nie ma wśród wariantów. Nie jest wystawiona."),
    warianty.POWOD_MNIEJSZA: ("Mniejszy", "mniejsza",
                              "Mniejsza konfiguracja niż wystawiony wpis. Wejdzie sama, gdy wystawiony "
                              "się wyprzeda."),
    warianty.POWOD_WARIANT: ("Jest jako wariant", "wariant",
                             "Ta konfiguracja jest już w ofercie jako wariant, liczony z wpisu bazowego "
                             "plus dopłata z cennika. Zwykle nic nie trzeba robić - spójrz tylko na cenę."),
}


def _zl(kwota) -> str:
    return "" if kwota is None else f"{kwota:.2f}"


def _konfig(ram, dysk) -> str:
    if not ram or not dysk:
        return "?"
    dysk_txt = f"{dysk // 1000} TB" if dysk >= 1000 and dysk % 1000 == 0 else f"{dysk} GB"
    return f"{ram} GB / {dysk_txt}"


def wiersze_poczekalni(poczekalnia: list[dict]) -> list[dict]:
    """Wiersze CSV poczekalni - te same kolumny w przebiegu generatora i w podgladzie."""
    return [{
        "rodzina": p["rodzina"], "opis": p["opis"], "id_wystawionego": p["id_wystawionego"],
        "sku_wystawionego": p.get("sku_wystawionego", ""), "id_shoper": p["id"], "sku_shoper": p["sku"],
        "ram_gb": p["ram"], "dysk_gb": p["dysk"], "stan": p["stan"],
        "cena_xml": _zl(p["cena_xml"]), "cena_wystawionego": _zl(p["cena_wystawionego"]),
        "cena_ze_wzoru": _zl(p["cena_ze_wzoru"]),
        "xml_minus_wzor": _zl(p["cena_xml"] - p["cena_ze_wzoru"]) if p["cena_ze_wzoru"] is not None else "",
        "co_to_jest": p["powod"], "ile_roznic": len(p["roznice"]),
        "roznice": " || ".join(f"{x['pole']}: wystawiony [{x['wystawiony'] or '-'}] / ten [{x['ten'] or '-'}]"
                               for x in p["roznice"]),
        "link": p.get("url", ""), "link_wystawionego": p.get("url_wystawionego", ""),
    } for p in poczekalnia]


def _h(tekst) -> str:
    return (str(tekst).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _link(url: str, tekst: str) -> str:
    # Tylko adresy sklepu z feedu; cokolwiek innego zostaje zwyklym tekstem.
    if url.startswith("https://") or url.startswith("http://"):
        return f'<a href="{_h(url)}" target="_blank" rel="noopener">{_h(tekst)}</a>'
    return _h(tekst)


def strona_poczekalni(poczekalnia: list[dict], kraj: str = "", kiedy: str = "") -> str:
    """Strona HTML poczekalni: rodziny pogrupowane, przy kazdym wpisie powod po ludzku,
    ceny obok siebie i roznice w tabelce 'wystawiony / ten'. Bez skryptow i bez
    zewnetrznych plikow - otwiera sie tak samo z dysku i ze strony GitHub Pages."""
    wg_powodu = {p: [x for x in poczekalnia if x["powod"] == p] for p in KOLEJNOSC_POWODOW}
    inne = [x for x in poczekalnia if x["powod"] not in OPIS_POWODU]
    czesci = []
    licznik = "".join(
        f'<a class="kafel {OPIS_POWODU[p][1]}" href="#{OPIS_POWODU[p][1]}"><b>{len(wg_powodu[p])}</b>'
        f'{_h(OPIS_POWODU[p][0])}</a>' for p in KOLEJNOSC_POWODOW)
    for powod in KOLEJNOSC_POWODOW + (["inne"] if inne else []):
        wpisy = wg_powodu.get(powod, inne)
        nazwa, klasa, wyjasnienie = OPIS_POWODU.get(powod, ("Inne", "inne", ""))
        czesci.append(f'<h2 id="{klasa}">{_h(nazwa)} <span>{len(wpisy)}</span></h2>'
                      f'<p class="wyj">{_h(wyjasnienie)}</p>')
        if not wpisy:
            czesci.append('<p class="pusto">Brak.</p>')
            continue
        rodziny: dict[str, list[dict]] = {}
        for p in wpisy:
            rodziny.setdefault(p["rodzina"], []).append(p)
        for grupa in rodziny.values():
            pierwszy = grupa[0]
            wystawiony = (f'{_link(pierwszy.get("url_wystawionego", ""), "ID " + str(pierwszy["id_wystawionego"]))}'
                          f' · {_h(pierwszy.get("sku_wystawionego", ""))}'
                          f' · {_konfig(pierwszy.get("ram_wystawionego"), pierwszy.get("dysk_wystawionego"))}'
                          f' · {_zl(pierwszy["cena_wystawionego"])} zł'
                          + (f' · {pierwszy["stan_wystawionego"]} szt.'
                             if pierwszy.get("stan_wystawionego") is not None else ""))
            karta = [f'<div class="karta"><div class="rodz">{_h(pierwszy["opis"])}</div>'
                     f'<div class="wyst"><i>wystawiony</i>{wystawiony}</div>']
            for p in grupa:
                ceny = f'{_zl(p["cena_xml"])} zł w Shoperze'
                if p["cena_ze_wzoru"] is not None:
                    roznica = p["cena_xml"] - p["cena_ze_wzoru"]
                    etykieta = "wystawiony" if powod == warianty.POWOD_DUBEL else "wariant w ofercie"
                    ceny += (f' · {etykieta} {_zl(p["cena_ze_wzoru"])} zł'
                             f' <em class="{"plus" if roznica > 0 else "minus" if roznica < 0 else ""}">'
                             f'({roznica:+.2f})</em>')
                karta.append(f'<div class="ten"><i>czeka</i>{_link(p.get("url", ""), "ID " + str(p["id"]))}'
                             f' · {_h(p["sku"])} · {_konfig(p["ram"], p["dysk"])} · {p["stan"]} szt.'
                             f'<div class="ceny">{ceny}</div>')
                if p["roznice"]:
                    karta.append('<table><tr><th>pole</th><th>wystawiony</th><th>ten</th></tr>' + "".join(
                        f'<tr><td>{_h(x["pole"])}</td><td>{_h(x["wystawiony"] or "—")}</td>'
                        f'<td>{_h(x["ten"] or "—")}</td></tr>' for x in p["roznice"]) + '</table>')
                elif powod == warianty.POWOD_DUBEL:
                    karta.append('<div class="brak">Opisy identyczne - różni się tylko ID i SKU.</div>')
                karta.append('</div>')
            karta.append('</div>')
            czesci.append("".join(karta))
    tytul = "Poczekalnia" + (f" · {kraj}" if kraj else "")
    return f"""<!doctype html>
<html lang="pl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h(tytul)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:900px;margin:0 auto;
      padding:24px 16px 60px;color:#14161a;line-height:1.5;background:#fff}}
 h1{{font-size:22px;margin:0 0 4px}} .pod{{color:#5a5c62;margin:0 0 20px;font-size:15px}}
 .kafle{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px}}
 .kafel{{flex:1 1 150px;border:1px solid #e0e0dd;border-radius:12px;padding:12px 14px;text-decoration:none;
        color:#5a5c62;font-size:14px}} .kafel b{{display:block;font-size:26px;color:#14161a}}
 .kafel.dubel{{border-color:#f3ccc8;background:#fdf1f0}} .kafel.spoza{{background:#fff8e8;border-color:#f0dfb4}}
 h2{{font-size:17px;margin:34px 0 4px}} h2 span{{color:#5a5c62;font-weight:400}}
 .wyj{{color:#5a5c62;font-size:14px;margin:0 0 12px}} .pusto{{color:#5a5c62}}
 .karta{{border:1px solid #e0e0dd;border-radius:12px;padding:14px 16px;margin-bottom:12px;font-size:14px}}
 .rodz{{font-weight:700;font-size:15px;margin-bottom:8px}}
 .wyst,.ten{{padding:6px 0;border-top:1px solid #f0f0ee;overflow-wrap:anywhere}}
 i{{display:inline-block;min-width:104px;font-style:normal;font-size:12px;text-transform:uppercase;
    letter-spacing:.06em;color:#5a5c62}}
 .ceny{{color:#5a5c62;margin:2px 0 0 108px}} em{{font-style:normal}} .plus{{color:#0f6b3c}} .minus{{color:#a01b12}}
 .brak{{margin:4px 0 0 108px;color:#5a5c62}}
 table{{border-collapse:collapse;margin:6px 0 2px 108px;font-size:13px;max-width:calc(100% - 108px)}}
 th,td{{text-align:left;padding:4px 10px;border-bottom:1px solid #f0f0ee;vertical-align:top}}
 th{{color:#5a5c62;font-weight:400}} td:first-child{{color:#5a5c62}}
 a{{color:#14161a}}
 @media (max-width:600px){{.ceny,.brak{{margin-left:0}} table{{margin-left:0;max-width:100%}}}}
</style>
<h1>{_h(tytul)}</h1>
<p class="pod">Wpisy z Shopera, których generator nie wystawił, bo w rodzinie stoi już inny wpis
(rodzina = model, klasa, procesor, rozdzielczość, dotyk, grafika). Nic tu nie jest zablokowane -
to lista do spokojnego przejrzenia.{" Przebieg: " + _h(kiedy) + "." if kiedy else ""}</p>
<div class="kafle">{licznik}</div>
{"".join(czesci)}
</html>
"""


def zapisz_poczekalnie(katalog: Path, poczekalnia: list[dict], kraj: str = "", kiedy: str = "") -> None:
    """CSV (do Excela) i HTML (do czytania) w katalogu wynikow rynku."""
    with (katalog / PLIK_POCZEKALNI_CSV).open("w", encoding="utf-8-sig", newline="") as uchwyt:
        zapis = csv.DictWriter(uchwyt, fieldnames=KOLUMNY_POCZEKALNI, delimiter=";",
                               lineterminator="\n", extrasaction="ignore")
        zapis.writeheader()
        zapis.writerows(wiersze_poczekalni(poczekalnia))
    (katalog / PLIK_POCZEKALNI_HTML).write_text(strona_poczekalni(poczekalnia, kraj, kiedy), encoding="utf-8")


def poczekalnia_wg_powodu(poczekalnia: list[dict]) -> dict[str, int]:
    return {OPIS_POWODU.get(p, (p,))[0]: sum(1 for x in poczekalnia if x["powod"] == p)
            for p in KOLEJNOSC_POWODOW}


# --------------------------------------------------------------------------- #
# plik
# --------------------------------------------------------------------------- #

def zapisz_plik(sciezka: Path, headers: list[str], oferty: list[list[dict]], akcja: str,
                kod_rynku: str = "DE") -> None:
    """Plik w ukladzie zapisz_add() generatora, z jedna roznica: akcje dostaje tylko
    wiersz oferty. Wiersz wariantu z wpisana akcja eBay czyta jako osobna oferte
    (tak skonczyl sie test 1 zmiany SKU, 15.11.2026)."""
    with sciezka.open("w", encoding="utf-8-sig", newline="") as uchwyt:
        zapis = csv.writer(uchwyt, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        zapis.writerow(["Info", "Version=1.0.0", f"Template=fx_category_template_EBAY_{kod_rynku.upper()}"])
        zapis.writerow(headers)
        for wiersze in oferty:
            for numer, wiersz in enumerate(wiersze):
                wiersz = dict(wiersz)
                wiersz[headers[0]] = akcja if numer == 0 else ""
                zapis.writerow([wiersz.get(h, "") for h in headers])


def wczytaj_cfg(g, root: Path, kod_kraju: str) -> dict:
    """Konfiguracja rynku zlozona tak samo jak w generate.main() - ten sam kraj,
    slownik, szablony i naglowek. Powtorzona tutaj, zeby narzedzie i testy nie
    musialy uruchamiac calego generatora; przy wpieciu w generate() zniknie."""
    settings = g.load_json(root / "config" / "settings.json")
    kraj = g.wczytaj_kraj(kod_kraju)
    bazowa_cena = dict(settings.get("cena") or {})
    settings.update({k: v for k, v in kraj.items()
                     if not k.startswith("_") and k not in ("pliki", "katalog_szablonow")})
    settings["cena"] = {**bazowa_cena, **(kraj.get("cena") or {})}
    pliki = kraj.get("pliki") or {}
    return {
        "settings": settings,
        "kraj": kraj,
        "translations": g.load_json(root / pliki["tlumaczenia"]),
        "aspects": g.load_json(root / pliki["aspekty"]),
        "manufacturers": g.load_json(root / "config" / "manufacturers.json"),
        "szablony": g.wczytaj_szablony(settings, root / kraj.get("katalog_szablonow", "templates")),
        "headers": g.read_headers(root / pliki["naglowek"]),
        "vocab": g.load_json(root / pliki["slownik"]),
    }
