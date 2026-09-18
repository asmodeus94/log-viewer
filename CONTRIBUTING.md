# Wnoszenie Wkładu w Rozwój Projektu (Contributing)

Dziękujemy za chęć wsparcia rozwoju `Log Viewer`! Poniżej znajdziesz krótkie informacje pomagające zrozumieć zasady, na podstawie których pracujemy i w jaki sposób sprawnie tworzyć, weryfikować, i dostarczać zmiany w repozytorium (tzw. _Pull Requesty_).

## Zgłaszanie Zmian (Pull Requests)

Aby twój wkład został pomyślnie zaakceptowany, prosimy o stosowanie się do następujących etapów i dobrych praktyk git flow:

1. **Stwórz Feature Branch**: Do każdego zgłoszenia nowej funkcji bądź naprawy błędu (Bugfix) należy utworzyć nową gałąź na bazie `main`. Nie committuj prosto do głównej gałęzi.
    ```bash
    git checkout -b feature/nowa-wspaniala-funkcja
    ```
2. **Zachowaj czystość historii zmian**: Dbaj, aby każdy zadeklarowany `commit` był samodzielną porcją kodu. Unikaj masywnych zlepków i stosuj krótkie, zwięzłe wiadomości uwzględniające standard konwencji _Conventional Commits_ (np. `feat: dodał przycisk X`, `fix: wyeliminował błąd wycieku Y`).
3. **Pisz Testy**: Jeśli modyfikujesz konkretne funkcje bądź piszesz mechanizm od zera, koniecznie dołącz przypadek testowy obwieszczający stan przed i po modyfikacji.
4. **Zweryfikuj Bramkę Jakości (Quality Gate)**: Przed utworzeniem commitu lub PR upewnij się, że skrypt `python scripts/verify.py` przechodzi bez błędów.
5. **Utwórz PR (Pull Request)**: Gdy wykonasz prace nad swoją gałęzią, wypchnij je i stwórz _Pull Request_ na GitHubie. Oczekuj na Code Review – proces recenzowania kodu przez opiekunów repozytorium.

## Środowisko Deweloperskie i Zależności

Do pracy nad projektem wymagany jest **Python w wersji co najmniej 3.10**.
Zainstaluj wymagane pakiety produkcyjne oraz narzędzia deweloperskie (linter, typowanie, framework testów):

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Zalecamy także natychmiastowe zainstalowanie hooka Git pre-commit, który zabezpiecza przed wysłaniem kodu niespełniającego standardów:
```bash
python scripts/install_hooks.py
```

## Standardy Kodowania

* Piszemy zgodnie z wytycznymi **PEP 8** dla języka Python (wersja docelowa: Python 3.10+).
* Używaj adnotacji typów (_Type Hints_ wg PEP 484) na zdefiniowanych obiektach i parametrach funkcji (np. `def fn(a: int) -> bool:`).
* Skrypt i funkcjonalności powinny zawierać zwięzłe i merytoryczne tzw. _Docstrings_ (szczególnie jeżeli dany moduł realizuje złożone wyliczenia matematyczne lub specyficzną logikę wątkową).
* Preferuj użycie modułu `pathlib.Path` zamiast przestarzałego `os.path`.
* **Komunikacja, Dokumentacja i Komentarze muszą być w języku polskim.** Projekt korzysta z języka polskiego jako głównego narzędzia do opisywania logiki. Oczekuje się, że wszystkie _docstringi_, komentarze bezpośrednio w kodzie, komunikaty o błędach (nie skierowane w świat), logi wewnętrzne oraz treści zapytań (prompty) będą sformułowane całkowicie w języku polskim, by utrzymać najwyższą spójność merytoryczną.
* Dodając nowe teksty w interfejsie użytkownika, należy bezwzględnie zaktualizować słowniki tłumaczeń (zarówno w języku polskim jak i angielskim) w pliku `log_viewer/i18n.py`.

## Bramka Jakości (Quality Gate) i Weryfikacja Kodu

Przed zatwierdzeniem zmian, kod **musi** spełniać wszystkie wymagania centralnej bramki jakości projektu:

```bash
python scripts/verify.py
```

Skrypt ten weryfikuje sekwencyjnie:
1. Kompilację plików szablonów interfejsu Qt (`scripts/compile_ui.py`).
2. Sprawdzenie formatowania i reguł lintera (`ruff format --check` oraz `ruff check`).
3. Statyczną analizę typów (`mypy log_viewer scripts`).
4. Pełne wykonanie testów jednostkowych i GUI (`pytest tests/`).
5. Opcjonalną walidację raportów SARIF (jeśli obecne w repozytorium).

### Przydatne opcje skryptu verify:
* **Automatyczna naprawa stylu i importów:**
  ```bash
  python scripts/verify.py --fix
  ```
* **Szybka weryfikacja (bez uruchamiania pytest):**
  ```bash
  python scripts/verify.py --quick
  ```
* **Uruchomienie pojedynczego kroku:**
  ```bash
  python scripts/verify.py --step ui     # tylko kompilacja UI
  python scripts/verify.py --step lint   # tylko ruff format + check
  python scripts/verify.py --step mypy   # tylko kontrola typów mypy
  python scripts/verify.py --step test   # tylko testy pytest
  python scripts/verify.py --step sarif  # tylko walidacja SARIF
  ```

## Uruchamianie Testów Jednostkowych

Aplikacja wykorzystuje środowisko `pytest` do egzekucji przypadków testowych.
Przed uruchomieniem testów upewnij się, że pliki interfejsu `.ui` są skompilowane (kompilacja przyrostowa):

```bash
python scripts/compile_ui.py
```

W środowisku CI/CD lub w terminalach CLI bez aktywnej sesji graficznej zalecamy uruchamianie testów wspierając się wirtualnym środowiskiem X (np. `xvfb` w dystrybucjach Linux):

```bash
xvfb-run -a python -m pytest tests/
```

W przypadku systemu Windows wystarczy standardowe wywołanie:
```powershell
.venv\Scripts\python -m pytest tests/
```

## Środowisko Testowe (Plik `conftest.py`)

Kluczowym elementem w folderze weryfikacji aplikacji jest plik konfiguracyjny testów `tests/conftest.py`. Znajdują się w nim _fixtures_ (dekoratory konfiguracyjne ze struktur środowiskowych), współdzielone na poczet całej floty asercji:

* `temp_log_file`: generuje i zapisuje wirtualny ciąg znaków ("dummy data") na potrzeby operacji asynchronicznego workera bądź silnika `FilterEngine` bez marnowania przestrzeni fizycznej na repozytorium. Po teście ten obiekt dokonuje _Cleanup_ - czyszcząc po sobie wygenerowany plik.
* `temp_config_path`: nadzoruje tworzenie pliku JSON z czystą, deweloperską konfiguracją okna `LogViewerWindow`. Uniezależnia on stan w testach od konfiguracji przechowywanej w profilu programisty lokalnego.
