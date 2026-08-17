import array

from log_viewer.bitset import Bitset


def test_bitset_empty():
    hits = array.array("Q", [])
    view = Bitset.from_indices(hits, 100).expand_context(2)
    assert len(view) == 0
    assert not view
    assert list(view) == []
    assert view.bisect_left(10) == 0


def test_bitset_no_context():
    hits = array.array("Q", [10, 20, 30])
    view = Bitset.from_indices(hits, 100).expand_context(0)
    assert len(view) == 3
    assert list(view) == [10, 20, 30]
    assert view[0] == 10
    assert view[2] == 30
    assert view.bisect_left(20) == 1
    assert view.bisect_left(25) == 2


def test_bitset_with_context():
    hits = array.array("Q", [10, 20, 30])
    view = Bitset.from_indices(hits, 100).expand_context(2)
    # Konteksty:
    # 10 -> 10, 11, 12
    # 20 -> 20, 21, 22
    # 30 -> 30, 31, 32
    assert len(view) == 9
    assert list(view) == [10, 11, 12, 20, 21, 22, 30, 31, 32]

    assert view[0] == 10
    assert view[2] == 12
    assert view[3] == 20

    assert view.bisect_left(11) == 1
    assert view.bisect_left(20) == 3
    assert view.bisect_left(25) == 6


def test_bitset_overlapping_context():
    hits = array.array("Q", [10, 11, 15])
    view = Bitset.from_indices(hits, 100).expand_context(3)
    # 10 -> 10, 11, 12, 13
    # 11 -> 11, 12, 13, 14
    # 15 -> 15, 16, 17, 18
    # Złączone: 10, 11, 12, 13, 14, 15, 16, 17, 18
    assert len(view) == 9
    assert list(view) == [10, 11, 12, 13, 14, 15, 16, 17, 18]
    assert view.bisect_left(14) == 4
    assert view.bisect_left(15) == 5


def test_bitset_slicing():
    hits = array.array("Q", [10, 20])
    view = Bitset.from_indices(hits, 100).expand_context(2)
    # Widok: 10, 11, 12, 20, 21, 22

    # Slicowanie pierwszego zakresu
    assert list(view[0:2]) == [10, 11]

    # Slicowanie na przestrzał zakresów
    assert list(view[1:5]) == [11, 12, 20, 21]

    # Slicowanie końca
    assert list(view[4:6]) == [21, 22]

    # Slicowanie poza zakresem (out of bounds)
    assert list(view[4:10]) == [21, 22]
    assert list(view[10:20]) == []


def test_bitset_total_lines_limit():
    hits = array.array("Q", [98])
    view = Bitset.from_indices(hits, 100).expand_context(5)
    # Powinno dojść tylko do 99 (skoro total_lines wynosi 100, dozwolone indeksy to 0-99)
    assert list(view) == [98, 99]
    assert len(view) == 2


def test_bitset_context_crossing_block_boundary():
    # Bit 63 is the last bit in the first 64-bit word
    hits = array.array("Q", [62])
    view = Bitset.from_indices(hits, 200).expand_context(4)
    # 62 -> 62, 63, 64, 65, 66
    assert list(view) == [62, 63, 64, 65, 66]
    assert len(view) == 5


def test_bitset_resize():
    hits = array.array("Q", [10, 20])
    view = Bitset(50)
    view.update_indices(hits)

    assert list(view) == [10, 20]

    view.resize(150)
    hits_new = array.array("Q", [60, 100])
    view.update_indices(hits_new)

    assert list(view) == [10, 20, 60, 100]


def test_bitset_from_raw_and_to_raw():
    bs = Bitset.from_indices(array.array("Q", [5, 12, 65]), 100)
    raw = bs.to_raw()
    assert raw[0] == 100
    assert len(raw[1]) == bs._num_words

    bs2 = Bitset.from_raw(raw[0], raw[1], raw[2])
    assert list(bs2) == [5, 12, 65]
    assert len(bs2) == 3
    assert len(bs2.words) == bs._num_words


def test_bitset_merge_chunk_words():
    bs = Bitset(200)
    # chunk 1: linie 0 i 2 -> słowo 0 ma bity (1<<0) | (1<<2) = 5
    bs.merge_chunk_words(0, [5])
    assert list(bs) == [0, 2]

    # chunk 2: słowo 1 (indeksy 64..) z bitem 1 -> linia 64+1=65 -> słowo 1 ma wartość 2
    bs.merge_chunk_words(1, [2])
    assert list(bs) == [0, 2, 65]


def test_bitset_expand_context_incremental_equivalence():
    # Test porównujący wynik expand_context_incremental z pełnym expand_context
    bs_base = Bitset(100)
    bs_base.update_indices([10, 60, 98])

    target = bs_base.expand_context(5)

    # Symulacja dodania 150 nowych linii do pliku (nowy rozmiar = 250)
    bs_base.resize(250)
    # Dodajemy nowe trafienia w liniach 105 i 200
    bs_base.update_indices([105, 200])

    # 1. Obliczenie pełne od zera
    expected = bs_base.expand_context(5)

    # 2. Obliczenie inkrementalne od linii 100
    bs_base.expand_context_incremental(target, from_line=100, context_after=5)

    assert list(target) == list(expected)
    assert len(target) == len(expected)


def test_bitset_expand_context_incremental_cross_boundary():
    # Trafienie na pozycji 98 z context=5 w pliku o rozmiarze 100
    # początkowo pokrywa linie 98, 99
    bs = Bitset(100)
    bs.update_indices([98])
    target = bs.expand_context(5)
    assert list(target) == [98, 99]

    # Plik urósł do 120 linii (brak nowych trafień)
    bs.resize(120)
    bs.expand_context_incremental(target, from_line=100, context_after=5)

    # Teraz kontekst z linii 98 powinien sięgać do 98 + 5 = 103 (98, 99, 100, 101, 102, 103)
    assert list(target) == [98, 99, 100, 101, 102, 103]
    assert len(target) == 6
