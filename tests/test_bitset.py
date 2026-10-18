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


def test_bitset_merge_chunk_words_no_overwrite():
    # Test weryfikujący, że scalanie chunków o długości > 2 słów nie nadpisuje istniejących danych (|=)
    bs = Bitset(300)
    # Wypełniamy słowa 1, 2, 3 wartościami testowymi
    # Słowo 1 (indeks 64): bit 0 -> linia 64
    # Słowo 2 (indeks 128): bit 0 -> linia 128
    # Słowo 3 (indeks 192): bit 0 -> linia 192
    bs.update_indices([64, 128, 192])
    assert list(bs) == [64, 128, 192]

    # Scalamy 4 słowa (base_word = 0, words = [słowo 0, słowo 1, słowo 2, słowo 3])
    # Nowy chunk zawiera:
    # Słowo 0: bit 1 (linia 1)
    # Słowo 1: bit 2 (linia 66)
    # Słowo 2: bit 3 (linia 131)
    # Słowo 3: bit 4 (linia 196)
    chunk_words = [1 << 1, 1 << 2, 1 << 3, 1 << 4]
    bs.merge_chunk_words(0, chunk_words)

    # Poprawny wynik musi zawierać zarówno stare jak i nowe bity:
    # Słowo 0: 1
    # Słowo 1: 64, 66
    # Słowo 2: 128, 131
    # Słowo 3: 192, 196
    assert list(bs) == [1, 64, 66, 128, 131, 192, 196]


def test_bitset_bool_early_exit():
    # Pusty bitset
    bs_empty = Bitset(500)
    assert not bs_empty
    assert bs_empty._counts is None

    # Bitset z bitem na pozycji 0
    bs_first = Bitset(500)
    bs_first.update_indices([0])
    assert bs_first
    assert bs_first._counts is None  # __bool__ nie powinno budować _counts

    # Bitset z bitem na dalekiej pozycji (np. słowo 7)
    bs_far = Bitset(1000)
    bs_far.update_indices([500])
    assert bs_far
    assert bs_far._counts is None

    # Po wyliczeniu total_count
    assert len(bs_far) == 1
    assert bs_far._total_count == 1
    assert bool(bs_far) is True


def test_bitset_total_count_caching_and_invalidation():
    # Test from_raw z przekazanym total_count
    words = array.array("Q", [1 << 5, 1 << 10])
    bs = Bitset.from_raw(100, words, total_count=2)
    assert bs._total_count == 2
    assert bs._counts is None

    # len() musi zwrócić 2 w O(1) bez budowania _counts
    assert len(bs) == 2
    assert bs._counts is None

    # Dopiero indeksowanie buduje _counts
    assert bs[0] == 5
    assert bs._counts is not None

    # Inwalidacja przez update_indices
    bs.update_indices([50])
    assert bs._total_count == -1
    assert bs._counts is None
    assert len(bs) == 3

    # Inwalidacja przez resize
    bs.resize(200)
    assert bs._total_count == -1
    assert bs._counts is None

    # Inwalidacja przez merge_chunk_words
    bs.merge_chunk_words(0, [1 << 1])
    assert bs._total_count == -1
    assert bs._counts is None

    # Inwalidacja przez or_words
    bs.or_words([1 << 2])
    assert bs._total_count == -1
    assert bs._counts is None


def test_bitset_copy_from():
    bs1 = Bitset(200)
    bs1.update_indices([10, 70, 150])
    # Zbuduj cache w bs1
    assert len(bs1) == 3
    assert bs1._counts is not None

    bs2 = Bitset(50)
    bs2.copy_from(bs1)

    assert bs2.size == 200
    assert bs2._num_words == bs1._num_words
    assert list(bs2) == [10, 70, 150]
    assert bs2._total_count == 3
    assert bs2._counts is not None


def test_bitset_and_operator():
    bs1 = Bitset(100)
    bs1.update_indices([5, 10, 65, 80])

    bs2 = Bitset(100)
    bs2.update_indices([10, 65, 90])

    bs_and = bs1 & bs2
    assert bs_and.size == 100
    assert list(bs_and) == [10, 65]

    # Różne rozmiary
    bs3 = Bitset(50)
    bs3.update_indices([5, 10, 40])
    bs_and2 = bs1 & bs3
    assert bs_and2.size == 50
    assert list(bs_and2) == [5, 10]


def test_bitset_invert_operator():
    bs = Bitset(5)
    bs.update_indices([1, 3])
    assert len(bs) == 2

    inverted = ~bs
    assert inverted.size == 5
    # total_count powinno być natychmiast wyliczone (5 - 2 = 3)
    assert inverted._total_count == 3
    assert list(inverted) == [0, 2, 4]


def test_bitset_iter():
    bs = Bitset(500)
    indices = [0, 1, 63, 64, 65, 127, 128, 255, 256, 499]
    bs.update_indices(indices)

    # Iterator powinien zwrócić dokładnie te same elementy
    iter_result = list(iter(bs))
    assert iter_result == indices

    # Pusty bitset
    bs_empty = Bitset(100)
    assert list(iter(bs_empty)) == []


def test_bitset_logarithmic_expand_context():
    # Test roznych wartosci kontekstu w zakresie 1..63 (potegi 2 i wartosci posrednie)
    for ctx in [1, 2, 3, 7, 8, 15, 16, 31, 32, 50, 63]:
        bs = Bitset(200)
        bs.update_indices([10, 80])
        exp = bs.expand_context(ctx)

        expected_lines = set(range(10, 10 + ctx + 1)) | set(range(80, 80 + ctx + 1))
        assert set(exp) == expected_lines

    # Test przecinajacych sie zakresow
    bs_overlap = Bitset(100)
    bs_overlap.update_indices([10, 15])
    exp_overlap = bs_overlap.expand_context(10)
    assert set(exp_overlap) == set(range(10, 26))

    # Test spojnosci expand_context i expand_context_incremental
    bs_full = Bitset(250)
    bs_full.update_indices([5, 60, 120])
    exp_direct = bs_full.expand_context(45)

    exp_incr = Bitset(250)
    bs_full.expand_context_incremental(exp_incr, from_line=0, context_after=45)
    assert list(exp_direct) == list(exp_incr)


def test_bitset_merge_chunk_words_and_or_words_optimized():
    # 1. merge_chunk_words do pustego zakresu (> 16 slow, array.array)
    bs1 = Bitset(64 * 40)
    arr_words = array.array("Q", [i * 2 + 1 for i in range(30)])
    bs1.merge_chunk_words(5, arr_words)
    assert bs1._words[5:35] == arr_words
    assert all(w == 0 for w in bs1._words[:5])
    assert all(w == 0 for w in bs1._words[35:])

    # 2. merge_chunk_words z lista intow
    bs2 = Bitset(64 * 40)
    list_words = [100 + i for i in range(25)]
    bs2.merge_chunk_words(10, list_words)
    assert list(bs2._words[10:35]) == list_words

    # 3. merge_chunk_words do niepustego zakresu (|=)
    bs3 = Bitset(64 * 40)
    bs3.update_indices([64 * 12 + 1])  # ustawia bit w slowie 12
    old_val = bs3._words[12]
    new_chunk = array.array("Q", [1 << 50] * 30)
    bs3.merge_chunk_words(10, new_chunk)
    assert bs3._words[12] == old_val | (1 << 50)
    assert bs3._words[10] == 1 << 50

    # 4. or_words do pustego bitsetu (> 16 slow)
    bs4 = Bitset(64 * 30)
    or_arr = array.array("Q", [1 << (i % 64) for i in range(25)])
    bs4.or_words(or_arr)
    assert bs4._words[:25] == or_arr

    # 5. or_words do niepustego bitsetu (> 16 slow, |=)
    bs5 = Bitset(64 * 30)
    bs5.update_indices([0, 64 * 5 + 3])
    bs5.or_words(or_arr)
    assert bs5._words[0] == 1 | or_arr[0]
    assert bs5._words[5] == (1 << 3) | or_arr[5]

    # 6. Puste slowa (brak zmian)
    bs5.merge_chunk_words(0, [])
    bs5.or_words([])


def test_bitset_set_cached_total_count():
    bs = Bitset(100)
    assert bs._total_count == -1
    bs.set_cached_total_count(15)
    assert bs._total_count == 15
    assert len(bs) == 15
    assert bs._counts is None  # nie wywoluje _build_cache


def test_bitset_expand_size():
    bs = Bitset(10)
    bs.update_indices([1, 5, 9])
    assert bs.size == 10
    assert len(bs) == 3
    assert 1 in bs
    assert 5 in bs
    assert 9 in bs
    assert 50 not in bs

    # Rozszerzenie mniejsze lub rowne nic nie robi
    bs.expand_size(10)
    assert bs.size == 10
    bs.expand_size(5)
    assert bs.size == 10

    # Rozszerzenie do 150 (przekracza granice slow 64-bitowych)
    bs.expand_size(150)
    assert bs.size == 150
    assert len(bs) == 3
    assert 1 in bs
    assert 5 in bs
    assert 9 in bs
    assert 50 not in bs

    # Dodanie indeksow w nowym zakresie
    bs.update_indices([50, 100, 149])
    assert bs.size == 150
    assert len(bs) == 6
    assert 50 in bs
    assert 100 in bs
    assert 149 in bs


def test_bitset_any_in_range():
    # 1. Pusty bitset
    bs = Bitset(1000)
    assert not bs.any_in_range(0, 1000)
    assert not bs.any_in_range(50, 100)

    # 2. Niepoprawne i brzegowe zakresy
    assert not bs.any_in_range(100, 50)
    assert not bs.any_in_range(50, 50)
    assert not bs.any_in_range(1000, 2000)
    assert not bs.any_in_range(2000, 3000)

    # 3. Pojedyncze słowo (0..63)
    bs.update_indices([10, 63])
    assert bs.any_in_range(10, 11)
    assert bs.any_in_range(0, 11)
    assert not bs.any_in_range(0, 10)
    assert not bs.any_in_range(11, 63)
    assert bs.any_in_range(63, 64)
    assert not bs.any_in_range(64, 100)

    # 4. Granice słów (63 vs 64)
    bs.update_indices([64])
    assert bs.any_in_range(63, 65)  # obejmuje 63 i 64
    assert bs.any_in_range(64, 65)  # obejmuje 64
    assert not bs.any_in_range(65, 70)

    # 5. Zakres wielosłowny z bitem w środkowym słowie
    bs2 = Bitset(64 * 10)
    bs2.update_indices([64 * 5 + 10])
    assert not bs2.any_in_range(0, 64 * 5)
    assert bs2.any_in_range(0, 64 * 6)
    assert bs2.any_in_range(64 * 5, 64 * 6)
    assert not bs2.any_in_range(64 * 6, 64 * 10)

    # 6. Zakres ujemny i wykraczający poza rozmiar (clamping)
    assert bs2.any_in_range(-50, 64 * 10 + 50)
    assert not bs.any_in_range(-50, 0)


def test_bitset_first_last_set_index():
    bs = Bitset(1000)
    assert bs.first_set_index() is None
    assert bs.last_set_index() is None

    bs.update_indices([100])
    assert bs.first_set_index() == 100
    assert bs.last_set_index() == 100

    bs.update_indices([5, 500, 999])
    assert bs.first_set_index() == 5
    assert bs.last_set_index() == 999
