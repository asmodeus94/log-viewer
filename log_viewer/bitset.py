import bisect
import itertools
import array
import operator
from typing import Sequence, Iterator, Union, overload

class Bitset(Sequence[int]):
    """
    Zoptymalizowana struktura Rank & Select Bitset do przechowywania milionów
    wyników wyszukiwania i filtrowania przy minimalnym zużyciu RAM.
    """
    def __init__(self, size: int):
        self._size = size
        self._num_words = (size + 63) // 64
        self._words = array.array('Q', [0] * self._num_words)
        
        self._counts = None
        self._total_count = -1

    @classmethod
    def from_indices(cls, indices: "array.array[int]", size: int) -> "Bitset":
        b = cls(size)
        b.update_indices(indices)
        return b

    def update_indices(self, indices):
        words = self._words
        for idx in indices:
            if 0 <= idx < self._size:
                words[idx // 64] |= (1 << (idx % 64))
        self._counts = None

    def _build_cache(self):
        if self._counts is not None:
            return
            
        counts = array.array('Q', itertools.accumulate(map(int.bit_count, self._words), initial=0))
        self._total_count = counts.pop()
        self._counts = counts

    def resize(self, new_size: int) -> None:
        if new_size <= self._size:
            return
        new_num_words = (new_size + 63) // 64
        if new_num_words > self._num_words:
            self._words.extend([0] * (new_num_words - self._num_words))
            self._num_words = new_num_words
        self._size = new_size
        self._counts = None

    @property
    def size(self) -> int:
        """Zwraca całkowity rozmiar uniwersum bitsetu (liczbę indeksowanych linii)."""
        return self._size

    def or_words(self, words: Sequence[int]) -> None:
        """Łączy alternatywą bitową (OR) słowa bitsetu z przekazaną sekwencją."""
        for i in range(min(len(words), self._num_words)):
            self._words[i] |= words[i]
        self._counts = None

    def copy_from(self, other: "Bitset") -> None:
        """Kopiuje zawartość z innego Bitsetu do bieżącej instancji."""
        self.resize(other._size)
        self._words = array.array('Q', other._words)
        self._num_words = other._num_words
        self._counts = None

    def clone(self) -> "Bitset":
        """Zwraca głęboką kopię bieżącego Bitsetu."""
        new_bs = Bitset(self._size)
        new_bs._words = array.array('Q', self._words)
        return new_bs

    def __len__(self) -> int:
        self._build_cache()
        return self._total_count

    def __bool__(self) -> bool:
        return len(self) > 0

    def __contains__(self, index: object) -> bool:
        if not isinstance(index, int) or index < 0 or index >= self._size:
            return False
        return bool(self._words[index // 64] & (1 << (index % 64)))

    def bisect_left(self, index: int) -> int:
        if index <= 0:
            return 0
        if index >= self._size:
            return len(self)
            
        self._build_cache()
        word_idx = index // 64
        bit_idx = index % 64
        
        count = self._counts[word_idx]
        if bit_idx > 0:
            mask = (1 << bit_idx) - 1
            count += (self._words[word_idx] & mask).bit_count()
        return count

    def bisect_right(self, index: int) -> int:
        return self.bisect_left(index + 1)

    @overload
    def __getitem__(self, index: int) -> int: ...
    @overload
    def __getitem__(self, index: slice) -> "array.array[int]": ...

    def __getitem__(self, index: Union[int, slice]):
        self._build_cache()
        
        if isinstance(index, slice):
            start, stop, step = index.indices(self._total_count)
            if step != 1:
                raise ValueError("Bitset slice step must be 1")
            
            result = array.array('Q')
            if start >= stop:
                return result
                
            word_idx = bisect.bisect_right(self._counts, start) - 1
            if word_idx < 0:
                word_idx = 0
                
            current_count = self._counts[word_idx]
            items_needed = stop - start
            items_skipped = start - current_count
            
            words = self._words
            for i in range(word_idx, self._num_words):
                w = words[i]
                if w:
                    base = i * 64
                    while w and items_needed > 0:
                        tz = (w & -w).bit_length() - 1
                        if items_skipped > 0:
                            items_skipped -= 1
                        else:
                            result.append(base + tz)
                            items_needed -= 1
                        w &= w - 1
                if items_needed == 0:
                    break
                    
            return result
        else:
            if index < 0:
                index += self._total_count
            if index < 0 or index >= self._total_count:
                raise IndexError("Bitset index out of range")
                
            word_idx = bisect.bisect_right(self._counts, index) - 1
            if word_idx < 0:
                word_idx = 0
                
            current_count = self._counts[word_idx]
            items_skipped = index - current_count
            w = self._words[word_idx]
            
            while w:
                tz = (w & -w).bit_length() - 1
                if items_skipped == 0:
                    return word_idx * 64 + tz
                items_skipped -= 1
                w &= w - 1
            
            raise IndexError("Bitset corrupted state")

    def expand_context(self, context_after: int) -> "Bitset":
        """Zwraca nowy Bitset rozszerzony o podaną liczbę linii w dół (kontekst)."""
        new_bs = Bitset(self._size)
        if context_after <= 0:
            new_bs._words = array.array('Q', self._words)
            return new_bs
            
        new_words = new_bs._words
        words = self._words
        num_words = self._num_words
        
        horizon = -1
        
        for i in range(num_words):
            w = words[i]
            base = i * 64
            
            if horizon >= base + 63:
                new_words[i] = 0xFFFFFFFFFFFFFFFF
                if w:
                    highest_bit = w.bit_length() - 1
                    reach = base + highest_bit + context_after
                    if reach > horizon:
                        horizon = reach
                continue
                
            new_w = 0
            if horizon >= base:
                covered_bits = min(horizon - base + 1, 64)
                if covered_bits == 64:
                    new_w = 0xFFFFFFFFFFFFFFFF
                else:
                    new_w = (1 << covered_bits) - 1
                
            if w:
                if context_after < 64:
                    expanded_w = w
                    for shift in range(1, context_after + 1):
                        expanded_w |= (w << shift)
                    new_w |= (expanded_w & 0xFFFFFFFFFFFFFFFF)
                    
                    highest_bit = w.bit_length() - 1
                    reach = base + highest_bit + context_after
                    if reach > horizon:
                        horizon = reach
                else:
                    lowest_bit = (w & -w).bit_length() - 1
                    mask = (0xFFFFFFFFFFFFFFFF << lowest_bit) & 0xFFFFFFFFFFFFFFFF
                    new_w |= mask
                    
                    highest_bit = w.bit_length() - 1
                    reach = base + highest_bit + context_after
                    if reach > horizon:
                        horizon = reach
            new_words[i] = new_w
            
        # Ostatnie słowo musi być ucięte do size, żeby nie mieć jedynek poza plikiem
        if self._size > 0:
            last_valid_bit = (self._size - 1) % 64
            mask = (1 << (last_valid_bit + 1)) - 1
            if last_valid_bit == 63:
                mask = 0xFFFFFFFFFFFFFFFF
            new_words[num_words - 1] &= mask
            
        return new_bs

    def __and__(self, other: "Bitset") -> "Bitset":
        if not isinstance(other, Bitset):
            raise TypeError("Unsupported operand type(s) for &: 'Bitset' and '{}'".format(type(other).__name__))

        # Ograniczamy do mniejszego rozmiaru
        min_size = min(self._size, other._size)
        new_bs = Bitset(min_size)

        num_words = min(self._num_words, other._num_words)
        new_words = new_bs._words
        w1 = self._words
        w2 = other._words

        # Używamy zoptymalizowanej metody C (map + operator) i oszczędzamy pamięć (RAM) 
        # bez tworzenia fizycznych kopii bufora list stosując islice (zamiast tab[:num]).
        new_words[:num_words] = array.array('Q', map(operator.and_, itertools.islice(w1, num_words), itertools.islice(w2, num_words)))

        if min_size > 0:
            last_valid_bit = (min_size - 1) % 64
            mask = (1 << (last_valid_bit + 1)) - 1
            if last_valid_bit == 63:
                mask = 0xFFFFFFFFFFFFFFFF
            new_words[-1] &= mask

        return new_bs

    def __invert__(self) -> "Bitset":
        new_bs = Bitset(self._size)
        new_words = new_bs._words
        old_words = self._words
        num_words = self._num_words
        
        for i in range(num_words):
            new_words[i] = ~old_words[i] & 0xFFFFFFFFFFFFFFFF
            
        if self._size > 0:
            last_valid_bit = (self._size - 1) % 64
            mask = (1 << (last_valid_bit + 1)) - 1
            if last_valid_bit == 63:
                mask = 0xFFFFFFFFFFFFFFFF
            new_words[-1] &= mask
            
        if self._counts is not None and self._total_count >= 0:
            new_bs._total_count = self._size - self._total_count
            
        return new_bs

def bisect_left_custom(a, x):
    if hasattr(a, 'bisect_left'):
        return a.bisect_left(x)
    return bisect.bisect_left(a, x)

def bisect_right_custom(a, x):
    if hasattr(a, 'bisect_right'):
        return a.bisect_right(x)
    return bisect.bisect_right(a, x)
