#!/usr/bin/env python3
"""Warianty z przedluzona gwarancja: blizniak oferty z sufiksem SKU i wyzsza cena.

Po co osobny krok na poziomie FEEDU, a nie flaga w build_row:
blizniak musi zachowywac sie jak zwyczajna oferta w kazdym trybie. Gdy dopniemy
go do feedu, wszystko dalej dziala samo - zbierz_produkty go widzi, tryb
'aktualizacja' poprawia mu cene i stan, a petla zerujaca wygasza go, kiedy
znika z listy. Gdybysmy produkowali go dopiero przy zapisie CSV, nie byloby
go w 'w_feedzie' i pierwsza aktualizacja wyzerowalaby wszystkie blizniaki.

Czego ten modul NIE robi:
  - nie tworzy blizniakow dla typow spoza listy 'typy'. Nowe komputery do gier
    odpadaja tu same: maja 24 miesiace prosto z feedu, wiec blizniak nie mialby
    czym sie roznic od oryginalu.
  - nie pilnuje, zeby suma stanow nie przekroczyla magazynu. Stan blizniaka to
    stan oryginalu (decyzja biznesowa), wiec przy stanie 1 sa wystawione dwie
    sztuki jednej. Progiem 'min_sztuk' mozna to ograniczyc.
"""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path


def _attrs(offer) -> dict[str, str]:
    return {a.get("name"): (a.text or "").strip() for a in offer.findall("./attrs/a")}


def wczytaj_liste(sciezka: Path, kolumna: str) -> tuple[list[str], str]:
    """SKU do zdublowania z pliku CSV. Zwraca (lista, powod bledu).

    Plik czytamy sami, a nie przez pandas/openpyxl, zeby GitHub Actions nie
    musial niczego instalowac. Stad CSV, nie XLSX - Excel zapisze taki plik
    przez 'Zapisz jako CSV'.
    """
    if not sciezka.is_file():
        return [], ""                         # brak pliku = to samo co pusta lista
    with sciezka.open(encoding="utf-8-sig", newline="") as handle:
        proba = handle.read(4096)
        handle.seek(0)
        try:                                  # Excel zapisuje raz ',' raz ';'
            dialekt = csv.Sniffer().sniff(proba, delimiters=",;\t")
        except csv.Error:
            dialekt = csv.excel
        wiersze = list(csv.DictReader(handle, dialect=dialekt))
    if not wiersze:
        return [], ""                         # pusta lista = funkcja uspiona
    naglowki = {(k or "").strip().lower(): k for k in wiersze[0]}
    klucz = naglowki.get(kolumna.strip().lower())
    if klucz is None:
        return [], (f"{sciezka.name}: brak kolumny '{kolumna}' "
                    f"(sa: {', '.join(str(k) for k in wiersze[0])})")
    out, widziane = [], set()
    for wiersz in wiersze:
        sku = (wiersz.get(klucz) or "").strip()
        if sku and sku not in widziane:
            widziane.add(sku)
            out.append(sku)
    return out, ""


def _klonuj(offer, cfg: dict) -> ET.Element:
    """Kopia oferty z nowym SKU, podniesiona cena i znacznikiem wariantu."""
    nowa = ET.fromstring(ET.tostring(offer))
    sufiks = cfg["sufiks_sku"]
    for element in nowa.findall("./attrs/a"):
        if element.get("name") == "SKU":
            element.text = f"{(element.text or '').strip()}{sufiks}"
    # Ceny tutaj NIE ruszamy. Podnosi ja cena_eur() na podstawie 'mnoznik_ceny'
    # z profilu wariantu - dzieki temu mnoznik dziala na cenie koncowej, czyli
    # tej, ktora widzi kupujacy, a nie na samej cenie towaru.
    attrs_el = nowa.find("./attrs")
    if attrs_el is None:
        attrs_el = ET.SubElement(nowa, "attrs")
    ET.SubElement(attrs_el, "a", {"name": cfg["pole_wariantu"]}).text = cfg["wartosc_wariantu"]
    return nowa


def dopnij(feed_bytes: bytes, cfg_all: dict, root_repo: Path,
           typ_fn=None) -> tuple[bytes, dict]:
    """Dokleja do feedu blizniaki dla SKU z listy. Zwraca (feed, raport).

    'typ_fn' to typ_produktu() z generate.py - podajemy go z zewnatrz, zeby nie
    robic importu w kolko. MUSI rozpoznawac warianty, nie tylko kategorie: nowy
    komputer do gier ma juz 24 miesiace, a jego blizniak trafilby z powrotem na
    profil 'Desktop-PC nowy' i wyszedl jako oferta identyczna z oryginalem -
    ten sam tytul, ta sama cena, ta sama gwarancja. Czyli czysty duplikat.
    """
    cfg = cfg_all["settings"].get("gwarancja_rozszerzona", {})
    if not cfg.get("enabled"):
        return feed_bytes, {}

    sciezka = root_repo / cfg.get("lista_sku", "config/gwarancja-24.csv")
    lista, blad = wczytaj_liste(sciezka, cfg.get("kolumna_sku", "SKU"))
    raport: dict = {"gw_lista": f"{sciezka.name} ({len(lista)} SKU)"}
    if blad:                                  # zly naglowek - to juz prawdziwy blad
        raport["gw_blad_listy"] = blad
        return feed_bytes, raport

    szukane = set(lista)
    if not szukane:
        # Brak pliku albo pusta lista to normalny stan, nie awaria - funkcja spi,
        # dopoki ktos nie wpisze SKU. Mowimy to wprost, zeby raport nie wygladal
        # na bledny.
        raport["gw_lista"] = f"{sciezka.name}: brak SKU, wariant gwarancyjny uspiony"
        return feed_bytes, raport

    settings = cfg_all["settings"]
    typ_bazowy = {k: v for k, v in settings["typ_produktu"].items() if not k.startswith("_")}
    dozwolone_typy = set(cfg.get("typy", ["Notebook"]))
    prog = int(cfg.get("min_sztuk", 1))
    sufiks = cfg["sufiks_sku"]

    root = ET.fromstring(feed_bytes)
    istniejace = {_attrs(o).get("SKU", "") for o in root.findall("./o")}
    dodane, pominieto = [], {}

    def pomin(sku: str, powod: str) -> None:
        pominieto.setdefault(powod, []).append(sku)

    for offer in list(root.findall("./o")):
        attrs = _attrs(offer)
        sku = attrs.get("SKU", "")
        if sku not in szukane:
            continue
        szukane.discard(sku)
        kategoria = (offer.findtext("./cat") or "").strip()
        if typ_fn:
            typ = typ_fn(kategoria, settings, attrs)
        else:
            typ = typ_bazowy.get(kategoria, settings["typ_produktu"]["_domyslnie"])
        if typ not in dozwolone_typy:
            pomin(sku, f"typ '{typ}' nie ma wariantu gwarancyjnego")
            continue
        if f"{sku}{sufiks}" in istniejace:
            pomin(sku, "blizniak juz jest w feedzie")
            continue
        stan = int(float(offer.get("stock", "0") or 0))
        if stan < prog:
            pomin(sku, f"stan ponizej progu {prog}")
            continue
        root.append(_klonuj(offer, cfg))
        dodane.append(f"{sku}{sufiks}")

    raport.update({
        "gw_dodane": len(dodane),
        "gw_typy": sorted(dozwolone_typy),
        # SKU z listy, ktorych nie bylo w feedzie. Bez tego lista po cichu gnije:
        # produkt wychodzi ze sprzedazy, wpis zostaje, nikt tego nie widzi.
        "gw_sku_poza_feedem": sorted(szukane),
        "gw_pominieto": {k: sorted(v) for k, v in pominieto.items()},
    })
    return ET.tostring(root, encoding="utf-8"), raport
