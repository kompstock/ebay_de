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


def kolumna_celu(wiersz):
    """Kolumna z jezykiem docelowym.

    Nazywa sie 'tlumaczenie'. Starsze arkusze maja 'po_wlosku' - nazwe, ktora
    zostala z pierwszego rynku i mylila przy kazdym kolejnym. Czytamy obie,
    zeby arkusz wypelniony przed zmiana dalej dzialal.
    """
    return wiersz.get("tlumaczenie") or wiersz.get("po_wlosku") or ""
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
                   if (kolumna_celu(w) or "").strip() and not komentarz(w["klucz"])]

    kraj = json.loads((ROOT / "config" / "kraje" / f"{kod}.json").read_text(encoding="utf-8"))
    zrodlo = json.loads((ROOT / "config" / "kraje" / f"{ZRODLO}.json").read_text(encoding="utf-8"))
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))

    def podstawa(nazwa: str):
        """Plik, na ktory nakladamy wiersze z arkusza.

        Jesli rynek ma juz swoj plik - bierzemy JEGO. Wczesniej zawsze szla
        baza niemiecka, wiec drugie wczytanie arkusza wymazywalo wszystko,
        czego w arkuszu nie ma: mapowania portow, kolory, nazwy systemow.
        Arkusz pokrywa czesc pliku, nie caly, wiec reszta wracala po niemiecku.
        Baza zrodlowa zostaje tylko dla rynku, ktory nie ma jeszcze pliku.
        """
        wlasny = ROOT / kraj.get("pliki", {}).get(nazwa, "")
        sciezka = wlasny if wlasny.is_file() else ROOT / zrodlo["pliki"][nazwa]
        return json.loads(sciezka.read_text(encoding="utf-8")), sciezka

    aspekty, skad_aspekty = podstawa("aspekty")
    tlumaczenia, skad_tlumaczenia = podstawa("tlumaczenia")

    licznik: dict[str, int] = {}
    # Wiersze z arkusza, ktorych odpowiednik zniknal z konfiguracji zrodlowej.
    przedawnione: list[tuple[str, str, str]] = []
    # Sekcje, ktore w pliku docelowym sa listami - skladamy je z wierszy
    # po calej petli, zeby zachowac kolejnosc i nie dopisywac duplikatow.
    listy: dict[str, list] = {}

    def policz(gdzie: str) -> None:
        licznik[gdzie] = licznik.get(gdzie, 0) + 1

    for w in wiersze:
        sekcja, klucz, wartosc = w["sekcja"], w["klucz"], kolumna_celu(w).strip()
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
            # Sekcja profilu ma cztery ksztalty i KAZDY trafia gdzie indziej.
            # Wczesniej kod patrzyl tylko na nazwe profilu i wrzucal wszystko
            # na jego najwyzszy poziom - 38 z 55 wierszy ladowalo w zlym
            # miejscu, tworzac klucze, ktorych generator nigdy nie czyta.
            profil = kraj.setdefault("profile_kraju", {}).setdefault(czesci[1], {})
            reszta = czesci[2:]
            if not reszta:                                  # profil/<p>
                profil[klucz] = wartosc
            elif reszta == ["cechy_domyslne"]:              # LISTA, skladana nizej
                listy[sekcja] = listy.get(sekcja, [])
                listy[sekcja].append(wartosc)
            elif reszta == ["formfaktor"]:                  # profil/<p>/formfaktor
                profil.setdefault("formfaktor", {}).setdefault("mapa", {})[klucz] = wartosc
            elif reszta[0] == "nadpisz" and len(reszta) == 2:
                # Nadpisania opisuja kondycje towaru, wiec klamstwo w tym
                # miejscu trafia wprost do oferty. Wpis, ktorego nie ma po
                # stronie zrodlowej, pochodzi ze starego arkusza - ktos go z
                # konfiguracji usunal wlasnie dlatego, ze byl nieprawdziwy.
                # Nie wnosimy go z powrotem; wypisujemy na koncu do decyzji.
                wzor = (settings.get("profile_produktu", {}).get(czesci[1], {})
                        or {})
                wzor = (wzor.get("nadpisz_tlumaczenia", {}) if isinstance(wzor, dict)
                        else {}).get(reszta[1], {})
                if klucz not in wzor:
                    przedawnione.append((sekcja, klucz, wartosc))
                    continue
                (profil.setdefault("nadpisz_tlumaczenia", {})
                       .setdefault(reszta[1], {}))[klucz] = wartosc
            else:
                raise SystemExit(f"nieznany ksztalt sekcji profilu: {sekcja}")
            policz(f"kraje/{kod}.json")
        elif czesci[0] == "translations":
            cel = tlumaczenia
            for czesc in czesci[1:]:
                cel = cel.setdefault(czesc, {})
            cel[klucz] = wartosc
            policz("translations.json")
        elif czesci[0] == "aspects":
            # '_dozwolone' i 'wartosci' to LISTY, nie slowniki. Eksport wypisuje
            # je po jednym elemencie na wiersz, wiec przypisanie cel[klucz]
            # wywracalo sie na "list indices must be integers".
            cel = aspekty.setdefault(czesci[1], {})
            lista = False
            for czesc in czesci[2:]:
                if isinstance(cel, dict) and isinstance(cel.get(czesc), list):
                    listy.setdefault(sekcja, []).append(wartosc)
                    lista = True
                    break
                cel = cel.setdefault(czesc, {})
            if not lista:
                cel[klucz] = wartosc
            policz("aspects.json")

    for sekcja, elementy in listy.items():
        czesci = sekcja.split("/")
        if czesci[0] == "profil":
            kraj["profile_kraju"][czesci[1]][czesci[2]] = list(dict.fromkeys(elementy))
        else:
            cel = aspekty[czesci[1]]
            for czesc in czesci[2:-1]:
                cel = cel[czesc]
            cel[czesci[-1]] = list(dict.fromkeys(elementy))

    katalog = ROOT / "config" / "kraje" / kod
    katalog.mkdir(parents=True, exist_ok=True)
    (ROOT / "config" / "kraje" / f"{kod}.json").write_text(
        json.dumps(kraj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (katalog / "aspects.json").write_text(
        json.dumps(aspekty, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (katalog / "translations.json").write_text(
        json.dumps(tlumaczenia, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"wczytano {len(wiersze) - len(przedawnione)} z {len(wiersze)} tlumaczen:")
    for gdzie, ile in sorted(licznik.items()):
        print(f"  {gdzie}: {ile}")
    print(f"  baza aspektow: {skad_aspekty.relative_to(ROOT).as_posix()}")
    print(f"  baza tlumaczen: {skad_tlumaczenia.relative_to(ROOT).as_posix()}")
    if przedawnione:
        print(f"\nPOMINIETO {len(przedawnione)} wierszy - nie ma ich "
              f"w kraje/{ZRODLO} ani w settings.json, czyli arkusz jest starszy "
              "niz konfiguracja. Jesli maja wrocic, dopisz je najpierw u zrodla:")
        for sekcja, klucz, wartosc in przedawnione:
            print(f"  [{sekcja}] {klucz} -> {wartosc[:50]}")
    print(f"\nSzablony opisu skopiuj osobno do templates/{kod}/ - to cale pliki HTML.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
