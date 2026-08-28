#!/usr/bin/env python3
"""Wyciaga dozwolone wartosci aspektow z szablonu kategorii eBay.

Szablon moze obejmowac kilka kategorii naraz - wtedy w pliku sa sekcje
"Info;>>> For categoryId: 177". Slownik budujemy osobno dla kazdej.

Kategorie, ktorych w szablonie NIE MA, zostaja nietkniete. Wczesniej ten skrypt
nadpisywal caly plik, wiec kazde odswiezenie slownika po cichu kasowalo
kategorie 179 (komputery) - jej szablonu nie ma w ebay-template.csv, bo
dopisuje ja tools/dodaj_kategorie_179.py. Efekt byl taki, ze komputery
przestawaly wychodzic bez zadnego komunikatu.

    python3 tools/build_vocab.py   ->   config/ebay-vocab.json
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "config" / "ebay-template.csv"
OUT = ROOT / "config" / "ebay-vocab.json"

raw = SRC.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
kategorie: dict[str, dict] = {}
biezaca = None

for line in raw.split("\n"):
    m = re.match(r"Info;>>> For categoryId:\s*(\d+)", line)
    if m:
        biezaca = m.group(1)
        kategorie.setdefault(biezaca, {"_wymagane": [], "aspekty": {}})
        continue
    if not biezaca:
        continue
    m = re.match(r'Info;"?>>> The recommended value\(s\) for aspect ([^:]+): (.*?)"?$', line)
    if m:
        kategorie[biezaca]["aspekty"][m.group(1).strip()] = [
            v.strip() for v in m.group(2).split(";") if v.strip()]
    m = re.match(r'Info;"?>>> The required aspects are (.*?)"?$', line)
    if m:
        kategorie[biezaca]["_wymagane"] = [v.strip() for v in m.group(1).split(";") if v.strip()]

zachowane = {}
if OUT.is_file():
    poprzednie = json.loads(OUT.read_text(encoding="utf-8")).get("kategorie", {})
    zachowane = {k: v for k, v in poprzednie.items() if k not in kategorie}

OUT.write_text(json.dumps({"_zrodlo": SRC.name, "kategorie": {**zachowane, **kategorie}},
                          ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
for kat, dane in kategorie.items():
    print(f"  kategoria {kat}: {len(dane['aspekty'])} aspektow, wymagane {dane['_wymagane']}")
for kat, dane in zachowane.items():
    print(f"  kategoria {kat}: zachowana bez zmian (nie ma jej w {SRC.name}), "
          f"{len(dane.get('aspekty', {}))} aspektow")
