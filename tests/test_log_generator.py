"""Testy jednostkowe dla narzędzia dev-scripts/log_generator.py (ETA, postęp, tryb Append)."""

from __future__ import annotations

import argparse
import importlib.util
import threading
from pathlib import Path
from typing import Any

# noinspection PyPackageRequirements
import pytest


def _load_log_generator_module() -> Any:
    """Ładuje dynamicznie moduł dev-scripts/log_generator.py na potrzeby testów."""
    gen_path = Path(__file__).resolve().parent.parent / "dev-scripts" / "log_generator.py"
    spec = importlib.util.spec_from_file_location("log_generator", gen_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def log_gen_mod() -> Any:
    """Udostępnia załadowany moduł log_generator dla testów w tym pliku."""
    return _load_log_generator_module()


class TestTimeAndEtaHelpers:
    """Testy funkcji pomocniczych format_duration, compute_eta oraz _format_batch_status."""

    def test_format_duration_ranges(self, log_gen_mod: Any) -> None:
        assert log_gen_mod.format_duration(0.0) == "0.0s"
        assert log_gen_mod.format_duration(4.24) == "4.2s"
        assert log_gen_mod.format_duration(59.9) == "59.9s"
        assert log_gen_mod.format_duration(60.0) == "1m 00s"
        assert log_gen_mod.format_duration(135.0) == "2m 15s"
        assert log_gen_mod.format_duration(3600.0) == "1h 00m 00s"
        assert log_gen_mod.format_duration(3852.0) == "1h 04m 12s"

    def test_format_duration_negative_and_non_finite(self, log_gen_mod: Any) -> None:
        assert log_gen_mod.format_duration(-10.5) == "0.0s"
        assert log_gen_mod.format_duration(-0.001) == "0.0s"
        assert log_gen_mod.format_duration(float("nan")) == "0.0s"
        assert log_gen_mod.format_duration(float("inf")) == "0.0s"
        assert log_gen_mod.format_duration(float("-inf")) == "0.0s"

    def test_compute_eta_normal_progress(self, log_gen_mod: Any) -> None:
        assert log_gen_mod.compute_eta(25, 100, 10.0) == pytest.approx(30.0)
        assert log_gen_mod.compute_eta(500, 1000, 2.5) == pytest.approx(2.5)

    def test_compute_eta_edge_cases_never_negative(self, log_gen_mod: Any) -> None:
        assert log_gen_mod.compute_eta(0, 100, 5.0) == 0.0
        assert log_gen_mod.compute_eta(50, 100, 0.0) == 0.0
        assert log_gen_mod.compute_eta(100, 100, 5.0) == 0.0
        assert log_gen_mod.compute_eta(150, 100, 5.0) == 0.0
        assert log_gen_mod.compute_eta(-10, 100, 5.0) == 0.0
        assert log_gen_mod.compute_eta(50, -100, 5.0) == 0.0
        assert log_gen_mod.compute_eta(50, 100, -2.0) == 0.0
        assert log_gen_mod.compute_eta(float("nan"), 100, 5.0) == 0.0
        assert log_gen_mod.compute_eta(50, float("inf"), 5.0) == 0.0

    def test_format_batch_status_clamps_and_formats(self, log_gen_mod: Any) -> None:
        pct, text = log_gen_mod._format_batch_status("Progress", 200, 100, "lines", -5.0, "lines/s", -1.0)
        assert pct == 100
        assert "(100.0%)" in text
        assert "Speed: 0.0 lines/s" in text
        assert "Elapsed: 0.0s" in text
        assert "ETA: 0.0s" in text


class TestFastLogGenerator:
    """Testy FastLogGenerator.generate_to_size w trybach Overwrite i Append."""

    def test_generate_to_size_overwrite_and_append_smaller_target(self, log_gen_mod: Any, tmp_path: Path) -> None:
        out_file = tmp_path / "fast_append.log"
        initial_target = 256 * 1024  # 256 KB
        append_target = 64 * 1024  # 64 KB — mniej niż rozmiar istniejącego pliku

        written_1, elapsed_1 = log_gen_mod.FastLogGenerator.generate_to_size(
            filepath=str(out_file),
            target_bytes=initial_target,
            chunk_lines=500,
            overwrite=True,
        )
        assert written_1 == initial_target
        assert elapsed_1 >= 0.0
        assert out_file.stat().st_size == initial_target

        events: list[tuple[int, int, float, float]] = []
        logs: list[str] = []

        written_2, elapsed_2 = log_gen_mod.FastLogGenerator.generate_to_size(
            filepath=str(out_file),
            target_bytes=append_target,
            chunk_lines=500,
            overwrite=False,
            progress_callback=lambda w, t, s, el: events.append((w, t, s, el)),
            log_callback=logs.append,
        )

        assert written_2 == append_target
        assert elapsed_2 >= 0.0
        assert out_file.stat().st_size == initial_target + append_target
        assert len(events) >= 1
        assert events[-1][0] == append_target
        assert events[-1][1] == append_target

        for w, t, spd, el in events:
            assert 0 <= w <= t
            assert spd >= 0.0
            assert el >= 0.0
            assert log_gen_mod.compute_eta(w, t, el) >= 0.0

        assert any("Appended" in msg and "total file size:" in msg for msg in logs)

    def test_generate_to_size_zero_target_reports_final_progress(self, log_gen_mod: Any, tmp_path: Path) -> None:
        out_file = tmp_path / "fast_zero.log"
        events: list[tuple[int, int, float, float]] = []

        written, elapsed = log_gen_mod.FastLogGenerator.generate_to_size(
            filepath=str(out_file),
            target_bytes=0,
            chunk_lines=100,
            overwrite=True,
            progress_callback=lambda w, t, s, el: events.append((w, t, s, el)),
        )

        assert written == 0
        assert elapsed >= 0.0
        assert out_file.stat().st_size == 0
        assert len(events) == 1
        assert events[0][0] == 0
        assert events[0][1] == 0

    def test_generate_to_size_cancellation(self, log_gen_mod: Any, tmp_path: Path) -> None:
        out_file = tmp_path / "fast_cancel.log"
        stop_event = threading.Event()
        stop_event.set()
        events: list[tuple[int, int, float, float]] = []
        logs: list[str] = []

        written, elapsed = log_gen_mod.FastLogGenerator.generate_to_size(
            filepath=str(out_file),
            target_bytes=128 * 1024,
            chunk_lines=1000,
            overwrite=True,
            stop_event=stop_event,
            progress_callback=lambda w, t, s, el: events.append((w, t, s, el)),
            log_callback=logs.append,
        )

        assert written == 0
        assert elapsed >= 0.0
        assert any("cancelled" in msg.lower() for msg in logs)
        assert len(events) == 1
        assert events[0][0] == 0


class TestTargetAndJsonBatchGenerators:
    """Testy generatorów wsadowych TargetLogGenerator oraz JsonLogGenerator."""

    def test_target_run_batch_overwrite_append_and_progress(self, log_gen_mod: Any, tmp_path: Path) -> None:
        out_file = tmp_path / "target_batch.log"
        events: list[tuple[int, int, float, float]] = []

        generated = log_gen_mod.TargetLogGenerator.run_batch(
            filepath=str(out_file),
            count=250,
            phrase="[TARGET]",
            interval=10,
            overwrite=True,
            progress_callback=lambda cur, tot, spd, el: events.append((cur, tot, spd, el)),
        )

        assert generated == 250
        lines = out_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 250
        assert sum(1 for line in lines if "[TARGET]" in line) == 25
        assert len(events) >= 1
        assert events[-1][0] == 250
        assert events[-1][1] == 250
        assert events[-1][2] >= 0.0
        assert events[-1][3] >= 0.0

        appended = log_gen_mod.TargetLogGenerator.run_batch(
            filepath=str(out_file),
            count=50,
            phrase="[TARGET]",
            interval=10,
            overwrite=False,
        )
        assert appended == 50
        assert len(out_file.read_text(encoding="utf-8").splitlines()) == 300

    def test_json_run_batch_progress_and_long_lines(self, log_gen_mod: Any, tmp_path: Path) -> None:
        out_file = tmp_path / "json_batch.log"
        events: list[tuple[int, int, float, float]] = []

        written = log_gen_mod.JsonLogGenerator.run_batch(
            filepath=str(out_file),
            count=45,
            overwrite=True,
            fmt_mode="HYBRID",
            multi_structure=True,
            include_decoys=True,
            long_lines=True,
            progress_callback=lambda cur, tot, spd, el: events.append((cur, tot, spd, el)),
        )

        assert written == 45
        lines = out_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 45
        assert len(events) >= 1
        for cur, tot, spd, el in events:
            assert 0 <= cur <= tot
            assert spd >= 0.0
            assert el >= 0.0
            assert log_gen_mod.compute_eta(cur, tot, el) >= 0.0
        assert events[-1][0] == 45
        assert events[-1][1] == 45

    def test_target_run_batch_cancellation(self, log_gen_mod: Any, tmp_path: Path) -> None:
        out_file = tmp_path / "target_cancel.log"
        stop_event = threading.Event()
        stop_event.set()
        events: list[tuple[int, int, float, float]] = []

        generated = log_gen_mod.TargetLogGenerator.run_batch(
            filepath=str(out_file),
            count=100,
            overwrite=True,
            stop_event=stop_event,
            progress_callback=lambda cur, tot, spd, el: events.append((cur, tot, spd, el)),
        )

        assert generated == 0
        assert len(events) == 1
        assert events[0][0] == 0
        assert events[0][1] == 100


class TestGeneratorGuiTabs:
    """Testy zakładek GUI generatora (TargetGenTab, JsonGenTab, FastGenTab)."""

    def test_tabs_eta_display_and_target_overwrite_toggle(self, log_gen_mod: Any, qtbot: Any) -> None:
        if not log_gen_mod.GUI_AVAILABLE:
            pytest.skip("PySide6 niedostępne w środowisku testowym")

        window = log_gen_mod.GeneratorMainWindow()
        qtbot.addWidget(window)

        # TargetGenTab: domyślnie tryb live -> chk_overwrite wyłączony, po zmianie na batch -> włączony
        target_tab = window.tab_target_gen
        assert target_tab.chk_overwrite.isChecked() is True
        assert target_tab.chk_overwrite.isEnabled() is False
        target_tab.rb_batch.setChecked(True)
        assert target_tab.chk_overwrite.isEnabled() is True

        target_tab.update_stats(250, 1000, 500.0, 0.5)
        assert target_tab.progress_bar.value() == 25
        assert "lines/s" in target_tab.lbl_status.text()
        assert "Elapsed:" in target_tab.lbl_status.text()
        assert "ETA: 1.5s" in target_tab.lbl_status.text()

        # FastGenTab: sprawdzenie obecności ETA i braku wartości ujemnych
        fast_tab = window.tab_fast_gen
        fast_tab.update_stats(5 * 1024 * 1024, 10 * 1024 * 1024, 50.0, 0.1)
        assert fast_tab.progress_bar.value() == 50
        assert "ETA: 0.1s" in fast_tab.lbl_stats.text()

        # JsonGenTab: sprawdzenie ETA oraz prędkości rec/s
        json_tab = window.tab_json_gen
        json_tab.update_stats(500, 1000, 250.0, 2.0)
        assert json_tab.progress_bar.value() == 50
        assert "rec/s" in json_tab.lbl_status.text()
        assert "ETA: 2.0s" in json_tab.lbl_status.text()


class TestGeneratorCliModes:
    """Testy wyjścia postępu oraz ETA w trybach wsadowych CLI."""

    def test_cli_fast_target_json_batch_display_eta(
        self, log_gen_mod: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fast_out = tmp_path / "cli_fast.log"
        log_gen_mod.run_cli_fast(
            argparse.Namespace(output=str(fast_out), size_gb=None, size_mb=0.1, chunk_lines=500, append=False)
        )
        out_fast = capsys.readouterr().out
        assert "ETA:" in out_fast
        assert "Elapsed:" in out_fast
        assert fast_out.exists()

        target_out = tmp_path / "cli_target.log"
        log_gen_mod.run_cli_target(
            argparse.Namespace(
                output=str(target_out), phrase="[TARGET]", interval=5, live=False, count=50, append=False
            )
        )
        out_target = capsys.readouterr().out
        assert "lines/s" in out_target
        assert "ETA:" in out_target
        assert len(target_out.read_text(encoding="utf-8").splitlines()) == 50

        json_out = tmp_path / "cli_json.log"
        log_gen_mod.run_cli_json(
            argparse.Namespace(
                output=str(json_out),
                format="JSON",
                multi_structure=False,
                decoys=False,
                long_lines=False,
                live=False,
                count=20,
                append=False,
                indent=None,
            )
        )
        out_json = capsys.readouterr().out
        assert "rec/s" in out_json
        assert "ETA:" in out_json
        assert len(json_out.read_text(encoding="utf-8").splitlines()) == 20
