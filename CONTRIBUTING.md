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
4. **Utwórz PR (Pull Request)**: Gdy wykonasz prace nad swoją gałęzią, wypchnij je i stwórz _Pull Request_ na githubie. Oczekuj na Code Review – proces recenzowania kodu przez opiekunów repozytorium.

## Standardy Kodowania

* Piszemy zgodnie z wytycznymi **PEP 8** dla języka Python.
* Używaj adnotacji typów (_Type Hints_) na zdefiniowanych przez Ciebie obiektach i parametrach funkcji (np. `def fn(a: int) -> bool:`). Poprawia to czytelność kodu.
* Skrypt i funkcjonalności powinny zawierać zwięzłe i merytoryczne tzw. _Docstrings_ (szczególnie jeżeli dany moduł realizuje złożone wyliczenia matematyczne lub specyficzną logikę wątkową).
* Projekt korzysta i promuje posługiwanie się językiem polskim w sekcjach takich jak komentarze w kodzie, komunikacja czy dokumentacja, aby utrzymać zbieżność merytoryczną w zespole.
* Dodając nowe teksty w interfejsie użytkownika, należy zaktualizować słowniki tłumaczeń (zarówno w języku polskim jak i angielskim) w pliku `log_reader/i18n.py`.

## Uruchamianie Testów Jednostkowych

Aplikacja wykorzystuje środowisko `pytest` do egzekucji przypadków testowych.
Ponieważ projekt bazuje w dużej mierze na widżetach graficznych, przed uruchomieniem testów upewnij się, że pliki interfejsu `.ui` są skompilowane. W przeciwnym razie testy mogą zakończyć się błędem braku modułu:

```bash
python scripts/compile_ui.py
```

W środowisku CI/CD lub w terminalach CLI mogą występować błędy związane z niedostępnością sesji "okienkowej" wyświetlania. Zalecamy dlatego uruchamianie testów wspierając się wirtualnym środowiskiem X (np. `xvfb` w dystrybucjach Linux).

**Aby odpalić wszystkie testy zgrupowane pod katalogiem `tests/` wykonaj polecenie**:

```bash
xvfb-run -a python -m pytest tests/
```

W przypadku Windows, o ile dysponujesz włączonym lokalnie sesją okna, można pominąć narzędzie Xvfb i puścić prosto `pytest tests/`.

## Środowisko Testowe (Plik `conftest.py`)

Kluczowym elementem w folderze weryfikacji aplikacji jest plik konfiguracyjny testów `tests/conftest.py`. Znajdują się w nim _fixtures_ (dekoratory konfiguracyjne ze struktur środowiskowych), współdzielone na poczet całej floty asercji:

* `temp_log_file`: generuje i zapisuje bardzo długi wirtualny ciąg znaków ("dummy data") na potrzeby operacji asynchronicznego worker'a bądź silnika `FilterEngine` bez marnowania przestrzeni fizycznej na repozytorium. Po teście ten obiekt dokonuje _Cleanup_ - czyszcząc po sobie wygenerowany plik.
* `temp_config_path`: nadzoruje tworzenie pliku JSON z czystą, deweloperską konfiguracją okna `LogViewerWindow`. Uniezależnia on stan w testach od konfiguracji przechowywanej w profilu programisty lokalnego. Ponadto manipuluje zmienną systemową modułów, nakazując środowisku weryfikacyjnemu ładowanie zawartości stricte z root foldera repozytorium.
