# Wytyczne i instrukcje dla Agentów AI i programistów

Plik ten zawiera zbiór kluczowych reguł i uwag dla agentów AI pracujących z repozytorium projektu **Log Viewer**. Przestrzeganie tych zasad jest niezbędne dla zapewnienia stabilności i spójności rozwijanego kodu.

## 1. Wydajność i stabilność
- Należy bezwzględnie unikać wprowadzania ciężkich elementów graficznych ("bells and whistles"), które mogłyby pogorszyć wydajność aplikacji, opóźniać ładowanie lub powodować awarie.
- Najwyższym priorytetem jest zawsze wydajność i stabilność podczas przetwarzania ogromnych plików (wielogigabajtowych).

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
- Wspieraj użycie tzw. "Promoted Widgets" z Qt Designer dla niestandardowych (customowych) komponentów, np. umieszczając je w `log_reader/ui/`.
- Zarządzaj zależnymi od kontekstu elementami UI, (np. stan filtrów lub wyszukiwarki), na poziomie pojedynczych kart. Kodowanie znaków jest zamierzonym wyjątkiem i musi ściśle pozostać globalnym ustawieniem aplikacji.

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
- Kiedy wprowadzane są nowe teksty w plikach UI, dodawaj zawsze i bezwzględnie ich tłumaczenia w obu dedykowanych słownikach w pliku `log_reader/i18n.py`. Zaktualizuj także testy w module weryfikującym (`tests/test_i18n.py`).
- Jeżeli pasek narzędzi lub inne elementy wymagają dynamicznych zmian wynikających np. z tłumaczeń to ich logika powinna rezydować bezpośrednio w kodzie Pythona, nie wewnątrz w `.ui`.

## 11. Zależności i Wymagania podczas Testów
- Upewnij się, że odpowiednio zainsalowane są `pip install -r requirements.txt pytest xvfbwrapper` przed uruchomieniem jakichkolwiek testów graficznych. Należy także puszczać uprzednio kompilację UI w pętli. Do puszczania testów używaj polecenia: `xvfb-run -a python -m pytest tests/`.
