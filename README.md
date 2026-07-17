# Czytnik Logów / Log Reader — wersja PySide6

Aplikacja okienkowa (PySide6/Qt6) do przeglądania bardzo dużych plików logów (wielu GB) bez ładowania ich w całości do pamięci.

## Funkcje

- **Wirtualne okno** — ładuje tylko 5000 linii naraz
- **Rzadki indeks** — byte-offset ↔ line-number co 1 MB dla szybkiego skoku
- **Multiprocessing** — równoległe indeksowanie dużych plików (2-4x szybsze)
  z paskiem postępu i możliwością anulowania (Anuluj w dialogu)
- **Wyszukiwanie** — zwykły tekst / regex / case-sensitive / negacja
- **Filtrowanie w tle** — QThread z paskiem postępu
- **Edycja in-place** — bufor zmian + walidacja mtime + backup
- **Follow mode** — `tail -f` z inkrementalną aktualizacją indeksu
- **Drag&drop** — przeciągnij plik na okno (natywne Qt DnD, bez dodatkowych bibliotek)
- **Kompresja** — `.gz` / `.bz2` / `.xz` (odczyt + Save As)
- **Kodowanie** — UTF-8, Latin-1, CP1250, CP1252, ISO-8859-2, ASCII
- **Konfiguracja** — `~/.logreader.json` (font, parametry, język)
- **Dwujęzyczny UI** — polski (domyślnie) / angielski

## Struktura projektu

```
log_reader_pyside6_app/
├── log_reader/              # Pakiet aplikacji
│   ├── __init__.py          # Eksport publicznych klas
│   ├── __main__.py          # python -m log_reader
│   ├── main.py              # Entry point (QApplication)
│   ├── app.py               # LogViewerWindow (QMainWindow)
│   ├── exceptions.py        # FileChangedError, CompressedSaveError
│   ├── helpers.py           # fmt_size, truncate, DnD, kompresja, stałe
│   ├── i18n.py              # Słownik PL/EN (~96 kluczy)
│   ├── config.py            # UserConfig (~/.logreader.json)
│   ├── indexer.py           # LineIndexer (rzadki indeks, multiprocessing)
│   ├── filter_engine.py     # FilterEngine (skanowanie w tle, session isolation)
│   ├── edit_buffer.py       # EditBuffer (edycja, walidacja mtime, uprawnienia)
│   ├── workers.py           # QThread workers (IndexerWorker, FilterWorker, SaveWorker)
│   └── widgets.py           # LineNumberArea, LogPlainTextEdit, SettingsDialog
├── tests/                   # Testy pytest
│   ├── conftest.py          # Fixtures
│   ├── test_exceptions.py   # Testy wyjątków
│   ├── test_helpers.py      # fmt_size, truncate, DnD, kompresja
│   ├── test_indexer.py      # LineIndexer, parallel/single, read_lines
│   ├── test_filter_engine.py # FilterEngine, cancel, session isolation
│   ├── test_edit_buffer.py  # EditBuffer, FileChangedError, CompressedSaveError
│   ├── test_config.py       # UserConfig, save/load, type safety
│   └── test_i18n.py         # Parzystość kluczy PL/EN
├── requirements.txt         # PySide6, pytest
└── README.md                # Ten plik
```

## Instalacja

```bash
cd log_reader_pyside6_app
pip install -r requirements.txt
```

## Uruchomienie

```bash
python -m log_reader [plik.log]
```

## Konfiguracja w IntelliJ IDEA Ultimate (z pluginem Python)

### Krok 1: Otwórz projekt
1. **File → Open**
2. Wybierz katalog `log_reader_pyside6_app/`
3. IntelliJ wykryje projekt Python — kliknij **OK**

### Krok 2: Skonfiguruj interpreter Python
1. **File → Settings → Project: log_reader_pyside6_app → Python Interpreter**
2. Kliknij **gear icon** → **Add...**
3. Wybierz **Virtualenv** (zalecane)
4. Kliknij **OK**
5. IntelliJ zapyta czy zainstalować zależności z `requirements.txt` — kliknij **Install**

### Krok 3: Oznacz katalogi
1. Prawy klik na `log_reader/` → **Mark Directory as → Sources Root**
2. Prawy klik na `tests/` → **Mark Directory as → Test Sources Root**

### Krok 4: Konfiguracja uruchomienia aplikacji
1. **Run → Edit Configurations → + → Python**
2. Wypełnij:
   - **Name**: `Czytnik Logów`
   - **Module name**: `log_reader`
   - **Parameters**: opcjonalnie ścieżka do pliku log
   - **Working directory**: katalog projektu
3. Kliknij **OK**

### Krok 5: Konfiguracja uruchomienia testów
1. **Run → Edit Configurations → + → Python tests → pytest**
2. **Target**: `tests/`

### Krok 6: Uruchom
- **Aplikacja**: `Shift+F10`
- **Testy**: prawy klik na `tests/` → Run pytest

## Skróty klawiaturowe

| Skrót | Akcja |
|-------|-------|
| `Ctrl+O` | Otwórz plik |
| `Ctrl+S` | Zapisz edycje |
| `Ctrl+F` | Znajdź |
| `F3` | Znajdź następny |
| `Shift+F3` | Znajdź poprzedni |
| `Ctrl+L` | Filtruj |
| `Ctrl+G` | Skok do linii/offsetu |
| `Ctrl+D` | Edytuj bieżącą linię |
| `Ctrl+B` | Przełącz zakładkę (pojedynczą lub całą selekcję) |
| `F4` | Następna zakładka |
| `Shift+F4` | Poprzednia zakładka |
| `Ctrl+E` | Eksportuj widok |
| `Ctrl+Q` | Zakończ |

## Zakładki — jak działają

- **Dodawanie** — ustaw kursor w linii i naciśnij `Ctrl+B` (Cmd+B na macOS).
  Linia staje się zakładką (zielone tło) i pojawia się w panelu „Zakładki”.
  **Zawsze działa tylko na jednej linii** — nawet jeśli masz zaznaczony
  większy fragment tekstu, zakładkuje tylko linię kursora i automatycznie
  czyści selekcję Qt (żeby zniknęło podświetlenie, które można pomylić z
  kolorem zakładki). Po dodaniu/usunięciu zakładki kursor zostaje w tej samej
  linii (nie skacze na początek dokumentu).
- **Bieżąca linia** — linia z kursorem ma delikatne tło (ciemno-szare w
  dark theme, jasno-szare w light theme), widoczne nawet gdy linia nie jest
  zakładką. Podświetlenie przesuwa się ze strzałkami, kliknięciem myszy i
  skokiem z panelu zakładek. Nie mylić z kolorem zakładki (ten jest zielony
  i zostaje na konkretnej linii aż do jej odznaczenia).
- **Nawigacja** — dwuklik na wpisie w panelu skacze do tej linii. `F4` /
  `Shift+F4` przeskakują między zakładkami.
- **Usuwanie** — w panelu można zaznaczyć wiele wpisów naraz (Ctrl+klik,
  Shift+klik, Ctrl+A) i usunąć je wszystkie przyciskiem **„Usuń zaznaczone”**
  (albo `Delete`/`Backspace`). Po usunięciu zaznaczenie automatycznie
  przesuwa się na następny element (jak w IDE) — nie trzeba ponownie klikać.
  Ten sam mechanizm działa w panelu „Edycje w buforze”.
- **Czyszczenie wszystkich** — menu *Zakładki → Wyczyść zakładki*.

## Filtr z kontekstem (stack trace)

Standardowy filtr pokazuje tylko linie pasujące do wzorca — problem pojawia
się przy stack trace PHP/Python, gdzie błąd jest w jednej linii, a kontekst
 stosu poniżej. Rozwiązanie: **pole „Kontekst:"** na pasku narzędzi filtra.

- Wpisz `0` (domyślnie) → tylko trafienia, jak dotychczas.
- Wpisz `5` → dla każdego trafienia pokazuje też 5 następujących linii jako
  kontekst (z delikatnym szaro-zielonym tłem, odróżniającym je od trafień).
- Trafienia i kontekst są sortowane razem, więc widzisz błąd + stos poniżej
  w naturalnej kolejności.
- Kontekst nie dubluje trafień (jeśli linia jest i trafieniem, i kontekstem,
  pokazuje się raz, z tłem trafienia).
- `Wyczyść` filtr czyści też kontekst.

## Interfejs zakładkowy (kart)

- Każdy otwarty plik żyje we własnej karcie (`QTabWidget`). Stan pliku —
  indeks, wirtualne okno, zakładki, edycje, wyniki wyszukiwania, filtr — jest
  zamknięty w obiekcie `LogTab` i **nie przenika** między kartami.
- Przełączanie kart przywraca natychmiast jej stan (pasek narzędzi i menu są
  współdzielone, ale operują zawsze na aktywnej karcie przez delegację).
- Zamykanie karty (×) zwalnia jej wątki i indeks.

## Testy

```bash
pytest tests/ -v
```

## Licencja

MIT
