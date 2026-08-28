# KOMPRE → eBay.de

Generator plików CSV do eBay File Exchange z feedu XML.

## Jak używać

Wszystko dzieje się w zakładce **Actions → Generuj CSV eBay DE → Run workflow**.
Wybierasz tryb, po przebiegu pobierasz plik ze strony Pages albo z artefaktu.

| tryb | co potrzebne | co dostajesz |
|---|---|---|
| `pierwsze` | nic | `ebay-add.csv` ze wszystkimi produktami |
| `nowe` | `input/aktywne.csv` | `ebay-add.csv` tylko z nowymi SKU |
| `aktualizacja` | `input/aktywne.csv` | `ebay-revise.csv` z cenami i ilościami |
| `test` | jak wyżej | to samo z akcją `VerifyAdd` — eBay sprawdza, nic nie wystawia |

`input/aktywne.csv` to raport **All active listings** pobrany z eBaya.
Przeciągasz go do folderu `input/` przez przeglądarkę i commitujesz.

**Po każdym wgraniu `Add` pobierz raport ponownie.** Numery aukcji nadaje eBay
i bez świeżego raportu narzędzie nie wie, że te oferty już istnieją.

## Laptopy i komputery

Oba typy jadą **jednym przebiegiem** i domyślnie lądują w jednym `ebay-add.csv`.
Rozdzielone są dane, nie proces — inaczej tryb `aktualizacja` wyzerowałby aukcje
tego typu, którego akurat nie ma w feedzie.

Wszystko, co zależy od typu towaru, siedzi w `settings.json` w bloku
`profile_produktu`: szablon opisu, nazwy pól w feedzie, pola wymagane, kategoria
eBay, cechy zakładane z góry. W kodzie nie ma ani jednego `if kategoria == "Komputery"`.

| | laptop | komputer |
|---|---|---|
| źródło | XML sklepowy **+ Allegro** | **tylko XML sklepowy** |
| kategoria eBay | 177, Apple 111422 | 179 |
| szablon | `description-notebook.html` | `description-desktop.html` |
| pola wymagane | z przekątną ekranu | bez ekranu i baterii |
| Wi-Fi / Bluetooth / mikrofon | zakładane | **nie zakładane** |
| `Inklusive Ladegerät` | niepuste „W zestawie" = Ja | z treści pola |

Nowy typ towaru = wpis w `typ_produktu` + blok w `profile_produktu` + szablon.

Komputery z Allegro są zablokowane celowo: `empi.xml` nie ma pola `Obudowa`, więc
nie dałoby się ani ustalić `C:Formfaktor`, ani odsiać All-in-One. Próba dopisania
kategorii komputerowej do `allegro.kategorie_zrodlowe` kończy się blokadą.

Jeśli eBay odrzuci wiersze kategorii 179 przez pustą kolumnę `*C:Bildschirmgröße`
(pochodzi z szablonu laptopów), ustaw `rozdziel_pliki_add: true` — dostaniesz
`ebay-add-laptopy.csv` i `ebay-add-komputery.csv` osobno.

## Zasady, których pilnuje kod

- Brak kompletnych danych GPSR → produkt nie trafia do CSV.
- Wartość opisowa bez tłumaczenia w słowniku → produkt nie trafia do CSV.
- Polski znak lub polskie słowo w gotowym opisie → produkt nie trafia do CSV.
- Wartość aspektu spoza słownika eBaya → pole zostaje puste, wartość ląduje w `review.csv`.
- Sufiks tytułu wynika z pola `Zainstalowany system`. Nigdy nie jest stały.
- Klasa stanu (`[Klasa A-]`) nie pojawia się w opisie — służy wyłącznie do `ConditionID`.
- Apple trafia do kategorii 111422, reszta do 177.
- Cena = `PLN / kurs NBP` w górę, plus ukryta dopłata za wysyłkę z `settings.json`.

## Bramki bezpieczeństwa

- Mniej niż `min_produktow_w_feedzie` produktów z zapasem → przerwanie.
- Zerowanie objęłoby więcej niż `max_udzial_zerowanych` aktywnych aukcji → przerwanie.
- SKU z kilkoma aktywnymi aukcjami → pomijany, trafia do raportu.
- Tryb wymagający raportu bez raportu → przerwanie.

## Pliki konfiguracyjne

| plik | co zawiera |
|---|---|
| `config/settings.json` | kategorie, dopłata, progi, profile eBay, `profile_produktu` |
| `config/translations.json` | tłumaczenia całych wartości opisowych (wspólne dla obu typów) |
| `config/aspects.json` | mapowania aspektów, porty, klawiatury, systemy |
| `config/manufacturers.json` | dane GPSR producentów |
| `config/ebay-vocab.json` | dozwolone wartości; 177 i 111422 z szablonu eBaya, 179 dopisane |
| `templates/_style.html` | CSS wspólny dla obu szablonów opisu |
| `templates/description-notebook.html` | opis laptopa |
| `templates/description-desktop.html` | opis komputera |

Gdy tłumaczenie ze wspólnego słownika jest nieprawdą dla drugiego typu
(„Zasilacz z przewodem" to dla laptopa „Notebook, Netzteil mit Kabel"),
podmień je w `profile_produktu.<typ>.nadpisz_tlumaczenia` zamiast psuć wspólny wpis.

## Narzędzia

```bash
python3 tools/build_vocab.py       # przebuduj słownik po aktualizacji szablonu eBay
```

```bash
python3 tools/collect_values.py    # lista wartości z feedu do przetłumaczenia
```

`build_vocab.py` zostawia nietknięte kategorie, których nie ma w szablonie eBaya —
dzięki temu nie kasuje kategorii 179. Do odtworzenia jej wpisu służy
`tools/dodaj_kategorie_179.py`.
