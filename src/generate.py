#!/usr/bin/env python3
"""Generate an eBay DE CSV from the public KOMPRE XML feed."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

HEADERS = [
    "*Action(SiteID=Germany|Country=PL|Currency=EUR|Version=1193|CC=UTF-8)",
    "CustomLabel", "*Category", "StoreCategory", "*Title", "Subtitle",
    "Relationship", "RelationshipDetails", "ScheduleTime", "*ConditionID", "VAT%",
    "*C:Marke", "*C:Bildschirmgröße", "*C:Prozessor", "C:Festplattentyp",
    "C:Produktart", "C:Festplattenkapazität", "C:Besonderheiten",
    "C:SSD-Festplattenkapazität", "C:Grafikprozessor", "C:Erscheinungsjahr",
    "C:Farbe", "C:Prozessorgeschwindigkeit", "C:Maximale Auflösung",
    "C:Herstellernummer", "C:Modell", "C:Betriebssystem", "C:Anzahl der Einheiten",
    "C:Maßeinheit", "C:Inklusive Ladegerät", "C:Ladebereich des Geräts",
    "C:Arbeitsspeichergröße", "C:Grafikprozessortyp", "C:Konnektivität",
    "C:Herstellergarantie", "C:Ursprungsland", "C:Serie", "C:Breite", "C:Gewicht",
    "C:Höhe", "C:Länge", "C:Passend für", "PicURL", "GalleryType", "VideoID",
    "*Description", "*Format", "*Duration", "*StartPrice", "BuyItNowPrice",
    "BestOfferEnabled", "BestOfferAutoAcceptPrice", "MinimumBestOfferPrice",
    "*Quantity", "ImmediatePayRequired", "*Location", "ShippingType",
    "ShippingService-1:Option", "ShippingService-1:Cost", "ShippingService-2:Option",
    "ShippingService-2:Cost", "*DispatchTimeMax", "PromotionalShippingDiscount",
    "ShippingDiscountProfileID", "DomesticRateTable", "*ReturnsAcceptedOption",
    "ReturnsWithinOption", "RefundOption", "ShippingCostPaidByOption",
    "AdditionalDetails", "ShippingProfileName", "ReturnProfileName",
    "PaymentProfileName", "TakeBackPolicyID", "Regional TakeBackPolicies",
    "ProductCompliancePolicyID", "Regional ProductCompliancePolicies",
    "EcoParticipationFee", "RepairScore", "Product Safety Pictograms",
    "Product Safety Statements", "Product Safety Component", "Regulatory Document Ids",
    "Manufacturer Name", "Manufacturer AddressLine1", "Manufacturer AddressLine2",
    "Manufacturer City", "Manufacturer Country", "Manufacturer PostalCode",
    "Manufacturer StateOrProvince", "Manufacturer Phone", "Manufacturer Email",
    "Manufacturer ContactURL", "Responsible Person 1", "Responsible Person 1 Type",
    "Responsible Person 1 AddressLine1", "Responsible Person 1 AddressLine2",
    "Responsible Person 1 City", "Responsible Person 1 Country",
    "Responsible Person 1 PostalCode", "Responsible Person 1 StateOrProvince",
    "Responsible Person 1 Phone", "Responsible Person 1 Email",
    "Responsible Person 1 ContactURL",
]

REQUIRED_XML = (
    "Producent", "SKU", "Model", "Procesor", "Przekątna ekranu",
    "Ilość pamięci RAM", "Dysk",
)

PORT_PATTERNS = [
    (r"thunderbolt\s*4", "Thunderbolt 4"),
    (r"thunderbolt\s*3", "Thunderbolt 3"),
    (r"mini\s*displayport", "Mini DisplayPort"),
    (r"displayport", "DisplayPort"),
    (r"hdmi(?:\s*[0-9.]+[a-z]?)?", None),
    (r"usb\s*typu\s*c|usb[\s-]*c", "USB-C"),
    (r"usb\s*3\.2(?:\s*gen\s*[0-9])?", None),
    (r"usb\s*3\.1(?:\s*gen\s*[0-9])?", None),
    (r"usb\s*3\.0", "USB 3.0"),
    (r"usb\s*2\.0", "USB 2.0"),
    (r"rj[\s-]*45", "RJ-45"),
    (r"micro\s*sd", "microSD"),
    (r"czytnik\s+kart", "Kartenleser"),
    (r"audio|słuchawk|mikrofon", "Audio"),
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_bytes(url: str, timeout: int = 90) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kompre-ebay-csv/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_nbp_rate(url: str) -> dict[str, Any]:
    payload = json.loads(fetch_bytes(url).decode("utf-8"))
    rate = payload["rates"][0]
    return {
        "currency": payload["currency"],
        "code": payload["code"],
        "table": payload["table"],
        "number": rate["no"],
        "effective_date": rate["effectiveDate"],
        "rate": float(rate["mid"]),
    }


def text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def offer_attrs(offer: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in offer.findall("./attrs/a"):
        name = (item.get("name") or "").strip()
        if name:
            result[name] = text(item)
    return result


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_capacity(value: str) -> str:
    value = normalize_space(value)
    value = re.sub(r"(?i)\s*(GB|TB)$", r" \1", value)
    return value


def screen_size_de(value: str) -> str:
    number = re.search(r"\d+(?:[.,]\d+)?", value)
    return f"{number.group(0).replace('.', ',')} Zoll" if number else value


def brand(value: str) -> str:
    known = {
        "LENOVO": "Lenovo", "DELL": "Dell", "HP": "HP", "FUJITSU": "Fujitsu",
        "APPLE": "Apple", "ACER": "Acer", "ASUS": "ASUS", "TOSHIBA": "Toshiba",
        "MICROSOFT": "Microsoft", "PANASONIC": "Panasonic", "SAMSUNG": "Samsung",
    }
    return known.get(value.upper(), value.title())


def processor_aspect(processor: str, series: str) -> str:
    p = processor.upper()
    if "RYZEN 5 PRO" in p:
        match = re.search(r"\b([4-9])\d{3}", p)
        return f"AMD Ryzen 5 PRO {match.group(1)}000 Series" if match else "AMD Ryzen 5 PRO"
    if "RYZEN 7 PRO" in p:
        match = re.search(r"\b([3-9])\d{3}", p)
        return f"AMD Ryzen 7 PRO {match.group(1)}000 Series" if match else "AMD Ryzen 7 PRO"
    if "RYZEN 5" in p:
        match = re.search(r"\b([3-9])\d{3}", p)
        return f"AMD Ryzen 5 {match.group(1)}000 Series" if match else "AMD Ryzen 5"
    if "RYZEN 7" in p:
        match = re.search(r"\b([3-9])\d{3}", p)
        return f"AMD Ryzen 7 {match.group(1)}000 Series" if match else "AMD Ryzen 7"
    intel = re.search(r"\bI([3579])[- ]?(\d{4,5})[A-Z]*\b", p)
    if intel:
        generation = int(intel.group(2)[0:2] if len(intel.group(2)) == 5 else intel.group(2)[0])
        return f"Intel Core i{intel.group(1)} {generation}. Gen"
    return series or processor


def translate_fragments(value: str, translations: dict[str, Any]) -> tuple[str, bool]:
    if not value:
        return "Nicht angegeben", False
    if value in translations.get("exact_states", {}):
        return translations["exact_states"][value], True
    result = value
    matched = False
    for source, target in sorted(
        translations["phrases"].items(), key=lambda item: len(item[0]), reverse=True
    ):
        pattern = re.compile(re.escape(source), re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(target, result)
            matched = True
    result = re.sub(r"\s*,\s*", ", ", result)
    return normalize_space(result), matched


def condition_summary(value: str, translations: dict[str, Any]) -> str:
    for prefix, translated in translations["condition_class"].items():
        if value.startswith(prefix):
            rest = value[len(prefix):].strip(" ,.-")
            rest_de, _ = translate_fragments(rest, translations)
            return f"{translated}. {rest_de}."
    translated, _ = translate_fragments(value, translations)
    return translated.rstrip(".") + "."


def parse_ports(value: str) -> list[str]:
    ports: list[str] = []
    lower = value.lower()
    for pattern, fixed in PORT_PATTERNS:
        for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
            label = fixed or normalize_space(match.group(0)).upper().replace("GEN", "Gen")
            label = re.sub(r"USB\s+TYPU\s+C", "USB-C", label, flags=re.IGNORECASE)
            label = re.sub(r"^HDMI", "HDMI", label, flags=re.IGNORECASE)
            label = re.sub(r"^USB", "USB", label, flags=re.IGNORECASE)
            if label not in ports:
                ports.append(label)
    return ports


def e(str_value: Any) -> str:
    return html.escape(str(str_value or ""), quote=True)


def spec_row(label: str, value: str) -> str:
    return f"<tr><th>{e(label)}</th><td>{e(value or 'Nicht angegeben')}</td></tr>"


def render_description(
    template: str,
    attrs: dict[str, str],
    images: list[str],
    translations: dict[str, Any],
) -> tuple[str, list[tuple[str, str]]]:
    review: list[tuple[str, str]] = []
    case_condition, case_matched = translate_fragments(attrs.get("Stan obudowy", ""), translations)
    screen_condition, screen_matched = translate_fragments(attrs.get("Stan ekranu", ""), translations)
    if attrs.get("Stan obudowy") and not case_matched:
        review.append(("Stan obudowy", attrs["Stan obudowy"]))
    if attrs.get("Stan ekranu") and not screen_matched:
        review.append(("Stan ekranu", attrs["Stan ekranu"]))

    ports = parse_ports(attrs.get("Złącza zewnętrzne", ""))
    manufacturer = brand(attrs.get("Producent", ""))
    disk = clean_capacity(attrs.get("Dysk", ""))
    ram = clean_capacity(attrs.get("Ilość pamięci RAM", ""))
    finish = translations["screen_finish"].get(
        attrs.get("Powłoka matrycy", ""), attrs.get("Powłoka matrycy", "")
    )
    gpu_type = translations["gpu_type"].get(
        attrs.get("Rodzaj karty graficznej", ""), attrs.get("Rodzaj karty graficznej", "")
    )
    touchscreen = translations["yes_no"].get(
        attrs.get("Ekran dotykowy", ""), attrs.get("Ekran dotykowy", "")
    )
    camera = translations["yes_no"].get(attrs.get("Kamera", ""), attrs.get("Kamera", ""))
    drive = translations["drive"].get(attrs.get("Napęd", ""), attrs.get("Napęd", ""))
    keyboard_info, keyboard_matched = translate_fragments(
        attrs.get("Informacje dodatkowe", ""), translations
    )
    if not keyboard_matched:
        keyboard_info = attrs.get("Klawiatura (ISO lub ANSI)", "Nicht angegeben")

    battery, _ = translate_fragments(attrs.get("Bateria", ""), translations)
    supplied, _ = translate_fragments(attrs.get("W zestawie", ""), translations)
    specs = [
        ("Hersteller", manufacturer), ("Modell", attrs.get("Model", "")),
        ("Prozessor", attrs.get("Procesor", "")), ("Kerne", attrs.get("Ilość rdzeni", "")),
        ("Arbeitsspeicher", f"{ram} {attrs.get('Typ pamięci RAM', '')}".strip()),
        ("Festplatte", f"{disk} {attrs.get('Typ dysku', '')}".strip()),
        ("Display", f"{screen_size_de(attrs.get('Przekątna ekranu', ''))}, {attrs.get('Rozdzielczość ekranu', '')}, {finish}".strip(" ,")),
        ("Grafik", f"{attrs.get('Model karty graficznej', '')}, {gpu_type}".strip(" ,")),
        ("Touchscreen", touchscreen), ("Optisches Laufwerk", drive),
        ("Betriebssystem", attrs.get("Zainstalowany system", attrs.get("Licencja", ""))),
        ("Tastatur-Layout", attrs.get("Klawiatura (ISO lub ANSI)", "")),
        ("Webcam", camera), ("Akku", battery),
        ("Lieferumfang", supplied),
    ]
    replacements = {
        "processor": attrs.get("Procesor", ""),
        "ram": ram,
        "ram_type": attrs.get("Typ pamięci RAM", ""),
        "disk": disk,
        "disk_type": attrs.get("Typ dysku", ""),
        "screen_size": screen_size_de(attrs.get("Przekątna ekranu", "")),
        "resolution": attrs.get("Rozdzielczość ekranu", ""),
        "operating_system": attrs.get("Zainstalowany system", attrs.get("Licencja", "")),
        "manufacturer": manufacturer,
        "model": attrs.get("Model", ""),
        "condition_summary": condition_summary(attrs.get("Kondycja sprzętu", ""), translations),
        "case_condition": case_condition,
        "screen_condition": screen_condition,
        "keyboard_info": keyboard_info,
        "battery": battery,
        "spec_rows": "".join(spec_row(label, value) for label, value in specs if value),
        "port_items": "".join(f"<li>{e(port)}</li>" for port in ports) or "<li>Nicht angegeben</li>",
        "main_image_block": (
            f'<div class="kpx-media"><img src="{e(images[0])}" '
            f'alt="{e(manufacturer)} {e(attrs.get("Model", ""))}"></div>'
            if images else ""
        ),
    }
    rendered = template
    raw_keys = {"spec_rows", "port_items", "main_image_block"}
    for key, value in replacements.items():
        rendered = rendered.replace("{{" + key + "}}", value if key in raw_keys else e(value))
    return normalize_space(rendered), review


def build_title(attrs: dict[str, str]) -> str:
    parts = [
        brand(attrs.get("Producent", "")), attrs.get("Model", ""),
        screen_size_de(attrs.get("Przekątna ekranu", "")),
        "Notebook", clean_capacity(attrs.get("Ilość pamięci RAM", "")),
        clean_capacity(attrs.get("Dysk", "")), attrs.get("Typ dysku", ""),
    ]
    title = normalize_space(" ".join(part for part in parts if part))
    if len(title) <= 80:
        return title
    short = normalize_space(" ".join(parts[:6]))
    return short[:80].rstrip(" -|/")


def images_for_offer(offer: ET.Element, limit: int) -> list[str]:
    images: list[str] = []
    for node in offer.findall("./imgs/*"):
        url = (node.get("url") or "").strip()
        if url and url not in images:
            images.append(url)
    return images[:limit]


def build_row(
    offer: ET.Element,
    attrs: dict[str, str],
    settings: dict[str, Any],
    translations: dict[str, Any],
    template: str,
    eur_rate: float,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    images = images_for_offer(offer, int(settings["max_images"]))
    description, review = render_description(template, attrs, images, translations)
    disk = clean_capacity(attrs.get("Dysk", ""))
    ram = clean_capacity(attrs.get("Ilość pamięci RAM", ""))
    ports = parse_ports(attrs.get("Złącza zewnętrzne", ""))
    features = []
    if attrs.get("Kamera", "").lower().startswith("tak"):
        features.append("Eingebaute Webcam")
    if attrs.get("Ekran dotykowy") == "Tak":
        features.append("Touchscreen")
    if "Bluetooth" in attrs.get("Złącza zewnętrzne", ""):
        features.append("Bluetooth")
    price_eur = math.ceil(float(offer.get("price", "0").replace(",", ".")) / eur_rate)

    values: dict[str, str] = {
        HEADERS[0]: "Add",
        "CustomLabel": attrs.get("SKU", ""),
        "*Category": settings["ebay_category"],
        "*Title": build_title(attrs),
        "*ConditionID": settings["condition_id"],
        "VAT%": settings["vat_percent"],
        "*C:Marke": brand(attrs.get("Producent", "")),
        "*C:Bildschirmgröße": screen_size_de(attrs.get("Przekątna ekranu", "")),
        "*C:Prozessor": processor_aspect(
            attrs.get("Procesor", ""), attrs.get("Seria procesora", "")
        ),
        "C:Festplattentyp": (
            "SSD (Solid State Drive)" if attrs.get("Typ dysku") == "SSD"
            else "HDD (Hard Disk Drive)" if attrs.get("Typ dysku") == "HDD"
            else attrs.get("Typ dysku", "")
        ),
        "C:Produktart": "Notebook / Laptop",
        "C:Festplattenkapazität": disk,
        "C:Besonderheiten": "|".join(features),
        "C:SSD-Festplattenkapazität": disk if attrs.get("Typ dysku") == "SSD" else "",
        "C:Grafikprozessor": attrs.get("Model karty graficznej", ""),
        "C:Prozessorgeschwindigkeit": attrs.get("Taktowanie", "").replace(".", ","),
        "C:Maximale Auflösung": attrs.get("Rozdzielczość ekranu", ""),
        "C:Herstellernummer": "Nicht zutreffend",
        "C:Modell": attrs.get("Model", ""),
        "C:Betriebssystem": attrs.get("Zainstalowany system", attrs.get("Licencja", "")),
        "C:Anzahl der Einheiten": "1",
        "C:Maßeinheit": "Einheit",
        "C:Inklusive Ladegerät": "Ja" if attrs.get("W zestawie") else "",
        "C:Arbeitsspeichergröße": ram,
        "C:Grafikprozessortyp": translations["gpu_type"].get(
            attrs.get("Rodzaj karty graficznej", ""), attrs.get("Rodzaj karty graficznej", "")
        ),
        "C:Konnektivität": "|".join(ports),
        "C:Herstellergarantie": "Keine",
        "PicURL": "|".join(images),
        "GalleryType": settings["gallery_type"],
        "*Description": description,
        "*Format": settings["format"],
        "*Duration": settings["duration"],
        "*StartPrice": f"{price_eur:.2f}",
        "*Quantity": str(int(float(offer.get("stock", "0")))),
        "*Location": settings["location"],
        "ShippingProfileName": settings["shipping_profile_name"],
        "ReturnProfileName": settings["return_profile_name"],
        "PaymentProfileName": settings["payment_profile_name"],
        "Product Safety Pictograms": "EBPSP201",
        "Manufacturer Name": brand(attrs.get("Producent", "")),
    }
    return {header: values.get(header, "") for header in HEADERS}, review


def generate(
    feed_bytes: bytes,
    nbp: dict[str, Any],
    settings: dict[str, Any],
    translations: dict[str, Any],
    template: str,
    output_dir: Path,
) -> dict[str, Any]:
    root = ET.fromstring(feed_bytes)
    selected = []
    skipped = Counter()
    rows: list[dict[str, str]] = []
    review_values: set[tuple[str, str]] = set()

    for offer in root.findall("./o"):
        if text(offer.find("./cat")) != settings["xml_category"]:
            continue
        selected.append(offer)
        stock = int(float(offer.get("stock", "0") or 0))
        if stock <= 0:
            skipped["stock_zero"] += 1
            continue
        attrs = offer_attrs(offer)
        missing = [name for name in REQUIRED_XML if not attrs.get(name)]
        if missing:
            skipped["missing_required_xml"] += 1
            review_values.add(("Brak wymaganych pól", f"{attrs.get('SKU', offer.get('id', ''))}: {', '.join(missing)}"))
            continue
        try:
            row, review = build_row(
                offer, attrs, settings, translations, template, nbp["rate"]
            )
        except (ValueError, TypeError) as exc:
            skipped["conversion_error"] += 1
            review_values.add(("Błąd konwersji", f"{attrs.get('SKU', offer.get('id', ''))}: {exc}"))
            continue
        rows.append(row)
        review_values.update(review)

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "ebay-de-laptops.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(["Info", "Version=1.0.0", "Template=fx_category_template_EBAY_DE"])
        writer.writerow(HEADERS)
        writer.writerows([[row[header] for header in HEADERS] for row in rows])

    review_path = output_dir / "translation-review.csv"
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerow(["Pole", "Wartość XML"])
        writer.writerows(sorted(review_values))

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_category": settings["xml_category"],
        "products_in_category": len(selected),
        "products_exported": len(rows),
        "products_skipped": sum(skipped.values()),
        "skipped_reasons": dict(skipped),
        "translation_review_values": len(review_values),
        "nbp": nbp,
        "pricing": "ceil(price_pln / nbp_eur_mid)",
        "condition_id": settings["condition_id"],
        "vat_percent": settings["vat_percent"],
        "csv_columns": len(HEADERS),
    }
    (output_dir / "generation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed-file", type=Path)
    parser.add_argument("--nbp-rate", type=float, help="Test-only EUR rate override")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_json(ROOT / "config" / "settings.json")
    translations = load_json(ROOT / "config" / "translations.json")
    template = (ROOT / "templates" / "description.html").read_text(encoding="utf-8")
    feed_bytes = args.feed_file.read_bytes() if args.feed_file else fetch_bytes(settings["feed_url"])
    nbp = (
        {
            "currency": "euro", "code": "EUR", "table": "A", "number": "TEST",
            "effective_date": "TEST", "rate": args.nbp_rate,
        }
        if args.nbp_rate else fetch_nbp_rate(settings["nbp_url"])
    )
    report = generate(
        feed_bytes, nbp, settings, translations, template, args.output_dir
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["products_exported"] else 2


if __name__ == "__main__":
    sys.exit(main())
