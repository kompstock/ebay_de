# Komputery stacjonarne — gotowe pliki do wdrożenia

Wszystko przetestowałem lokalnie (symulowany feed z desktopem SFF/Tower/Micro-Mini-Tiny, z AIO, z Apple-desktopem oraz z laptopem) — filtrowanie, mapowanie obudowy i logika opisu działają zgodnie z założeniami. Nie mogłem przetestować end-to-end z Twoimi prawdziwymi plikami `translations.json` / `aspects.json` / `manufacturers.json` / `templates/description.html` (nie miałem ich treści), dlatego **koniecznie uruchom workflow w trybie "test" po wdrożeniu**, zanim zrobisz "początkowy"/"nowe".

## Co zrobić, krok po kroku

1. **Podmień w repo cały plik `config/settings.json`** na ten z tej paczki. Zawiera już Twoje wcześniejsze zmiany (Komputery w xml_categories) plus nową kategorię 179 i mapowanie obudowy.

2. **Podmień w repo cały plik `src/generate.py`** na ten z tej paczki. To Twój kod z wbudowanymi poprawkami — nic poza tym się nie zmieniło, zachowanie dla laptopów jest 1:1 takie samo jak dziś (przetestowałem to osobno).

3. **Podmień w repo cały plik `config/ebay-header.csv`** na ten z tej paczki — jedyna zmiana to dodana kolumna `C:Formfaktor`.

4. **Dodaj nowy plik `tools/dodaj_kategorie_179.py`** do repo (nowy plik, nic nie nadpisuje).

5. **Uruchom ten skrypt RAZ**, zanim odpalisz generator z komputerami — dopisuje kategorię 179 do `config/ebay-vocab.json` na bazie Twojej istniejącej kategorii 177 (żebym nie musiał ręcznie przepisywać setek dozwolonych wartości procesorów/modeli — to zbyt ryzykowne). Najprościej: dodaj na końcu istniejącego workflow (`.github/workflows/generate.yml`) krok przed uruchomieniem `src/generate.py`:
   ```yaml
   - name: Dopisz kategorie 179 (komputery) do ebay-vocab.json
     run: python3 tools/dodaj_kategorie_179.py
   ```
   Albo uruchom go lokalnie jednorazowo i zacommituj zmieniony `ebay-vocab.json`.

6. Zrób commit wszystkich czterech plików naraz (żeby nie było momentu, gdzie `generate.py` oczekuje kolumny/kategorii, której jeszcze nie ma).

7. Uruchom workflow w trybie **test**. Sprawdź `review.csv` i `generation-report.json` na stronie Pages — szukaj komputerów (`produktow_w_kategorii`, `pominieto.desktop_aio_pominiety`, `pominieto.desktop_apple_pominiety` powinny się pojawić jako klucze, jeśli feed ma takie oferty).

## Co konkretnie naprawiłem / dodałem w kodzie

- Komputery przechodzą przez ten sam feed `kompre.xml`, kategoria `<cat>Komputery</cat>` — bez drugiego feedu.
- All-in-One i Apple w kategorii "Komputery" są całkowicie pomijane (nie trafiają nawet do review.csv jako błąd — to świadome pominięcie, nie awaria).
- Desktopy **nie są już blokowane** brakiem przekątnej ekranu (wcześniej to by wykluczyło 100% komputerów).
- Opis oferty (`render_description`) **nie wymaga już** pól "Stan ekranu" i "Bateria" dla desktopów — wcześniej ich brak blokował produkt całkowicie (`KeyError`/twarda blokada), teraz są po prostu pomijane w opisie.
- `C:Produktart` = "Desktop" dla komputerów (zamiast "Notebook / Laptop").
- Nowe pole `C:Formfaktor`, wypełniane z atrybutu feedu "Obudowa" (SFF/Tower/Micro-Mini-Tiny/Desktop → wartości zgodne z oficjalnym słownikiem eBay dla kategorii 179).
- `C:Inklusive Ladegerät` dla desktopów liczony z **treści** pola "W zestawie" ("Zasilacz z przewodem" → Ja, "Brak przewodu zasilającego" → Nein) — dla laptopów zostawiłem stare zachowanie bez zmian, żeby nic tam nie zepsuć.
- Wi-Fi/Bluetooth/mikrofon **nie są już automatycznie dopisywane** do desktopów (dla laptopów bez zmian).
- Nowy wpis kategorii 179 w `ebay-vocab.json` (przez skrypt) — z wymaganymi polami Marke/Produktart/Prozessor i pełną listą wartości `Formfaktor` skopiowaną 1:1 z oficjalnego szablonu eBay, który mi przesłałeś.

## Czego świadomie nie ruszałem

- Allegro (`empi.xml`) — bez zmian, nadal tylko laptopy poleasingowe.
- Apple laptopy — bez zmian.
- Apple desktopy (iMac) — pomijane celowo, tak jak ustaliliśmy.
