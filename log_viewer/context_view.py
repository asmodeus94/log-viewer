import array
import bisect
from typing import Sequence, Iterator, Union, overload

class ContextExpandedView(Sequence[int]):
    """
    Leniwy widok na wyniki filtrowania z kontekstem.
    Zamiast alokować miliony elementów dla rozszerzonego kontekstu,
    w locie wylicza wartości na podstawie scalonych zakresów (ranges).
    """
    def __init__(self, hits: "array.array[int]", context_after: int, total_lines: int):
        self._hits = hits
        self._context_after = context_after
        self._total_lines = total_lines
        
        self._range_starts = array.array('Q')
        self._range_ends = array.array('Q')
        self._cumulative_lengths = array.array('Q')
        
        if not hits:
            self._length = 0
            return
            
        current_start = hits[0]
        current_end = min(hits[0] + context_after + 1, total_lines)
        
        length_so_far = 0
        
        for i in range(1, len(hits)):
            hit = hits[i]
            if hit <= current_end:
                current_end = min(hit + context_after + 1, total_lines)
            else:
                length = current_end - current_start
                self._range_starts.append(current_start)
                self._range_ends.append(current_end)
                length_so_far += length
                self._cumulative_lengths.append(length_so_far)
                
                current_start = hit
                current_end = min(hit + context_after + 1, total_lines)
                
        length = current_end - current_start
        self._range_starts.append(current_start)
        self._range_ends.append(current_end)
        length_so_far += length
        self._cumulative_lengths.append(length_so_far)
        
        self._length = length_so_far

    def __len__(self) -> int:
        return self._length
        
    def __bool__(self) -> bool:
        return self._length > 0

    @overload
    def __getitem__(self, index: int) -> int: ...
    @overload
    def __getitem__(self, index: slice) -> "array.array[int]": ...
    
    def __getitem__(self, index: Union[int, slice]):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            if step != 1:
                raise ValueError("ContextExpandedView only supports step=1 for slicing")
            result = array.array('Q')
            if start >= stop:
                return result
                
            start_range_idx = bisect.bisect_right(self._cumulative_lengths, start)
            if start_range_idx == len(self._range_starts):
                return result
                
            range_start = self._range_starts[start_range_idx]
            range_end = self._range_ends[start_range_idx]
            offset_in_range = start if start_range_idx == 0 else start - self._cumulative_lengths[start_range_idx - 1]
            current_val = range_start + offset_in_range
            
            elements_to_take = stop - start
            
            while elements_to_take > 0 and start_range_idx < len(self._range_starts):
                range_start = self._range_starts[start_range_idx]
                range_end = self._range_ends[start_range_idx]
                avail = range_end - current_val
                take = min(avail, elements_to_take)
                
                result.fromlist(list(range(current_val, current_val + take)))
                
                elements_to_take -= take
                start_range_idx += 1
                if start_range_idx < len(self._range_starts):
                    current_val = self._range_starts[start_range_idx]
                    
            return result
        else:
            if index < 0:
                index += self._length
            if index < 0 or index >= self._length:
                raise IndexError("ContextExpandedView index out of range")
                
            range_idx = bisect.bisect_right(self._cumulative_lengths, index)
            offset_in_range = index if range_idx == 0 else index - self._cumulative_lengths[range_idx - 1]
            return self._range_starts[range_idx] + offset_in_range

    def __iter__(self) -> Iterator[int]:
        for i in range(len(self._range_starts)):
            for val in range(self._range_starts[i], self._range_ends[i]):
                yield val
                
    def bisect_left(self, value: int) -> int:
        if not self._range_starts:
            return 0
            
        low, high = 0, len(self._range_starts) - 1
        ans_range = len(self._range_starts)
        while low <= high:
            mid = (low + high) // 2
            start = self._range_starts[mid]
            end = self._range_ends[mid]
            if end > value:
                ans_range = mid
                high = mid - 1
            else:
                low = mid + 1
                
        if ans_range == len(self._range_starts):
            return self._length
            
        start = self._range_starts[ans_range]
        end = self._range_ends[ans_range]
        prev_length = self._cumulative_lengths[ans_range - 1] if ans_range > 0 else 0
        if value <= start:
            return prev_length
        else:
            return prev_length + (value - start)
            
    def bisect_right(self, value: int) -> int:
        return self.bisect_left(value + 1)

def bisect_left_custom(a, x):
    if hasattr(a, 'bisect_left'):
        return a.bisect_left(x)
    import bisect
    return bisect.bisect_left(a, x)

def bisect_right_custom(a, x):
    if hasattr(a, 'bisect_right'):
        return a.bisect_right(x)
    import bisect
    return bisect.bisect_right(a, x)
