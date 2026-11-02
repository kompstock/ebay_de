#!/usr/bin/env python3
"""Ta sama rzecz wystawiona dwa razy: wstrzymujemy nowa, aktywnej nie ruszamy.

CO UZNAJEMY ZA DUPLIKAT
Identyczna specyfikacja ORAZ identyczna cena w PLN. Sama specyfikacja nie
wystarcza: sprzedawca ma po kilka ofert tego samego modelu w roznych cenach
i to sa rozne produkty (inny stan, inna partia). Dopiero ta sama cena co do
grosza mowi, ze to ten sam towar wystawiony dwa razy.

Specyfikacja w odcisku jest WASKA i taka ma zostac. Kazde dodatkowe pole to
kolejna szansa, ze ten sam laptop opisany po dwoch stronach rozjedzie sie na
jednym znaku i duplikat przeciecze na eBay. Czego w odcisku nie ma i dlaczego -
patrz POLA_ODCISKU.

BLIZNIAK GWARANCYJNY
Pole wariantu GW24 do odcisku nie wchodzi, wiec blizniak ma odcisk oryginalu.
Przed wstrzymaniem wlasnego oryginalu chroni go zasada "tylko miedzy zrodlami":
blizniak dziedziczy zrodlo po ofercie, z ktorej powstal, a dwoch ofert z tego
samego zrodla nigdy nie porownujemy. Zostaje jeden uklad, w ktorym to kosztuje:
GW24 po jednej stronie, zwykla oferta po drugiej - wtedy zwykla zostanie
wstrzymana, choc rozni je 24 miesiace serwisu. Swiadoma decyzja wlasciciela:
w pomiarze na zywym feedzie to pole nie zmienilo ani jednej decyzji, a kosztowalo
kolejna szanse na przeciek. Gdyby takich par zaczelo przybywac, pole wraca.

SKAD SIE BIORA
Z dwoch zrodel naraz - ten sam laptop idzie do Shopera i na Allegro, a stamtad
do nas. Porownujemy WYLACZNIE miedzy zrodlami: Shoper z Allegro i Allegro
z Shoperem. Dwie oferty z tego samego zrodla nigdy nie sa duplikatem - to dwie
rozne partie tego samego modelu, ktore sprzedawca swiadomie wystawil osobno,
a sklejenie ich zgubiloby towar.

CO Z TYM ROBIMY
Dzialamy WYLACZNIE na wejsciu, przy wystawianiu. Oferta, ktora ma juz swojego
blizniaka, po prostu nie idzie na eBay. Aktywnej aukcji nie zdejmujemy nigdy,
nawet gdy duplikat jest oczywisty: aukcja z historia, obserwujacymi i pozycja
w wyszukiwarce jest warta wiecej niz czystosc katalogu, a blad w odcisku
kosztowalby wtedy zywa oferte. Gdy duplikat trafil na eBay obiema polowami,
zostawiamy obie i pokazujemy je w raporcie - do recznej decyzji.

Stad kandydatem do wstrzymania jest zawsze oferta jeszcze niewystawiona.
Wybor miedzy dwiema takimi: zostaje Shoper, bo ma prawdziwe dane o kondycji.
"""

from __future__ import annotations

import re
from collections import defaultdict


# Pola, z ktorych powstaje odcisk - plus procesor, ktory feed podaje w kilku
# miejscach naraz i wyciaga go zrodlo_procesora().
#
# Czego tu swiadomie NIE MA, zmierzone na zywym feedzie (1026 ofert, 2026-11-02):
#   'Typ dysku'        nie rozdzielil ani jednej pary miedzy zrodlami. Przy
#                      zgodnym modelu, RAM-ie i dysku zgadzal sie zawsze tez on.
#   'Przekatna ekranu' rozdzielala cztery grupy i w kazdej stal ten sam sprzet:
#                      "14,1 Zoll" kontra "14 Zoll", "15 Zoll" kontra "15,6 Zoll".
#                      Pole nie rozroznia produktow, przenosi tylko literowki -
#                      a kazda literowka to duplikat, ktory przeszedl na eBay.
POLA_ODCISKU = ("Producent", "Model", "Ilość pamięci RAM", "Dysk")

# Ta sama firma pod dwiema nazwami: Dynabook to dawna dywizja PC Toshiby, wiec
# "Toshiba Tecra A50-J" i "Dynabook Tecra A50-J" to jeden laptop. Alias dziala
# TYLKO w odcisku - brand_name() zostaje nietkniete, bo to ono wpisuje marke do
# aukcji, a tam ma stac to, co podaje feed.
ALIASY_MAREK = {"dynabook": "toshiba"}

_GENERACJA = re.compile(r"\bgen\.?\s*(\d+)")
_WOKOL_MYSLNIKA = re.compile(r"\s*-\s*")


def _marka(attrs: dict, funkcje: dict) -> str:
    marka = funkcje["norm"](funkcje["brand_name"](attrs.get("Producent", "")))
    return ALIASY_MAREK.get(marka, marka)


def _model(attrs: dict, funkcje: dict) -> str:
    """Model w zapisie, ktory nie zalezy od tego, kto go wpisywal.

    Prostujemy WYLACZNIE zapis: generacje ("Gen1", "Gen.1", "Gen. 2" to jedno
    i to samo), kropke na koncu, odstepy wokol myslnika. Zadne slowo nie znika
    poza nazwa producenta z poczatku - jedno zrodlo pisze "Lenovo ThinkPad T480",
    drugie samo "ThinkPad T480".

    Dopasowania po zawieraniu sie tu NIE MA i nie ma go celowo. "ZBook 15 G7"
    kontra "ZBook Firefly 15 G7" albo "Toughpad FZ-G1" kontra "FZ-G1 Mk3" to dla
    automatu ten sam uklad co "845 G7" kontra "EliteBook 845 G7" - a w pierwszych
    dwoch przypadkach to inna maszyna. Przy wlaczonym 'odrzucaj' pomylka nie
    konczy sie brzydkim raportem, tylko towarem, ktory nigdy nie trafi na eBay,
    wiec takie pary zostaja rozne i decyzje o nich podejmuje czlowiek.
    """
    model = funkcje["norm"](attrs.get("Model", ""))
    pierwsze, _, reszta = model.partition(" ")
    if reszta and pierwsze in funkcje["marki"]:
        model = reszta
    model = _GENERACJA.sub(r"gen \1", model)
    model = _WOKOL_MYSLNIKA.sub("-", model)
    return model.strip().rstrip(".").strip()


def odcisk(attrs: dict, funkcje: dict) -> str:
    """Znormalizowana specyfikacja. Pusty string = za malo danych, nie porownujemy.

    Marka, model i procesor musza byc znane. Bez tego odcisk zlepilby ze soba
    przypadkowe oferty o podobnych parametrach - a to gorsze niz przeoczenie
    duplikatu, bo skonczyloby sie wstrzymaniem zdrowego towaru.
    """
    norm = funkcje["norm"]
    marka = _marka(attrs, funkcje)
    model = _model(attrs, funkcje)
    kandydaci = funkcje["cpu_candidates"](funkcje["zrodlo_procesora"](attrs))
    cpu = norm(kandydaci[0]) if kandydaci else ""
    if not (marka and model and cpu):
        return ""
    czesci = [marka, model, cpu]
    for pole in ("Ilość pamięci RAM", "Dysk"):
        czesci.append(norm(funkcje["clean_capacity"](attrs.get(pole, ""))))
    return "|".join(czesci)


def _rozstrzygnij(grupa: list[dict], preferowane_zrodlo: str) -> tuple[list[dict], list[dict]]:
    """(wstrzymane, kolizje_aktywnych) dla jednej grupy. Reszta idzie normalnie.

    Ile sztuk naprawde stoi w magazynie: tyle, ile liczy najliczniejsze zrodlo
    w grupie. Przy trzech ofertach w Shoperze i dwoch na Allegro sa trzy sztuki
    - dwie widziane podwojnie i jedna tylko w sklepie. Nadwyzka nie ma po
    drugiej stronie odpowiednika, wiec nie jest niczyim duplikatem i zostaje.

    Wstrzymac wolno tylko oferte jeszcze niewystawiona. Gdy niewystawionych
    jest mniej, niz wynosi nadmiar, reszta idzie do 'kolizje_aktywnych' - tam
    duplikat stoi juz na eBayu obiema polowami i decyzje podejmuje czlowiek.

    Miedzy dwiema niewystawionymi zostaje Shoper: ma prawdziwe dane o kondycji,
    Allegro wstawia domyslne. Dalej po SKU, zeby wynik byl powtarzalny.
    """
    wg_zrodla: dict[str, list[dict]] = defaultdict(list)
    for oferta in grupa:
        wg_zrodla[oferta["zrodlo"]].append(oferta)
    nadmiar = len(grupa) - max(len(v) for v in wg_zrodla.values())
    if nadmiar <= 0:
        return [], []
    kandydaci = sorted((o for o in grupa if not o["aktywna"]),
                       key=lambda o: (o["zrodlo"] == preferowane_zrodlo, o["sku"]))
    wstrzymane = kandydaci[:nadmiar]
    if len(wstrzymane) == nadmiar:
        return wstrzymane, []
    return wstrzymane, [o for o in grupa if o["aktywna"]]


def znajdz(produkty, aktywne: set, settings: dict, funkcje: dict) -> dict:
    """Grupy duplikatow i rozstrzygniecie. Sam niczego z feedu nie usuwa.

    Zwraca 'wstrzymane_sku' - liste SKU, ktorych nie wystawiamy. Odsiewa je
    generate.py, i tylko gdy 'odrzucaj' jest wlaczone. Na liscie nigdy nie ma
    SKU aktywnego na eBayu, wiec odsianie nie zabiera zadnej aukcji: oferta
    nieaktywna nie podlega tez zerowaniu w trybie 'aktualizacja'.
    """
    cfg = settings.get("duplikaty", {})
    if not cfg.get("enabled"):
        return {}

    pole_zrodla = cfg.get("pole_zrodla", "Źródło")
    domyslne_zrodlo = cfg.get("domyslne_zrodlo", "Shoper")
    preferowane = cfg.get("preferowane_zrodlo", domyslne_zrodlo)
    odrzucaj = bool(cfg.get("odrzucaj"))

    grupy: dict[tuple, list[dict]] = defaultdict(list)
    bez_odcisku = 0
    for offer, attrs in produkty:
        klucz_spec = odcisk(attrs, funkcje)
        if not klucz_spec:
            bez_odcisku += 1
            continue
        try:
            cena = round(float((offer.get("price") or "0").replace(",", ".")), 2)
        except ValueError:
            continue
        sku = attrs.get("SKU", "")
        grupy[(klucz_spec, cena)].append({
            "sku": sku,
            "zrodlo": attrs.get(pole_zrodla) or domyslne_zrodlo,
            "stan": int(float(offer.get("stock", "0") or 0)),
            "cena": cena,
            "aktywna": sku in aktywne,
            "spec": klucz_spec,
        })

    # Do raportu ida tylko grupy siegajace przez co najmniej dwa zrodla.
    # Powtorzenie w obrebie jednego zrodla to osobna partia, nie duplikat.
    pary, w_jednym_zrodle = [], 0
    for grupa in grupy.values():
        if len(grupa) < 2:
            continue
        if len({o["zrodlo"] for o in grupa}) < 2:
            w_jednym_zrodle += 1
            continue
        pary.append(sorted(grupa, key=lambda o: o["sku"]))
    pary.sort(key=lambda g: (-len(g), g[0]["spec"]))

    wiersze, wstrzymane_sku, grup_z_kolizja = [], [], 0
    for grupa in pary:
        wstrzymane, kolizje = _rozstrzygnij(grupa, preferowane)
        id_wstrzymanych = {id(o) for o in wstrzymane}
        id_kolizji = {id(o) for o in kolizje}
        grup_z_kolizja += int(bool(kolizje))
        # "Z czym koliduje": oferta z drugiego zrodla, ktora idzie na eBay
        # zamiast tej wstrzymanej. Kolejka, zeby przy wiekszej grupie kazda
        # wstrzymana wskazywala inna - inaczej raport sugerowalby, ze trzy
        # sztuki sa duplikatem jednej.
        kolejka = sorted((o for o in grupa if id(o) not in id_wstrzymanych),
                         key=lambda o: (not o["aktywna"], o["zrodlo"] == preferowane, o["sku"]))
        for oferta in grupa:
            if id(oferta) in id_wstrzymanych:
                wstrzymane_sku.append(oferta["sku"])
                inne = [o for o in kolejka if o["zrodlo"] != oferta["zrodlo"]]
                partner = inne[0] if inne else (kolejka[0] if kolejka else None)
                if partner is not None and len(kolejka) > 1:
                    kolejka.remove(partner)
                decyzja = "NIE WYSTAWIAMY"
                koliduje_z = partner["sku"] if partner else ""
            elif id(oferta) in id_kolizji:
                decyzja = "zostaje - kolizja aktywnych"
                koliduje_z = " ".join(o["sku"] for o in kolizje if o is not oferta)
            else:
                decyzja = "zostaje"
                koliduje_z = ""
            wiersze.append({
                "specyfikacja": oferta["spec"].replace("|", " | "),
                "cena_pln": f"{oferta['cena']:.2f}",
                "SKU": oferta["sku"],
                "zrodlo": oferta["zrodlo"],
                "stan": oferta["stan"],
                "aktywna_na_ebay": "tak" if oferta["aktywna"] else "nie",
                "decyzja": decyzja,
                "koliduje_z": koliduje_z,
            })

    return {
        "grup": len(pary),
        "ofert": sum(len(g) for g in pary),
        "wstrzymanych": len(wstrzymane_sku),
        "odrzucanie": "wlaczone" if odrzucaj else "sam raport, feed nietkniety",
        # Duplikat, ktory obiema polowami stoi juz na eBayu. Nie ruszamy go
        # sami - to jedyne miejsce, gdzie potrzebna jest reczna decyzja.
        "kolizje_aktywnych": grup_z_kolizja,
        "bez_odcisku": bez_odcisku,
        "powtorzenia_w_jednym_zrodle": w_jednym_zrodle,
        "wstrzymane_sku": sorted(wstrzymane_sku) if odrzucaj else [],
        "wiersze": wiersze,
    }


KOLUMNY = ("specyfikacja", "cena_pln", "SKU", "zrodlo", "stan",
           "aktywna_na_ebay", "decyzja", "koliduje_z")
