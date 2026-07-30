#!/usr/bin/env python3
"""Generuje CSV eBay DE z feedu XML KOMPRE.

Zasady egzekwowane twardo:
  1. Brak kompletnych danych GPSR (producent + osoba odpowiedzialna w UE)
     -> produkt NIE trafia do CSV.
  2. Wartosc opisowa bez dokladnego tlumaczenia w slowniku
     -> produkt NIE trafia do CSV, laduje w kolejce review.
  3. Wartosc aspektu spoza zamknietego slownika
     -> pole zostaje puste, wartosc laduje w kolejce review.
  4. Polskie slowo lub znak diakrytyczny w gotowym opisie
     -> produkt NIE trafia do CSV.
  5. Klasa stanu ([Klasa A-]) nie pojawia sie w opisie.
     Sluzy wylacznie do wyboru ConditionID.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_XML = (
    "Producent", "SKU", "Model", "Procesor", "Przekątna ekranu",
    "Ilość pamięci RAM", "Dysk",
)

REQUIRED_TRANSLATIONS = (
    "Kondycja sprzętu", "Stan obudowy", "Stan ekranu", "Bateria", "W zestawie",
)

POLISH_CHARS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
POLISH_WORDS = re.compile(
    r"(?<![\wäöüß])(i|oraz|lub|albo|we|ze|na|do|dla|od|po|przy|jest|są|"
    r"może|możliwy|możliwe|brak|bez|nowy|nowa|używany|używana|sprawny|"
    r"laptop|klawiatura|ekran|obudowa|zasilacz|sprzęt|typu|złącze|gniazdo|czytnik)(?![\wäöüß])",
    re.IGNORECASE,
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_bytes(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kompre-ebay-csv/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_nbp_rate(url: str) -> dict[str, Any]:
    payload = json.loads(fetch_bytes(url).decode("utf-8"))
    rate = payload["rates"][0]
    return {"currency": payload["currency"], "code": payload["code"],
            "table": payload["table"], "number": rate["no"],
            "effective_date": rate["effectiveDate"], "rate": float(rate["mid"])}


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.replace("\u0142", "l").replace("\u0141", "l")
    value = re.sub(r"\s*,\s*", ", ", value)   # "a,b" i "a, b" to ten sam klucz
    return re.sub(r"\s+", " ", value).strip().lower()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def offer_attrs(offer: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in offer.findall("./attrs/a"):
        name = (item.get("name") or "").strip()
        if name:
            out[name] = normalize_space(text(item))
    return out


class Review:
    def __init__(self) -> None:
        self.items: set[tuple[str, str, str]] = set()

    def add(self, kind: str, field: str, value: str) -> None:
        self.items.add((kind, field, value))

    def rows(self) -> list[tuple[str, str, str]]:
        return sorted(self.items)

    def top(self, kind: str, by: str = "value", limit: int = 20):
        idx = 1 if by == "field" else 2
        counter = Counter(item[idx] for item in self.items if item[0] == kind)
        return [{"wartosc": v, "produktow": n} for v, n in counter.most_common(limit)]


def suggest_translation(value: str, translations: dict) -> str:
    out = value
    for source, target in sorted(
        translations.get("_podpowiedzi", {}).items(), key=lambda i: len(i[0]), reverse=True
    ):
        out = re.sub(re.escape(source), target, out, flags=re.IGNORECASE)
    return normalize_space(out)


def translate_value(field: str, value: str, translations: dict, review: Review) -> str | None:
    if not value:
        return ""
    hit = translations["values"].get(norm(value))
    if hit:
        return hit
    review.add("tlumaczenie", field,
               f"{value}  ->  [propozycja] {suggest_translation(value, translations)}")
    return None


def has_polish_leak(text_value: str) -> str | None:
    """Sprawdza tylko tekst widoczny dla kupujacego.

    CSS musi wypasc PRZED sprawdzeniem - selektor '.kpx-key i{...}' zawiera
    samotne 'i' i bez tego wywala falszywy alarm na kazdym produkcie.
    """
    plain = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", text_value)
    plain = re.sub(r"(?s)<!--.*?-->", " ", plain)
    plain = html.unescape(re.sub(r"<[^>]+>", " ", plain))
    for char in plain:
        if char in POLISH_CHARS:
            return f"znak diakrytyczny {char!r}"
    match = POLISH_WORDS.search(plain)
    return f"polskie slowo {match.group(0)!r}" if match else None


def brand_name(value: str) -> str:
    known = {"lenovo": "Lenovo", "dell": "Dell", "hp": "HP", "fujitsu": "Fujitsu",
             "apple": "Apple", "acer": "Acer", "asus": "ASUS", "toshiba": "Toshiba",
             "microsoft": "Microsoft", "panasonic": "Panasonic", "samsung": "Samsung"}
    return known.get(norm(value), value.title())


def clean_capacity(value: str) -> str:
    return normalize_space(re.sub(r"(?i)(\d)\s*(GB|TB|MB)\b", r"\1 \2", value or ""))


def screen_size_de(value: str) -> str:
    number = re.search(r"\d+(?:[.,]\d+)?", value or "")
    return f"{number.group(0).replace('.', ',')} Zoll" if number else ""


def base_clock(value: str) -> str:
    match = re.search(r"(\d+[.,]\d+)", value or "")
    return f"{match.group(1).replace('.', ',')} GHz" if match else ""


def cpu_candidates(processor: str) -> list[str]:
    """Zwraca kandydatow od najbardziej do najmniej precyzyjnego.

    Feed uzywa formatu 'i5 - 1135G7, 8MB Cache, 11 gen.' albo 'Ryzen 5 PRO 4650U'.
    """
    p = normalize_space((processor or "").upper())
    out: list[str] = []

    ultra = re.search(r"ULTRA\s*([3579])\s*-?\s*(\d{3}[A-Z]*)", p)
    if ultra:
        out.append(f"Intel Core Ultra {ultra.group(1)} {ultra.group(2)}")
        return out

    ryzen = re.search(r"RYZEN\s*([3579])\s*(PRO)?\s*(\d{4}[A-Z]*)", p)
    if ryzen:
        pro = " PRO" if ryzen.group(2) else ""
        out.append(f"AMD Ryzen {ryzen.group(1)}{pro} {ryzen.group(3)}")
        out.append(f"AMD Ryzen {ryzen.group(1)}{pro} {ryzen.group(3)[0]}000 Series")
        out.append(f"AMD Ryzen {ryzen.group(1)}{pro}")
        return out

    core = re.search(r"\bI([3579])\s*-?\s*(\d{4,5}[A-Z]{0,2}\d?)", p)
    if core:
        rodzina, model = core.group(1), core.group(2)
        out.append(f"Intel Core i{rodzina}-{model}")
        cyfry = re.match(r"\d+", model).group(0)
        gen = re.search(r"(\d{1,2})\s*GEN", p)
        if gen:
            numer = int(gen.group(1))
        elif len(cyfry) == 5:
            numer = int(cyfry[:2])
        elif cyfry.startswith("1") and len(cyfry) == 4:
            numer = int(cyfry[:2])          # 1135G7 -> 11, 1235U -> 12
        else:
            numer = int(cyfry[0])           # 8365U -> 8
        out.append(f"Intel Core i{rodzina} {numer}. Gen")
        out.append(f"Intel Core i{rodzina}")
        return out

    celeron = re.search(r"CELERON\s*([A-Z]?\d{4}[A-Z]*)", p)
    if celeron:
        out += [f"Intel Celeron {celeron.group(1)}", "Intel Celeron"]
        return out

    silver = re.search(r"SILVER\s*([A-Z]?\d{4}[A-Z]*)", p)
    if silver:
        out += [f"Intel Pentium Silver {silver.group(1)}", "Intel Pentium Silver", "Intel Pentium"]
        return out

    if "PENTIUM" in p:
        out.append("Intel Pentium")
    return out


def processor_aspect(processor: str, series: str, review: Review) -> str:
    p = (processor or "").upper()
    for label, pattern in (("AMD Ryzen 3 PRO", r"RYZEN 3 PRO"), ("AMD Ryzen 5 PRO", r"RYZEN 5 PRO"),
                           ("AMD Ryzen 7 PRO", r"RYZEN 7 PRO"), ("AMD Ryzen 9 PRO", r"RYZEN 9 PRO"),
                           ("AMD Ryzen 3", r"RYZEN 3"), ("AMD Ryzen 5", r"RYZEN 5"),
                           ("AMD Ryzen 7", r"RYZEN 7"), ("AMD Ryzen 9", r"RYZEN 9")):
        if re.search(pattern, p):
            digits = re.search(r"\b([2-9])\d{3}", p)
            return f"{label} {digits.group(1)}000 Series" if digits else label
    intel = re.search(r"\bI([3579])[- ]?(\d{4,5})([A-Z]*)", p)
    if intel:
        exact = f"Intel Core i{intel.group(1)}-{intel.group(2)}{intel.group(3)}"
        num = intel.group(2)
        gen = num[:2] if len(num) == 5 else num[0]
        return f"{exact}|{int(gen)}. Gen"
    if "CELERON" in p:
        return "Intel Celeron"
    if "PENTIUM" in p:
        return "Intel Pentium"
    review.add("aspekt", "C:Prozessor", processor)
    return series or processor


def parse_ports_raw(value: str) -> list[tuple[int, str]]:
    """Feed uzywa trzech formatow zapisu zlacz. Obslugujemy wszystkie."""
    v = normalize_space(value or "")
    if not v:
        return []
    out: list[tuple[int, str]] = []

    if re.search(r"szt\.?", v, re.I):                       # "HDMI - 1 szt."
        for chunk in re.split(r"szt\.?", v, flags=re.I):
            m = re.match(r"^\s*(.*?)\s*-\s*(\d+)\s*$", chunk)
            if m and m.group(1).strip():
                out.append((int(m.group(2)), m.group(1).strip(" ,;.")))
        if out:
            return out

    if re.search(r"\d+\s*x\s", v, re.I):                    # "2 x USB-C"
        for count, label in re.findall(r"(\d+)\s*x\s*(.+?)(?=\s*\d+\s*x\s|$)", v):
            label = label.strip(" ,;.")
            if label:
                out.append((int(count), label))
        if out:
            return out

    for label in re.split(r"[,;]", v):                       # "USB 3.0, HDMI, LAN"
        label = label.strip(" ,;.")
        if label:
            out.append((1, label))
    return out


def port_label(label: str, aspects: dict, review: Review) -> str:
    """Etykieta portu po niemiecku. Brak reguly -> pusty string (port pomijany)."""
    czysty = re.sub(r"\s*\([^)]*\)", "", label).strip(" ,;.")
    hit = aspects["konnektivitaet"]["opis_etykiety"].get(norm(czysty))
    if hit:
        return hit
    for wzorzec, niemiecki in aspects["porty_reguly"]["reguly"]:
        if re.search(wzorzec, norm(czysty)) or re.search(wzorzec, czysty):
            return niemiecki
    review.add("port", "opis", label)
    return ""


def connectivity_aspect(ports, aspects: dict, review: Review) -> str:
    cfg = aspects["konnektivitaet"]
    allowed, mapping = cfg["_dozwolone"], cfg["z_portow"]
    found: list[str] = list(cfg["zawsze_dodaj"]["wartosci"])
    for _, label in ports:
        key = norm(label)
        hit = mapping.get(key)
        if not hit:
            for source, target in sorted(mapping.items(), key=lambda i: -len(i[0])):
                if key.startswith(source):
                    hit = target
                    break
        if not hit:
            if not any(key.startswith(k) for k in cfg["_pomijane"] if not k.startswith("_")):
                review.add("aspekt", "C:Konnektivität", label)
            continue
        if hit in allowed and hit not in found:
            found.append(hit)
    return "|".join(sorted(found))


def features_aspect(attrs: dict, ports, aspects: dict, podswietlenie: bool | None = None) -> str:
    out = ["Bluetooth", "Wi-Fi", "Eingebautes Mikrofon"]
    if attrs.get("Ekran dotykowy") == "Tak":
        out.append("Touchscreen")
    if any(norm(l).startswith(("rj-45", "rj45")) for _, l in ports):
        out.append("10/100 LAN Karte")
    if norm(attrs.get("Kamera", "")).startswith("tak"):
        out.append(aspects["besonderheiten"]["_webcam"])
    if podswietlenie:
        out.append("Hintergrundbeleuchtete Tastatur")
    allowed = aspects["besonderheiten"]["_dozwolone"]
    return "|".join(v for v in out if v in allowed)


def gpu_clean(value: str) -> list[str]:  # noqa: C901
    """Feed: 'Grafika Intel HD 630 + Radeon Pro 460 4GB' -> kandydaci dla eBaya."""
    v = normalize_space(value or "")
    v = re.split(r"\s*[+/]\s*", v)[0]                       # tylko uklad podstawowy
    v = re.sub(r"(?i)^grafika\s+", "", v)
    v = re.sub(r"(?i)\s*(dla procesorow|for)\s+.*$", "", v)
    v = re.sub(r"(?i)\s*\d+\s*GB.*$", "", v).strip()
    if re.match(r"(?i)^UHD\s+Intel", v):
        v = re.sub(r"(?i)^UHD\s+Intel\s*", "Intel UHD ", v)
    out = [v]
    if v.upper().startswith("RADEON"):
        out.insert(0, "AMD " + v)
    # eBay nazywa uklady Vega bez "RX" (wyjatek: Vega 10)
    bez_rx = re.sub(r"(?i)\bRX\s+(Vega\s+(?!10\b)\d+)", r"\1", out[0])
    if bez_rx != out[0]:
        out.insert(0, bez_rx)
    # "Intel UHD 620" -> "Intel UHD Graphics 620"
    for kandydat in list(out):
        if re.match(r"(?i)^Intel (UHD|HD)\b", kandydat) and "Graphics" not in kandydat:
            out.append(re.sub(r"(?i)^Intel (UHD|HD)\s*", lambda m: f"Intel {m.group(1).upper()} Graphics ", kandydat).strip())
        if re.match(r"(?i)^Intel Iris Xe$", kandydat.strip()):
            out.append("Intel Iris Xe Graphics")
    # ostatnia deska ratunku: sama rodzina
    u = out[0].upper()
    if u.startswith("INTEL UHD"):
        out.append("Intel UHD Graphics")
    elif u.startswith("INTEL HD"):
        out.append("Intel HD Graphics")
    elif "IRIS XE" in u:
        out.append("Intel Iris Xe Graphics")
    elif "RADEON" in u and "VEGA" not in u:
        out.append("AMD Radeon Graphics")
    return [x for x in dict.fromkeys(out) if x]


def series_aspect(model: str, aspects: dict) -> str:
    key = norm(model)
    for pattern, value in sorted(aspects["serie"]["wzorce"].items(), key=lambda i: -len(i[0])):
        if pattern in key:
            return value
    return ""


def year_aspect(model: str, aspects: dict) -> str:
    return aspects["erscheinungsjahr"]["modele"].get(norm(model), "")


def colour_aspect(model: str, aspects: dict) -> str:
    key = norm(model)
    for pattern, value in aspects["farbe"]["z_modelu"].items():
        if pattern in key:
            return value
    return aspects["farbe"]["domyslnie"]


def keyboard_parts(value: str, aspects: dict, review: Review) -> tuple[str, bool | None]:
    """Zwraca (opis po niemiecku, czy podswietlana). None = brak informacji."""
    cfg = aspects["klawiatura_czesci"]
    czesci, podswietlenie = [], None
    fragmenty: list[str] = []
    for kawalek in re.split(r"[,;]", value or ""):
        w_nawiasie = re.findall(r"\(([^)]*)\)", kawalek)
        fragmenty.append(re.sub(r"\([^)]*\)", " ", kawalek))
        fragmenty += w_nawiasie
    if True:
        for fragment in fragmenty:
            fragment = (fragment or "").strip(" ()")
            if not fragment:
                continue
            klucz = norm(fragment)
            if klucz in cfg["podswietlenie_nie"]:
                podswietlenie = False
            elif klucz in cfg["podswietlenie_tak"]:
                podswietlenie = True
            niemiecki = cfg["czesci"].get(klucz)
            if niemiecki:
                czesci.append(niemiecki)
            else:
                review.add("klawiatura", "Klawiatura (ISO lub ANSI)", fragment)
                return "", podswietlenie
    return ", ".join(dict.fromkeys(czesci)), podswietlenie


def kategoria_produktu(producent: str, settings: dict) -> str:
    """Apple ma wlasna kategorie eBay z innym slownikiem systemow."""
    if norm(producent) == "apple":
        return settings["kategorie"]["apple"]
    return settings["kategorie"]["domyslna"]


def sufiks_tytulu(system: str, aspects: dict) -> str:
    """Sufiks MUSI wynikac z pola systemu. Nieznana wartosc = brak sufiksu."""
    return aspects["sufiks_tytulu"].get(system, "")


def condition_class(value: str) -> str:
    match = re.search(r"\[klasa\s*([a-c][+\-]?)\]", norm(value))
    return match.group(1).upper() if match else ""


def gpsr_block(producent: str, manufacturers: dict) -> tuple[dict[str, str], str]:
    key = norm(producent)
    key = manufacturers["aliasy"].get(key, key)
    entry = manufacturers["producenci"].get(key)
    if not entry:
        return {}, f"gpsr: brak wpisu dla '{producent}'"
    if not entry.get("responsible_person"):
        return {}, f"gpsr: brak osoby odpowiedzialnej w UE dla '{producent}'"
    m, r = entry["manufacturer"], entry["responsible_person"]
    return {
        "Manufacturer Name": m["name"],
        "Manufacturer AddressLine1": m["address1"],
        "Manufacturer AddressLine2": m.get("address2", ""),
        "Manufacturer City": m["city"],
        "Manufacturer Country": m["country"],
        "Manufacturer PostalCode": m["postal"],
        "Manufacturer StateOrProvince": m.get("state", ""),
        "Manufacturer Phone": m.get("phone", ""),
        "Manufacturer Email": m["email"],
        "Manufacturer ContactURL": m.get("url", ""),
        "Responsible Person 1": r["name"],
        "Responsible Person 1 Type": r["type"],
        "Responsible Person 1 AddressLine1": r["address1"],
        "Responsible Person 1 AddressLine2": r.get("address2", ""),
        "Responsible Person 1 City": r["city"],
        "Responsible Person 1 Country": r["country"],
        "Responsible Person 1 PostalCode": r["postal"],
        "Responsible Person 1 StateOrProvince": r.get("state", ""),
        "Responsible Person 1 Phone": r.get("phone", ""),
        "Responsible Person 1 Email": r["email"],
        "Responsible Person 1 ContactURL": r.get("url", ""),
    }, ""


def vocab_match(aspect: str, value: str, slownik: dict, review: Review,
                prefix: str = "", strict: bool = True) -> str:
    """Dopasowuje wartosc do slownika eBaya.

    strict=True  -> brak dopasowania daje puste pole (pola z zamknieta lista).
    strict=False -> brak dopasowania przepuszcza wartosc surowa (modele, pojemnosci),
                    bo eBay przyjmuje tam tekst dowolny.
    """
    allowed = slownik["aspekty"].get(aspect)
    if not allowed or not value:
        return value
    if value in allowed:
        return value
    by_norm = {norm(a): a for a in allowed}
    if norm(value) in by_norm:
        return by_norm[norm(value)]
    target = norm(f"{prefix} {value}".strip())
    best = ""
    for candidate in allowed:
        nc = norm(candidate)
        if (target == nc or target.startswith(nc + " ")) and len(nc) > len(norm(best)):
            best = candidate
    if best:
        return best
    bez_spacji = re.sub(r"(\d)\s+(GB|TB|MB)\b", r"\1\2", value)   # "240 GB" vs "240GB"
    if bez_spacji != value and bez_spacji in allowed:
        return bez_spacji
    review.add("aspekt", f"C:{aspect}", value)
    return value if not strict else ""


def scal_porty(ports, aspects: dict, review: Review) -> list[tuple[str, int]]:
    """Sumuje sztuki tego samego portu: 2x USB 3.2 + 3x USB 3.2 -> 5x USB 3.2."""
    razem: dict[str, int] = {}
    for count, label in ports:
        niemiecki = port_label(label, aspects, review)
        if niemiecki:
            razem[niemiecki] = razem.get(niemiecki, 0) + count
    return list(razem.items())


def spec_row(label: str, value: str) -> str:
    return f"<tr><th>{e(label)}</th><td>{e(value)}</td></tr>" if value else ""


def render_description(template, attrs, images, translations, aspects, settings, review):
    de: dict[str, str] = {}
    for field in REQUIRED_TRANSLATIONS:
        raw = attrs.get(field, "")
        if not raw:
            return "", f"opis: brak pola {field}"
        value = translate_value(field, raw, translations, review)
        if value is None:
            return "", f"opis: brak tlumaczenia {field}"
        de[field] = value

    extra = attrs.get("Informacje dodatkowe", "")
    extra_de = translate_value("Informacje dodatkowe", extra, translations, review) if extra else ""
    if extra_de is None:
        extra_de = ""

    ports = parse_ports_raw(attrs.get("Złącza zewnętrzne", ""))
    manufacturer = brand_name(attrs.get("Producent", ""))
    model = attrs.get("Model", "")
    ram = clean_capacity(attrs.get("Ilość pamięci RAM", ""))
    disk = clean_capacity(attrs.get("Dysk", ""))
    finish = translations["screen_finish"].get(attrs.get("Powłoka matrycy", ""), "")
    gpu_type = translations["gpu_type"].get(attrs.get("Rodzaj karty graficznej", ""), "")
    operating_system = aspects["betriebssystem"].get(
        attrs.get("Zainstalowany system", ""), attrs.get("Zainstalowany system", ""))

    specs = [
        ("Hersteller", manufacturer),
        ("Modell", model),
        ("Prozessor", attrs.get("Procesor", "")),
        ("Prozessorkerne", attrs.get("Ilość rdzeni", "")),
        ("Taktfrequenz", base_clock(attrs.get("Taktowanie", ""))),
        ("Arbeitsspeicher", normalize_space(f"{ram} {attrs.get('Typ pamięci RAM', '')}")),
        ("Festplatte", normalize_space(f"{disk} {attrs.get('Typ dysku', '')}")),
        ("Display", ", ".join(x for x in [
            screen_size_de(attrs.get("Przekątna ekranu", "")),
            attrs.get("Rozdzielczość ekranu", ""), finish] if x)),
        ("Grafik", ", ".join(x for x in [
            attrs.get("Model karty graficznej", ""), gpu_type] if x)),
        ("Touchscreen", "nicht vorhanden" if attrs.get("Ekran dotykowy") == "Nie"
         else "vorhanden" if attrs.get("Ekran dotykowy") == "Tak" else ""),
        ("Optisches Laufwerk", translations["drive"].get(attrs.get("Napęd", ""), "")),
        ("Betriebssystem", operating_system),
        ("Tastatur-Layout", keyboard_parts(
            attrs.get("Klawiatura (ISO lub ANSI)", ""), aspects, review)[0]),
        ("Webcam", translations["yes_no"].get(attrs.get("Kamera", ""), "")),
        ("Akku", de["Bateria"]),
        ("Lieferumfang", de["W zestawie"]),
    ]

    gpu_note = ("Die Grafik ist integriert. F&uuml;r aktuelle Spiele, 3D-Rendering oder "
                "gro&szlig;e Videoschnitt-Projekte ist das Ger&auml;t nicht gedacht."
                if norm(gpu_type).startswith("integriert") else "")

    values = {
        "processor": attrs.get("Procesor", ""),
        "ram": ram, "ram_type": attrs.get("Typ pamięci RAM", ""),
        "disk": disk, "disk_type": attrs.get("Typ dysku", ""),
        "screen_size": screen_size_de(attrs.get("Przekątna ekranu", "")),
        "screen_finish": finish,
        "resolution": attrs.get("Rozdzielczość ekranu", ""),
        "operating_system": operating_system,
        "manufacturer": manufacturer, "model": model,
        "condition_summary": de["Kondycja sprzętu"],
        "case_condition": de["Stan obudowy"],
        "screen_condition": de["Stan ekranu"],
        
        "battery": de["Bateria"],
        "company_since": settings["company_since"],
        "company_locations": settings["company_locations_de"],
        "os_language_note": settings["os_language_note_de"],
    }
    raw = {
        "gpu_note": gpu_note,
        "spec_rows": "".join(spec_row(l, v) for l, v in specs),
        "port_items": "".join(
            f"<li>{e(str(c) + 'x ' + n)}</li>" for n, c in scal_porty(ports, aspects, review)),
        "hinweis_row": (f"<tr><th>Hinweis</th><td>{e(extra_de)}</td></tr>" if extra_de else ""),
        "main_image_block": (
            f'<div class="kpx-media"><img src="{e(images[0])}" '
            f'alt="{e(manufacturer)} {e(model)}"></div>' if images else ""),
    }
    out = template
    for key, value in {**values, **raw}.items():
        out = out.replace("{{" + key + "}}", value if key in raw else e(value))
    out = normalize_space(out)

    leak = has_polish_leak(out)
    return ("", f"opis: niedotlumaczony ({leak})") if leak else (out, "")


def build_title(attrs: dict, settings: dict, aspects: dict) -> str:
    kandydaci = cpu_candidates(attrs.get("Procesor", ""))
    cpu = kandydaci[0] if kandydaci else ""
    parts = [brand_name(attrs.get("Producent", "")), attrs.get("Model", ""),
             cpu.replace("Intel Core ", "").replace("AMD ", ""), clean_capacity(attrs.get("Ilość pamięci RAM", "")),
             clean_capacity(attrs.get("Dysk", "")), attrs.get("Typ dysku", ""),
             screen_size_de(attrs.get("Przekątna ekranu", "")),
             sufiks_tytulu(attrs.get("Zainstalowany system", ""), aspects)]
    title = normalize_space(" ".join(p for p in parts if p))
    while len(title) > 80 and " " in title:
        title = title.rsplit(" ", 1)[0]
    return title


def build_row(offer, attrs, cfg, headers, review):
    settings, translations = cfg["settings"], cfg["translations"]
    aspects, manufacturers = cfg["aspects"], cfg["manufacturers"]

    gpsr, gpsr_error = gpsr_block(attrs.get("Producent", ""), manufacturers)
    if gpsr_error:
        review.add("gpsr", attrs.get("Producent", ""), gpsr_error)
        return None, gpsr_error

    images: list[str] = []
    for node in offer.findall("./imgs/*"):
        url = (node.get("url") or "").strip()
        if url and url not in images:
            images.append(url)
    images = images[: int(settings["max_images"])]

    description, desc_error = render_description(
        cfg["template"], attrs, images, translations, aspects, settings, review)
    if desc_error:
        return None, desc_error

    ports = parse_ports_raw(attrs.get("Złącza zewnętrzne", ""))
    disk = clean_capacity(attrs.get("Dysk", ""))
    ram = clean_capacity(attrs.get("Ilość pamięci RAM", ""))
    model = attrs.get("Model", "")
    grade = condition_class(attrs.get("Kondycja sprzętu", ""))
    cond_map = aspects["condition_id"][aspects["condition_id"]["_tryb"]]

    kategoria = kategoria_produktu(attrs.get("Producent", ""), settings)
    slownik = cfg["vocab"]["kategorie"][kategoria]
    vocab = slownik
    cpu_aspect = ""
    for option in cpu_candidates(attrs.get("Procesor", "")):
        if option in vocab["aspekty"]["Prozessor"]:
            cpu_aspect = option
            break
    if not cpu_aspect:
        review.add("aspekt", "C:Prozessor", attrs.get("Procesor", ""))

    gpu_raw = attrs.get("Model karty graficznej", "")
    gpu_aspect = aspects["grafikprozessor_alias"].get(norm(gpu_raw), "")
    if not gpu_aspect:
        for option in gpu_clean(gpu_raw):
            if option in vocab["aspekty"]["Grafikprozessor"]:
                gpu_aspect = option
                break
    if gpu_raw and not gpu_aspect:
        review.add("aspekt", "C:Grafikprozessor", gpu_raw)

    quantity = int(float(offer.get("stock", "0") or 0))
    cap = int(settings.get("max_quantity", 0) or 0)
    if cap:
        quantity = min(quantity, cap)

    price_eur = cena_eur(offer, cfg)

    values: dict[str, str] = {
        headers[0]: "Add",
        "CustomLabel": attrs.get("SKU", ""),
        "*Category": kategoria,
        "*Title": build_title(attrs, settings, aspects),
        "*ConditionID": cond_map.get(grade, cond_map["_brak"]),
        "VAT%": settings["vat_percent"],
        "*C:Marke": brand_name(attrs.get("Producent", "")),
        "*C:Bildschirmgröße": vocab_match("Bildschirmgröße", screen_size_de(attrs.get("Przekątna ekranu", "")), vocab, review, ""),
        "*C:Prozessor": cpu_aspect,
        "C:Festplattentyp": aspects["festplattentyp"].get(attrs.get("Typ dysku", ""), ""),
        "C:Produktart": ("Notebook / Laptop" if "Produktart" in slownik["aspekty"] else ""),
        "C:Festplattenkapazität": vocab_match("Festplattenkapazität", disk, vocab, review, "", strict=False),
        "C:Besonderheiten": features_aspect(
            attrs, ports, aspects,
            keyboard_parts(attrs.get("Klawiatura (ISO lub ANSI)", ""), aspects, Review())[1]),
        "C:SSD-Festplattenkapazität": (vocab_match("SSD-Festplattenkapazität", disk, vocab, review, "", strict=False) if attrs.get("Typ dysku") == "SSD" else ""),
        "C:Grafikprozessor": gpu_aspect,
        "C:Erscheinungsjahr": vocab_match("Erscheinungsjahr", year_aspect(model, aspects), vocab, review),
        "C:Farbe": vocab_match("Farbe", colour_aspect(model, aspects), vocab, review),
        "C:Prozessorgeschwindigkeit": vocab_match("Prozessorgeschwindigkeit", base_clock(attrs.get("Taktowanie", "")), vocab, review, ""),
        "C:Maximale Auflösung": vocab_match("Maximale Auflösung", attrs.get("Rozdzielczość ekranu", ""), vocab, review, "", strict=False),
        "C:Herstellernummer": "Nicht zutreffend",
        "C:Modell": vocab_match("Modell", normalize_space(f'{brand_name(attrs.get("Producent", ""))} {model}'), vocab, review, "", strict=False),
        "C:Betriebssystem": vocab_match(
            "Betriebssystem",
            aspects["betriebssystem"].get(attrs.get("Zainstalowany system", ""), ""),
            slownik, review),
        "C:Anzahl der Einheiten": "1",
        "C:Maßeinheit": "Einheit",
        "C:Inklusive Ladegerät": "Ja" if attrs.get("W zestawie") else "",
        "C:Arbeitsspeichergröße": vocab_match("Arbeitsspeichergröße", ram, vocab, review, "", strict=False),
        "C:Grafikprozessortyp": aspects["grafikprozessortyp"].get(
            attrs.get("Rodzaj karty graficznej", ""), ""),
        "C:Konnektivität": connectivity_aspect(ports, aspects, review),
        "C:Herstellergarantie": aspects["herstellergarantie"]["wartosc"],
        "C:Serie": vocab_match("Serie", series_aspect(model, aspects), vocab, review),
        "C:Passend für": "|".join(aspects["passend_fuer"]["wartosci"]),
        "PicURL": "|".join(images),
        "GalleryType": settings["gallery_type"],
        "*Description": description,
        "*Format": settings["format"],
        "*Duration": settings["duration"],
        "*StartPrice": f"{price_eur:.2f}",
        "*Quantity": str(quantity),
        "*Location": settings["location"],
        "ShippingProfileName": settings["shipping_profile_name"],
        "ReturnProfileName": settings["return_profile_name"],
        "PaymentProfileName": settings["payment_profile_name"],
        "Product Safety Pictograms": settings["product_safety_pictograms"],
        **gpsr,
    }
    for aspect in slownik["_wymagane"]:
        for column in (f"*C:{aspect}", f"C:{aspect}"):
            if column in values and not values[column]:
                review.add("blokada", attrs.get("SKU", ""), f"puste pole wymagane C:{aspect}")
                return None, f"aspekt: puste wymagane C:{aspect}"

    return {h: values.get(h, "") for h in headers}, ""


def wczytaj_raport(path: Path, site: str) -> tuple[dict[str, dict], list[str]]:
    """Raport aktywnych ofert z eBaya = nasza pamiec o tym, co juz wystawione.

    Zwraca (mapa SKU -> dane, lista SKU zdublowanych).
    Zdublowany SKU jest pomijany - nie zgadujemy, ktora aukcje ruszyc.
    """
    mapa: dict[str, dict] = {}
    duplikaty: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for wiersz in csv.DictReader(handle):
            if (wiersz.get("Listing site") or "").strip().upper() != site.upper():
                continue
            sku = (wiersz.get("Custom label (SKU)") or "").strip()
            if not sku:
                continue
            if sku in mapa:
                duplikaty.add(sku)
                continue
            mapa[sku] = {
                "item": (wiersz.get("Item number") or "").strip(),
                "tytul": (wiersz.get("Title") or "").strip(),
                "ilosc": int(float(wiersz.get("Available quantity") or 0)),
                "cena": float((wiersz.get("Start price") or "0").replace(",", ".")),
                "waluta": (wiersz.get("Currency") or "EUR").strip(),
            }
    for sku in duplikaty:
        mapa.pop(sku, None)
    return mapa, sorted(duplikaty)


def cena_eur(offer, cfg) -> int:
    """PLN z feedu -> EUR po kursie NBP, w gore, plus ukryta doplata za wysylke."""
    pln = float((offer.get("price") or "0").replace(",", "."))
    return math.ceil(pln / cfg["rate"]) + int(cfg["settings"]["doplata_wysylka_eur"])


def read_headers(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter=";"):
            if row and row[0].startswith("*Action("):
                return row
    raise ValueError(f"brak wiersza naglowka w {path}")


def zbierz_produkty(root, cfg, review, skipped):
    """Produkty z naszych kategorii, z zapasem, z kompletem pol wymaganych."""
    out = []
    for offer in root.findall("./o"):
        if text(offer.find("./cat")) not in cfg["settings"]["xml_categories"]:
            continue
        skipped["w_kategorii"] += 1
        if int(float(offer.get("stock", "0") or 0)) <= 0:
            skipped["stock_zero"] += 1
            continue
        attrs = offer_attrs(offer)
        brakuje = [f for f in REQUIRED_XML if not attrs.get(f)]
        if brakuje:
            skipped["brak_pol_xml"] += 1
            review.add("feed", attrs.get("SKU", offer.get("id", "")), ", ".join(brakuje))
            continue
        out.append((offer, attrs))
    return out


def zapisz_add(sciezka: Path, headers: list[str], wiersze: list[dict], akcja: str) -> None:
    with sciezka.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(["Info", "Version=1.0.0", "Template=fx_category_template_EBAY_DE"])
        writer.writerow(headers)
        for wiersz in wiersze:
            wiersz[headers[0]] = akcja
            writer.writerow([wiersz[h] for h in headers])


KOLUMNY_REVISE = ["Action", "Category name", "Item number", "Title", "Listing site", "Currency",
                  "Start price", "Buy It Now price", "Available quantity", "Relationship",
                  "Relationship details", "Custom label (SKU)"]


def zapisz_revise(sciezka: Path, wiersze: list[dict]) -> None:
    with sciezka.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(["#INFO", "Version=1.0.0",
                         "Template= eBay-active-revise-price-quantity-download_PL"] + [""] * 9)
        writer.writerow(KOLUMNY_REVISE)
        for w in wiersze:
            writer.writerow([w.get(k, "") for k in KOLUMNY_REVISE])


def generate(feed_bytes, nbp, cfg, output_dir: Path, tryb: str, raport: Path | None):
    headers = cfg["headers"]
    cfg["rate"] = nbp["rate"]
    settings = cfg["settings"]
    root = ET.fromstring(feed_bytes)
    review = Review()
    skipped: Counter = Counter()
    blokady: list[str] = []

    produkty = zbierz_produkty(root, cfg, review, skipped)
    if len(produkty) < int(settings["min_produktow_w_feedzie"]):
        blokady.append(f"feed ma tylko {len(produkty)} produktow z zapasem, "
                       f"minimum to {settings['min_produktow_w_feedzie']} - "
                       "wyglada na niepelne pobranie, przerywam")

    aktywne: dict[str, dict] = {}
    duplikaty: list[str] = []
    if tryb in ("nowe", "aktualizacja"):
        if not raport or not raport.exists():
            blokady.append(f"tryb '{tryb}' wymaga raportu aktywnych ofert w {raport}")
        else:
            aktywne, duplikaty = wczytaj_raport(raport, settings["listing_site"])
            for sku in duplikaty:
                review.add("konflikt", sku, "ten sam SKU ma kilka aktywnych aukcji - pomijam")

    output_dir.mkdir(parents=True, exist_ok=True)
    wiersze_add: list[dict] = []
    wiersze_revise: list[dict] = []
    zerowane = 0

    if not blokady and tryb in ("pierwsze", "nowe", "test"):
        for offer, attrs in produkty:
            if tryb == "nowe" and attrs.get("SKU", "") in aktywne:
                skipped["juz_wystawione"] += 1
                continue
            try:
                wiersz, blad = build_row(offer, attrs, cfg, headers, review)
            except (ValueError, TypeError, KeyError) as exc:
                skipped["blad_konwersji"] += 1
                review.add("blad", attrs.get("SKU", ""), repr(exc))
                continue
            if blad:
                skipped[blad.split(":")[0]] += 1
                review.add("blokada", attrs.get("SKU", ""), blad)
                continue
            wiersze_add.append(wiersz)
        zapisz_add(output_dir / "ebay-add.csv", headers,
                   wiersze_add, "VerifyAdd" if tryb == "test" else "Add")

    if not blokady and tryb == "aktualizacja":
        nazwy = settings["kategorie_nazwy"]
        w_feedzie = set()
        for offer, attrs in produkty:
            sku = attrs.get("SKU", "")
            w_feedzie.add(sku)
            biezaca = aktywne.get(sku)
            if not biezaca:
                skipped["nie_wystawione"] += 1
                continue
            nowa_cena = cena_eur(offer, cfg)
            nowa_ilosc = int(float(offer.get("stock", "0") or 0))
            cap = int(settings.get("max_quantity", 0) or 0)
            if cap:
                nowa_ilosc = min(nowa_ilosc, cap)
            zmiana_ceny = abs(nowa_cena - biezaca["cena"]) >= float(settings["prog_zmiany_ceny_eur"])
            zmiana_ilosci = nowa_ilosc != biezaca["ilosc"]
            if not (zmiana_ceny or zmiana_ilosci):
                skipped["bez_zmian"] += 1
                continue
            kat = kategoria_produktu(attrs.get("Producent", ""), settings)
            wiersze_revise.append({
                "Action": "Revise", "Category name": nazwy.get(kat, ""),
                "Item number": biezaca["item"], "Title": biezaca["tytul"],
                "Listing site": settings["listing_site"], "Currency": biezaca["waluta"],
                "Start price": f"{nowa_cena if zmiana_ceny else biezaca['cena']:.1f}",
                "Buy It Now price": "", "Available quantity": str(nowa_ilosc),
                "Relationship": "", "Relationship details": "",
                "Custom label (SKU)": sku})

        for sku, biezaca in aktywne.items():
            if sku in w_feedzie or biezaca["ilosc"] == 0:
                continue
            zerowane += 1
            wiersze_revise.append({
                "Action": "Revise", "Category name": "", "Item number": biezaca["item"],
                "Title": biezaca["tytul"], "Listing site": settings["listing_site"],
                "Currency": biezaca["waluta"], "Start price": f"{biezaca['cena']:.1f}",
                "Buy It Now price": "", "Available quantity": "0",
                "Relationship": "", "Relationship details": "", "Custom label (SKU)": sku})

        if aktywne and zerowane / len(aktywne) > float(settings["max_udzial_zerowanych"]):
            blokady.append(f"zerowanie objelo by {zerowane} z {len(aktywne)} aukcji "
                           f"({zerowane / len(aktywne):.0%}) - to wyglada na blad feedu, przerywam")
            wiersze_revise = []
        else:
            zapisz_revise(output_dir / "ebay-revise.csv", wiersze_revise)

    with (output_dir / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerow(["Rodzaj", "Pole / SKU", "Wartość"])
        writer.writerows(review.rows())

    raport_json = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tryb": tryb,
        "ok": not blokady,
        "blokady": blokady,
        "produktow_w_kategorii": skipped["w_kategorii"],
        "produktow_z_zapasem": len(produkty),
        "aktywnych_na_ebay": len(aktywne),
        "sku_zdublowane": duplikaty,
        "do_wystawienia": len(wiersze_add),
        "do_aktualizacji": len(wiersze_revise),
        "w_tym_zerowanych": zerowane,
        "pominieto": dict(skipped),
        "review_items": len(review.rows()),
        "nbp": nbp,
        "kurs_z_doplata_eur": settings["doplata_wysylka_eur"],
        "do_uzupelnienia": {
            "marki_bez_gpsr": review.top("gpsr", by="field"),
            "brakujace_tlumaczenia": review.top("tlumaczenie"),
            "wartosci_aspektow": review.top("aspekt"),
            "etykiety_portow": review.top("port"),
            "klawiatury": review.top("klawiatura"),
        },
    }
    (output_dir / "generation-report.json").write_text(
        json.dumps(raport_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return raport_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-produktow", type=int)
    parser.add_argument("--tryb", default="pierwsze",
                        choices=["pierwsze", "nowe", "aktualizacja", "test"])
    parser.add_argument("--raport", type=Path, default=ROOT / "input" / "aktywne.csv")
    parser.add_argument("--feed-file", type=Path)
    parser.add_argument("--nbp-rate", type=float)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()

    settings = load_json(ROOT / "config" / "settings.json")
    if args.min_produktow is not None:
        settings["min_produktow_w_feedzie"] = args.min_produktow
    cfg = {
        "settings": settings,
        "translations": load_json(ROOT / "config" / "translations.json"),
        "aspects": load_json(ROOT / "config" / "aspects.json"),
        "manufacturers": load_json(ROOT / "config" / "manufacturers.json"),
        "template": (ROOT / "templates" / "description.html").read_text(encoding="utf-8"),
        "headers": read_headers(ROOT / "config" / "ebay-header.csv"),
        "vocab": load_json(ROOT / "config" / "ebay-vocab.json"),
    }
    feed = args.feed_file.read_bytes() if args.feed_file else fetch_bytes(settings["feed_url"])
    nbp = ({"currency": "euro", "code": "EUR", "table": "A", "number": "TEST",
            "effective_date": "TEST", "rate": args.nbp_rate}
           if args.nbp_rate else fetch_nbp_rate(settings["nbp_url"]))

    report = generate(feed, nbp, cfg, args.output_dir, args.tryb, args.raport)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
