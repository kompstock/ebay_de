#!/usr/bin/env python3
"""Wypelniony arkusz tlumaczen -> pliki konfiguracyjne kraju.

Odwrotnosc tools/eksport_do_tlumaczenia.py. Rozklada jeden CSV z powrotem na
plik kraju, aspects.json i translations.json, zeby nikt nie musial przepisywac
kilkuset wartosci recznie - a przy przepisywaniu gubic co dziesiata.

    python3 tools/wczytaj_tlumaczenia.py it do-tlumaczenia-it-gotowe.csv

Czego NIE nadpisuje: kolumn aspektow, profili handlowych, VAT-u i sufiksu SKU.
To nie sa tlumaczenia, tylko ustalenia rynku, i siedza w pliku kraju na stale.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZRODLO = "de"
# Klucze dokumentacyjne w plikach konfiguracyjnych. Nie sa wartosciami oferty,
# wiec nie podlegaja tlumaczeniu - eksport nie powinien ich w ogole pokazywac.
KOMENTARZE = ("_about", "_uwaga", "_zasada")


def komentarz(klucz: str) -> bool:
    return klucz.startswith(KOMENTARZE)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    kod, plik = sys.argv[1].lower(), Path(sys.argv[2])
    with plik.open(encoding="utf-8-sig", newline="") as uchwyt:
        wiersze = [w for w in csv.DictReader(uchwyt, delimiter=";")
                   if (w.get("po_wlosku") or "").strip() and not komentarz(w["klucz"])]

    kraj = json.loads((ROOT / "config" / "kraje" / f"{kod}.json").read_text(encoding="utf-8"))
    zrodlo = json.loads((ROOT / "config" / "kraje" / f"{ZRODLO}.json").read_text(encoding="utf-8"))
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    aspekty = json.loads((ROOT / zrodlo["pliki"]["aspekty"]).read_text(encoding="utf-8"))
    tlumaczenia = json.loads((ROOT / zrodlo["pliki"]["tlumaczenia"]).read_text(encoding="utf-8"))

    licznik: dict[str, int] = {}

    def policz(gdzie: str) -> None:
        licznik[gdzie] = licznik.get(gdzie, 0) + 1

    for w in wiersze:
        sekcja, klucz, wartosc = w["sekcja"], w["klucz"], w["po_wlosku"].strip()
        czesci = sekcja.split("/")
        if sekcja in ("etykiety_opisu", "slowa"):
            kraj.setdefault(sekcja, {})[klucz] = wartosc
            policz(f"kraje/{kod}.json")
        elif sekcja == "faq_windows":
            kraj["faq_windows"] = wartosc
            policz(f"kraje/{kod}.json")
        elif sekcja == "settings":
            kraj[klucz] = wartosc                 # nadpisze settings.json przy scalaniu
            policz(f"kraje/{kod}.json")
        elif czesci[0] in ("zdania_wiodace", "zdania_wiodace_gaming"):
            kraj.setdefault(czesci[0], {})[klucz] = wartosc
            policz(f"kraje/{kod}.json")
        elif czesci[0] == "profil":
            kraj.setdefault("profile_kraju", {}).setdefault(czesci[1], {})[klucz] = wartosc
            policz(f"kraje/{kod}.json")
        elif czesci[0] == "translations":
            cel = tlumaczenia
            for czesc in czesci[1:]:
                cel = cel.setdefault(czesc, {})
            cel[klucz] = wartosc
            policz("translations.json")
        elif czesci[0] == "aspects":
            cel = aspekty.setdefault(czesci[1], {})
            for czesc in czesci[2:]:
                cel = cel.setdefault(czesc, {})
            cel[klucz] = wartosc
            policz("aspects.json")

    katalog = ROOT / "config" / "kraje" / kod
    katalog.mkdir(parents=True, exist_ok=True)
    (ROOT / "config" / "kraje" / f"{kod}.json").write_text(
        json.dumps(kraj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (katalog / "aspects.json").write_text(
        json.dumps(aspekty, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (katalog / "translations.json").write_text(
        json.dumps(tlumaczenia, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"wczytano {len(wiersze)} tlumaczen:")
    for gdzie, ile in sorted(licznik.items()):
        print(f"  {gdzie}: {ile}")
    pominiete = sum(1 for _ in wiersze)
    print(f"\nSzablony opisu skopiuj osobno do templates/{kod}/ - to cale pliki HTML.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
