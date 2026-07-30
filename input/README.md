# Folder na raport z eBaya

Tryby **nowe** i **aktualizacja** potrzebują aktualnej listy Twoich aukcji.
Bez niej narzędzie nie wie, co już jest wystawione.

## Jak pobrać

W eBayu: **Seller Hub → Berichte (Raporty) → Downloads → All active listings**.
Raport przychodzi jako plik CSV.

## Jak wgrać

1. Wejdź do tego folderu na GitHubie.
2. **Add file → Upload files**, przeciągnij pobrany plik.
3. Zmień nazwę na dokładnie **`aktywne.csv`**.
4. Commit.

## Kiedy odświeżać

**Po każdym wgraniu pliku `Add` na eBay.** Numery aukcji nadaje eBay i pojawiają
się dopiero w kolejnym raporcie. Jeśli tego nie zrobisz, następny przebieg
w trybie `nowe` uzna te oferty za nieistniejące i wystawi je drugi raz.

Przed zwykłą aktualizacją cen wystarczy raport z tego samego dnia.
