#!/usr/bin/env python3
"""Drugie zrodlo ofert: laptopy z Allegro (empi.xml z repo marekkomp/...).

Allegro nie podaje dwoch rzeczy, ktorych pipeline wymaga twardo:

  1. Klucza jednostkowego. Pole 'Sygnatura/SKU Sprzedajacego' to opis
     konfiguracji, nie identyfikator - 317 ofert ma tylko 144 rozne wartosci.
     Dlatego SKU bierzemy z 'ID oferty' (atrybut id na <o>), unikalnego w 100%.
  2. Danych o kondycji (Kondycja sprzetu, Stan obudowy, Stan ekranu, Bateria,
     W zestawie). Wstawiamy wartosci domyslne z settings.json. Musza byc
     zachowawcze - to twierdzenia o towarze w ofercie niemieckiej.

Kolizja SKU z Shoperem = ta sama sztuka w obu zrodlach. Shoper wygrywa,
bo ma prawdziwe dane o kondycji zamiast domyslnych.

Cena i ilosc licza sie tak samo jak dla feedu Shopera - tym zajmuje sie
generate.py. Tutaj tylko obcinamy stan do zera ponizej 'min_sztuk'.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Iterable


def _text(node) -> str:
    return (node.text or "").strip() if node is not None else ""


def _attrs(offer) -> dict[str, str]:
    return {a.get("name"): _text(a) for a in offer.findall("./attrs/a")}


def wybierz_zrodlo(acfg: dict, root_repo: Path, plik_cli: Path | None) -> tuple[bytes, str]:
    """Kolejnosc: --allegro-file, potem plik w repo, potem publiczne repo.

    Swiadomie NIE porownujemy daty modyfikacji. W GitHub Actions checkout nie
    zachowuje mtime plikow, wiec 'nowszy' bylby losowy. Plik obecny w repo
    znaczy 'uzyj tego' i tak jest raportowany.
    """
    if plik_cli:
        return plik_cli.read_bytes(), f"{plik_cli} (--allegro-file)"

    lokalny = root_repo / acfg.get("plik_lokalny", "input/empi.xml")
    if lokalny.exists():
        return lokalny.read_bytes(), f"{acfg.get('plik_lokalny')} (plik w repo)"

    url = acfg["xml_url"]
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read(), f"{url} (publiczne repo)"


def _mapuj_wartosc(pole: str, wartosc: str, mapy: dict) -> str:
    """Slownik wartosci jest per pole; brak wpisu = zostawiamy jak bylo."""
    return mapy.get(pole, {}).get(wartosc, wartosc)


def przerob_oferte(offer, acfg: dict, sku_zajete: set[str],
                   stan: int) -> tuple[ET.Element | None, str]:
    """Oferta Allegro -> oferta w dialekcie feedu Shopera, albo powod odrzucenia."""
    sku = (offer.get("id") or "").strip()
    if not sku:
        return None, "brak ID oferty"
    if sku in sku_zajete:
        return None, "SKU zajete przez Shoper"

    zrodlo = _attrs(offer)
    nowe: dict[str, str] = {"SKU": sku}

    for docelowe, allegrowe in acfg["mapa_atrybutow"].items():
        for kandydat in ([allegrowe] if isinstance(allegrowe, str) else allegrowe):
            if zrodlo.get(kandydat):
                nowe[docelowe] = zrodlo[kandydat]
                break

    for pole, wartosc in list(nowe.items()):
        nowe[pole] = _mapuj_wartosc(pole, wartosc, acfg.get("mapa_wartosci", {}))

    if nowe.get("Dysk", "").isdigit():           # Allegro podaje samo '256'
        nowe["Dysk"] = f"{nowe['Dysk']} GB"
    if "Złącza zewnętrzne" in nowe:              # Allegro rozdziela pionowa kreska
        nowe["Złącza zewnętrzne"] = nowe["Złącza zewnętrzne"].replace("|", ", ")
    if nowe.get("Producent"):                    # w feedzie sa 'HP.' i 'Dell ..'
        nowe["Producent"] = nowe["Producent"].rstrip(" .")
    if "|" in nowe.get("Model", ""):             # sprzedawca dokleja haslo reklamowe
        nowe["Model"] = nowe["Model"].split("|")[0].strip()

    nowe.update(acfg["domyslna_kondycja"])       # piec pol, ktorych Allegro nie ma

    nowa = ET.Element("o", {
        "id": sku,
        "url": offer.get("url", ""),
        "price": offer.get("price", ""),
        "avail": "1" if stan > 0 else "99",
        "stock": str(stan),
        "basket": "1" if stan > 0 else "0",
    })
    ET.SubElement(nowa, "cat").text = acfg["kategoria_docelowa"]
    ET.SubElement(nowa, "name").text = _text(offer.find("name"))

    imgs = ET.SubElement(nowa, "imgs")
    for i, node in enumerate(offer.findall("./imgs/*")):
        url = (node.get("url") or "").strip()
        if url:
            ET.SubElement(imgs, "main" if i == 0 else "i", {"url": url})

    attrs_el = ET.SubElement(nowa, "attrs")
    for pole, wartosc in nowe.items():
        if wartosc:
            ET.SubElement(attrs_el, "a", {"name": pole}).text = wartosc
    return nowa, ""


def sprawdz_kategorie(cfg: dict) -> list[str]:
    """Z Allegro biora sie WYLACZNIE laptopy. Komputery ida tylko z XML-a sklepowego.

    Powod jest w danych, nie w wygodzie: dla peceta pipeline potrzebuje pola
    'Obudowa' (C:Formfaktor i odsiew All-in-One), a empi.xml go nie ma - kazdy
    desktop z Allegro dostalby domyslna bryle i mogl przejsc jako AiO.
    Dopisanie kategorii komputerowej do 'kategorie_zrodlowe' ma sie skonczyc
    czytelna blokada, a nie cicho wystawionymi ofertami.
    """
    settings = cfg["settings"]
    acfg = settings.get("allegro", {})
    docelowa = acfg.get("kategoria_docelowa", "")
    typ = settings["typ_produktu"].get(docelowa, settings["typ_produktu"]["_domyslnie"])
    if typ != "Notebook":
        return [f"config/settings.json: allegro.kategoria_docelowa to '{docelowa}' "
                f"(typ '{typ}') - z Allegro wolno brac tylko laptopy"]
    return []
def scal(feed_shoper: bytes, feed_allegro: bytes, cfg: dict) -> tuple[bytes, dict]:
    """Jeden feed dla generate.py: Shoper + doklejone laptopy z Allegro."""
    acfg = cfg["settings"]["allegro"]
    prog = int(acfg.get("min_sztuk", 1))
    root = ET.fromstring(feed_shoper)
    kategorie: Iterable[str] = cfg["settings"]["xml_categories"]

    sku_zajete = {
        _attrs(o).get("SKU", "")
        for o in root.findall("./o")
        if _text(o.find("cat")) in kategorie
    }
    sku_zajete.discard("")
    z_shopera = len(sku_zajete)

    pominieto: Counter = Counter()
    dodane = wyzerowane = 0
    for offer in ET.fromstring(feed_allegro).findall("./o"):
        if _text(offer.find("cat")) not in acfg["kategorie_zrodlowe"]:
            pominieto["inna kategoria"] += 1
            continue
        stan = int(float(offer.get("stock", "0") or 0))
        if stan <= 0:
            pominieto["stock zero w zrodle"] += 1
            continue

        # Ponizej progu oferta wchodzi do feedu ze stanem 0. Dzieki temu
        # zbierz_produkty ja pomija przy wystawianiu, a tryb 'aktualizacja'
        # wyzeruje juz istniejaca aukcje - bez osobnej sciezki w kodzie.
        docelowy = stan if stan >= prog else 0
        nowa, powod = przerob_oferte(offer, acfg, sku_zajete, docelowy)
        if nowa is None:
            pominieto[powod] += 1
            continue
        if docelowy == 0:
            wyzerowane += 1
        else:
            dodane += 1
        sku_zajete.add(nowa.get("id"))
        root.append(nowa)

    raport = {
        "sku_z_shopera": z_shopera,
        "dodane_z_allegro": dodane,
        "ponizej_progu_min_sztuk": wyzerowane,
        "prog_min_sztuk": prog,
        "pominieto_z_allegro": dict(pominieto),
    }
    return ET.tostring(root, encoding="utf-8"), raport
