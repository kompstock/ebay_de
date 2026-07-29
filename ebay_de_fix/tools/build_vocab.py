#!/usr/bin/env python3
"""Wyciaga dozwolone wartosci aspektow z szablonu kategorii eBay.

Szablon (config/ebay-template.csv) zawiera wiersze:
    Info;">>> The recommended value(s) for aspect Marke: Acer; Apple; ..."

Zamiast zgadywac slowniki, generujemy je z pliku eBaya:
    python3 tools/build_vocab.py
-> config/ebay-vocab.json

Po kazdej aktualizacji szablonu w eBayu wystarczy podmienic CSV i uruchomic to.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "config" / "ebay-template.csv"
OUT = ROOT / "config" / "ebay-vocab.json"

raw = SRC.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
vocab, required = {}, []
for line in raw.split("\n"):
    m = re.match(r'Info;"?>>> The recommended value\(s\) for aspect ([^:]+): (.*?)"?$', line)
    if m:
        vocab[m.group(1).strip()] = [v.strip() for v in m.group(2).split(";") if v.strip()]
    r = re.match(r'Info;"?>>> The required aspects are (.*?)"?$', line)
    if r:
        required = [v.strip() for v in r.group(1).split(";") if v.strip()]

OUT.write_text(json.dumps(
    {"_zrodlo": SRC.name, "_wymagane": required, "aspekty": vocab},
    ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"aspektow: {len(vocab)}, wymagane: {required}")
for k, v in sorted(vocab.items(), key=lambda i: -len(i[1]))[:6]:
    print(f"  {k}: {len(v)}")
