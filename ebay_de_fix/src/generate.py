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
    out: list[tuple[int, str]] = []
    for count, label in re.findall(r"(\d+)\s*x\s*(.+?)(?=\s*\d+\s*x\s|$)", value or ""):
        label = re.sub(r"\s*\([^)]*\)", "", label).strip(" ,;")
        if label:
            out.append((int(count), label))
    return out


def port_label(label: str, aspects: dict, review: Review) -> str:
    """Etykieta portu w opisie. Nieznana = zostaje surowa i idzie do review."""
    mapping = aspects["konnektivitaet"]["opis_etykiety"]
    hit = mapping.get(norm(label))
    if hit:
        return hit
    if re.search(r"[ąćęłńóśźż]|typu|zlacze|gniazdo|czytnik", norm(label) + label.lower()):
        review.add("port", "opis", label)
    return label


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


def features_aspect(attrs: dict, ports, aspects: dict) -> str:
    out = ["Bluetooth", "Wi-Fi", "Eingebautes Mikrofon"]
    if attrs.get("Ekran dotykowy") == "Tak":
        out.append("Touchscreen")
    if any(norm(l).startswith(("rj-45", "rj45")) for _, l in ports):
        out.append("10/100 LAN Karte")
    if norm(attrs.get("Kamera", "")).startswith("tak"):
        out.append(aspects["besonderheiten"]["_webcam"])
    allowed = aspects["besonderheiten"]["_dozwolone"]
    return "|".join(v for v in out if v in allowed)


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


def vocab_match(aspect: str, value: str, vocab: dict, review: Review, prefix: str = "") -> str:
    """Dopasowuje wartosc do slownika eBaya. Brak dopasowania -> puste pole + review."""
    allowed = vocab["aspekty"].get(aspect)
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
    review.add("aspekt", f"C:{aspect}", value)
    return ""


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
        attrs.get("Zainstalowany system", attrs.get("Licencja", "")), "")

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
        ("Tastatur-Layout", attrs.get("Klawiatura (ISO lub ANSI)", "")),
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
        "keyboard_info": extra_de,
        "battery": de["Bateria"],
        "company_since": settings["company_since"],
        "company_locations": settings["company_locations_de"],
        "os_language_note": settings["os_language_note_de"],
    }
    raw = {
        "gpu_note": gpu_note,
        "spec_rows": "".join(spec_row(l, v) for l, v in specs),
        "port_items": "".join(
            f"<li>{e(str(c) + 'x ' + port_label(l, aspects, review))}</li>" for c, l in ports),
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


def build_title(attrs: dict, settings: dict) -> str:
    parts = [brand_name(attrs.get("Producent", "")), attrs.get("Model", ""),
             attrs.get("Procesor", ""), clean_capacity(attrs.get("Ilość pamięci RAM", "")),
             clean_capacity(attrs.get("Dysk", "")), attrs.get("Typ dysku", ""),
             screen_size_de(attrs.get("Przekątna ekranu", "")),
             settings.get("title_suffix", "")]
    title = normalize_space(" ".join(p for p in parts if p))
    while len(title) > 80 and " " in title:
        title = title.rsplit(" ", 1)[0]
    return title


def build_row(offer, attrs, cfg, headers, review):
    settings, translations = cfg["settings"], cfg["translations"]
    aspects, manufacturers = cfg["aspects"], cfg["manufacturers"]

    gpsr, gpsr_error = gpsr_block(attrs.get("Producent", ""), manufacturers)
    if gpsr_error:
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

    vocab = cfg["vocab"]
    raw_cpu = processor_aspect(attrs.get("Procesor", ""), attrs.get("Seria procesora", ""), review)
    cpu_aspect = ""
    for option in raw_cpu.split("|"):
        candidate = vocab_match("Prozessor", option.strip(), vocab, Review(), "Intel Core")
        if candidate:
            cpu_aspect = candidate
            break
    if not cpu_aspect:
        review.add("aspekt", "C:Prozessor", raw_cpu)

    quantity = int(float(offer.get("stock", "0") or 0))
    cap = int(settings.get("max_quantity", 0) or 0)
    if cap:
        quantity = min(quantity, cap)

    price_eur = math.ceil(float(offer.get("price", "0").replace(",", ".")) / cfg["rate"])

    values: dict[str, str] = {
        headers[0]: "Add",
        "CustomLabel": attrs.get("SKU", ""),
        "*Category": settings["ebay_category"],
        "*Title": build_title(attrs, settings),
        "*ConditionID": cond_map.get(grade, cond_map["_brak"]),
        "VAT%": settings["vat_percent"],
        "*C:Marke": brand_name(attrs.get("Producent", "")),
        "*C:Bildschirmgröße": vocab_match("Bildschirmgröße", screen_size_de(attrs.get("Przekątna ekranu", "")), vocab, review, ""),
        "*C:Prozessor": cpu_aspect,
        "C:Festplattentyp": aspects["festplattentyp"].get(attrs.get("Typ dysku", ""), ""),
        "C:Produktart": "Notebook / Laptop",
        "C:Festplattenkapazität": vocab_match("Festplattenkapazität", disk, vocab, review, ""),
        "C:Besonderheiten": features_aspect(attrs, ports, aspects),
        "C:SSD-Festplattenkapazität": disk if attrs.get("Typ dysku") == "SSD" else "",
        "C:Grafikprozessor": vocab_match("Grafikprozessor", aspects["grafikprozessor_alias"].get(norm(attrs.get("Model karty graficznej", "")), attrs.get("Model karty graficznej", "")), vocab, review, ""),
        "C:Erscheinungsjahr": vocab_match("Erscheinungsjahr", year_aspect(model, aspects), vocab, review),
        "C:Farbe": vocab_match("Farbe", colour_aspect(model, aspects), vocab, review),
        "C:Prozessorgeschwindigkeit": vocab_match("Prozessorgeschwindigkeit", base_clock(attrs.get("Taktowanie", "")), vocab, review, ""),
        "C:Maximale Auflösung": vocab_match("Maximale Auflösung", attrs.get("Rozdzielczość ekranu", ""), vocab, review, ""),
        "C:Herstellernummer": "Nicht zutreffend",
        "C:Modell": vocab_match("Modell", model, vocab, review, brand_name(attrs.get("Producent", ""))),
        "C:Betriebssystem": aspects["betriebssystem"].get(
            attrs.get("Zainstalowany system", attrs.get("Licencja", "")), ""),
        "C:Anzahl der Einheiten": "1",
        "C:Maßeinheit": "Einheit",
        "C:Inklusive Ladegerät": "Ja" if attrs.get("W zestawie") else "",
        "C:Arbeitsspeichergröße": vocab_match("Arbeitsspeichergröße", ram, vocab, review, ""),
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
    for aspect in cfg["vocab"]["_wymagane"]:
        for column in (f"*C:{aspect}", f"C:{aspect}"):
            if column in values and not values[column]:
                review.add("blokada", attrs.get("SKU", ""), f"puste pole wymagane C:{aspect}")
                return None, f"aspekt: puste wymagane C:{aspect}"

    return {h: values.get(h, "") for h in headers}, ""


def read_headers(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter=";"):
            if row and row[0].startswith("*Action("):
                return row
    raise ValueError(f"brak wiersza naglowka w {path}")


def generate(feed_bytes, nbp, cfg, output_dir: Path) -> dict[str, Any]:
    headers = cfg["headers"]
    cfg["rate"] = nbp["rate"]
    root = ET.fromstring(feed_bytes)
    review = Review()
    skipped: Counter = Counter()
    rows: list[dict[str, str]] = []
    in_category = 0

    for offer in root.findall("./o"):
        if text(offer.find("./cat")) not in cfg["settings"]["xml_categories"]:
            continue
        in_category += 1
        if int(float(offer.get("stock", "0") or 0)) <= 0:
            skipped["stock_zero"] += 1
            continue
        attrs = offer_attrs(offer)
        missing = [f for f in REQUIRED_XML if not attrs.get(f)]
        if missing:
            skipped["brak_pol_xml"] += 1
            review.add("feed", attrs.get("SKU", offer.get("id", "")), ", ".join(missing))
            continue
        try:
            row, error = build_row(offer, attrs, cfg, headers, review)
        except (ValueError, TypeError, KeyError) as exc:
            skipped["blad_konwersji"] += 1
            review.add("blad", attrs.get("SKU", ""), repr(exc))
            continue
        if error:
            skipped[error.split(":")[0]] += 1
            review.add("blokada", attrs.get("SKU", ""), error)
            continue
        rows.append(row)

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "ebay-de-laptops.csv").open(
            "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(["Info", "Version=1.0.0", "Template=fx_category_template_EBAY_DE"])
        writer.writerow(headers)
        writer.writerows([[row[h] for h in headers] for row in rows])

    with (output_dir / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerow(["Rodzaj", "Pole / SKU", "Wartość"])
        writer.writerows(review.rows())

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "products_in_category": in_category,
        "products_exported": len(rows),
        "products_skipped": sum(skipped.values()),
        "skipped_reasons": dict(skipped),
        "review_items": len(review.rows()),
        "nbp": nbp,
        "condition_mode": cfg["aspects"]["condition_id"]["_tryb"],
        "csv_columns": len(headers),
    }
    (output_dir / "generation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed-file", type=Path)
    parser.add_argument("--nbp-rate", type=float)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()

    settings = load_json(ROOT / "config" / "settings.json")
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

    report = generate(feed, nbp, cfg, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["products_exported"] else 2


if __name__ == "__main__":
    sys.exit(main())
