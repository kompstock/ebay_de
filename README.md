# Folder na raport z eBaya

Tryby **nowe** i **aktualizacja** potrzebują aktualnej listy Twoich aukcji.
Bez niej narzędzie nie wie, co już jest wystawione.

## Jak pobrać

W eBayu: **Seller Hub → Berichte (Raporty) → Downloads → All active listings**.
Raport przychodzi jako plik CSV.

## Jak wgrać

1. Wejdź do folderu **`input/ebay/`** na GitHubie.
2. **Add file → Upload files**, przeciągnij pobrany plik.
3. Commit.

Nazwy nie trzeba zmieniać — plik może zostać pod swoją własną, tą z eBaya.
Jeśli w folderze leży kilka plików, brany jest najnowszy, a jego nazwa trafia
do `output/generation-report.json` w polu `zrodlo_raportu`. Zawsze widzisz,
z czego poszedł przebieg.

Stare pliki możesz kasować przy okazji, ale nie musisz.

## Co jeśli wgrasz nie ten plik

Przebieg zatrzyma się z komunikatem, które kolumny nie pasują. Eksport
sprzedanych albo zakończonych ofert nie przejdzie — bez tej kontroli
narzędzie po cichu uznałoby, że nie masz żadnych aktywnych aukcji, i przy
trybie **nowe** wystawiłoby wszystko po raz drugi.

## Kiedy odświeżać

**Po każdym wgraniu pliku `Add` na eBay.** Numery aukcji nadaje eBay i pojawiają
się dopiero w kolejnym raporcie. Jeśli tego nie zrobisz, następny przebieg
w trybie `nowe` uzna te oferty za nieistniejące i wystawi je drugi raz.

Przed zwykłą aktualizacją cen wystarczy raport z tego samego dnia.

## Stara ścieżka

`input/aktywne.csv` nadal działa i ma pierwszeństwo dopiero po `input/ebay/`.
Jeśli oba istnieją, wygrywa plik z `input/ebay/`.
