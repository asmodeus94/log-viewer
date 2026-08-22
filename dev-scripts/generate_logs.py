"""
Unified Log Generator Tool
Developer utility for generating various types of logs:
1. Multi-App Live Simulator (concurrent application logs with transactions and stack traces)
2. Fast Large File Generator (high-throughput binary buffered generation up to GBs)
3. Target Log Generator ([TARGET] keyword injection for filter and search testing)
4. Structured JSON / JSONL Generator (rich microservice event payloads)

Supports both PySide6 Graphical User Interface (GUI) and Command Line Interface (CLI).
"""

import argparse
import json
import multiprocessing
import os
import random
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================================
# CONSTANTS AND GENERATION DATASETS
# ============================================================================

# Fast generator log levels and template messages
FAST_LOG_LEVELS = ["INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"]
FAST_LOG_MESSAGES = [
    "User logged in successfully.",
    "Database connection error.",
    "High RAM usage detected.",
    "Configuration file updated.",
    "Network packets lost.",
    "Background task started.",
    "Unauthorized access attempt.",
    "Data synchronized successfully.",
    "Cache invalidated for key session_tokens.",
    "Worker thread pool resized to 16 workers.",
    "Periodic health check passed.",
    "Disk I/O latency spike detected on volume /dev/sda1.",
]

# JSON / JSONL generator dataset
JSON_SERVICES = [
    "auth-service",
    "payment-api",
    "user-service",
    "order-processor",
    "notification-worker",
    "analytics-pipeline",
    "gateway-proxy",
    "search-indexer",
]

JSON_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
JSON_LEVEL_WEIGHTS = [0.15, 0.55, 0.15, 0.12, 0.03]

JSON_HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
JSON_ENDPOINTS = [
    "/api/v1/users",
    "/api/v1/auth/login",
    "/api/v1/checkout",
    "/api/v2/orders",
    "/healthz",
    "/metrics",
    "/api/v1/products/search",
    "/api/v1/payments/charge",
    "/api/v1/notifications/send",
]
JSON_STATUS_CODES = [200, 201, 204, 400, 401, 403, 404, 429, 500, 502, 503]

JSON_ERROR_MESSAGES = [
    "Connection to Redis primary node timed out after 3000ms",
    "Database lock wait timeout exceeded; try restarting transaction",
    "Failed to authenticate JWT token: signature expired",
    "Third-party payment gateway returned HTTP 502 Bad Gateway",
    "Out of memory error in worker pool #4",
    "Disk queue limit exceeded for kafka topic 'user-events'",
    "Rate limit exceeded for IP address 198.51.100.42",
    "Invalid payload: missing required field 'cart_id'",
]

JSON_USERS = [f"usr_{uuid.uuid4().hex[:8]}" for _ in range(20)]
JSON_IPS = [
    f"192.168.1.{random.randint(1, 254)}",
    f"10.0.0.{random.randint(1, 254)}",
    f"172.16.{random.randint(0, 50)}.{random.randint(1, 254)}",
]

# Wspólne style CSS dla przycisków
STYLE_BTN_PRIMARY = """
    QPushButton {
        background-color: #2563eb;
        color: #ffffff;
        font-weight: bold;
        border: none;
        border-radius: 4px;
        padding: 8px 16px;
    }
    QPushButton:hover {
        background-color: #1d4ed8;
    }
    QPushButton:pressed {
        background-color: #1e40af;
    }
    QPushButton:disabled {
        background-color: #94a3b8;
        color: #f1f5f9;
    }
"""

STYLE_BTN_STOP = """
    QPushButton {
        background-color: #dc2626;
        color: #ffffff;
        font-weight: bold;
        border: none;
        border-radius: 4px;
        padding: 8px 16px;
    }
    QPushButton:hover {
        background-color: #b91c1c;
    }
    QPushButton:pressed {
        background-color: #991b1b;
    }
    QPushButton:disabled {
        background-color: #94a3b8;
        color: #f1f5f9;
    }
"""

STYLE_BTN_INSTANCE = """
    QPushButton {
        background-color: #475569;
        color: #ffffff;
        border-radius: 4px;
        padding: 5px 10px;
        font-weight: 500;
        border: none;
    }
    QPushButton:hover {
        background-color: #334155;
    }
    QPushButton:checked {
        background-color: #d97706;
        color: #ffffff;
        font-weight: bold;
    }
"""

STYLE_BTN_AUTOSCROLL = """
    QPushButton {
        background-color: #334155;
        color: #cbd5e1;
        border-radius: 4px;
        padding: 4px 10px;
        font-weight: 500;
        border: 1px solid #475569;
    }
    QPushButton:hover {
        background-color: #475569;
        color: #ffffff;
    }
    QPushButton:checked {
        background-color: #0284c7;
        color: #ffffff;
        font-weight: bold;
        border: 1px solid #38bdf8;
    }
"""


# ============================================================================
# CORE GENERATOR LOGIC
# ============================================================================


class MultiAppWorker:
    """Worker generating logs for a single APP instance in a background thread."""

    def __init__(
        self,
        file_index: int,
        output_dir: str,
        delay_min: float,
        delay_max: float,
        log_callback: Callable[[str], None] | None = None,
        finished_callback: Callable[[], None] | None = None,
    ) -> None:
        self.file_index = file_index
        self.output_dir = output_dir
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.log_callback = log_callback
        self.finished_callback = finished_callback

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()

    def stop(self) -> None:
        self.stop_event.set()

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def is_paused(self) -> bool:
        return not self.pause_event.is_set()

    def _emit_log(self, msg: str) -> None:
        if self.log_callback:
            self.log_callback(msg)

    def run(self) -> None:
        app_name = f"APP-{self.file_index}"
        file_path = os.path.join(self.output_dir, f"app_{self.file_index}.log")
        log_levels = ["INFO", "DEBUG", "WARN", "ERROR"]

        self._emit_log(f"[{app_name}] Started -> {file_path}")

        try:
            with open(file_path, "a", encoding="utf-8") as f:
                counter = 1
                while not self.stop_event.is_set():
                    if not self.pause_event.is_set():
                        self.pause_event.wait(timeout=0.2)
                        continue

                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                    if counter % 12 == 0:
                        tx_id = f"TX-{hex(random.getrandbits(32))[2:].upper()}"
                        lines = [
                            f"[{now}] [{app_name}] [INFO] Transaction {tx_id} processing started\n",
                            f"[{now}] [{app_name}] [DEBUG] Connecting to authorization service for {tx_id}...\n",
                            f"[{now}] [{app_name}] [WARN] Payment gateway latency detected\n",
                            f"[{now}] [{app_name}] [ERROR] Transaction {tx_id} rejected: Insufficient funds (Code 403)\n",
                            f"[{now}] [{app_name}] [INFO] Channel closed for {tx_id}\n",
                        ]
                        f.writelines(lines)
                        counter += len(lines) - 1
                    elif counter % 19 == 0:
                        err_id = f"ERR-{random.randint(100, 999)}"
                        lines = [
                            f"[{now}] [{app_name}] [ERROR] Critical error {err_id} in synchronization module\n",
                            "Traceback (most recent call last):\n",
                            '  File "core/sync.py", line 102, in run_sync\n',
                            "    payload = prepare_data(packet)\n",
                            '  File "core/utils.py", line 45, in prepare_data\n',
                            "    return packet.headers['X-Auth']\n",
                            "KeyError: 'X-Auth'\n",
                            f"[{now}] [{app_name}] [INFO] Restarting service after error {err_id}\n",
                        ]
                        f.writelines(lines)
                        counter += len(lines) - 1
                    else:
                        level = random.choice(log_levels)
                        random_hex = hex(random.getrandbits(64))[2:]
                        line = (
                            f"[{now}] [{app_name}] [{level}] "
                            f"Standard background task ({counter}). Status OK. Ref: {random_hex}\n"
                        )
                        f.write(line)

                    f.flush()

                    if counter % 50 == 0:
                        self._emit_log(f"[{app_name}] Written {counter} lines...")

                    counter += 1
                    time.sleep(random.uniform(self.delay_min, self.delay_max))
        except BaseException as e:
            self._emit_log(f"[{app_name}] Finished or interrupted: {e}")
        finally:
            self._emit_log(f"[{app_name}] Session terminated.")
            if self.finished_callback:
                self.finished_callback()


class FastLogGenerator:
    """High-throughput binary log generator using memory buffer chunks."""

    @staticmethod
    def generate_chunk(lines_count: int) -> bytes:
        """Generates a byte buffer containing formatted log lines."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"[{timestamp}] [{random.choice(FAST_LOG_LEVELS)}] {random.choice(FAST_LOG_MESSAGES)}\n"
            for _ in range(lines_count)
        ]
        return "".join(lines).encode("utf-8")

    @classmethod
    def generate_to_size(
        cls,
        filepath: str,
        target_bytes: int,
        chunk_lines: int = 100_000,
        overwrite: bool = True,
        stop_event: threading.Event | None = None,
        progress_callback: Callable[[int, int, float, float], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> tuple[int, float]:
        """
        Generates a file of the specified target size in bytes.
        Returns: (written_bytes, elapsed_seconds).
        """
        mode = "wb" if overwrite else "ab"
        start_time = time.time()
        written_bytes = 0 if overwrite or not os.path.exists(filepath) else os.path.getsize(filepath)

        if log_callback:
            target_mb = target_bytes / (1024 * 1024)
            log_callback(f"Fast write started to '{filepath}'. Target: {target_mb:.2f} MB.")

        try:
            with open(filepath, mode) as f:
                while written_bytes < target_bytes:
                    if stop_event and stop_event.is_set():
                        if log_callback:
                            log_callback("Generation cancelled by user.")
                        break

                    chunk = cls.generate_chunk(chunk_lines)
                    if written_bytes + len(chunk) > target_bytes:
                        chunk = chunk[: target_bytes - written_bytes]

                    f.write(chunk)
                    written_bytes += len(chunk)

                    elapsed = time.time() - start_time
                    speed_mb = (written_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0

                    if progress_callback:
                        progress_callback(written_bytes, target_bytes, speed_mb, elapsed)

            elapsed = time.time() - start_time
            if log_callback:
                log_callback(f"Done! Written {written_bytes / (1024**2):.2f} MB in {elapsed:.2f} s.")
            return written_bytes, elapsed
        except Exception as e:
            if log_callback:
                log_callback(f"Fast write error: {e}")
            raise


class TargetLogGenerator:
    """Target log generator injecting specific keywords for search and filter testing."""

    @staticmethod
    def generate_line(counter: int, phrase: str = "[TARGET]", interval: int = 15) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if counter % interval == 0:
            return f"[{now}] {phrase} <- Target keyword detected! Context payload following...\n"
        return f"[{now}] [INFO]  Standard log entry. Context counter: {counter % interval}\n"

    @classmethod
    def run_batch(
        cls,
        filepath: str,
        count: int,
        phrase: str = "[TARGET]",
        interval: int = 15,
        overwrite: bool = False,
        stop_event: threading.Event | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> int:
        """Generates a batch of test lines."""
        mode = "w" if overwrite else "a"
        if log_callback:
            log_callback(f"Generating {count} test lines to '{filepath}' (keyword: {phrase})...")

        generated = 0
        with open(filepath, mode, encoding="utf-8") as f:
            for i in range(1, count + 1):
                if stop_event and stop_event.is_set():
                    break
                line = cls.generate_line(i, phrase, interval)
                f.write(line)
                generated += 1
                if i % 100 == 0 or i == count:
                    f.flush()
                    if progress_callback:
                        progress_callback(generated, count)

        if log_callback:
            log_callback(f"Finished writing {generated} lines to '{filepath}'.")
        return generated

    @classmethod
    def run_live(
        cls,
        filepath: str,
        delay: float = 0.3,
        phrase: str = "[TARGET]",
        interval: int = 15,
        stop_event: threading.Event | None = None,
        line_callback: Callable[[int, str], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Generates continuous live stream with delay."""
        if log_callback:
            log_callback(f"Live target stream started for '{filepath}' (interval: {delay}s)...")

        counter = 1
        with open(filepath, "a", encoding="utf-8") as f:
            while not (stop_event and stop_event.is_set()):
                line = cls.generate_line(counter, phrase, interval)
                f.write(line)
                f.flush()
                if line_callback:
                    line_callback(counter, line.strip())
                counter += 1
                time.sleep(delay)

        if log_callback:
            log_callback(f"Live target stream stopped. Total lines generated: {counter - 1}.")


class JsonLogGenerator:
    """Structured JSON / JSON Lines log generator."""

    @staticmethod
    def generate_entry(i: int, base_time: datetime | None = None) -> dict:
        if base_time is None:
            now_dt = datetime.now()
            timestamp = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        else:
            dt = base_time + timedelta(milliseconds=i * 125 + random.randint(0, 50))
            timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        level = random.choices(JSON_LEVELS, weights=JSON_LEVEL_WEIGHTS)[0]
        service = random.choice(JSON_SERVICES)
        trace_id = uuid.uuid4().hex

        log = {
            "id": i + 1,
            "timestamp": timestamp,
            "level": level,
            "service": service,
            "trace_id": trace_id,
            "span_id": uuid.uuid4().hex[:16],
        }

        if level in ["ERROR", "CRITICAL"]:
            log["message"] = random.choice(JSON_ERROR_MESSAGES)
            log["error"] = {
                "code": f"ERR_{random.randint(1000, 9999)}",
                "retryable": random.choice([True, False]),
                "stack_trace": [
                    f"File '/app/services/{service}.py', line {random.randint(20, 200)}, in execute",
                    f"File '/app/lib/db.py', line {random.randint(50, 500)}, in query",
                    f"RuntimeError: {log['message']}",
                ],
            }
        elif random.random() > 0.4:
            method = random.choice(JSON_HTTP_METHODS)
            endpoint = random.choice(JSON_ENDPOINTS)
            status = random.choice(JSON_STATUS_CODES)
            latency = round(random.uniform(2.5, 850.0), 2)
            log["message"] = f"{method} {endpoint} -> {status} ({latency}ms)"
            log["http"] = {
                "method": method,
                "url": endpoint,
                "status_code": status,
                "latency_ms": latency,
                "client_ip": random.choice(JSON_IPS),
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            if status >= 400:
                log["user_id"] = random.choice(JSON_USERS)
        else:
            log["message"] = f"Processed event payload in {service}"
            log["context"] = {
                "user_id": random.choice(JSON_USERS),
                "environment": random.choice(["production", "staging"]),
                "memory_usage_mb": round(random.uniform(120.0, 512.0), 1),
                "active_threads": random.randint(4, 32),
            }

        return log

    @classmethod
    def run_batch(
        cls,
        filepath: str,
        count: int,
        overwrite: bool = True,
        indent: int | None = None,
        stop_event: threading.Event | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> int:
        """Generates a batch of JSON records."""
        mode = "w" if overwrite else "a"
        base_time = datetime.now() - timedelta(minutes=max(1, count // 10))

        if log_callback:
            log_callback(f"Generating {count} JSON records to '{filepath}'...")

        written = 0
        with open(filepath, mode, encoding="utf-8") as f:
            for i in range(count):
                if stop_event and stop_event.is_set():
                    break
                entry = cls.generate_entry(i, base_time)
                line = json.dumps(entry, ensure_ascii=False, indent=indent)
                f.write(line + "\n")
                written += 1
                if (i + 1) % 50 == 0 or (i + 1) == count:
                    f.flush()
                    if progress_callback:
                        progress_callback(written, count)

        if log_callback:
            log_callback(f"Written {written} JSON records to '{filepath}'.")
        return written

    @classmethod
    def run_live(
        cls,
        filepath: str,
        delay: float = 0.1,
        stop_event: threading.Event | None = None,
        line_callback: Callable[[int, str], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Continuous live JSON stream."""
        if log_callback:
            log_callback(f"Live JSON streaming started for '{filepath}' (delay: {delay}s)...")

        counter = 0
        with open(filepath, "a", encoding="utf-8") as f:
            while not (stop_event and stop_event.is_set()):
                entry = cls.generate_entry(counter)
                line = json.dumps(entry, ensure_ascii=False)
                f.write(line + "\n")
                f.flush()
                counter += 1
                if line_callback:
                    line_callback(counter, line)
                time.sleep(delay)

        if log_callback:
            log_callback(f"Live JSON streaming stopped. Total records generated: {counter}.")


# ============================================================================
# COMMAND LINE INTERFACE (CLI)
# ============================================================================


def run_cli_live(args: argparse.Namespace) -> None:
    """Runs multi-app simulation in CLI mode."""
    out_dir = os.path.abspath(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)
    num_files = args.apps
    d_min = args.min_delay
    d_max = args.max_delay

    print("=== Multi-App Live Simulator ===")
    print(f"Output Directory: {out_dir}")
    print(f"Applications: {num_files}, Delay: {d_min:.2f}s - {d_max:.2f}s")
    print("Press Ctrl+C to stop simulation.\n")

    workers: list[MultiAppWorker] = []
    threads: list[threading.Thread] = []

    for i in range(1, num_files + 1):
        worker = MultiAppWorker(
            i, out_dir, d_min, d_max, log_callback=lambda msg: print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        )
        t = threading.Thread(target=worker.run, daemon=True)
        workers.append(worker)
        threads.append(t)
        t.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping workers...")
        for w in workers:
            w.resume()
            w.stop()
        for t in threads:
            t.join()
        print("All generators stopped successfully.")


def run_cli_fast(args: argparse.Namespace) -> None:
    """Runs fast file generator in CLI mode."""
    filepath = os.path.abspath(args.output)
    if args.size_gb:
        target_bytes = int(args.size_gb * 1024 * 1024 * 1024)
    elif args.size_mb:
        target_bytes = int(args.size_mb * 1024 * 1024)
    else:
        target_bytes = 1 * 1024 * 1024 * 1024  # Default 1 GB

    print("=== Fast Large File Generator ===")
    print(f"Output File: {filepath}")
    print(f"Target Size: {target_bytes / (1024**2):.2f} MB ({target_bytes / (1024**3):.2f} GB)")
    print("Press Ctrl+C to cancel.\n")

    stop_event = threading.Event()

    def progress_cb(written: int, total: int, speed: float, elapsed: float) -> None:
        pct = (written / total) * 100 if total > 0 else 0
        written_mb = written / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        print(
            f"\rProgress: {written_mb:.1f} / {total_mb:.1f} MB ({pct:.1f}%) | "
            f"Speed: {speed:.1f} MB/s | Elapsed: {elapsed:.1f}s",
            end="",
            flush=True,
        )

    try:
        written, elapsed = FastLogGenerator.generate_to_size(
            filepath=filepath,
            target_bytes=target_bytes,
            chunk_lines=args.chunk_lines,
            overwrite=not args.append,
            stop_event=stop_event,
            progress_callback=progress_cb,
            log_callback=lambda msg: print(f"\n{msg}"),
        )
        avg_speed = (written / (1024**2)) / elapsed if elapsed > 0 else 0
        print(f"\nDone! Generated {written / (1024**2):.2f} MB in {elapsed:.2f} s ({avg_speed:.1f} MB/s).")
    except KeyboardInterrupt:
        stop_event.set()
        print("\nGeneration interrupted by user.")


def run_cli_target(args: argparse.Namespace) -> None:
    """Runs target log generator in CLI mode."""
    filepath = os.path.abspath(args.output)
    phrase = args.phrase
    interval = args.interval

    print("=== Target Keyword Log Generator ===")
    print(f"Output File: {filepath}")
    print(f"Keyword: '{phrase}', Interval: every {interval} lines")

    if args.live:
        print(f"Mode: Live Streaming (delay: {args.delay:.2f}s). Press Ctrl+C to stop.\n")
        stop_event = threading.Event()
        try:
            TargetLogGenerator.run_live(
                filepath=filepath,
                delay=args.delay,
                phrase=phrase,
                interval=interval,
                stop_event=stop_event,
                line_callback=lambda idx, line: print(f"[{idx}] {line}"),
                log_callback=print,
            )
        except KeyboardInterrupt:
            stop_event.set()
            print("\nGeneration stopped.")
    else:
        count = args.count
        print(f"Mode: Batch ({count} lines)\n")
        stop_event = threading.Event()
        try:
            TargetLogGenerator.run_batch(
                filepath=filepath,
                count=count,
                phrase=phrase,
                interval=interval,
                overwrite=not args.append,
                stop_event=stop_event,
                progress_callback=lambda cur, tot: print(
                    f"\rProgress: {cur}/{tot} lines ({(cur / tot) * 100:.1f}%)", end="", flush=True
                ),
                log_callback=lambda msg: print(f"\n{msg}"),
            )
        except KeyboardInterrupt:
            stop_event.set()
            print("\nBatch generation stopped.")


def run_cli_json(args: argparse.Namespace) -> None:
    """Runs JSON log generator in CLI mode."""
    filepath = os.path.abspath(args.output)

    print("=== JSON / JSONL Log Generator ===")
    print(f"Output File: {filepath}")

    if args.live:
        print(f"Mode: Live Streaming (delay: {args.delay:.2f}s). Press Ctrl+C to stop.\n")
        stop_event = threading.Event()
        try:
            JsonLogGenerator.run_live(
                filepath=filepath,
                delay=args.delay,
                stop_event=stop_event,
                line_callback=lambda idx, line: print(f"[{idx}] {line}"),
                log_callback=print,
            )
        except KeyboardInterrupt:
            stop_event.set()
            print("\nJSON streaming stopped.")
    else:
        count = args.count
        print(f"Mode: Batch ({count} records)\n")
        stop_event = threading.Event()
        try:
            JsonLogGenerator.run_batch(
                filepath=filepath,
                count=count,
                overwrite=not args.append,
                indent=args.indent if args.indent and args.indent > 0 else None,
                stop_event=stop_event,
                progress_callback=lambda cur, tot: print(
                    f"\rProgress: {cur}/{tot} records ({(cur / tot) * 100:.1f}%)", end="", flush=True
                ),
                log_callback=lambda msg: print(f"\n{msg}"),
            )
        except KeyboardInterrupt:
            stop_event.set()
            print("\nBatch generation stopped.")


# ============================================================================
# GRAPHICAL USER INTERFACE (PySide6 GUI)
# ============================================================================

try:
    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QFontDatabase, QIcon
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLayout,
        QLineEdit,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSpinBox,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


if GUI_AVAILABLE:

    class TabSignals(QObject):
        """Universal signal hub for background GUI workers."""

        log_msg = Signal(str)
        progress_val = Signal("qint64", "qint64")  # (current, total)
        progress_stats = Signal("qint64", "qint64", float, float)  # (written_bytes, total_bytes, speed_mb, elapsed_s)
        finished = Signal()

    class MultiAppTab(QWidget):
        """Tab 1: Live concurrent multi-application load simulation."""

        def __init__(self, main_window: "GeneratorMainWindow") -> None:
            super().__init__()
            self.main_window = main_window
            self.workers: list[MultiAppWorker] = []
            self.threads: list[threading.Thread] = []
            self.init_ui()

        def init_ui(self) -> None:
            layout = QVBoxLayout(self)

            # Configuration Form
            cfg_group = QGroupBox("Multi-App Simulation Settings")
            form = QFormLayout()

            self.spin_files = QSpinBox()
            self.spin_files.setRange(1, 50)
            self.spin_files.setValue(5)
            form.addRow("Number of applications (APP-N):", self.spin_files)

            self.spin_min_delay = QDoubleSpinBox()
            self.spin_min_delay.setRange(0.001, 10.0)
            self.spin_min_delay.setSingleStep(0.05)
            self.spin_min_delay.setValue(0.1)
            form.addRow("Min delay (s):", self.spin_min_delay)

            self.spin_max_delay = QDoubleSpinBox()
            self.spin_max_delay.setRange(0.001, 20.0)
            self.spin_max_delay.setSingleStep(0.05)
            self.spin_max_delay.setValue(0.5)
            form.addRow("Max delay (s):", self.spin_max_delay)

            dir_layout = QHBoxLayout()
            self.entry_dir = QLineEdit(os.path.abspath("./simulation_logs"))
            self.btn_browse = QPushButton("Browse...")
            self.btn_browse.clicked.connect(self.browse_dir)
            dir_layout.addWidget(self.entry_dir)
            dir_layout.addWidget(self.btn_browse)
            form.addRow("Output directory:", dir_layout)

            cfg_group.setLayout(form)
            layout.addWidget(cfg_group)

            # Control Buttons
            act_layout = QHBoxLayout()
            self.btn_start = QPushButton("START SIMULATION")
            self.btn_start.setStyleSheet(STYLE_BTN_PRIMARY)
            self.btn_start.clicked.connect(self.start_generation)

            self.btn_stop = QPushButton("STOP ALL")
            self.btn_stop.setStyleSheet(STYLE_BTN_STOP)
            self.btn_stop.setEnabled(False)
            self.btn_stop.clicked.connect(self.stop_generation)

            act_layout.addWidget(self.btn_start)
            act_layout.addWidget(self.btn_stop)
            layout.addLayout(act_layout)

            # Instance Real-Time Controls
            instances_group = QGroupBox("Instance Real-Time Control")
            inst_layout = QVBoxLayout()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(140)

            self.instances_container = QWidget()
            self.instances_wrap_layout = QVBoxLayout(self.instances_container)
            self.instances_wrap_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(self.instances_container)

            inst_layout.addWidget(scroll)
            instances_group.setLayout(inst_layout)
            layout.addWidget(instances_group)
            layout.addStretch()

        def browse_dir(self) -> None:
            d = QFileDialog.getExistingDirectory(self, "Select Output Directory")
            if d:
                self.entry_dir.setText(d)

        def clear_instances_ui(self) -> None:
            while self.instances_wrap_layout.count():
                child = self.instances_wrap_layout.takeAt(0)
                if child is not None:
                    widget = child.widget()
                    if widget is not None:
                        widget.deleteLater()
                    sub_layout = child.layout()
                    if sub_layout is not None:
                        self._clear_layout(sub_layout)

        def _clear_layout(self, layout: QLayout) -> None:
            while layout.count():
                child = layout.takeAt(0)
                if child is not None:
                    widget = child.widget()
                    if widget is not None:
                        widget.deleteLater()
                    sub_layout = child.layout()
                    if sub_layout is not None:
                        self._clear_layout(sub_layout)
            layout.deleteLater()

        def build_instances_ui(self) -> None:
            self.clear_instances_ui()
            current_row = QHBoxLayout()
            self.instances_wrap_layout.addLayout(current_row)

            for idx, worker in enumerate(self.workers):
                if idx > 0 and idx % 5 == 0:
                    current_row = QHBoxLayout()
                    self.instances_wrap_layout.addLayout(current_row)

                btn = QPushButton(f"Pause APP-{worker.file_index}")
                btn.setCheckable(True)
                btn.setStyleSheet(STYLE_BTN_INSTANCE)
                btn.clicked.connect(lambda checked, w=worker, b=btn: self.toggle_pause(checked, w, b))
                current_row.addWidget(btn)

            current_row.addStretch()

        def toggle_pause(self, checked: bool, worker: MultiAppWorker, btn: QPushButton) -> None:
            if checked:
                worker.pause()
                btn.setText(f"Resume APP-{worker.file_index}")
                self.main_window.append_log(f">>> APP-{worker.file_index} paused.")
            else:
                worker.resume()
                btn.setText(f"Pause APP-{worker.file_index}")
                self.main_window.append_log(f">>> APP-{worker.file_index} resumed.")

        def start_generation(self) -> None:
            out_dir = self.entry_dir.text()
            if not os.path.exists(out_dir):
                try:
                    os.makedirs(out_dir, exist_ok=True)
                except Exception as e:
                    self.main_window.append_log(f"ERROR: Cannot create directory {out_dir}: {e}")
                    return

            self.btn_start.setEnabled(False)
            self.spin_files.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.main_window.append_log(">>> Starting Multi-App simulation...")

            num_files = self.spin_files.value()
            d_min = self.spin_min_delay.value()
            d_max = self.spin_max_delay.value()

            self.workers = []
            self.threads = []

            for i in range(1, num_files + 1):
                worker = MultiAppWorker(
                    i,
                    out_dir,
                    d_min,
                    d_max,
                    log_callback=self.main_window.append_log,
                )
                t = threading.Thread(target=worker.run, daemon=True)
                self.workers.append(worker)
                self.threads.append(t)
                t.start()

            self.build_instances_ui()

        def stop_generation(self) -> None:
            self.btn_stop.setEnabled(False)
            self.main_window.append_log(">>> Stopping all workers...")
            for w in self.workers:
                w.resume()
                w.stop()

            threading.Thread(target=self._wait_for_stop, daemon=True).start()

        def _wait_for_stop(self) -> None:
            for t in self.threads:
                t.join()
            self.workers.clear()
            self.threads.clear()

            def reset() -> None:
                self.btn_start.setEnabled(True)
                self.spin_files.setEnabled(True)
                self.clear_instances_ui()
                self.main_window.append_log(">>> All Multi-App workers stopped.")

            QTimer.singleShot(0, reset)

    class FastGenTab(QWidget):
        """Tab 2: High-speed large file benchmark generator."""

        def __init__(self, main_window: "GeneratorMainWindow") -> None:
            super().__init__()
            self.main_window = main_window
            self.stop_event = threading.Event()
            self.signals = TabSignals()
            self.signals.log_msg.connect(self.main_window.append_log)
            self.signals.progress_stats.connect(self.update_stats)
            self.signals.finished.connect(self.on_finished)
            self.init_ui()

        def init_ui(self) -> None:
            layout = QVBoxLayout(self)

            group = QGroupBox("Fast File Generation Parameters")
            form = QFormLayout()

            # Output Path
            file_layout = QHBoxLayout()
            self.entry_file = QLineEdit(os.path.abspath("benchmark_fast.log"))
            self.btn_browse = QPushButton("Browse...")
            self.btn_browse.clicked.connect(self.browse_file)
            file_layout.addWidget(self.entry_file)
            file_layout.addWidget(self.btn_browse)
            form.addRow("Output file path:", file_layout)

            # Target Size
            size_layout = QHBoxLayout()
            self.spin_size = QDoubleSpinBox()
            self.spin_size.setRange(0.01, 1000.0)
            self.spin_size.setValue(1.0)
            self.spin_size.setSingleStep(0.5)

            self.combo_unit = QComboBox()
            self.combo_unit.addItems(["GB", "MB"])
            size_layout.addWidget(self.spin_size)
            size_layout.addWidget(self.combo_unit)
            size_layout.addStretch()
            form.addRow("Target size:", size_layout)

            # Buffer Size
            self.spin_chunk = QSpinBox()
            self.spin_chunk.setRange(1_000, 1_000_000)
            self.spin_chunk.setValue(100_000)
            self.spin_chunk.setSingleStep(10_000)
            form.addRow("Buffer chunk size (lines):", self.spin_chunk)

            # Overwrite Mode
            self.chk_overwrite = QCheckBox("Overwrite existing file (uncheck to append)")
            self.chk_overwrite.setChecked(True)
            form.addRow("Write mode:", self.chk_overwrite)

            group.setLayout(form)
            layout.addWidget(group)

            # Progress Bar and Status
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            layout.addWidget(self.progress_bar)

            self.lbl_stats = QLabel("Ready to generate.")
            self.lbl_stats.setStyleSheet("font-weight: bold; color: #2563eb;")
            layout.addWidget(self.lbl_stats)

            # Buttons
            act_layout = QHBoxLayout()
            self.btn_start = QPushButton("START GENERATION")
            self.btn_start.setStyleSheet(STYLE_BTN_PRIMARY)
            self.btn_start.clicked.connect(self.start_fast_gen)

            self.btn_stop = QPushButton("STOP")
            self.btn_stop.setStyleSheet(STYLE_BTN_STOP)
            self.btn_stop.setEnabled(False)
            self.btn_stop.clicked.connect(self.stop_fast_gen)

            act_layout.addWidget(self.btn_start)
            act_layout.addWidget(self.btn_stop)
            layout.addLayout(act_layout)
            layout.addStretch()

        def browse_file(self) -> None:
            f, _ = QFileDialog.getSaveFileName(
                self, "Select Output File", filter="Log Files (*.log *.txt);;All Files (*.*)"
            )
            if f:
                self.entry_file.setText(f)

        def update_stats(self, written: int, total: int, speed: float, elapsed: float) -> None:
            pct = int((written / total) * 100) if total > 0 else 0
            self.progress_bar.setValue(min(100, pct))
            w_mb = written / (1024 * 1024)
            t_mb = total / (1024 * 1024)
            self.lbl_stats.setText(
                f"Written: {w_mb:.1f} / {t_mb:.1f} MB ({pct}%) | Speed: {speed:.1f} MB/s | Elapsed: {elapsed:.1f}s"
            )

        def start_fast_gen(self) -> None:
            filepath = self.entry_file.text().strip()
            if not filepath:
                self.main_window.append_log("ERROR: Please specify output file path.")
                return

            size_val = self.spin_size.value()
            unit = self.combo_unit.currentText()
            multiplier = 1024 * 1024 * 1024 if unit == "GB" else 1024 * 1024
            target_bytes = int(size_val * multiplier)

            self.stop_event.clear()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.progress_bar.setValue(0)
            self.lbl_stats.setText("Generation in progress...")

            def worker() -> None:
                try:
                    FastLogGenerator.generate_to_size(
                        filepath=filepath,
                        target_bytes=target_bytes,
                        chunk_lines=self.spin_chunk.value(),
                        overwrite=self.chk_overwrite.isChecked(),
                        stop_event=self.stop_event,
                        progress_callback=lambda w, t, s, e: self.signals.progress_stats.emit(w, t, s, e),
                        log_callback=lambda msg: self.signals.log_msg.emit(msg),
                    )
                except Exception as e:
                    self.signals.log_msg.emit(f"FAST GENERATION ERROR: {e}")
                finally:
                    self.signals.finished.emit()

            threading.Thread(target=worker, daemon=True).start()

        def stop_fast_gen(self) -> None:
            self.stop_event.set()
            self.btn_stop.setEnabled(False)

        def on_finished(self) -> None:
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)

    class TargetGenTab(QWidget):
        """Tab 3: Target keyword log generator for search and filter testing."""

        def __init__(self, main_window: "GeneratorMainWindow") -> None:
            super().__init__()
            self.main_window = main_window
            self.stop_event = threading.Event()
            self.signals = TabSignals()
            self.signals.log_msg.connect(self.main_window.append_log)
            self.signals.progress_val.connect(self.update_progress)
            self.signals.finished.connect(self.on_finished)
            self.init_ui()

        def init_ui(self) -> None:
            layout = QVBoxLayout(self)

            group = QGroupBox("Target [TARGET] Generator Settings")
            form = QFormLayout()

            # Output File
            file_layout = QHBoxLayout()
            self.entry_file = QLineEdit(os.path.abspath("test_target_logs.txt"))
            self.btn_browse = QPushButton("Browse...")
            self.btn_browse.clicked.connect(self.browse_file)
            file_layout.addWidget(self.entry_file)
            file_layout.addWidget(self.btn_browse)
            form.addRow("Output file path:", file_layout)

            # Target Phrase
            self.entry_phrase = QLineEdit("[TARGET]")
            form.addRow("Target keyword/phrase:", self.entry_phrase)

            # Interval
            self.spin_interval = QSpinBox()
            self.spin_interval.setRange(1, 1000)
            self.spin_interval.setValue(15)
            form.addRow("Inject target every N lines:", self.spin_interval)

            # Operation Mode
            self.rb_live = QRadioButton("Continuous mode (Live stream with delay)")
            self.rb_batch = QRadioButton("Batch mode (Generate exact line count)")
            self.rb_live.setChecked(True)
            self.rb_live.toggled.connect(self.toggle_mode)

            mode_layout = QVBoxLayout()
            mode_layout.addWidget(self.rb_live)
            mode_layout.addWidget(self.rb_batch)
            form.addRow("Operation mode:", mode_layout)

            # Live Options
            self.spin_delay = QDoubleSpinBox()
            self.spin_delay.setRange(0.01, 10.0)
            self.spin_delay.setValue(0.3)
            self.spin_delay.setSingleStep(0.05)
            form.addRow("Delay per line (s):", self.spin_delay)

            # Batch Options
            self.spin_count = QSpinBox()
            self.spin_count.setRange(1, 10_000_000)
            self.spin_count.setValue(5000)
            self.spin_count.setSingleStep(1000)
            self.spin_count.setEnabled(False)
            form.addRow("Line count (batch mode):", self.spin_count)

            group.setLayout(form)
            layout.addWidget(group)

            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            layout.addWidget(self.progress_bar)

            self.lbl_status = QLabel("Ready.")
            self.lbl_status.setStyleSheet("font-weight: bold; color: #2563eb;")
            layout.addWidget(self.lbl_status)

            # Buttons
            act_layout = QHBoxLayout()
            self.btn_start = QPushButton("START GENERATION")
            self.btn_start.setStyleSheet(STYLE_BTN_PRIMARY)
            self.btn_start.clicked.connect(self.start_gen)

            self.btn_stop = QPushButton("STOP")
            self.btn_stop.setStyleSheet(STYLE_BTN_STOP)
            self.btn_stop.setEnabled(False)
            self.btn_stop.clicked.connect(self.stop_gen)

            act_layout.addWidget(self.btn_start)
            act_layout.addWidget(self.btn_stop)
            layout.addLayout(act_layout)
            layout.addStretch()

        def browse_file(self) -> None:
            f, _ = QFileDialog.getSaveFileName(
                self, "Select Output File", filter="Text Files (*.txt *.log);;All Files (*.*)"
            )
            if f:
                self.entry_file.setText(f)

        def toggle_mode(self) -> None:
            is_live = self.rb_live.isChecked()
            self.spin_delay.setEnabled(is_live)
            self.spin_count.setEnabled(not is_live)

        def update_progress(self, current: int, total: int) -> None:
            if total > 0:
                pct = int((current / total) * 100)
                self.progress_bar.setValue(pct)
                self.lbl_status.setText(f"Progress: {current} / {total} lines ({pct}%)")
            else:
                self.lbl_status.setText(f"Generated lines: {current}")

        def start_gen(self) -> None:
            filepath = self.entry_file.text().strip()
            if not filepath:
                self.main_window.append_log("ERROR: Please specify output file path.")
                return

            phrase = self.entry_phrase.text()
            interval = self.spin_interval.value()
            self.stop_event.clear()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)

            if self.rb_live.isChecked():
                delay = self.spin_delay.value()
                self.progress_bar.setRange(0, 0)
                self.lbl_status.setText("Live streaming active...")

                def worker() -> None:
                    try:
                        TargetLogGenerator.run_live(
                            filepath=filepath,
                            delay=delay,
                            phrase=phrase,
                            interval=interval,
                            stop_event=self.stop_event,
                            line_callback=lambda cur, line: self.signals.progress_val.emit(cur, 0),
                            log_callback=lambda msg: self.signals.log_msg.emit(msg),
                        )
                    except Exception as e:
                        self.signals.log_msg.emit(f"TARGET LIVE ERROR: {e}")
                    finally:
                        self.signals.finished.emit()

            else:
                count = self.spin_count.value()
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)
                self.lbl_status.setText("Batch generation in progress...")

                def worker() -> None:
                    try:
                        TargetLogGenerator.run_batch(
                            filepath=filepath,
                            count=count,
                            phrase=phrase,
                            interval=interval,
                            overwrite=False,
                            stop_event=self.stop_event,
                            progress_callback=lambda cur, tot: self.signals.progress_val.emit(cur, tot),
                            log_callback=lambda msg: self.signals.log_msg.emit(msg),
                        )
                    except Exception as e:
                        self.signals.log_msg.emit(f"TARGET BATCH ERROR: {e}")
                    finally:
                        self.signals.finished.emit()

            threading.Thread(target=worker, daemon=True).start()

        def stop_gen(self) -> None:
            self.stop_event.set()
            self.btn_stop.setEnabled(False)

        def on_finished(self) -> None:
            self.progress_bar.setRange(0, 100)
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)

    class JsonGenTab(QWidget):
        """Tab 4: Structured JSON / JSON Lines generator."""

        def __init__(self, main_window: "GeneratorMainWindow") -> None:
            super().__init__()
            self.main_window = main_window
            self.stop_event = threading.Event()
            self.signals = TabSignals()
            self.signals.log_msg.connect(self.main_window.append_log)
            self.signals.progress_val.connect(self.update_progress)
            self.signals.finished.connect(self.on_finished)
            self.init_ui()

        def init_ui(self) -> None:
            layout = QVBoxLayout(self)

            group = QGroupBox("JSON / JSON Lines Generator Settings")
            form = QFormLayout()

            # Output File
            file_layout = QHBoxLayout()
            self.entry_file = QLineEdit(os.path.abspath("sample_json_logs.jsonl"))
            self.btn_browse = QPushButton("Browse...")
            self.btn_browse.clicked.connect(self.browse_file)
            file_layout.addWidget(self.entry_file)
            file_layout.addWidget(self.btn_browse)
            form.addRow("Output file path:", file_layout)

            # Mode
            self.rb_batch = QRadioButton("Batch mode (Generate exact record count)")
            self.rb_live = QRadioButton("Continuous mode (Live stream JSON)")
            self.rb_batch.setChecked(True)
            self.rb_batch.toggled.connect(self.toggle_mode)

            mode_layout = QVBoxLayout()
            mode_layout.addWidget(self.rb_batch)
            mode_layout.addWidget(self.rb_live)
            form.addRow("Generation mode:", mode_layout)

            # Batch Count
            self.spin_count = QSpinBox()
            self.spin_count.setRange(1, 5_000_000)
            self.spin_count.setValue(2000)
            self.spin_count.setSingleStep(500)
            form.addRow("Record count (batch):", self.spin_count)

            # Live Delay
            self.spin_delay = QDoubleSpinBox()
            self.spin_delay.setRange(0.01, 10.0)
            self.spin_delay.setValue(0.1)
            self.spin_delay.setSingleStep(0.05)
            self.spin_delay.setEnabled(False)
            form.addRow("Stream delay (s):", self.spin_delay)

            # Overwrite
            self.chk_overwrite = QCheckBox("Overwrite file (batch mode)")
            self.chk_overwrite.setChecked(True)
            form.addRow("Write options:", self.chk_overwrite)

            group.setLayout(form)
            layout.addWidget(group)

            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            layout.addWidget(self.progress_bar)

            self.lbl_status = QLabel("Ready.")
            self.lbl_status.setStyleSheet("font-weight: bold; color: #2563eb;")
            layout.addWidget(self.lbl_status)

            # Buttons
            act_layout = QHBoxLayout()
            self.btn_start = QPushButton("START GENERATION")
            self.btn_start.setStyleSheet(STYLE_BTN_PRIMARY)
            self.btn_start.clicked.connect(self.start_gen)

            self.btn_stop = QPushButton("STOP")
            self.btn_stop.setStyleSheet(STYLE_BTN_STOP)
            self.btn_stop.setEnabled(False)
            self.btn_stop.clicked.connect(self.stop_gen)

            act_layout.addWidget(self.btn_start)
            act_layout.addWidget(self.btn_stop)
            layout.addLayout(act_layout)
            layout.addStretch()

        def browse_file(self) -> None:
            f, _ = QFileDialog.getSaveFileName(
                self, "Select Output File", filter="JSON Files (*.jsonl *.json *.log);;All Files (*.*)"
            )
            if f:
                self.entry_file.setText(f)

        def toggle_mode(self) -> None:
            is_batch = self.rb_batch.isChecked()
            self.spin_count.setEnabled(is_batch)
            self.chk_overwrite.setEnabled(is_batch)
            self.spin_delay.setEnabled(not is_batch)

        def update_progress(self, current: int, total: int) -> None:
            if total > 0:
                pct = int((current / total) * 100)
                self.progress_bar.setValue(pct)
                self.lbl_status.setText(f"Progress: {current} / {total} records ({pct}%)")
            else:
                self.lbl_status.setText(f"Generated JSON records: {current}")

        def start_gen(self) -> None:
            filepath = self.entry_file.text().strip()
            if not filepath:
                self.main_window.append_log("ERROR: Please specify output file path.")
                return

            self.stop_event.clear()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)

            if self.rb_batch.isChecked():
                count = self.spin_count.value()
                overwrite = self.chk_overwrite.isChecked()
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)
                self.lbl_status.setText("Batch generation in progress...")

                def worker() -> None:
                    try:
                        JsonLogGenerator.run_batch(
                            filepath=filepath,
                            count=count,
                            overwrite=overwrite,
                            stop_event=self.stop_event,
                            progress_callback=lambda cur, tot: self.signals.progress_val.emit(cur, tot),
                            log_callback=lambda msg: self.signals.log_msg.emit(msg),
                        )
                    except Exception as e:
                        self.signals.log_msg.emit(f"JSON BATCH ERROR: {e}")
                    finally:
                        self.signals.finished.emit()

            else:
                delay = self.spin_delay.value()
                self.progress_bar.setRange(0, 0)
                self.lbl_status.setText("Live JSON streaming active...")

                def worker() -> None:
                    try:
                        JsonLogGenerator.run_live(
                            filepath=filepath,
                            delay=delay,
                            stop_event=self.stop_event,
                            line_callback=lambda cur, line: self.signals.progress_val.emit(cur, 0),
                            log_callback=lambda msg: self.signals.log_msg.emit(msg),
                        )
                    except Exception as e:
                        self.signals.log_msg.emit(f"JSON LIVE ERROR: {e}")
                    finally:
                        self.signals.finished.emit()

            threading.Thread(target=worker, daemon=True).start()

        def stop_gen(self) -> None:
            self.stop_event.set()
            self.btn_stop.setEnabled(False)

        def on_finished(self) -> None:
            self.progress_bar.setRange(0, 100)
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)

    class GeneratorMainWindow(QWidget):
        """Main Window for the Unified Log Generator Application."""

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Log Generator Tool")
            self.resize(780, 720)

            self._auto_scroll: bool = True
            self._programmatic_scroll: bool = False

            # Ustawienie ikony okna aplikacji jeśli dostępna
            base_res_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            app_icon_path = base_res_dir / "assets" / "icon.png"
            if app_icon_path.exists():
                self.setWindowIcon(QIcon(str(app_icon_path)))

            self.init_ui()

        def init_ui(self) -> None:
            main_layout = QVBoxLayout(self)

            # Application Header
            title_lbl = QLabel("🛠️ Developer Utility: Log Generator")
            title_lbl.setStyleSheet("font-size: 15pt; font-weight: bold; padding: 4px;")
            main_layout.addWidget(title_lbl)

            # Tabs
            self.tabs = QTabWidget()
            self.tab_multi_app = MultiAppTab(self)
            self.tab_fast_gen = FastGenTab(self)
            self.tab_target_gen = TargetGenTab(self)
            self.tab_json_gen = JsonGenTab(self)

            self.tabs.addTab(self.tab_multi_app, "🔴 Multi-App Simulator (Live)")
            self.tabs.addTab(self.tab_fast_gen, "⚡ Fast File Gen (GB/MB)")
            self.tabs.addTab(self.tab_target_gen, "🎯 Target Logs [TARGET]")
            self.tabs.addTab(self.tab_json_gen, "📦 JSON / JSONL Logs")

            main_layout.addWidget(self.tabs)

            # Output Console
            console_header = QHBoxLayout()
            console_header.addWidget(QLabel("Event and Output Log:"))
            console_header.addStretch()

            self.btn_autoscroll = QPushButton("Auto-scroll")
            self.btn_autoscroll.setCheckable(True)
            self.btn_autoscroll.setChecked(True)
            self.btn_autoscroll.setStyleSheet(STYLE_BTN_AUTOSCROLL)
            self.btn_autoscroll.setToolTip(
                "Automatyczne przewijanie konsoli do najnowszych wpisów (wyłącza się przy ręcznym przewinięciu w górę)"
            )
            self.btn_autoscroll.toggled.connect(self._on_autoscroll_btn_toggled)
            console_header.addWidget(self.btn_autoscroll)

            btn_clear_log = QPushButton("Clear Log")
            btn_clear_log.clicked.connect(self.clear_console)
            console_header.addWidget(btn_clear_log)
            main_layout.addLayout(console_header)

            self.console = QTextEdit()
            self.console.setReadOnly(True)
            self.console.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            self.console.setStyleSheet("background-color: #1e1e1e; color: #a9b7c6;")
            self.console.setMaximumHeight(160)

            v_scroll = self.console.verticalScrollBar()
            v_scroll.valueChanged.connect(self._on_scrollbar_value_changed)
            v_scroll.rangeChanged.connect(self._on_scrollbar_range_changed)

            main_layout.addWidget(self.console)

        def _on_autoscroll_btn_toggled(self, checked: bool) -> None:
            self._auto_scroll = checked
            if checked:
                self._scroll_to_bottom()

        def _on_scrollbar_value_changed(self, value: int) -> None:
            if self._programmatic_scroll:
                return
            v_scroll = self.console.verticalScrollBar()
            is_at_bottom = value >= v_scroll.maximum() - 4
            if is_at_bottom != self._auto_scroll:
                self._auto_scroll = is_at_bottom
                self.btn_autoscroll.blockSignals(True)
                self.btn_autoscroll.setChecked(is_at_bottom)
                self.btn_autoscroll.blockSignals(False)

        def _on_scrollbar_range_changed(self, min_val: int, max_val: int) -> None:
            if self._auto_scroll:
                self._scroll_to_bottom()

        def _scroll_to_bottom(self) -> None:
            v_scroll = self.console.verticalScrollBar()
            self._programmatic_scroll = True
            try:
                v_scroll.setValue(v_scroll.maximum())
            finally:
                self._programmatic_scroll = False

        def append_log(self, text: str) -> None:
            now = datetime.now().strftime("%H:%M:%S")
            self._programmatic_scroll = True
            try:
                self.console.append(f"[{now}] {text}")
                if self._auto_scroll:
                    v_scroll = self.console.verticalScrollBar()
                    v_scroll.setValue(v_scroll.maximum())
            finally:
                self._programmatic_scroll = False

        def clear_console(self) -> None:
            self.console.clear()


# ============================================================================
# ENTRY POINT (MAIN)
# ============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified Log Generator Tool for Developers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage Examples:
  python generate_logs.py                           # Starts Graphical User Interface (GUI)
  python generate_logs.py fast --output test.log --size-gb 2
  python generate_logs.py target --output filter.log --phrase "[TARGET]" --count 5000
  python generate_logs.py json --output sample.jsonl --count 1000
  python generate_logs.py live --apps 3 --output-dir ./sim_logs
        """,
    )

    parser.add_argument("--gui", action="store_true", help="Force launch in Graphical User Interface (GUI) mode")

    subparsers = parser.add_subparsers(dest="subcommand", help="Command line execution mode")

    # Subparser: live (multi-app)
    parser_live = subparsers.add_parser("live", help="Concurrent multi-application live simulator")
    parser_live.add_argument("--apps", type=int, default=5, help="Number of applications (default: 5)")
    parser_live.add_argument("--min-delay", type=float, default=0.1, help="Minimum delay between lines in seconds")
    parser_live.add_argument("--max-delay", type=float, default=0.5, help="Maximum delay between lines in seconds")
    parser_live.add_argument(
        "--output-dir", type=str, default="./simulation_logs", help="Target directory for log files"
    )

    # Subparser: fast
    parser_fast = subparsers.add_parser("fast", help="High-throughput large file benchmark generator")
    parser_fast.add_argument("--output", type=str, default="simulation.log", help="Output file path")
    parser_fast.add_argument("--size-gb", type=float, default=None, help="Target size in GB")
    parser_fast.add_argument("--size-mb", type=float, default=None, help="Target size in MB")
    parser_fast.add_argument("--chunk-lines", type=int, default=100_000, help="Buffer chunk size (number of lines)")
    parser_fast.add_argument("--append", action="store_true", help="Append to file instead of overwriting")

    # Subparser: target
    parser_target = subparsers.add_parser("target", help="Test log generator with target keyword injection")
    parser_target.add_argument("--output", type=str, default="test_logs.txt", help="Output file path")
    parser_target.add_argument("--phrase", type=str, default="[TARGET]", help="Keyword/phrase to inject")
    parser_target.add_argument("--interval", type=int, default=15, help="Injection interval (every N lines)")
    parser_target.add_argument("--live", action="store_true", help="Continuous live streaming mode")
    parser_target.add_argument("--delay", type=float, default=0.3, help="Delay in seconds for live mode")
    parser_target.add_argument("--count", type=int, default=1000, help="Line count for batch mode")
    parser_target.add_argument("--append", action="store_true", help="Append to file instead of overwriting")

    # Subparser: json
    parser_json = subparsers.add_parser("json", help="Structured JSON / JSON Lines log generator")
    parser_json.add_argument("--output", type=str, default="sample_json_logs.jsonl", help="Output file path")
    parser_json.add_argument("--count", type=int, default=1000, help="Record count for batch mode")
    parser_json.add_argument("--live", action="store_true", help="Continuous live streaming mode")
    parser_json.add_argument("--delay", type=float, default=0.1, help="Delay in seconds for live mode")
    parser_json.add_argument("--indent", type=int, default=None, help="JSON indentation (default 1 line per record)")
    parser_json.add_argument("--append", action="store_true", help="Append to file instead of overwriting")

    return parser


def main() -> None:
    multiprocessing.freeze_support()
    parser = build_parser()
    args = parser.parse_args()

    # CLI dispatch if subcommand provided and GUI flag is not explicitly set
    if args.subcommand and not args.gui:
        if args.subcommand == "live":
            run_cli_live(args)
        elif args.subcommand == "fast":
            run_cli_fast(args)
        elif args.subcommand == "target":
            run_cli_target(args)
        elif args.subcommand == "json":
            run_cli_json(args)
        return

    # Start Graphical User Interface (GUI)
    if not GUI_AVAILABLE:
        print("ERROR: PySide6 is not installed in this environment.")
        print("Install it with: pip install PySide6")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = GeneratorMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
