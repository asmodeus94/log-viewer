"""Wyjątki aplikacji Czytnik Logów."""

from __future__ import annotations


class FileChangedError(Exception):
    """
    Rzucany gdy plik źródłowy zmienił się między otwarciem a zapisem edycji
    (rotacja logrotate, modyfikacja przez inny proces, etc.). W takim
    przypadku zapis jest blokowany — nadpisanie nadpisaloby NOWY plik starymi
    danymi + edycjami.
    """
    pass


class CompressedSaveError(Exception):
    """
    Rzucany gdy użytkownik próbuje zapisać edycje do pliku skompresowanego
    (.gz/.bz2/.xz). Zapis in-place dla skompresowanych plików jest skomplikowany
    (trzeba zrekompresować cały plik) i ryzykowny (błąd w środku = utrata
    danych). Zamiast tego sugerujemy 'Save As' do nowego pliku.
    """
    pass
