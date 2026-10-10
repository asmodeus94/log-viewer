"""
Testy wydajnościowe i weryfikacja regresji (pytest-benchmark).

Plik zawiera zestaw benchmarków pokrywających kluczowe komponenty wydajnościowe Log Viewer:
1. Bitset (operacje bitowe, iteracja, bisekcja, slice, rozwijanie kontekstu)
2. LineIndexer (budowanie indeksu, losowy dostęp do linii, translacja offsetów)
3. FilterEngine (strategie wyszukiwania tekstu i regex na surowych bajtach)
4. Formatters (szybkie formatowanie struktur JSON i XML)
5. EditBuffer (buforowanie i sprawdzanie modyfikacji linii w pamięci)

Uruchomienie pomiarów wydajnościowych:
    .venv\\Scripts\\python.exe -m pytest tests/test_benchmarks.py --benchmark-enable
    .venv\\Scripts\\python.exe scripts/verify.py --benchmark

Zapisywanie bazy porównawczej (baseline):
    .venv\\Scripts\\python.exe -m pytest tests/test_benchmarks.py --benchmark-enable --benchmark-save=baseline

Porównanie z bazą (wykrywanie regresji):
    .venv\\Scripts\\python.exe -m pytest tests/test_benchmarks.py --benchmark-enable --benchmark-compare
"""

from __future__ import annotations

import array
import os
import random
import tempfile
from typing import Any

import pytest
from log_viewer.bitset import Bitset, bisect_left_custom
from log_viewer.edit_buffer import EditBuffer
from log_viewer.filter_engine import PlainTextStrategy, RegexStrategy
from log_viewer.formatters import format_json, format_xml
from log_viewer.indexer import LineIndexer

# ============================================================================
# DANE TESTOWE I POMOCNICZE
# ============================================================================


def _create_sample_bitset(size: int = 500_000, hit_density: float = 0.05) -> Bitset:
    """Tworzy instancję Bitset o zadanym rozmiarze i gęstości trafień."""
    rng = random.Random(42)
    step = int(1.0 / hit_density) if hit_density > 0 else 100
    indices = array.array("Q", (i for i in range(0, size, step) if rng.random() < 0.8))
    return Bitset.from_indices(indices, size)


def _generate_synthetic_log_chunk(num_lines: int = 20_000) -> bytes:
    """Generuje spójny blok bajtów imitujący realistyczny format logów serwerowych."""
    levels = [b"INFO", b"WARNING", b"ERROR", b"DEBUG"]
    messages = [
        b"User authenticated successfully with session token.",
        b"Connection to Redis cache node timed out after 3000ms.",
        b"Periodic health check passed. Worker pool active.",
        b"Payment gateway latency detected on transaction payload.",
        b"Cache invalidated for key session_tokens_v2.",
        b"Disk I/O latency spike detected on storage partition.",
    ]
    lines: list[bytes] = []
    for i in range(num_lines):
        lvl = levels[i % 4]
        msg = messages[i % 6]
        lines.append(b"[2026-10-10 12:00:%02d.000] [%b] Line %08d: %b\n" % (i % 60, lvl, i, msg))
    return b"".join(lines)


# ============================================================================
# 1. BENCHMARKI DLA STRUKTURY BITSET
# ============================================================================


@pytest.mark.benchmark
def test_benchmark_bitset_iteration(benchmark: Any) -> None:
    """Mierzy wydajność bezpośredniej iteracji (__iter__) po ustawionych bitach w Rank & Select Bitset."""
    bs = _create_sample_bitset(size=300_000, hit_density=0.1)

    def run_iter() -> int:
        count = 0
        for _ in bs:
            count += 1
        return count

    res = benchmark(run_iter)
    assert res > 0


@pytest.mark.benchmark
def test_benchmark_bitset_bisect(benchmark: Any) -> None:
    """Mierzy czas wykonywania zapytań bisekcji (Rank & Select) bisect_left_custom na dużej tablicy bitowej."""
    bs = _create_sample_bitset(size=500_000, hit_density=0.05)
    rng = random.Random(123)
    queries = [rng.randint(0, 499_999) for _ in range(500)]

    def run_bisect() -> int:
        total = 0
        for q in queries:
            total += bisect_left_custom(bs, q)
        return total

    res = benchmark(run_bisect)
    assert res > 0


@pytest.mark.benchmark
def test_benchmark_bitset_slice_access(benchmark: Any) -> None:
    """Mierzy czas pobierania plasterków (slice) indeksów z Bitsetu (symulacja pobierania stron widoku)."""
    bs = _create_sample_bitset(size=500_000, hit_density=0.08)
    total_hits = len(bs)

    def run_slice() -> int:
        accum = 0
        for start in range(0, min(total_hits - 200, 2000), 200):
            part = bs[start : start + 200]
            accum += len(part)
        return accum

    res = benchmark(run_slice)
    assert res > 0


@pytest.mark.benchmark
def test_benchmark_bitset_bitwise_and(benchmark: Any) -> None:
    """Mierzy operację koniunkcji bitowej (__and__) dwóch Bitsetów o rozmiarze 500 000 linii."""
    bs1 = _create_sample_bitset(size=500_000, hit_density=0.05)
    bs2 = _create_sample_bitset(size=500_000, hit_density=0.03)

    def run_and() -> Bitset:
        return bs1 & bs2

    res = benchmark(run_and)
    assert isinstance(res, Bitset)


@pytest.mark.benchmark
def test_benchmark_bitset_bitwise_invert(benchmark: Any) -> None:
    """Mierzy czas pełnej negacji logicznej (__invert__) Bitsetu (używane w filtrze negowanym)."""
    bs = _create_sample_bitset(size=500_000, hit_density=0.05)

    def run_invert() -> Bitset:
        return ~bs

    res = benchmark(run_invert)
    assert isinstance(res, Bitset)


@pytest.mark.benchmark
def test_benchmark_bitset_expand_context(benchmark: Any) -> None:
    """Mierzy szybkość rozszerzania kontekstu linii (expand_context) dla wyników filtrowania."""
    bs = _create_sample_bitset(size=200_000, hit_density=0.02)

    def run_expand() -> Bitset:
        return bs.expand_context(context_after=5)

    res = benchmark(run_expand)
    assert len(res) >= len(bs)


# ============================================================================
# 2. BENCHMARKI DLA SILNIKA FILTROWANIA (FilterEngine / Strategies)
# ============================================================================


@pytest.mark.benchmark
def test_benchmark_filter_plain_case_sensitive(benchmark: Any) -> None:
    """Mierzy przepustowość skanowania surowych bajtów w poszukiwaniu tekstu (case-sensitive)."""
    chunk = _generate_synthetic_log_chunk(num_lines=25_000)
    strategy = PlainTextStrategy(needle="ERROR", case_sensitive=True, negate=False, encoding="utf-8")

    def run_match() -> list[int]:
        return strategy.match_chunk(chunk, start_line=0)

    hits = benchmark(run_match)
    assert len(hits) > 0


@pytest.mark.benchmark
def test_benchmark_filter_plain_case_insensitive(benchmark: Any) -> None:
    """Mierzy wydajność wyszukiwania z ignorowaniem wielkości liter na buforze bajtowym."""
    chunk = _generate_synthetic_log_chunk(num_lines=25_000)
    strategy = PlainTextStrategy(needle="error", case_sensitive=False, negate=False, encoding="utf-8")

    def run_match() -> list[int]:
        return strategy.match_chunk(chunk, start_line=0)

    hits = benchmark(run_match)
    assert len(hits) > 0


@pytest.mark.benchmark
def test_benchmark_filter_regex_bytes(benchmark: Any) -> None:
    """Mierzy wydajność skanowania wyrażeń regularnych na surowych bajtach (RegexStrategy)."""
    chunk = _generate_synthetic_log_chunk(num_lines=25_000)
    pattern = r"Line \d{4}00"
    strategy = RegexStrategy(pattern=pattern, case_sensitive=True, negate=False, encoding="utf-8")

    def run_regex() -> list[int]:
        return strategy.match_chunk(chunk, start_line=0)

    hits = benchmark(run_regex)
    assert len(hits) > 0


@pytest.mark.benchmark
def test_benchmark_filter_negated_plain(benchmark: Any) -> None:
    """Mierzy wydajność odwróconego dopasowania (negate=True) w chunku danych."""
    chunk = _generate_synthetic_log_chunk(num_lines=15_000)
    strategy = PlainTextStrategy(needle="WARNING", case_sensitive=True, negate=True, encoding="utf-8")

    def run_negated() -> list[int]:
        return strategy.match_chunk(chunk, start_line=0)

    hits = benchmark(run_negated)
    assert len(hits) > 0


# ============================================================================
# 3. BENCHMARKI DLA INDEKSERA (LineIndexer)
# ============================================================================


@pytest.mark.benchmark
def test_benchmark_indexer_build_single(benchmark: Any) -> None:
    """Mierzy czas budowy indeksu rzadkiego (LineIndexer) dla pliku logu o rozmiarze ~3 MB (30 000 linii)."""
    data = _generate_synthetic_log_chunk(num_lines=30_000)
    fd, path = tempfile.mkstemp(suffix=".log")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        def build() -> int:
            with LineIndexer(path) as idx:
                return idx.line_count

        lines = benchmark(build)
        assert lines == 30_000
    finally:
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


@pytest.mark.benchmark
def test_benchmark_indexer_random_seek(benchmark: Any) -> None:
    """Mierzy czas losowego dostępu (Random Seek) do wybranych linii w pliku przy użyciu indeksu."""
    data = _generate_synthetic_log_chunk(num_lines=20_000)
    fd, path = tempfile.mkstemp(suffix=".log")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        rng = random.Random(999)
        sample_lines = [rng.randint(0, 19_900) for _ in range(50)]

        with LineIndexer(path) as idx:

            def run_seeks() -> int:
                count = 0
                for line_no in sample_lines:
                    res = idx.read_lines(line_no, 10)
                    count += len(res)
                return count

            total_read = benchmark(run_seeks)
            assert total_read == 500
    finally:
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


@pytest.mark.benchmark
def test_benchmark_indexer_line_at_byte_offset(benchmark: Any) -> None:
    """Mierzy szybkość translacji byte_offset -> line_no dla suwaka nawigacyjnego w GUI."""
    data = _generate_synthetic_log_chunk(num_lines=20_000)
    fd, path = tempfile.mkstemp(suffix=".log")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        file_size = len(data)

        rng = random.Random(777)
        sample_offsets = [rng.randint(0, file_size - 1) for _ in range(200)]

        with LineIndexer(path) as idx:

            def run_offsets() -> int:
                total_lines = 0
                for off in sample_offsets:
                    line_no, _ = idx.line_at_byte_offset(off)
                    total_lines += line_no
                return total_lines

            total = benchmark(run_offsets)
            assert total > 0
    finally:
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


@pytest.mark.benchmark
def test_benchmark_indexer_incremental_update(benchmark: Any) -> None:
    """Mierzy czas przyrostowej aktualizacji indeksu (update_from) po dopisaniu nowych linii (Tail mode)."""
    data = _generate_synthetic_log_chunk(num_lines=15_000)
    extra_data = _generate_synthetic_log_chunk(num_lines=2_000)
    fd, path = tempfile.mkstemp(suffix=".log")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        def run_update() -> int:
            # Tworzymy indexer i dopisujemy dane
            with LineIndexer(path) as idx:
                initial_size = idx.size
                with open(path, "ab") as f_append:
                    f_append.write(extra_data)
                new_size = initial_size + len(extra_data)
                added = idx.update_from(new_size)
                # Obcinamy plik z powrotem do początkowej długości dla kolejnych rund
                with open(path, "r+b") as f_reset:
                    f_reset.truncate(initial_size)
                return added

        added_lines = benchmark(run_update)
        assert added_lines == 2_000
    finally:
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


# ============================================================================
# 4. BENCHMARKI DLA FORMATERÓW (Formatters: JSON & XML)
# ============================================================================


@pytest.mark.benchmark
def test_benchmark_formatter_json(benchmark: Any) -> None:
    """Mierzy szybkość wykrywania i formatowania zagnieżdżonego rekordu JSON w linii logu."""
    raw_log = (
        '2026-10-10 12:00:00 [INFO] Payload received: {"event": "transaction_completed", '
        '"user": {"id": "usr_9988", "roles": ["admin", "developer"], "meta": {"ip": "192.168.1.100"}}, '
        '"items": [{"sku": "ITEM-1", "qty": 2, "price": 49.99}, {"sku": "ITEM-2", "qty": 1, "price": 12.50}], '
        '"status": "CONFIRMED"}'
    )

    def run_json() -> str:
        return format_json(raw_log)

    formatted = benchmark(run_json)
    assert "\n" in formatted


@pytest.mark.benchmark
def test_benchmark_formatter_xml(benchmark: Any) -> None:
    """Mierzy szybkość formatowania i parsowania drzewa XML z defusedxml."""
    raw_xml = (
        '<response status="success"><transaction id="tx-1002"><customer id="c-44"><name>Jan Kowalski</name>'
        '<email>jan@example.com</email></customer><order><item code="ABC" qty="3"/><item code="XYZ" '
        'qty="1"/></order><total currency="PLN">450.00</total></transaction></response>'
    )

    def run_xml() -> str:
        return format_xml(raw_xml)

    formatted = benchmark(run_xml)
    assert "\n" in formatted


# ============================================================================
# 5. BENCHMARKI DLA EDIT BUFFER (EditBuffer)
# ============================================================================


@pytest.mark.benchmark
def test_benchmark_edit_buffer_lookup(benchmark: Any) -> None:
    """Mierzy wydajność odpytywania i buforowania modyfikacji w EditBuffer dla 10 000 wpisów."""
    buf = EditBuffer()
    for i in range(0, 50_000, 5):
        buf.set(i, f"Zmodyfikowana linia numer {i} z nowa trescia rekordu")

    rng = random.Random(444)
    queries = [rng.randint(0, 50_000) for _ in range(5_000)]

    def run_lookup() -> int:
        hits = 0
        for q in queries:
            if buf.has(q):
                hits += 1
        return hits

    result = benchmark(run_lookup)
    assert result > 0
