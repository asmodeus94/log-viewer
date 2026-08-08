# Wytyczne i instrukcje dla Agentów AI i programistów

Plik ten zawiera zbiór kluczowych reguł i uwag dla agentów AI pracujących z repozytorium projektu **Log Viewer**. Przestrzeganie tych zasad jest niezbędne dla zapewnienia stabilności, wysokiej wydajności i spójności rozwijanego kodu.

## 1. Wydajność i obsługa dużych plików (I/O)
- Należy bezwzględnie unikać wprowadzania ciężkich elementów graficznych ("bells and whistles"), które mogłyby pogorszyć wydajność aplikacji, opóźniać ładowanie lub powodować awarie.
- Najwyższym priorytetem jest zawsze wydajność i stabilność podczas przetwarzania ogromnych plików logów (wielogigabajtowych).
- **Bezwzględny zakaz wczytywania całych plików do pamięci** (np. za pomocą `read()` lub `readlines()`).
- Przy odczycie plików należy używać wyłącznie generatorów, iterowania linia po linii (lazy loading) lub bezpiecznego odczytu porcjowanego (chunking, np. `read(chunk_size)`).
- **Uwaga dotycząca `mmap`:** Ze względu na naturę plików logów (które mogą być aktywnie dopisywane przez inne procesy) oraz różnice w blokowaniu plików w systemie Windows, **nie używaj modułu `mmap`**. Prowadzi to do nieprzewidywalnych błędów dostępu i problemów z cross-platformowością. Zamiast tego polegaj na standardowym I/O, generatorach lub chunkingu w wątkach pobocznych.

## 2. Planowanie
- Przed wprowadzaniem jakichkolwiek zmian, agent powinien wejść w tryb "deep planning mode". Oznacza to m.in. zadawanie pytań wyjaśniających i upewnienie się co do celu, chyba że wątpliwości da się rozwiązać przez dogłębną analizę kodu.

## 3. Kompilacja i Skrypty Zewnętrzne
- Skrypty narzędziowe i wspierające proces budowania aplikacji (jak np. skompilowanie plików interfejsu .ui do py) powinny być pisane w wieloplatformowym **Pythonie**, należy unikać specyficznych dla systemów narzędzi takich jak `make`.
- Pliki interfejsu Qt (`.ui`) należy kompilować z wykorzystaniem narzędzia `pyside6-uic` do postaci plików python (np. `ui_*.py`). Nie należy korzystać z dynamicznego ładowania przez `QUiLoader`. Wygenerowane pliki należy traktować jako artefakty i trzymać je w `.gitignore`.
- Kompilacja plików UI powinna odbywać się **przyrostowo** (inkrementalnie). Skrypt powinien weryfikować czas modyfikacji i kompilować jedynie pliki, których wersja `.ui` jest nowsza niż wygenerowany `.py`.
- Preferowanym punktem wejścia do aplikacji łączącym kompilację przyrostową z uruchomieniem jest zautomatyzowany skrypt `run.py`.

## 4. Wieloplatformowość (Cross-platform)
- Wykorzystuj metody neutralne platformowo dla operacji plikowych i systemowych (np. przy uzyskiwaniu metadanych o plikach). Ma to działać jednakowo pod Windowsem, macOS, a także w Linuksie.

## 5. UI Layout i Biznesowa Logika
- Oddzielaj w całości wygląd (layout) od logiki biznesowej, używając do tego dedykowanych plików `.ui` przetrzymywanych w osobnym folderze.
- Wspieraj użycie tzw. "Promoted Widgets" z Qt Designer dla niestandardowych (customowych) komponentów, np. umieszczając je w `log_viewer/ui/`.
- Zarządzaj zależnymi od kontekstu elementami UI (np. stan filtrów lub wyszukiwarki) na poziomie pojedynczych kart. Kodowanie znaków jest zamierzonym wyjątkiem i musi ściśle pozostać globalnym ustawieniem aplikacji.

## 6. Język Komunikacji
- Językiem w którym należy komentować cały kod jest język **polski**. Dodatkowo cała komunikacja z klientem musi się również odbywać po polsku.

## 7. Diagramy Mermaid
- Przy projektowaniu lub edycji diagramów opartych na Mermaid, nie używaj "non-breaking spaces" (tzw. twardych spacji). Zawsze umieszczaj nazwy węzłów w cudzysłowach, szczególnie te, w których występują znaki specjalne (tj. np. nawiasy, kropki, ampersandy).

## 8. Testy Jednostkowe (Testy na plikach)
- Jeśli test zawiera odwołania do plików, zawsze stosuj w asercjach ścieżki normalizowane przez `os.path.normpath` – jest to w szczególności ważne dla platformy Windows.
- Pamiętaj o jawnym zamykaniu plików i obsługuj wyjątek zablokowanych plików (`PermissionError`) przed próbą wyrzucenia testowego pliku poleceniem typu `os.unlink`.

## 9. Przypisywanie uprawnień do plików w Py
- Podczas tworzenia plików w Pythonie gdzie wymagane są restrykcyjne uprawnienia, preferowanym i bezpieczniejszym podejściem jest zbudowanie niestandardowego `opener`'a w funkcji `open()` zamiast używania kombinacji `os.open()` oraz `os.fdopen()`, by zapobiec tzw. "file descriptor leaks".

## 10. Internacjonalizacja i Słowniki Językowe
- Kiedy wprowadzane są nowe teksty w plikach UI, dodawaj zawsze i bezwzględnie ich tłumaczenia w obu dedykowanych słownikach w pliku `log_viewer/i18n.py`. Zaktualizuj także testy w module weryfikującym (`tests/test_i18n.py`).
- Jeżeli pasek narzędzi lub inne elementy wymagają dynamicznych zmian wynikających np. z tłumaczeń to ich logika powinna rezydować bezpośrednio w kodzie Pythona, nie wewnątrz w `.ui`.

## 11. Zależności i Wymagania podczas Testów
- Upewnij się, że odpowiednio zainstalowane są `pip install -r requirements.txt pytest xvfbwrapper` przed uruchomieniem jakichkolwiek testów graficznych. Należy także puszczać uprzednio kompilację UI w pętli. Do puszczania testów używaj polecenia: `xvfb-run -a python -m pytest tests/`.

## 12. Responsywność Interfejsu (GUI) i Wątkowanie
- **Złota zasada:** Główny wątek aplikacji (GUI thread) nie może być nigdy blokowany przez operacje wejścia/wyjścia (I/O) ani intensywne obliczenia (np. parsowanie logów, zaawansowane filtrowanie).
- Wszelkie długotrwałe operacje muszą być oddelegowane do wątków pobocznych przy użyciu mechanizmów Qt, takich jak `QThread`, `QRunnable` lub `QThreadPool`.
- Do wyświetlania dużych zbiorów danych używaj architektury Model/View w Qt (np. `QAbstractTableModel`, mechanizm `fetchMore()`). Unikaj widgetów uwarunkowanych elementowo (np. `QTableWidget`), ponieważ zniszczy to wydajność przy tysiącach wierszy.

## 13. Standardy Jakości Kodu (Clean Code)
- **Typowanie statyczne:** Każda nowa funkcja, metoda i klasa musi posiadać adnotacje typów (Type Hints wg PEP 484) dla argumentów i wartości zwracanych (np. `def parse_line(line: str) -> dict:`).
- **Pythonic style:** Wykorzystuj wbudowane mechanizmy języka – używaj f-stringów do formatowania tekstu, list/dict comprehensions dla wydajności, oraz menedżerów kontekstu (`with`) do zarządzania zasobami i blokadami.
- Preferuj nowoczesny moduł `pathlib` do operacji na ścieżkach nad tradycyjnym `os.path` (chyba że konwencja istniejącego kodu/testów wymaga inaczej).
- Zmiany w kodzie nie mogą generować nowych ostrzeżeń linterów (utrzymuj kod zgodny ze standardami PEP 8).

## 14. Dobre praktyki Qt / PySide6 (Z zebranych doświadczeń)
- **Wyjątki w QThread:** Zawsze przechwytuj `BaseException` (a nie tylko `Exception`) w głównych metodach wątków pracujących w tle. Pozwala to na poprawne złapanie `SystemExit` i zapobiega niekontrolowanym awariom pętli zdarzeń (np. podczas pakowania przez PyInstaller).
- **Zrzuty pamięci na sygnałach (Segfault):** PySide6 potrafi zagubić autorskie obiekty Pythona i zabić proces wywrotnym błędem "Access Violation" w C++, jeśli spróbujesz je przekazać jako argumenty sygnałów wywoływanych z innych wątków (`Qt.QueuedConnection`), a instancja w wątku po chwili zostanie odśmiecona (np. lokalny Bitset usunięty po wyjściu z funkcji `run()`). Zawsze rozbijaj złożone klasy do przekazania na uniwersalne struktury Pythona (np. zwykła List, array.array) przed ich wysłaniem przez Signal. Unikaj zagnieżdżonych "closures" w odbieraniu slotów, definiuj bezpiecznie przypięte metody klas `Slot`.
- **Zarządzanie C++ i PyObject (Thread i Memory Leaks):** Nieprzemyślane nadpisywanie referencji do QThread przed zakończeniem jego pracy (np. w pętli timera dla Tail Mode) tworzy drastyczne wycieki uchwytów (Handle Leaks) w środowisku Windows (0xCFFFFFFF). Startowanie wątków limituj blokadą stanu. Pamiętaj też, że sprawdzenie stanu przez `thread.isRunning()` rzuca wywrotnym z błędem środowiska `RuntimeError: Internal C++ object already deleted` po tym, gdy zadziała `deleteLater` po stronie C++, a instancja Pythonowego QThread nadal istnieje jako pusty duch. Sprawdzaj stany opatulając je w blok `try...except RuntimeError`.
- **Asynchroniczne Testy i Obiekty Widma (NoneType):** W bardzo asynchronicznym środowisku żądania Timerów lub Testów mogą odświeżyć widok żądając pobrania np. długości bufora (`len()`), na milisekundy zanim inny wątek zdąży przypisać mu instancję w klasie po starcie. Bezwzględnie testuj te luki używając `getattr(self, "obiekt", None) is not None` zanim wywołasz w ciemno metody, na wypadek tymczasowych pustek.
- **Zarządzanie scrollbarem:** Edytując masowo zawartość tekstową (np. `QPlainTextEdit`), zawsze używaj blokowania modyfikacji layoutu (np. `cursor.beginEditBlock()` oraz `cursor.endEditBlock()`). Ponadto, jeśli wykonujesz programowe przewinięcie (np. przywrócenie stanu okna), tymczasowo blokuj sygnały paska przewijania (`blockSignals(True)` / `False`), by uniknąć wywołania logiki np. ciągłego (infinite) doładowywania zarezerwowanej dla użytkownika.
- **Tłumienie awarii C++:** Pod żadnym pozorem nie otwieraj modalnych okien dialogowych (jak `QMessageBox`) bezpośrednio z poziomu zdarzeń/slotów odbierających dane z workerów, jeżeli w międzyczasie wątek został anulowany lub okno rodzicielskie jest właśnie niszczone. Często kończy się to natychmiastowym zrzutem pamięci (segfault).
- **Przetwarzanie po wątkach (Dostęp do plików):** Jeśli slot powiązany z `finished()` z asynchronicznego workera z powrotem odwołuje się do fizycznego pliku na dysku, wykonuj te operacje w bloku `try...except OSError`. Plik mógł ulec usunięciu (np. w środowisku automatycznych testów `pytest` przy zamykaniu temp files) podczas trwania operacji w tle.
- **Czcionki stałej szerokości (Monospace):** Jeśli aplikacja używa widoku kodu lub logów, nie używaj aliasów stringowych (jak "Monospace") z poziomu QSS. Używaj zawsze `QFontDatabase.systemFont(QFontDatabase.FixedFont)`, by uniknąć spadków wydajności aplikacji oraz błędów/spamu z systemu operacyjnego na temat brakującej czcionki.

## 15. Optymalizacja operacji listowych
- Do przeszukiwania posortowanych kolekcji i list (np. w mapowaniach numerów linii z fizycznymi bytami) zawsze używaj modułu **`bisect`** (`bisect_left` lub `bisect_right`) zamiast wbudowanego, liniowego `list.index()`.
- W przypadku operowania na zbiorach wyników mogących przekraczać miliony elementów (np. filtrowanie logów), bezwzględnie unikaj alokacji dużych list w Pythonie. Zamiast tego używaj wysoce zoptymalizowanej struktury **`Bitset`** opierającej się o wbudowany typ `array.array('Q')` i realizuj zliczanie iteracji wspierając się metodami implementowanymi po stronie języka C (np. `itertools.accumulate` w połączeniu z `int.bit_count`), by eliminować blokujące główny lub poboczny wątek pętle.
