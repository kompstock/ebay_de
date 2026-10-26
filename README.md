# Paczka do wgrania — Hiszpania, 26.10.2026

Wgraj do `kompstock/ebay_de` z zachowaniem ścieżek. 14 plików.

**NIE MA TU** `config/gwarancja-24.csv`, `config/sku-reczne.csv` ani `input/` — te
pliki żyją własnym życiem na GitHubie i nadpisanie ich kasuje zawartość.

## Nowe pliki (sama Hiszpania)

| Plik | Co to jest |
|---|---|
| `config/kraje/es.json` | rynek ES: sufiks `SPAIN`, VAT 21%, dopisek +3 €, 6 profili, profile handlowe |
| `config/kraje/es/ebay-header.csv` | 131 kolumn szablonu eBay.es, `Country=PL` |
| `config/kraje/es/ebay-vocab.json` | dozwolone wartości aspektów, kategorie 177, 111422, 179 |
| `config/kraje/es/aspects.json` | reguły złączy, kolorów, systemów — wartości hiszpańskie |
| `config/kraje/es/translations.json` | 369 tłumaczeń z arkusza |
| `templates/es/_style.html` | sam CSS |
| `templates/es/description-notebook.html` | opis laptopa |
| `templates/es/description-desktop.html` | opis peceta |
| `templates/es/description-desktop-nowy.html` | opis nowego peceta |

## Zmienione pliki

### `src/generate.py` — dwie poprawki, bez nich Hiszpania nie działa

1. **`znaki_dozwolone`** — rynek może wskazać litery, które są jego własne, a
   wyglądają na polskie. Hiszpański pisze `Configuración` przez `ó`, czyli ten
   sam znak co polskie `który`. Bez tego odpadała **każda** hiszpańska oferta.
   Siatka na polskie *słowa* działa dalej w całości.
2. **`format_generacji_cpu` przyjmuje listę** — eBay.es trzyma na jednej liście
   `Intel Core i5 8th Gen.` po angielsku i `Intel Core i5 12ª generación` po
   hiszpańsku. Jeden wzorzec nie trafiał w obie formy, a `*C:Procesador` jest
   wymagany, więc oferty ze starszym procesorem odpadały.

Niemcy i Włochy działają dokładnie jak dotąd — oba mechanizmy włączają się
tylko wtedy, gdy kraj coś w nich ustawi.

### `tests/test_generate.py`

- **18 testów było martwych.** W środku pliku stało `if __name__ == "__main__":
  unittest.main()`, a `unittest.main()` kończy proces — wszystko zdefiniowane
  niżej nigdy się nie uruchamiało. Usunięte, z ostrzeżeniem w tym miejscu.
- Nowa klasa `AlfabetRynku` — 5 testów na obie poprawki powyżej, sprawdzone
  mutacją (po zepsuciu poprawki test pada).
- **172 testy, wszystkie przechodzą** (przedtem uruchamiało się 149 ze 167).

### `tools/wczytaj_tlumaczenia.py` — cztery błędy importera

1. **Baza brana z pliku niemieckiego**, nie z pliku rynku — drugie wczytanie
   arkusza wymazywało wszystko, czego w arkuszu nie ma (mapowanie portów,
   kolory, nazwy systemów wracały po niemiecku). Najgroźniejszy z całej czwórki.
2. **Sekcje `aspects` z listami** wywracały import na `list indices must be
   integers` — 35 wierszy.
3. **Sekcje `profil` spłaszczane** — 38 z 55 wierszy lądowało na złym poziomie,
   tworząc klucze, których generator nigdy nie czyta.
4. **Kolizja nazw** — pomocnicza funkcja `cel` przykrywała zmienną `cel`;
   import wywracał się na `TypeError: 'dict' object is not callable`.

Plus **blokada wpisów przedawnionych**: wiersz, którego nie ma już w konfiguracji
źródłowej, nie wraca po cichu, tylko ląduje na liście do decyzji. Złapała dwa
wiersze, które wnosiły nieprawdę — dla „Normalne ślady użytkowania" arkusz
podawał „Nuevo, sin signos de uso."

### `tools/eksport_do_tlumaczenia.py`

Kolumna `po_wlosku` → `tlumaczenie` (przy hiszpańskim arkuszu tłumacz szukał
kolumny, której nie było). Docstring mówi teraz wprost, czym ma się kierować
tłumacz: `klucz` to prawda o towarze, `po_niemiecku` to wzorzec stylu, a
rozbieżność między nimi zgłasza się, zamiast tłumaczyć.

### `config/settings.json`

Z profili `Desktop-PC nowy` i `Desktop-PC nowy GW24` usunięty wpis
`"Normalne ślady użytkowania": "Neu, ohne Gebrauchsspuren."` — kazał opisowi
mówić „bez śladów użytkowania" o produkcie, który wg feedu te ślady ma.
W `_about` stoi teraz, dlaczego nie wolno go przywrócić.

## Po wgraniu

1. **`VerifyAdd` na eBay.es.** Wtedy wyjdzie, czy nazwy profili handlowych
   działają w gołej formie (`envio`, `return`, `lap / 167E / PL / 4FBD1D47 / PL`).
   DE i IT mają je zapisane z numerem, np. `… - (ID: 254129534019)`. Jeśli eBay
   nie dopasuje — dopisujemy numery do `config/kraje/es.json`.
2. **Profil płatności** miał status „Actualización en curso" — niech się najpierw
   ustabilizuje.
3. **Limit wystawiania.** Zostawało ~2,2 mln z 13 mln PLN. Trzeci rynek to
   trzecia kopia każdej oferty — progi stanów trzeba obniżyć przed startem.

## Czego tu nie ma, a warto wiedzieć

`templates/es/description-tablet-nowy.html` nie jest potrzebny do startu —
żaden profil go nie używa, tablety szły ręcznie.

`_podpowiedzi` w `config/kraje/es/translations.json` zostały po niemiecku.
Trafiają wyłącznie do raportu jako „[propozycja]" przy nieprzetłumaczonej
wartości — do oferty nigdy, bo taka wartość i tak blokuje produkt. Do dorzucenia
w następnym arkuszu.
