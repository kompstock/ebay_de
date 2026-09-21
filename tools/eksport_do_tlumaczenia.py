#!/usr/bin/env python3
"""Zbiera KAZDY napis, ktory kupujacy widzi w ofercie, do jednego pliku CSV.

PO CO
Dolozenie rynku to nie praca programistyczna, tylko jezykowa - ale napisy leza
w czterech roznych plikach plus szablonach HTML. Bez tego zestawienia tlumacz
musialby ich szukac po repozytorium i predzej czy pozniej ktoregos by nie
znalazl. Wtedy oferta wychodzi po niemiecku na wloskim rynku i nikt tego nie
zauwaza, bo eBay plik przyjmuje.

CZEGO TU NIE MA
Szablonow opisu (templates/<kraj>/*.html) - to cale strony HTML, nie pojedyncze
napisy, wiec tlumaczy sie je jako pliki. Skrypt wypisuje je na koncu z lista.

    python3 tools/eksport_do_tlumaczenia.py it   ->   do-tlumaczenia-it.csv

Kolumna 'po_wlosku' zostaje pusta do wypelnienia. Juz wypelnione wartosci
z pliku kraju sa przepisywane, wiec skrypt mozna odpalac wielokrotnie - nie
kasuje tego, co ktos juz przetlumaczyl.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZRODLO = "de"


def load(sciezka: Path) -> dict:
    return json.loads(sciezka.read_text(encoding="utf-8"))


# Ktory aspekt eBaya odpowiada ktoremu blokowi w aspects.json. Potrzebne po to,
# zeby do kazdego wiersza dolozyc LISTE dozwolonych wartosci z docelowego rynku -
# inaczej tlumacz musialby jej szukac w szablonie eBaya i zgadywac.
BLOK_DO_ASPEKTU = {
    "festplattentyp": "typ_dysku",
    "grafikprozessortyp": "typ_gpu",
    "betriebssystem": "system",
    "besonderheiten": "cechy",
    "produktart": "rodzaj_produktu",
    "farbe": "kolor",
}
# Bloki, w ktorych wartosci siedza pietro nizej ('farbe' ma 'domyslnie' i mape
# 'z_modelu'). Bez tego kolory nie trafialy do arkusza i wychodzily po niemiecku.
ZAGNIEZDZONE = {"farbe": ("domyslnie", "z_modelu")}

# Listy wartosci, ktore kupujacy widzi, a ktore nie sa zwyklymi parami klucz->napis.
# Kazda pozycja: (blok, sciezka w bloku, klucz aspektu, opis kontekstu).
LISTY = (
    ("besonderheiten", ("_dozwolone",), "cechy",
     "lista cech, ktore wolno wypuscic - wartosc spoza niej jest po cichu odrzucana"),
    ("passend_fuer", ("wartosci",), "przeznaczenie",
     "stala wartosc aspektu, ta sama w kazdej ofercie"),
    ("konnektivitaet", ("_dozwolone",), "zlacza",
     "lista wartosci, ktore wolno wypuscic do aspektu zlacz"),
)

# Bloki aspects.json, ktorych NIE tlumaczymy - kazdy z powodem. Test pilnuje, zeby
# nowy blok trafil tu albo do eksportu, a nie zostal po cichu po niemiecku.
NIE_TLUMACZYMY = {
    "serie": "nazwy serii producenta (ThinkPad, Latitude) - marki sie nie tlumaczy",
    "erscheinungsjahr": "roczniki modeli, same liczby",
    "condition_id": "numery stanow eBaya, nie tekst",
    "grafikprozessor_alias": "nazwy ukladow graficznych, miedzynarodowe",
    "klawiatura_czesci": "nie trafia do oferty - kod czyta stad tylko podswietlenie",
    "sufiks_tytulu": "skroty systemu w tytule (Win11 Pro) - miedzynarodowe",
    "herstellergarantie": "pole zostaje puste, gwarancja sprzedawcy idzie w opisie",
    "betriebssystem": "nazwy systemow, miedzynarodowe",
    "grafikprozessortyp": "eksportowane osobno przez BLOK_DO_ASPEKTU",
    "festplattentyp": "eksportowane osobno przez BLOK_DO_ASPEKTU",
    "farbe": "eksportowane osobno przez ZAGNIEZDZONE",
}


def dozwolone(kraj_cel: dict, vocab: dict, klucz_aspektu: str) -> str:
    """Wartosci, ktore eBay danego rynku przyjmie dla tego aspektu.

    Zbieramy z wszystkich kategorii naraz - tlumacz i tak wybiera jedna wartosc,
    a rozbijanie tego na kategorie tylko zacmilo by obraz.
    """
    kolumna = (kraj_cel.get("kolumny") or {}).get(klucz_aspektu, "")
    nazwa = kolumna.lstrip("*").removeprefix("C:")
    if not nazwa:
        return ""
    wartosci: list[str] = []
    for kategoria in (vocab.get("kategorie") or {}).values():
        for v in (kategoria.get("aspekty") or {}).get(nazwa, []):
            if v not in wartosci:
                wartosci.append(v)
    return " | ".join(wartosci)


def gotowe(dane: dict, sciezka: list[str], klucz: str):
    """Wartosc juz przetlumaczona, jesli kraj docelowy ja ma."""
    cel = dane
    for czesc in sciezka:
        if not isinstance(cel, dict):
            return None
        cel = cel.get(czesc, {})
    return cel.get(klucz) if isinstance(cel, dict) else None


def wiersze(kraj_zrodlo: dict, kraj_cel: dict, settings: dict, tlumaczenia: dict,
            aspekty: dict, vocab_cel: dict, tlumaczenia_cel: dict = None,
            aspekty_cel: dict = None) -> list[dict]:
    tlumaczenia_cel = tlumaczenia_cel or {}
    aspekty_cel = aspekty_cel or {}
    out: list[dict] = []

    def dodaj(sekcja: str, klucz: str, zrodlo, cel, kontekst: str = "",
              aspekt: str = "") -> None:
        if not isinstance(zrodlo, str) or not zrodlo.strip():
            return
        out.append({"sekcja": sekcja, "klucz": klucz, "kontekst": kontekst,
                    "po_niemiecku": zrodlo,
                    "po_wlosku": cel if isinstance(cel, str) else "",
                    "wybierz_z_listy": dozwolone(kraj_cel, vocab_cel, aspekt) if aspekt else ""})

    for klucz, wartosc in kraj_zrodlo.get("etykiety_opisu", {}).items():
        dodaj("etykiety_opisu", klucz, wartosc,
              kraj_cel.get("etykiety_opisu", {}).get(klucz),
              f"lewa kolumna tabelki w opisie oferty, np. \"{wartosc}: Intel Core i5\"")
    for klucz, wartosc in kraj_zrodlo.get("slowa", {}).items():
        dodaj("slowa", klucz, wartosc, kraj_cel.get("slowa", {}).get(klucz),
              "krotki napis, ktory generator wstawia sam - w opisie albo w polu oferty")
    dodaj("faq_windows", "faq_windows", kraj_zrodlo.get("faq_windows"),
          kraj_cel.get("faq_windows"),
          "gotowy blok HTML na koncu opisu, widoczny tylko przy Windowsie - "
          "tlumaczymy tekst, znacznikow nie ruszamy")

    # Jezykowe pola profili: okres gwarancji slowami, rodzaj produktu ze slownika
    # eBaya i zdanie o klawiaturze.
    cel_profile = kraj_cel.get("profile_kraju", {})
    for typ, profil in settings.get("profile_produktu", {}).items():
        if not isinstance(profil, dict):
            continue
        # Cechy zakladane z gory siedza w settings.json, czyli w pliku wspolnym -
        # dlatego przez dlugi czas nie trafialy do arkusza i szly na wloski rynek
        # po niemiecku. Dopasowujemy po pozycji na liscie, bo to lista, nie mapa.
        cel_cechy = cel_profile.get(typ, {}).get("cechy_domyslne") or []
        for i, cecha in enumerate(profil.get("cechy_domyslne", [])):
            dodaj(f"profil/{typ}/cechy_domyslne", cecha, cecha,
                  cel_cechy[i] if i < len(cel_cechy) else None,
                  "cecha zakladana z gory dla tego typu towaru - pole oferty",
                  aspekt="cechy")
        # Nadpisania slownika per profil - nowy zestaw opisuje kondycje inaczej
        # niz poleasingowy. Tez ida do oferty, wiec tez wymagaja tlumaczenia.
        for pole_feedu, mapa in (profil.get("nadpisz_tlumaczenia") or {}).items():
            if pole_feedu.startswith("_") or not isinstance(mapa, dict):
                continue
            cel_mapa = ((cel_profile.get(typ, {}).get("nadpisz_tlumaczenia") or {})
                        .get(pole_feedu) or {})
            for wartosc_feedu, napis in mapa.items():
                dodaj(f"profil/{typ}/nadpisz/{pole_feedu}", wartosc_feedu, napis,
                      cel_mapa.get(wartosc_feedu),
                      f"opis w ofercie, gdy feed podaje \"{wartosc_feedu}\"")
        for pole in ("gwarancja", "produktart", "tastatur_layout", "dopisek_tytulu"):
            dodaj(f"profil/{typ}", pole, profil.get(pole),
                  cel_profile.get(typ, {}).get(pole),
                  "rodzaj produktu - pole oferty; wybierz jedna z kolumny obok"
                  if pole == "produktart" else
                  "nieskracalny ogon TYTULU - liczy sie do limitu 80 znakow"
                  if pole == "dopisek_tytulu" else "zdanie widoczne w opisie oferty",
                  aspekt=BLOK_DO_ASPEKTU.get(pole, ""))

    for klucz in ("sekcja_business_notebook",):
        dodaj("settings", klucz, settings.get(klucz), kraj_cel.get(klucz),
              "sekcja HTML w opisie")
    for blok in ("zdania_wiodace", "zdania_wiodace_gaming"):
        for klucz, wartosc in (settings.get(blok) or {}).items():
            if not klucz.startswith("_"):
                dodaj(blok, klucz, wartosc, (kraj_cel.get(blok) or {}).get(klucz),
                      "zdanie pod kafelkami w opisie")

    for blok, zawartosc in tlumaczenia.items():
        if blok.startswith("_") or not isinstance(zawartosc, dict):
            continue
        for klucz, wartosc in zawartosc.items():
            if isinstance(wartosc, dict):
                for pod, v in wartosc.items():
                    dodaj(f"translations/{blok}/{klucz}", pod, v,
                          gotowe(tlumaczenia_cel, [blok, klucz], pod),
                          f"tresc z opisu; w feedzie sklepowym stoi tam \"{pod}\"")
            else:
                dodaj(f"translations/{blok}", klucz, wartosc,
                      gotowe(tlumaczenia_cel, [blok], klucz),
                      f"tresc z opisu; w feedzie sklepowym stoi tam \"{klucz}\"")

    # Etykiety zlacz - ida WPROST do sekcji zlacz w opisie oferty.
    cel_reguly = (aspekty_cel.get("porty_reguly") or {}).get("reguly", [])
    for i, regula in enumerate((aspekty.get("porty_reguly") or {}).get("reguly", [])):
        if isinstance(regula, list) and len(regula) == 2:
            # Reguly stoja w tej samej kolejnosci w obu krajach - podmienialismy
            # w miejscu, wiec dopasowanie po pozycji jest pewniejsze niz po tresci.
            juz = (cel_reguly[i][1] if i < len(cel_reguly)
                   and isinstance(cel_reguly[i], list) and len(cel_reguly[i]) == 2 else None)
            dodaj("porty_reguly", regula[1], regula[1], juz,
                  "nazwa zlacza widoczna w opisie oferty, w sekcji zlacz")

    for blok, sciezka, klucz_aspektu, kontekst in LISTY:
        def wejdz(zrodlo):
            cel = zrodlo.get(blok, {})
            for czesc in sciezka:
                cel = cel.get(czesc, []) if isinstance(cel, dict) else []
            return cel if isinstance(cel, list) else []
        lista_cel = wejdz(aspekty_cel)
        for i, wartosc in enumerate(wejdz(aspekty)):
            dodaj(f"aspects/{blok}/{sciezka[0]}", wartosc, wartosc,
                  lista_cel[i] if i < len(lista_cel) else None,
                  kontekst, aspekt=klucz_aspektu)

    for blok, klucz_aspektu in BLOK_DO_ASPEKTU.items():
        for klucz, wartosc in (aspekty.get(blok) or {}).items():
            # '_about' i '_uwaga_*' to dokumentacja pliku, nie wartosci oferty.
            # Klucze '_webcam', '_touchscreen' i '_podswietlenie' juz tak - stad
            # nie da sie tego zalatwic samym przedrostkiem podkreslenia.
            if klucz.startswith(("_about", "_uwaga", "_zasada")):
                continue
            if isinstance(wartosc, str):
                dodaj(f"aspects/{blok}", klucz, wartosc,
                      gotowe(aspekty_cel, [blok], klucz),
                      "pole oferty, nie opis - wybierz jedna wartosc z kolumny obok",
                      aspekt=klucz_aspektu)
            elif isinstance(wartosc, dict) and klucz in ZAGNIEZDZONE.get(blok, ()):
                # Kolory siedza pietro nizej: 'domyslnie' plus mapa 'z_modelu'.
                # Bez tego wychodzily po niemiecku i nikt ich nie widzial w arkuszu.
                for pod, v in wartosc.items():
                    if isinstance(v, str) and not pod.startswith("_"):
                        dodaj(f"aspects/{blok}/{klucz}", pod, v,
                              gotowe(aspekty_cel, [blok, klucz], pod),
                              "pole oferty, nie opis - wybierz jedna wartosc z kolumny obok",
                              aspekt=klucz_aspektu)
    return out


def main() -> int:
    kod = (sys.argv[1] if len(sys.argv) > 1 else "it").lower()
    kraj_zrodlo = load(ROOT / "config" / "kraje" / f"{ZRODLO}.json")
    sciezka_cel = ROOT / "config" / "kraje" / f"{kod}.json"
    kraj_cel = load(sciezka_cel) if sciezka_cel.is_file() else {}
    settings = load(ROOT / "config" / "settings.json")
    pliki = kraj_zrodlo["pliki"]
    # Slownik DOCELOWEGO rynku - z niego biora sie listy dozwolonych wartosci.
    sciezka_vocab = ROOT / (kraj_cel.get("pliki", {}).get("slownik", ""))
    vocab_cel = load(sciezka_vocab) if sciezka_vocab.is_file() else {}
    def cel(nazwa: str) -> dict:
        sciezka = ROOT / (kraj_cel.get("pliki", {}).get(nazwa, ""))
        return load(sciezka) if sciezka.is_file() else {}

    dane = wiersze(kraj_zrodlo, kraj_cel, settings,
                   load(ROOT / pliki["tlumaczenia"]), load(ROOT / pliki["aspekty"]),
                   vocab_cel, cel("tlumaczenia"), cel("aspekty"))

    out = ROOT / f"do-tlumaczenia-{kod}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as uchwyt:
        writer = csv.DictWriter(
            uchwyt, fieldnames=["sekcja", "klucz", "kontekst", "po_niemiecku",
                                "po_wlosku", "wybierz_z_listy"],
            delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(dane)

    puste = sum(1 for w in dane if not w["po_wlosku"])
    print(f"{out.name}: {len(dane)} napisow, do wypelnienia {puste}")
    katalog = ROOT / kraj_zrodlo.get("katalog_szablonow", "templates")
    szablony = sorted(p.name for p in katalog.glob("*.html"))
    print(f"\nOsobno, jako cale pliki - skopiuj do templates/{kod}/ i przetlumacz tresc:")
    for nazwa in szablony:
        print(f"  {katalog.name}/{nazwa}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
