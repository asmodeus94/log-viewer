import array
import pytest
from log_viewer.bitset import Bitset

def test_bitset_empty():
    hits = array.array('Q', [])
    view = Bitset.from_indices(hits, 100).expand_context(2)
    assert len(view) == 0
    assert not view
    assert list(view) == []
    assert view.bisect_left(10) == 0

def test_bitset_no_context():
    hits = array.array('Q', [10, 20, 30])
    view = Bitset.from_indices(hits, 100).expand_context(0)
    assert len(view) == 3
    assert list(view) == [10, 20, 30]
    assert view[0] == 10
    assert view[2] == 30
    assert view.bisect_left(20) == 1
    assert view.bisect_left(25) == 2

def test_bitset_with_context():
    hits = array.array('Q', [10, 20, 30])
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
    hits = array.array('Q', [10, 11, 15])
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
    hits = array.array('Q', [10, 20])
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
    hits = array.array('Q', [98])
    view = Bitset.from_indices(hits, 100).expand_context(5)
    # Powinno dojść tylko do 99 (skoro total_lines wynosi 100, dozwolone indeksy to 0-99)
    assert list(view) == [98, 99]
    assert len(view) == 2

def test_bitset_context_crossing_block_boundary():
    # Bit 63 is the last bit in the first 64-bit word
    hits = array.array('Q', [62])
    view = Bitset.from_indices(hits, 200).expand_context(4)
    # 62 -> 62, 63, 64, 65, 66
    assert list(view) == [62, 63, 64, 65, 66]
    assert len(view) == 5
