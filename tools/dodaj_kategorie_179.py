#!/usr/bin/env python3
"""Jednorazowy skrypt: dopisuje kategorie eBay 179 (PCs/Desktops & All-in-Ones)
do config/ebay-vocab.json.

DLACZEGO SKRYPT, A NIE RECZNA EDYCJA JSONA:
Kategoria 177 (notebooki) ma w "aspekty" listy po kilkaset dozwolonych wartosci
(Prozessor, Modell). Reczne przepisywanie takiej listy grozi literowka albo
zgubieniem wpisu, a to psuje sie po cichu - produkt po prostu nie trafia do
review.csv jako blad, tylko nie dopasuje CPU i pole zostanie puste. Bezpieczniej
jest WZIAC ISTNIEJACY, DZIALAJACY wpis kategorii 177 jako baze (te same
Prozessor/Modell/Farbe/Erscheinungsjahr sa i tak prawdziwe dla desktopow) i
zmienic tylko to, co naprawde jest inne.

Co robi ten skrypt:
  1. Kopiuje caly wpis kategorii "177" jako punkt startowy dla "179".
  2. Usuwa z niego "Bildschirmgröße" i "Maximale Auflösung" - desktopy (bez AIO)
     nie maja ekranu, te aspekty nie maja zastosowania.
  3. Ustawia "Produktart" na wylacznie ["Desktop"] - AIO i tak jest wykluczone
     wczesniej w generate.py (zbierz_produkty), wiec "Alles in einem" tu nie potrzebne.
  4. Dodaje nowy aspekt "Formfaktor" z lista wartosci DOKLADNIE taka, jaka eBay
     poda w oficjalnym szablonie CSV dla kategorii 179 (skopiowane 1:1 z pliku
     eBaycategorylistingtemplate...csv, sekcja "recommended value(s) for aspect
     Formfaktor").
  5. Ustawia "_wymagane" na ["Marke", "Produktart", "Prozessor"] - pierwsze dwa
     to oficjalne wymagane pola eBaya dla tej kategorii (z szablonu), "Prozessor"
     dopisany jako ta sama, wlasna gwarancja jakosci oferty co dla notebookow.

Uzycie:
    python3 tools/dodaj_kategorie_179.py

Uruchom raz, lokalnie albo w Actions, PRZED pierwszym uruchomieniem generatora
z wlaczonymi komputerami. Skrypt jest idempotentny - jesli "179" juz istnieje,
nadpisuje je tym samym, spojnym wpisem zamiast dublowac.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCIEZKA = ROOT / "config" / "ebay-vocab.json"

# Skopiowane doslownie z pliku szablonu eBay dla kategorii 179
# (">>> The recommended value(s) for aspect Formfaktor: ...").
FORMFAKTOR_WARTOSCI = [
    "Convertible Minitower (CMT)",
    "Micro Tower",
    "Mikroformfaktor (MFR)",
    "Mini Desktop",
    "Mini Pc",
    "Mini Tower",
    "Nettop",
    "Rackmount",
    "Small Form Factor (SFF)",
    "Tower",
    "UCFF",
    "UFF (Ultrafiltrationsfaktor)",
    "Ultra Small Form Factor (USFF)",
    "USDT",
]


def main() -> int:
    dane = json.loads(SCIEZKA.read_text(encoding="utf-8"))
    kategorie = dane.setdefault("kategorie", {})

    if "177" not in kategorie:
        print("BLAD: nie znalazlem kategorii '177' (notebooki) w ebay-vocab.json - "
              "nie mam z czego zbudowac bazy dla '179'. Sprawdz plik recznie.")
        return 1

    baza = copy.deepcopy(kategorie["177"])
    aspekty = baza.setdefault("aspekty", {})

    aspekty.pop("Bildschirmgröße", None)
    aspekty.pop("Maximale Auflösung", None)
    aspekty["Produktart"] = ["Desktop"]
    aspekty["Formfaktor"] = list(FORMFAKTOR_WARTOSCI)

    baza["_wymagane"] = ["Marke", "Produktart", "Prozessor"]
    baza["_uwaga"] = (
        "Wygenerowane z kategorii 177 przez tools/dodaj_kategorie_179.py. "
        "Bildschirmgröße/Maximale Auflösung usuniete (brak ekranu), "
        "Produktart ograniczone do 'Desktop' (bez AIO - te oferty sa "
        "pomijane wczesniej w generate.py), dodano Formfaktor z oficjalnego "
        "szablonu eBay."
    )

    kategorie["179"] = baza
    SCIEZKA.write_text(
        json.dumps(dane, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"OK: dopisano/zaktualizowano kategorie '179' w {SCIEZKA} "
          f"(baza: kopia '177', {len(aspekty)} aspektow).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
