#!/usr/bin/env python3
"""Zbiera z calego feedu wszystkie ROZNE wartosci pol opisowych.

To jest sposob na zbudowanie slownika raz, zamiast lapac braki produkt po
produkcie. Feed jest szablonowy - tych samych zdan jest kilkanascie, nie tysiace.

    python3 tools/collect_values.py --feed-file feed.xml > worklist.csv

Kolumna 'niemiecki' jest pusta - uzupelniasz ja i wklejasz do
config/translations.json -> values (klucz = kolumna 'klucz').
"""
import argparse, csv, json, re, sys, unicodedata, urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLA = ["Kondycja sprzętu", "Stan obudowy", "Stan ekranu", "Bateria",
        "W zestawie", "Informacje dodatkowe", "Złącza zewnętrzne"]


def norm(v):
    v = unicodedata.normalize("NFKD", str(v or ""))
    v = "".join(c for c in v if not unicodedata.combining(c)).replace("\u0142", "l")
    return re.sub(r"\s+", " ", v).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed-file", type=Path)
    args = ap.parse_args()
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    known = json.loads((ROOT / "config" / "translations.json").read_text(encoding="utf-8"))["values"]

    if args.feed_file:
        data = args.feed_file.read_bytes()
    else:
        req = urllib.request.Request(settings["feed_url"], headers={"User-Agent": "kompre/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()

    counts = defaultdict(Counter)
    for offer in ET.fromstring(data).findall("./o"):
        cat = (offer.findtext("./cat") or "").strip()
        if cat not in settings["xml_categories"]:
            continue
        for a in offer.findall("./attrs/a"):
            name = (a.get("name") or "").strip()
            if name in POLA and (a.text or "").strip():
                counts[name][re.sub(r"\s+", " ", a.text.strip())] += 1

    w = csv.writer(sys.stdout, delimiter=";", lineterminator="\n")
    w.writerow(["pole", "wystapien", "status", "klucz", "polski", "niemiecki"])
    for pole in POLA:
        for value, n in counts[pole].most_common():
            key = norm(value)
            w.writerow([pole, n, "OK" if key in known else "BRAK", key, value,
                        known.get(key, "")])


if __name__ == "__main__":
    main()
