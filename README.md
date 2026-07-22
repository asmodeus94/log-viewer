# Log Viewer

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![PySide6](https://img.shields.io/badge/PySide6-Qt6-green.svg)

**Log Viewer** to wieloplatformowa, wydajna przeglądarka i edytor przeznaczona do pracy z bardzo dużymi plikami logów (nawet wielogigabajtowymi). Główne założenie aplikacji to odczytywanie plików wielkimi partiami bez konieczności ładowania całości do pamięci operacyjnej (RAM). Pozwala na szybkie indeksowanie, podgląd na żywo (*tail -f*), zaawansowane wyszukiwanie i filtrowanie z użyciem wyrażeń regularnych oraz na intuicyjną edycję bezpośrednio w pliku logu.

## Główne możliwości:
* **Asynchroniczne ładowanie** plików wielkości wielu GB przy minimalnym zużyciu zasobów,
* **Zaawansowane filtrowanie** po konkretnych wzorcach lub wyrażeniach regularnych wraz z możliwością ustawienia tzw. linii kontekstu po każdym wystąpieniu.
* **Mini-mapa** z kolorowaniem logów (`ERROR`, `WARN`, `INFO`, `DEBUG`).
* Możliwość robienia **zakładek** (bookmarks) i nanoszenia i zapisywania **edycji** w pliku.
* Wsparcie trybów jasnych i ciemnych (Dark/Light).
* Interfejs wyposażony w funkcjonalność Drag&Drop.
* Obsługa wielokrotnego otwierania tego samego pliku (w kartach oznaczonych sufiksami `[A]`, `[B]`, itd.).
* Dynamiczna aktualizacja nagłówka okna prezentująca nazwę przeglądanego pliku (zgodną z nazwą bieżącej karty).
* Możliwość przeładowania zawartości i indeksu pliku (klawisz `F5` lub opcja `Przeładuj`).

## Wymagania systemowe

Projekt do poprawnego działania wymaga środowiska uruchomieniowego Pythona w wersji co najmniej **3.8**.
Graficzny interfejs zrealizowany jest przy użyciu frameworka Qt (wersja 6) poprzez oficjalny pakiet PySide6.

## Instalacja

Sklonuj repozytorium do wybranego katalogu na dysku twardym i utwórz w nim wirtualne środowisko Pythona:

```bash
git clone https://github.com/asmodeus94/log-viewer.git
cd log-viewer
python -m venv venv
```

Następnie aktywuj wirtualne środowisko i zainstaluj wymagane pakiety znajdujące się w pliku `requirements.txt`:

* **Windows:**
  ```cmd
  venv\Scripts\activate
  pip install -r requirements.txt
  ```

* **Linux / macOS:**
  ```bash
  source venv/bin/activate
  pip install -r requirements.txt
  ```

## Uruchomienie i Kompilacja interfejsu (UI)

Aplikacja wykorzystuje pliki z interfejsem graficznym (`.ui`) tworzone w Qt Designerze. Przed uruchomieniem są one kompilowane do formatu Pythona.
Rekomendowanym sposobem uruchamiania aplikacji jest skorzystanie ze skryptu `run.py` w głównym katalogu, który automatycznie wykona kompilację przyrostową (tylko dla zmienionych plików UI), a następnie włączy aplikację:

```bash
python run.py
```

Opcjonalnie możesz od razu wywołać podgląd pliku, przekazując jego ścieżkę jako argument:

```bash
python run.py /sciezka/do/twojego_logu.log
```

*(Jeśli z jakiegoś powodu chcesz uruchomić sam moduł bez automatycznej kompilacji UI, nadal możesz to zrobić poprzez `python -m log_reader`, pamiętając uprzednio o ręcznej kompilacji poleceniem `python scripts/compile_ui.py`).*

## Budowanie aplikacji (PyInstaller)

Projekt posiada dedykowany skrypt automatyzujący proces budowania aplikacji w formie pojedynczego pakietu instalacyjnego lub wykonywalnego (np. `.app` dla macOS lub `.exe` dla Windows). Aplikacja zostanie skonfigurowana do działania bez wyświetlania zbędnej konsoli systemowej i z załączoną natywną ikonką systemu.

1. Zainstaluj wymagane narzędzia deweloperskie i biblioteki:
    ```bash
    pip install -r requirements-dev.txt
    ```
2. Uruchom skrypt budujący w środowisku Twojego docelowego systemu operacyjnego:
    ```bash
    python scripts/build.py
    ```

Gotowa aplikacja okienkowa zostanie wygenerowana w folderze `dist/`. Na systemie Windows będzie to pojedynczy plik wykonywalny (`log-viewer.exe`), natomiast na systemie macOS pakiet aplikacji (`log-viewer.app`). Folder `dist/`, `build/` oraz pliki z rozszerzeniami systemowych ikon są ignorowane w systemie kontroli wersji.
