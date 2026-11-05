#!/usr/bin/env python3
"""Wyciaganie i wstawianie tekstu w szablonach opisu.

Szablon opisu to HTML: znaczniki, miejsca na dane w podwojnych klamrach i
proza dla kupujacego. Prozy jest w nim ponad 700 slow na szablon - wiecej niz
w calej reszcie konfiguracji razem wzietej - a mimo to nigdy nie trafiala do
arkusza tlumaczen. Tlumacz dostawal pliki HTML do reki albo nie dostawal nic.

Dlaczego nie dawac plikow HTML: skasowany nawias w "{{processor}}" albo
rozjechany znacznik psuje oferte, a widac to dopiero po wystawieniu na eBayu.
Tutaj tlumacz widzi SAM TEKST, a znaczniki skleja z powrotem skrypt.

Tekst idzie do arkusza BEZ ENCJI HTML ("é", nie "&eacute;") i tak samo wraca.
Pliki sa w UTF-8, a opis i tak jedzie w kolumnie CSV oznaczonej CC=UTF-8,
wiec encje nie sa do niczego potrzebne - byly tylko nawykiem z pierwszego
rynku. Dzieki temu klucz w arkuszu i tekst w pliku to dokladnie to samo i
nie ma miejsca na rozjazd.
"""
from __future__ import annotations

import html
import re

# Kawalki, ktorych tlumacz nie ma ogladac: CSS i skrypty.
_NIEWIDOCZNE = re.compile(r"(?is)<(style|script)\b.*?</\1>")
_TOKEN = re.compile(r"<[^>]*>|[^<]+")
_ALT = re.compile(r'(\balt=")([^"]*)(")')
_MIEJSCE = re.compile(r"\{\{[a-z_]+\}\}")


def _do_tlumaczenia(tekst: str) -> bool:
    """Czy ten kawalek to proza, czy tylko technikalia.

    Odpada sam odstep, sam znacznik miejsca ({{spec_rows}}) i sama
    interpunkcja. Zostaje to, co kupujacy faktycznie czyta.
    """
    bez = _MIEJSCE.sub(" ", html.unescape(tekst))
    return bool(re.search(r"[^\W\d_]{2,}", bez, re.UNICODE))


def wezly(zawartosc: str) -> list[str]:
    """Kawalki prozy z szablonu, w kolejnosci, bez powtorzen.

    Powtorzenie nie dostaje osobnego wiersza - "30 jours pour retourner" stoi
    w szablonie trzy razy i ma byc przetlumaczone raz, tak samo wszedzie.
    """
    out: list[str] = []
    widziane: set[str] = set()

    def dorzuc(kawalek: str) -> None:
        czysty = html.unescape(kawalek).strip()
        if czysty and czysty not in widziane and _do_tlumaczenia(kawalek):
            widziane.add(czysty)
            out.append(czysty)

    for token in _TOKEN.finditer(_NIEWIDOCZNE.sub(" ", zawartosc)):
        kawalek = token.group(0)
        if kawalek.startswith("<"):
            for m in _ALT.finditer(kawalek):
                dorzuc(m.group(2))
        else:
            dorzuc(kawalek)
    return out


def miejsca(tekst: str) -> set[str]:
    """Znaczniki miejsc w kawalku tekstu - musza przetrwac tlumaczenie."""
    return set(_MIEJSCE.findall(tekst))


def podmien(zawartosc: str, stare: str, nowe: str) -> tuple[str, int]:
    """Podmienia KAZDE wystapienie kawalka prozy. Zwraca (tresc, ile razy).

    Porownujemy po tekscie bez encji, wiec wpis z arkusza trafia tak samo w
    "&eacute;tat" jak w "état". Wstawiamy zawsze zwykly tekst.
    """
    cel = html.unescape(stare).strip()
    ile = 0
    wynik: list[str] = []
    # Wewnatrz <style> i <script> nie ruszamy NICZEGO. Sam selektor CSS nigdy
    # nie trafi do arkusza, ale gdyby kiedys trafil, podmiana rozwalilaby wyglad.
    w_srodku = 0

    def zamien_alt(m: re.Match) -> str:
        nonlocal ile
        if html.unescape(m.group(2)).strip() == cel:
            ile += 1
            return m.group(1) + nowe.replace('"', "&quot;") + m.group(3)
        return m.group(0)

    for token in _TOKEN.finditer(zawartosc):
        kawalek = token.group(0)
        if kawalek.startswith("<"):
            nazwa = re.match(r"</?\s*(style|script)\b", kawalek, re.I)
            if nazwa:
                w_srodku += -1 if kawalek.startswith("</") else 1
                w_srodku = max(w_srodku, 0)
            wynik.append(kawalek if w_srodku else _ALT.sub(zamien_alt, kawalek))
        elif w_srodku:
            wynik.append(kawalek)
        elif html.unescape(kawalek).strip() == cel:
            przed = kawalek[:len(kawalek) - len(kawalek.lstrip())]
            po = kawalek[len(kawalek.rstrip()):]
            wynik.append(f"{przed}{nowe}{po}")
            ile += 1
        else:
            wynik.append(kawalek)
    return "".join(wynik), ile
