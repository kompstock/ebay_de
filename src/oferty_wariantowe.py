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
      * wpis bazowy kazdej rodziny ZOSTAJE, ale dostaje SKU 'SHOPER-ID:<ID>' -
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
    przypiete = {r["id"] for r in map(warianty.rozloz_sku, sku_na_ebay) if r}
    wynik = warianty.zbuduj_rodziny(laptopy, cennik, limity, funkcje, przypiete=przypiete)
    z_poczekalni = {(p["id"], p["sku"]) for p in wynik["poczekalnia"]}
    zostaja = [(o, a) for o, a in produkty if (o.get("id", ""), a.get("SKU", "")) not in z_poczekalni]
    for rodzina in wynik["rodziny"]:
        rodzina["baza"]["attrs"]["SKU"] = warianty.sku_bazy(rodzina["id_bazy"], dopiski)
    return {"produkty": zostaja, "rodziny": wynik["rodziny"], "poczekalnia": wynik["poczekalnia"],
            "bez_rodziny": wynik["bez_rodziny"], "dopiski": dopiski, "ofert_w_pliku": 0,
            "aktualizacja": None}


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
