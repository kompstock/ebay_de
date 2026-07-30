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
| `config/settings.json` | kategorie, dopłata, progi, profile eBay |
| `config/translations.json` | tłumaczenia całych wartości opisowych |
| `config/aspects.json` | mapowania aspektów, porty, klawiatury, systemy |
| `config/manufacturers.json` | dane GPSR producentów |
| `config/ebay-vocab.json` | dozwolone wartości, generowane z szablonu eBaya |
| `templates/description.html` | szablon opisu oferty |

## Narzędzia

```bash
python3 tools/build_vocab.py       # przebuduj słownik po aktualizacji szablonu eBay
python3 tools/collect_values.py    # lista wartości z feedu do przetłumaczenia
```
