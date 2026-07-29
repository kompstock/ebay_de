# KOMPRE eBay DE CSV

Generator pobiera publiczny feed KOMPRE, wybiera produkty z kategorii `Laptopy`,
pobiera średni kurs EUR z tabeli A NBP i tworzy plik CSV zgodny z szablonem
eBay Germany.

## Najważniejsze reguły

- tylko produkty z `<cat>Laptopy</cat>` i `stock > 0`,
- cena EUR: `ceil(cena PLN / kurs EUR NBP)`,
- VAT: `19`,
- ilość: wartość `stock`,
- `ConditionID`: `3000`,
- kodowanie: UTF-8, separator `;`,
- opis: niemiecki HTML generowany z szablonu,
- uruchamianie: ręczne w GitHub Actions.

## Uruchomienie lokalne

```powershell
python src/generate.py
python -m unittest discover -s tests -v
```

Pliki wynikowe pojawią się w folderze `output/`.

## Wyniki

- `ebay-de-laptops.csv` – plik importu eBay,
- `generation-report.json` – dane kursu, statystyki i ostrzeżenia,
- `translation-review.csv` – wartości wymagające sprawdzenia mapowania.

