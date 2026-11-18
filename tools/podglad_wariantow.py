#!/usr/bin/env python3
"""Podglad wariantow RAM/dysk: co wyszloby z feedu, BEZ wystawiania czegokolwiek.

Czyta feed tak samo jak generator (te same kategorie, aliasy pol i odsiew),
bierze laptopy i liczy rodziny z wariantami wedlug config/warianty.json
i config/warianty-limity.csv. Zapisuje cztery pliki:

  warianty-podglad.csv     kazda rodzina z wystawionym wpisem (baza) i dorobionymi konfiguracjami:
                           cena, doplata, stan, SKU na eBayu (ID Shopera + konfiguracja)
  warianty-poczekalnia.csv pozostale wpisy o tych samych szesciu cechach: nie sa wystawiane,
                           niczego nie blokuja; obok cena wystawionego wpisu, cena ze wzoru
                           i roznice pole po polu - do recznego sprawdzenia
  warianty-modele.csv     wszystkie modele z feedu; pierwsze piec kolumn to kolumny
                          warianty-limity.csv, wiec po wpisaniu ograniczen mozna ja
                          zapisac w calosci jako config/warianty-limity.csv
  warianty-raport.json    liczby i bledy konfiguracji

Uruchomienie:
  python tools/podglad_wariantow.py                          # feed z settings.json
  python tools/podglad_wariantow.py --feed-url https://...   # inny adres
  python tools/podglad_wariantow.py --feed-file plik.xml     # plik z dysku
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import generate as g      # noqa: E402
import oferty_wariantowe  # noqa: E402
import warianty           # noqa: E402

TYP_LAPTOPA = "Notebook"


class _BezUwag:
    """zbierz_produkty() zglasza braki do review.csv. Podglad ich nie zapisuje."""
    def add(self, *_args, **_kwargs):
        pass


def zapisz_csv(sciezka: Path, kolumny: list[str], wiersze: list[dict]) -> None:
    with sciezka.open("w", encoding="utf-8-sig", newline="") as uchwyt:
        zapis = csv.DictWriter(uchwyt, fieldnames=kolumny, delimiter=";", extrasaction="ignore")
        zapis.writeheader()
        zapis.writerows(wiersze)


def zl(kwota) -> str:
    return "" if kwota is None else f"{kwota:.2f}"


def main() -> int:
    for strumien in (sys.stdout, sys.stderr):
        if hasattr(strumien, "reconfigure"):
            strumien.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Podglad wariantow RAM/dysk - niczego nie wystawia.")
    parser.add_argument("--feed-file", type=Path)
    parser.add_argument("--feed-url")
    parser.add_argument("--cennik", type=Path, default=ROOT / "config" / "warianty.json")
    parser.add_argument("--limity", type=Path, default=ROOT / "config" / "warianty-limity.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "warianty")
    parser.add_argument("--przypiete", default="",
                        help="ID Shopera wpisow, na ktorych stoja juz oferty (po przecinku). "
                             "Docelowo generator bierze je z raportu aktywnych ofert eBaya.")
    args = parser.parse_args()

    settings = g.load_json(ROOT / "config" / "settings.json")
    cennik = g.load_json(args.cennik)
    limity, bledy_listy = warianty.wczytaj_limity(args.limity)
    bledy = warianty.sprawdz_cennik(cennik) + bledy_listy
    feed = args.feed_file.read_bytes() if args.feed_file else g.fetch_bytes(args.feed_url or settings["feed_url"])
    zrodlo = str(args.feed_file or args.feed_url or settings["feed_url"])

    funkcje = {"norm": g.norm, "brand_name": g.brand_name, "cpu_candidates": g.cpu_candidates,
               "zrodlo_procesora": g.zrodlo_procesora, "clean_capacity": g.clean_capacity,
               "marki": g.MARKI_ZNANE, "condition_class": g.condition_class,
               "nazwa_modelu": g.nazwa_modelu}
    pominiete_przez_generator: Counter = Counter()
    wszystkie = g.zbierz_produkty(ET.fromstring(feed), {"settings": settings}, _BezUwag(),
                                  pominiete_przez_generator)
    laptopy = [(o, a) for o, a in wszystkie
               if g.typ_produktu(g.text(o.find("./cat")), settings, a) == TYP_LAPTOPA]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if bledy:
        raport = {"ok": False, "bledy": bledy, "feed": zrodlo}
        (args.output_dir / "warianty-raport.json").write_text(
            json.dumps(raport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(raport, ensure_ascii=False, indent=2))
        return 2

    przypiete = {x.strip() for x in args.przypiete.split(",") if x.strip()}
    wynik = warianty.zbuduj_rodziny(laptopy, cennik, limity, funkcje, przypiete=przypiete)
    rodziny, poczekalnia, bez_rodziny = wynik["rodziny"], wynik["poczekalnia"], wynik["bez_rodziny"]

    podglad = []
    for r in rodziny:
        for v in r["warianty"]:
            podglad.append({
                "rodzina": r["id"], "opis": r["opis"], "klasa": r["klasa"],
                "rodzaj": "baza" if v["baza"] else "wariant",
                "ram_gb": v["ram"], "dysk_gb": v["dysk"], "cena_pln": zl(v["cena"]),
                "doplata_pln": zl(v["doplata"]), "stan": v["stan"], "stan_bazy": r["baza"]["stan"],
                "sku_ebay": v["sku"], "sku_oferty_ebay": r["sku_oferty"],
                "id_shoper": r["id_bazy"], "sku_shoper": r["baza"]["sku"],
                "limit": r["limit"]["zrodlo"],
                "braki_cennika": " | ".join(r["braki_cennika"]),
            })
    zapisz_csv(args.output_dir / "warianty-podglad.csv",
               ["rodzina", "opis", "klasa", "rodzaj", "ram_gb", "dysk_gb", "cena_pln", "doplata_pln",
                "stan", "stan_bazy", "sku_ebay", "sku_oferty_ebay", "id_shoper", "sku_shoper", "limit",
                "braki_cennika"], podglad)

    oferty_wariantowe.zapisz_poczekalnie(args.output_dir, poczekalnia)

    zapisz_csv(args.output_dir / "warianty-modele.csv", warianty.KOLUMNY_LISTY_MODELI,
               warianty.lista_modeli(laptopy, rodziny, cennik, limity, funkcje))

    dorobione = [v for r in rodziny for v in r["warianty"] if not v["baza"]]
    raport = {
        "ok": True, "feed": zrodlo,
        "laptopow_ze_stanem": len(laptopy),
        "rodzin": len(rodziny),
        "rodzin_z_wariantami": sum(1 for r in rodziny if len(r["warianty"]) > 1),
        "rodzin_bez_rozbudowy": sum(1 for r in rodziny if len(r["warianty"]) == 1),
        "min_stan_bazy": cennik.get("min_stan_bazy"),
        "ofert_wariantowych_ze_stanem": sum(1 for r in rodziny if r["aktywna"] and len(r["warianty"]) > 1),
        "wariantow_ze_stanem": sum(1 for r in rodziny if r["aktywna"] and len(r["warianty"]) > 1
                                   for v in r["warianty"]),
        "dorobionych_wariantow": len(dorobione),
        "konfiguracji_razem": len(dorobione) + len(rodziny),
        "w_poczekalni": len(poczekalnia),
        "poczekalnia_wg_rodzaju": dict(Counter(p["powod"] for p in poczekalnia)),
        "poczekalnia_z_roznicami": sum(1 for p in poczekalnia if p["roznice"]),
        "ofert_przypietych_do_id": sum(1 for r in rodziny if r["przypieta"]),
        "bez_rodziny": len(bez_rodziny),
        "bez_rodziny_wg_powodu": dict(Counter(b["powod"] for b in bez_rodziny)),
        "bez_rodziny_sku": sorted(b["sku"] for b in bez_rodziny),
        "limity_wg_zrodla": dict(Counter(r["limit"]["zrodlo"].split(",")[0] for r in rodziny)),
        "braki_cennika": dict(Counter(b for r in rodziny for b in r["braki_cennika"])),
        "wierszy_na_liscie_ograniczen": len(limity),
        "max_sztuk_na_wariant": cennik.get("max_sztuk_na_wariant"),
        "uwaga": "Podglad liczy od cen z feedu. Ceny reczne (config/sku-reczne.csv) naklada "
                 "dopiero generator, wiec tu ich nie widac.",
    }
    (args.output_dir / "warianty-raport.json").write_text(
        json.dumps(raport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(raport, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
