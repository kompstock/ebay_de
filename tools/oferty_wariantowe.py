#!/usr/bin/env python3
"""Plik eBaya z ofertami wariantowymi RAM/dysk (krok 2). Osobne narzedzie: codzienny
przebieg generatora nic o nim nie wie i niczego w nim nie zmienia.

Czyta feed tak samo jak generator, bierze laptopy, liczy rodziny (src/warianty.py)
i dla kazdej, ktora ma warianty i dosc sztuk, sklada oferte (src/oferty_wariantowe.py).

Pliki w katalogu wynikowym:
  ebay-add-warianty.csv          do wgrania na eBay; tryb 'test' = VerifyAdd (nic nie wystawia),
                                 tryb 'pierwsze' = Add
  oferty-wariantowe-podglad.csv  jedna linia na oferte: tytul, liczba wariantow, ceny,
                                 ile zabiera z limitu sprzedazy
  oferty-wariantowe-raport.json  liczby, powody pominiec, laczna wartosc ofert

Przyklady:
  python tools/oferty_wariantowe.py --tryb test --tylko 7452,10207       # probka dwoch ofert
  python tools/oferty_wariantowe.py --tryb test --limit 5                # piec pierwszych
  python tools/oferty_wariantowe.py --kraj it --tryb test --feed-file 1.xml
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

import generate as g            # noqa: E402
import oferty_wariantowe as ow  # noqa: E402
import warianty                 # noqa: E402

AKCJE = {"test": "VerifyAdd", "pierwsze": "Add", "aktualizacja": "Revise"}


def main() -> int:
    for strumien in (sys.stdout, sys.stderr):
        if hasattr(strumien, "reconfigure"):
            strumien.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Plik eBaya z ofertami wariantowymi RAM/dysk.")
    parser.add_argument("--kraj", default="de")
    parser.add_argument("--tryb", default="test", choices=sorted(AKCJE),
                        help="test = VerifyAdd (eBay tylko sprawdza), pierwsze = Add, aktualizacja = "
                             "ceny i stany wariantow ofert, ktore juz stoja (wymaga --raport)")
    parser.add_argument("--feed-file", type=Path)
    parser.add_argument("--feed-url")
    parser.add_argument("--nbp-rate", type=float)
    parser.add_argument("--tylko", default="", help="ID Shopera wpisow bazowych, po przecinku")
    parser.add_argument("--raport", type=Path,
                        help="raport aktywnych ofert eBaya: rodziny, ktore maja juz oferte "
                             "wariantowa na tym rynku, nie trafia do pliku drugi raz")
    parser.add_argument("--limit", type=int, default=0, help="najwyzej tyle ofert (0 = wszystkie)")
    parser.add_argument("--budzet-pln", type=float, default=0,
                        help="laczna wartosc ofert (cena x ilosc wariantow) nie przekroczy tej kwoty; "
                             "tyle wolnego limitu sprzedazy chcesz na nie przeznaczyc (0 = bez ograniczenia)")
    parser.add_argument("--cennik", type=Path, default=ROOT / "config" / "warianty.json")
    parser.add_argument("--limity", type=Path, default=ROOT / "config" / "warianty-limity.csv")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    cfg = ow.wczytaj_cfg(g, ROOT, args.kraj)
    settings, kraj = cfg["settings"], cfg["kraj"]
    cfg["rate"] = args.nbp_rate or g.fetch_nbp_rate(settings["nbp_url"])["rate"]
    headers = cfg["headers"]
    output_dir = args.output_dir or (ROOT / "output" / kraj["kod"].lower() / "warianty")
    output_dir.mkdir(parents=True, exist_ok=True)

    cennik = g.load_json(args.cennik)
    limity, bledy_listy = warianty.wczytaj_limity(args.limity)
    bledy = warianty.sprawdz_cennik(cennik) + bledy_listy
    if bledy:
        raport = {"ok": False, "bledy": bledy}
        (output_dir / "oferty-wariantowe-raport.json").write_text(
            json.dumps(raport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(raport, ensure_ascii=False, indent=2))
        return 2

    zrodlo = str(args.feed_file or args.feed_url or settings["feed_url"])
    feed = args.feed_file.read_bytes() if args.feed_file else g.fetch_bytes(args.feed_url or settings["feed_url"])
    funkcje = {"norm": g.norm, "brand_name": g.brand_name, "cpu_candidates": g.cpu_candidates,
               "zrodlo_procesora": g.zrodlo_procesora, "clean_capacity": g.clean_capacity,
               "marki": g.MARKI_ZNANE, "condition_class": g.condition_class,
               "nazwa_modelu": g.nazwa_modelu}
    review = g.Review()
    pominiete_w_feedzie: Counter = Counter()
    produkty = g.zbierz_produkty(ET.fromstring(feed), cfg, review, pominiete_w_feedzie)
    laptopy = [(o, a) for o, a in produkty
               if g.typ_produktu(g.text(o.find("./cat")), settings, a) == "Notebook"]
    juz_na_ebay = ow.przypiete_z_raportu(args.raport, settings["listing_site"]) if args.raport else {}
    rodziny = warianty.zbuduj_rodziny(laptopy, cennik, limity, funkcje, przypiete=set(juz_na_ebay))["rodziny"]

    tylko = {x.strip() for x in args.tylko.split(",") if x.strip()}
    dopiski = [kraj.get("sufiks_sku", "")]
    if args.tryb == "aktualizacja":
        if not args.raport:
            parser.error("--tryb aktualizacja wymaga --raport")
        na_ebay = ow.oferty_z_raportu(args.raport, settings["listing_site"])
        wiersze_revise, licznik = ow.wiersze_aktualizacji(rodziny, na_ebay, cfg, headers, g, review, dopiski)
        g.zapisz_revise(output_dir / "ebay-revise-warianty.csv", wiersze_revise)
        raport = {"ok": True, "kraj": kraj["kod"], "tryb": args.tryb, "feed": zrodlo, "kurs_eur": cfg["rate"],
                  "wierszy_w_pliku": len(wiersze_revise), **licznik}
        (output_dir / "oferty-wariantowe-raport.json").write_text(
            json.dumps(raport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(raport, ensure_ascii=False, indent=2))
        return 0
    oferty, podglad, powody = [], [], Counter()
    poza_budzetem, wydane = 0, 0.0
    # Najpierw rodziny z najwiekszym stanem bazy: przy ograniczonym limicie sprzedazy
    # do pliku wchodza te oferty, ktore maja najwiecej towaru za soba.
    for rodzina in sorted(rodziny, key=lambda r: -r["baza"]["stan"]):
        if tylko and rodzina["id_bazy"] not in tylko:
            continue
        if args.limit and len(oferty) >= args.limit:
            break
        if rodzina["id_bazy"] in juz_na_ebay:
            wiersze, powod = None, f"oferta wariantowa juz stoi na eBayu ({juz_na_ebay[rodzina['id_bazy']]})"
        else:
            wiersze, powod = ow.zbuduj_oferte(rodzina, cfg, headers, g, review, dopiski)
        if wiersze and args.budzet_pln:
            koszt = ow.wartosc_oferty(wiersze) * cfg["rate"]
            if wydane + koszt > args.budzet_pln:
                wiersze, powod = None, "nie miesci sie w budzecie limitu"
                poza_budzetem += 1
            else:
                wydane += koszt
        if not wiersze:
            powody[powod.split(":")[0] if powod.startswith("wariant ")
                   else powod.split(" (")[0]] += 1
            podglad.append({"id_shoper": rodzina["id_bazy"], "rodzina": rodzina["opis"],
                            "wystawiona": "nie", "powod": powod, "stan_bazy": rodzina["baza"]["stan"]})
            continue
        oferty.append(wiersze)
        ceny = [float(d["*StartPrice"]) for d in wiersze[1:]]
        podglad.append({
            "id_shoper": rodzina["id_bazy"], "rodzina": rodzina["opis"], "wystawiona": "tak", "powod": "",
            "stan_bazy": rodzina["baza"]["stan"], "sku_oferty": wiersze[0]["CustomLabel"],
            "tytul": wiersze[0]["*Title"], "znakow_tytulu": len(wiersze[0]["*Title"]),
            "wariantow": len(wiersze) - 1, "sztuk_na_wariant": wiersze[1]["*Quantity"],
            "cena_od_eur": f"{min(ceny):.2f}", "cena_do_eur": f"{max(ceny):.2f}",
            "wartosc_eur": f"{ow.wartosc_oferty(wiersze):.2f}",
            "warianty": wiersze[0]["RelationshipDetails"],
        })

    ow.zapisz_plik(output_dir / "ebay-add-warianty.csv", headers, oferty, AKCJE[args.tryb], kraj["kod"])
    kolumny = ["id_shoper", "rodzina", "wystawiona", "powod", "stan_bazy", "sku_oferty", "tytul",
               "znakow_tytulu", "wariantow", "sztuk_na_wariant", "cena_od_eur", "cena_do_eur",
               "wartosc_eur", "warianty"]
    with (output_dir / "oferty-wariantowe-podglad.csv").open("w", encoding="utf-8-sig", newline="") as uchwyt:
        zapis = csv.DictWriter(uchwyt, fieldnames=kolumny, delimiter=";", extrasaction="ignore")
        zapis.writeheader()
        zapis.writerows(podglad)
    wartosc = sum(ow.wartosc_oferty(w) for w in oferty)
    raport = {
        "ok": True, "kraj": kraj["kod"], "tryb": args.tryb, "akcja": AKCJE[args.tryb], "feed": zrodlo,
        "kurs_eur": cfg["rate"],
        "laptopow_ze_stanem": len(laptopy), "rodzin": len(rodziny),
        "ofert_w_pliku": len(oferty), "wariantow_w_pliku": sum(len(w) - 1 for w in oferty),
        "nie_wystawione_wg_powodu": dict(powody),
        "wartosc_ofert_eur": round(wartosc, 2), "wartosc_ofert_pln": round(wartosc * cfg["rate"], 2),
        "uwaga_limit": "Limit sprzedazy eBaya liczy cene razy ilosc kazdego wariantu. "
                       "'wartosc_ofert_pln' to tyle, ile te oferty zajma z limitu.",
        "min_stan_bazy": cennik.get("min_stan_bazy"), "sztuk_na_wariant": cennik.get("max_sztuk_na_wariant"),
        "budzet_pln": args.budzet_pln or None, "ofert_poza_budzetem": poza_budzetem,
        "ofert_wariantowych_juz_na_ebay": len(juz_na_ebay),
        "uwagi_do_tlumaczen": len(review.rows()),
    }
    (output_dir / "oferty-wariantowe-raport.json").write_text(
        json.dumps(raport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(raport, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
