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

## Uruchomienie

Aby uruchomić aplikację po prawidłowym skonfigurowaniu środowiska, z katalogu głównego projektu wykonaj następującą komendę:

```bash
python -m log_reader
```
Możesz opcjonalnie od razu wywołać podgląd pliku, przekazując jego ścieżkę jako argument:
```bash
python -m log_reader /sciezka/do/twojego_logu.log
```
