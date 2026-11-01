"""
Log Generator Tool
Developer utility for generating various types of logs:
1. Multi-App Live Simulator (concurrent application logs with transactions, stack traces, and optional XML/JSON payloads)
2. Fast Large File Generator (high-throughput binary buffered generation up to GBs)
3. Target Log Generator ([TARGET] keyword injection for filter and search testing)
4. Structured JSON / XML / Hybrid Challenge Generator (rich microservice payloads, multi-structure lines, SOAP/XML namespaces, long lines, and parser stress traps)

Supports both PySide6 Graphical User Interface (GUI) and Command Line Interface (CLI).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
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

# JSON / XML generator dataset
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
    "Invalid payload: missing required field 'cart_id' (błąd walidacji żądania)",
]

POLISH_DESCRIPTIONS = [
    "Płatność kartą zakończona pomyślnie — potwierdzenie wysłane na adres użytkownika",
    "Weryfikacja dwuetapowa (2FA): wygenerowano kod autoryzacyjny dla sesji mobilnej",
    "Przekroczono czas oczekiwania na odpowiedź z systemu bilingowego (węzeł Kraków-DC2)",
    "Zaktualizowano koszyk zakupowy: przeliczono rabat lojalnościowy oraz stawkę VAT 23%",
    "Synchronizacja stanów magazynowych z hurtownią główną (Wrocław) zakończona ostrzeżeniem",
]

XML_OPERATIONS = [
    ("AuthorizePayment", "urn:payments:iso20022:v2"),
    ("ValidateIdentityToken", "urn:security:saml2:assertion"),
    ("SubmitOrderManifest", "http://schemas.enterprise.local/erp/orders/v3"),
    ("SyncInventoryState", "http://schemas.enterprise.local/warehouse/sync"),
    ("AuditSecurityEvent", "urn:sec:siem:audit:v1"),
]

# Fałszywe tropy przed właściwym JSON/XML (testują odporność extract_json i extract_xml na błędne znaczniki)
DECOY_PREFIXES = [
    "[ctx={env:prod, shard:3}][vector=[0,1,2]] [rule: latency < 250ms && retries <= 3] ",
    "[filter: price < 100.0 || discount > 15%] <unclosed_hint attr='test'> [meta={invalid_json: true}] ",
    "[worker#4 {pool=active}][array_slice=[10..20]] <temp_marker id=99> ",
    "[sql: SELECT * FROM orders WHERE total < 500 AND status IN ('NEW', 'PENDING')] {not:a:json} ",
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
        include_payloads: bool = True,
        log_callback: Callable[[str], None] | None = None,
        finished_callback: Callable[[], None] | None = None,
    ) -> None:
        self.file_index = file_index
        self.output_dir = output_dir
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.include_payloads = include_payloads
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
        file_path = str(Path(self.output_dir) / f"app_{self.file_index}.log")
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

                    if self.include_payloads and counter % 7 == 0:
                        # Linia hybrydowa z wieloma strukturami JSON + XML dla testów FormatDialog
                        hybrid_line = JsonLogGenerator.generate_formatted_line(
                            counter,
                            fmt_mode="HYBRID",
                            multi_structure=True,
                            include_decoys=(counter % 14 == 0),
                            long_line=False,
                        )
                        f.write(f"[{now}] [{app_name}] {hybrid_line}\n")
                    elif self.include_payloads and counter % 11 == 0:
                        xml_payload = JsonLogGenerator.generate_xml_string(counter, include_declaration=True)
                        f.write(f"[{now}] [{app_name}] [INFO] SOAP gateway exchange: {xml_payload}\n")
                    elif counter % 12 == 0:
                        tx_id = f"TX-{hex(random.getrandbits(32))[2:].upper()}"
                        req_json = json.dumps(
                            {"tx_id": tx_id, "amount": 249.99, "currency": "PLN", "desc": "Opłata subskrypcyjna"},
                            ensure_ascii=False,
                        )
                        resp_json = json.dumps(
                            {"tx_id": tx_id, "status": "REJECTED", "code": 403, "reason": "Insufficient funds"},
                            ensure_ascii=False,
                        )
                        lines = [
                            f"[{now}] [{app_name}] [INFO] Transaction {tx_id} processing started\n",
                            f"[{now}] [{app_name}] [DEBUG] Auth payload Request: {req_json} -> Response: {resp_json}\n",
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
        except BaseException as err:
            self._emit_log(f"[{app_name}] Finished or interrupted: {err}")
        finally:
            self._emit_log(f"[{app_name}] Session terminated.")
            if self.finished_callback:
                self.finished_callback()


class FastLogGenerator:
    """High-throughput binary log generator using pre-encoded byte buffers and buffered I/O."""

    _SUFFIX_TEMPLATES: list[bytes] = []
    _POOL_SIZE: int = 10_000
    _NUM_POOLS: int = 8
    _POOLS: list[list[bytes]] = []
    _pool_counter: int = 0

    @classmethod
    def _init_pools(cls) -> None:
        """Initializes pre-encoded line suffixes and randomized reference pools."""
        if not cls._POOLS:
            suffixes = [f" [{lvl}] {msg}\n".encode() for lvl in FAST_LOG_LEVELS for msg in FAST_LOG_MESSAGES]
            cls._SUFFIX_TEMPLATES = suffixes
            cls._POOLS = [[random.choice(suffixes) for _ in range(cls._POOL_SIZE)] for _ in range(cls._NUM_POOLS)]

    @classmethod
    def generate_chunk(cls, lines_count: int, timestamp: str | None = None) -> bytes:
        """
        Generates a byte buffer containing formatted log lines at gigabyte-per-second throughput.
        Supports custom timestamp or defaults to current timestamp.
        """
        if lines_count <= 0:
            return b""
        if not cls._POOLS:
            cls._init_pools()

        ts_str = timestamp if timestamp is not None else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{ts_str}]".encode("ascii")

        pool = cls._POOLS[cls._pool_counter % cls._NUM_POOLS]
        cls._pool_counter += 1

        if lines_count <= cls._POOL_SIZE:
            return b"".join(prefix + s for s in pool[:lines_count])

        block = b"".join(prefix + s for s in pool)
        reps = lines_count // cls._POOL_SIZE
        rem = lines_count % cls._POOL_SIZE
        if rem == 0:
            return block * reps
        return (block * reps) + b"".join(prefix + s for s in pool[:rem])

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
        file_p = Path(filepath)
        written_bytes = 0 if overwrite or not file_p.exists() else file_p.stat().st_size

        if log_callback:
            target_mb = target_bytes / (1024 * 1024)
            log_callback(f"Fast write started to '{filepath}'. Target: {target_mb:.2f} MB.")

        cur_dt = datetime.now()
        last_progress_time = 0.0

        try:
            with open(filepath, mode, buffering=8 * 1024 * 1024) as f:
                while written_bytes < target_bytes:
                    if stop_event and stop_event.is_set():
                        if log_callback:
                            log_callback("Generation cancelled by user.")
                        break

                    chunk = cls.generate_chunk(chunk_lines, timestamp=cur_dt.strftime("%Y-%m-%d %H:%M:%S"))
                    if written_bytes + len(chunk) > target_bytes:
                        chunk = chunk[: target_bytes - written_bytes]

                    f.write(chunk)
                    written_bytes += len(chunk)
                    cur_dt += timedelta(seconds=1)

                    now = time.time()
                    elapsed = now - start_time
                    speed_mb = (written_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0

                    if progress_callback and ((now - last_progress_time >= 0.05) or (written_bytes >= target_bytes)):
                        progress_callback(written_bytes, target_bytes, speed_mb, elapsed)
                        last_progress_time = now

            elapsed = time.time() - start_time
            if log_callback:
                log_callback(f"Done! Written {written_bytes / (1024**2):.2f} MB in {elapsed:.2f} s.")
            return written_bytes, elapsed
        except Exception as err:
            if log_callback:
                log_callback(f"Fast write error: {err}")
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
    """
    Generator logów strukturalnych JSON, XML oraz hybrydowych (Challenge Mode).
    Tworzy realistyczne oraz trudne przypadki testowe dla parserów, formaterów (FormatDialog),
    kolorowania składni (StructuredCodeHighlighter) i wyszukiwarki podciągów.
    """

    @staticmethod
    def _format_timestamp(i: int, base_time: datetime | None = None) -> str:
        if base_time is None:
            now_dt = datetime.now()
            return now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        dt = base_time + timedelta(milliseconds=i * 125 + random.randint(0, 50))
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    @staticmethod
    def generate_entry(i: int, base_time: datetime | None = None, long_payload: bool = False) -> dict:
        """Generuje pojedynczy słownik rekordu JSON (bogaty w typy, zagnieżdżenia i polskie znaki)."""
        timestamp = JsonLogGenerator._format_timestamp(i, base_time)
        level = random.choices(JSON_LEVELS, weights=JSON_LEVEL_WEIGHTS)[0]
        service = random.choice(JSON_SERVICES)
        trace_id = uuid.uuid4().hex

        log: dict[str, object] = {
            "id": i + 1,
            "timestamp": timestamp,
            "level": level,
            "service": service,
            "trace_id": trace_id,
            "span_id": uuid.uuid4().hex[:16],
            "description_pl": random.choice(POLISH_DESCRIPTIONS),
        }

        if level in ["ERROR", "CRITICAL"]:
            err_msg = random.choice(JSON_ERROR_MESSAGES)
            log["message"] = err_msg
            log["error"] = {
                "code": f"ERR_{random.randint(1000, 9999)}",
                "retryable": random.choice([True, False]),
                "diagnostics": None,
                "threshold_exceeded": 99.95,
                "stack_trace": [
                    f"File '/app/services/{service}.py', line {random.randint(20, 200)}, in execute",
                    f"File '/app/lib/db.py', line {random.randint(50, 500)}, in query",
                    f"RuntimeError: {err_msg}",
                ],
            }
        elif random.random() > 0.35:
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
                "cached": status in (200, 204),
                "upstream_node": None if status < 500 else "gw-fallback-02.local",
                "client_ip": random.choice(JSON_IPS),
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "headers": {
                    "X-Request-Id": trace_id[:12],
                    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
                    "X-Forwarded-Proto": "https",
                },
            }
            if status >= 400:
                log["user_id"] = random.choice(JSON_USERS)
        else:
            log["message"] = f"Processed event payload in {service}"
            log["context"] = {
                "user_id": random.choice(JSON_USERS),
                "environment": random.choice(["production", "staging"]),
                "region": random.choice(["eu-central-1 (Warszawa)", "eu-west-1 (Dublin)"]),
                "memory_usage_mb": round(random.uniform(120.0, 512.0), 1),
                "cpu_load_normalized": round(random.uniform(0.01, 0.99), 4),
                "active_threads": random.randint(4, 32),
                "feature_flags": {"new_checkout": True, "legacy_xml_bridge": False, "beta_audit": None},
            }

        if long_payload:
            # Bardzo długi ładunek (> 14 000 znaków) testujący ucięcie [truncated] w głównym widoku
            # oraz pobieranie pełnej linii z dysku w FormatDialog i przewijanie poziome do końca linii
            items = [
                {
                    "seq": idx,
                    "sku": f"SKU-PL-{1000 + idx}",
                    "name": f"Moduł przemysłowy dużej mocy #{idx} (seria Żółć-Źdźbło)",
                    "qty": (idx % 10) + 1,
                    "unit_price": round(19.99 + idx * 1.5, 2),
                    "verified": idx % 2 == 0,
                    "checksum": uuid.uuid4().hex,
                }
                for idx in range(95)
            ]
            log["bulk_audit_items"] = items
            log["tail_search_marker"] = f"END_OF_LONG_LINE_MARKER_{i + 1}"

        return log

    @staticmethod
    def generate_response_entry(i: int, trace_id: str) -> dict:
        """Generuje drugi obiekt JSON (Response / Metrics) do linii wielostrukturowych."""
        status_ok = i % 5 != 0
        return {
            "response_for_seq": i + 1,
            "trace_id": trace_id,
            "status": "CONFIRMED" if status_ok else "REJECTED_BY_POLICY",
            "http_code": 200 if status_ok else 422,
            "execution_metrics": {
                "db_queries": random.randint(1, 14),
                "db_time_ms": round(random.uniform(1.1, 120.5), 2),
                "cache_hit": status_ok,
                "warnings": [] if status_ok else ["Przekroczony limit autoryzacyjny transakcji dziennych"],
            },
        }

    @staticmethod
    def generate_xml_string(
        i: int,
        base_time: datetime | None = None,
        include_declaration: bool = True,
        long_payload: bool = False,
        is_response: bool = False,
    ) -> str:
        """
        Generuje złożony, jedno-wierszowy dokument XML zawierający przestrzenie nazw (SOAP/Enterprise),
        atrybuty, komentarze <!-- ... -->, sekcje CDATA oraz polskie znaki diakrytyczne.
        """
        timestamp = JsonLogGenerator._format_timestamp(i, base_time)
        op_name, op_ns = XML_OPERATIONS[i % len(XML_OPERATIONS)]
        service = JSON_SERVICES[i % len(JSON_SERVICES)]
        tx_id = f"TX-{i + 1000:05d}-{uuid.uuid4().hex[:6].upper()}"
        desc_pl = POLISH_DESCRIPTIONS[i % len(POLISH_DESCRIPTIONS)]
        decl = '<?xml version="1.0" encoding="UTF-8"?>' if include_declaration else ""

        if is_response:
            status_code = "200_OK" if i % 4 != 0 else "500_FAULT"
            return (
                f"{decl}"
                f'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
                f'xmlns:res="{op_ns}" correlationId="{tx_id}">'
                f"<soap:Header>"
                f'<res:ServerInfo node="waw-cluster-0{(i % 3) + 1}" processedAt="{timestamp}" cached="false"/>'
                f"</soap:Header>"
                f"<soap:Body>"
                f"<!-- Wynik przetwarzania transakcji {tx_id} ({service}) -->"
                f'<res:{op_name}Response status="{status_code}" latencyMs="{round(random.uniform(4.2, 310.0), 2)}">'
                f"<res:Message>{desc_pl}</res:Message>"
                f'<res:AuditTrail verified="true" level="STRICT">'
                f"<![CDATA[RawBackendAck: status={status_code} &amp; signature=SHA256:{uuid.uuid4().hex[:16]} <ok/>]]>"
                f"</res:AuditTrail>"
                f"</res:{op_name}Response>"
                f"</soap:Body>"
                f"</soap:Envelope>"
            )

        extra_items = ""
        if long_payload:
            chunks = [
                f'<ns:Item seq="{idx}" sku="SKU-{idx:04d}" active="true" '
                f'warehouse="Magazyn-Łódź-{idx % 5}">Element kontrolny nr {idx} — weryfikacja spójności</ns:Item>'
                for idx in range(110)
            ]
            extra_items = (
                f'<ns:BulkItems count="{len(chunks)}">'
                + "".join(chunks)
                + f'<ns:TailMarker marker="END_OF_LONG_XML_{i + 1}"/></ns:BulkItems>'
            )

        return (
            f"{decl}"
            f'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
            f'xmlns:ns="{op_ns}" xmlns:sec="urn:enterprise:security:v1">'
            f"<soap:Header>"
            f"<!-- Nagłówek autoryzacyjny i routing dla usługi {service} -->"
            f'<sec:AuthToken userId="{random.choice(JSON_USERS)}" role="OPERATOR_PL" '
            f'expires="{timestamp}" mfaVerified="true"/>'
            f'<ns:Routing traceId="{tx_id}" sourceIp="{random.choice(JSON_IPS)}" priority="{(i % 5) + 1}"/>'
            f"</soap:Header>"
            f"<soap:Body>"
            f'<ns:{op_name}Request id="req_{i + 1}" retryCount="{i % 3}" async="false">'
            f"<ns:Description>{desc_pl}</ns:Description>"
            f'<ns:Parameters currency="PLN" taxRate="0.23" grossAmount="{round(random.uniform(50.0, 9500.0), 2)}">'
            f"<ns:QueryFilter><![CDATA[SELECT * FROM ledger WHERE amount > 100.00 AND status <> 'CANCELLED' & valid=1]]></ns:QueryFilter>"
            f"</ns:Parameters>"
            f"{extra_items}"
            f"</ns:{op_name}Request>"
            f"</soap:Body>"
            f"</soap:Envelope>"
        )

    @classmethod
    def generate_formatted_line(
        cls,
        i: int,
        base_time: datetime | None = None,
        fmt_mode: str = "HYBRID",
        indent: int | None = None,
        multi_structure: bool = True,
        include_decoys: bool = True,
        long_line: bool = False,
    ) -> str:
        """
        Generuje pojedynczą linię logu w wybranym trybie:
        - 'JSON': czysty JSON / JSONL lub linia logu z 1-2 obiektami JSON
        - 'XML': linia logu z 1-2 dokumentami XML (SOAP/namespaces/CDATA/komentarze)
        - 'HYBRID': mieszanka linii JSON, XML oraz linii łączących Request (JSON/XML) + Response (XML/JSON) w jednej linii
        """
        mode_upper = fmt_mode.upper()
        timestamp = cls._format_timestamp(i, base_time)
        service = JSON_SERVICES[i % len(JSON_SERVICES)]
        level = JSON_LEVELS[i % len(JSON_LEVELS)]

        is_long = long_line and (i % 15 == 14)
        use_decoy = include_decoys and (i % 6 == 4)
        decoy_str = DECOY_PREFIXES[i % len(DECOY_PREFIXES)] if use_decoy else ""
        log_prefix = f"{timestamp} [{level}] [{service}] {decoy_str}"

        # Tryb czystego JSONL (gdy wybrano JSON bez dodatkowych utrudnień logowych)
        if mode_upper == "JSON" and not multi_structure and not include_decoys:
            entry = cls.generate_entry(i, base_time, long_payload=is_long)
            return json.dumps(entry, ensure_ascii=False, indent=indent)

        if mode_upper == "JSON":
            req_entry = cls.generate_entry(i, base_time, long_payload=is_long)
            req_str = json.dumps(req_entry, ensure_ascii=False, indent=indent)
            if multi_structure and (i % 2 == 0):
                resp_entry = cls.generate_response_entry(i, str(req_entry.get("trace_id", "")))
                resp_str = json.dumps(resp_entry, ensure_ascii=False, indent=indent)
                return f"{log_prefix}Incoming JSON Request: {req_str} >>> Outgoing JSON Response: {resp_str} [STATUS=COMPLETED]"
            return f"{log_prefix}Event JSON Payload: {req_str}"

        if mode_upper == "XML":
            req_xml = cls.generate_xml_string(i, base_time, include_declaration=True, long_payload=is_long)
            if multi_structure and (i % 2 == 0):
                resp_xml = cls.generate_xml_string(
                    i, base_time, include_declaration=False, long_payload=False, is_response=True
                )
                return f"{log_prefix}SOAP Request Envelope: {req_xml} >>> SOAP Response Envelope: {resp_xml} [SOAP_EXCHANGE_DONE]"
            return f"{log_prefix}XML Document Payload: {req_xml}"

        # Tryb HYBRID / CHALLENGE — rotacja najciekawszych scenariuszy dla Log Viewera
        variant = i % 5
        if variant == 0:
            # Dwa obiekty JSON w jednej linii (Request + Response)
            req_entry = cls.generate_entry(i, base_time, long_payload=is_long)
            resp_entry = cls.generate_response_entry(i, str(req_entry.get("trace_id", "")))
            req_s = json.dumps(req_entry, ensure_ascii=False)
            resp_s = json.dumps(resp_entry, ensure_ascii=False)
            return (
                f"{log_prefix}REST API Call Request: {req_s} | Response: {resp_s} [duration={random.randint(5, 450)}ms]"
            )
        if variant == 1:
            # Dwa bloki XML w jednej linii (SOAP Request + SOAP Response)
            req_xml = cls.generate_xml_string(i, base_time, include_declaration=True, long_payload=is_long)
            resp_xml = cls.generate_xml_string(
                i, base_time, include_declaration=False, long_payload=False, is_response=True
            )
            return f"{log_prefix}SOAP Bridge Request: {req_xml} ==> SOAP Bridge Response: {resp_xml} [bridge_ok=true]"
        if variant == 2:
            # Linia mieszana: JSON Request transformowany na XML Downstream (test przełączania JSON <-> XML w FormatDialog!)
            req_entry = cls.generate_entry(i, base_time, long_payload=False)
            req_s = json.dumps(req_entry, ensure_ascii=False)
            downstream_xml = cls.generate_xml_string(i, base_time, include_declaration=True, long_payload=is_long)
            return f"{log_prefix}Gateway Transform JSON_IN: {req_s} -> XML_OUT: {downstream_xml} [transformed=1]"
        if variant == 3:
            # Pojedynczy bogaty obiekt JSON z zagnieżdżonym ciągiem XML oraz wyzwaniami składniowymi
            req_entry = cls.generate_entry(i, base_time, long_payload=is_long)
            req_entry["embedded_xml_preview"] = (
                '<audit id="99" status="OK"><!-- inner comment --><msg>Zażółć gęślą jaźń</msg></audit>'
            )
            return f"{log_prefix}Audit Event: {json.dumps(req_entry, ensure_ascii=False)}"
        # variant == 4: Bogaty XML z przestrzeniami nazw, CDATA i komentarzami
        xml_doc = cls.generate_xml_string(i, base_time, include_declaration=True, long_payload=is_long)
        return f"{log_prefix}Enterprise Bus Message: {xml_doc} [ack=true]"

    @classmethod
    def run_batch(
        cls,
        filepath: str,
        count: int,
        overwrite: bool = True,
        indent: int | None = None,
        fmt_mode: str = "JSON",
        multi_structure: bool = False,
        include_decoys: bool = False,
        long_lines: bool = False,
        stop_event: threading.Event | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> int:
        """Generates a batch of structured JSON / XML / Hybrid records."""
        mode = "w" if overwrite else "a"
        base_time = datetime.now() - timedelta(minutes=max(1, count // 10))

        if log_callback:
            log_callback(f"Generating {count} structured ({fmt_mode}) records to '{filepath}'...")

        written = 0
        with open(filepath, mode, encoding="utf-8") as f:
            for i in range(count):
                if stop_event and stop_event.is_set():
                    break
                line = cls.generate_formatted_line(
                    i,
                    base_time=base_time,
                    fmt_mode=fmt_mode,
                    indent=indent,
                    multi_structure=multi_structure,
                    include_decoys=include_decoys,
                    long_line=long_lines,
                )
                f.write(line + "\n")
                written += 1
                if (i + 1) % 50 == 0 or (i + 1) == count:
                    f.flush()
                    if progress_callback:
                        progress_callback(written, count)

        if log_callback:
            log_callback(f"Written {written} structured ({fmt_mode}) records to '{filepath}'.")
        return written

    @classmethod
    def run_live(
        cls,
        filepath: str,
        delay: float = 0.1,
        fmt_mode: str = "JSON",
        multi_structure: bool = False,
        include_decoys: bool = False,
        long_lines: bool = False,
        stop_event: threading.Event | None = None,
        line_callback: Callable[[int, str], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Continuous live structured JSON / XML / Hybrid stream."""
        if log_callback:
            log_callback(f"Live {fmt_mode} streaming started for '{filepath}' (delay: {delay}s)...")

        counter = 0
        with open(filepath, "a", encoding="utf-8") as f:
            while not (stop_event and stop_event.is_set()):
                line = cls.generate_formatted_line(
                    counter,
                    base_time=None,
                    fmt_mode=fmt_mode,
                    indent=None,
                    multi_structure=multi_structure,
                    include_decoys=include_decoys,
                    long_line=long_lines,
                )
                f.write(line + "\n")
                f.flush()
                counter += 1
                if line_callback:
                    line_callback(counter, line)
                time.sleep(delay)

        if log_callback:
            log_callback(f"Live {fmt_mode} streaming stopped. Total records generated: {counter}.")


# ============================================================================
# COMMAND LINE INTERFACE (CLI)
# ============================================================================


def run_cli_live(args: argparse.Namespace) -> None:
    """Runs multi-app simulation in CLI mode."""
    out_dir_path = Path(str(args.output_dir)).resolve()
    out_dir_path.mkdir(parents=True, exist_ok=True)
    out_dir = str(out_dir_path)
    num_files = args.apps
    d_min = args.min_delay
    d_max = args.max_delay
    include_payloads = not getattr(args, "no_payloads", False)

    print("=== Multi-App Live Simulator ===")
    print(f"Output Directory: {out_dir}")
    print(f"Applications: {num_files}, Delay: {d_min:.2f}s - {d_max:.2f}s, XML/JSON Payloads: {include_payloads}")
    print("Press Ctrl+C to stop simulation.\n")

    workers: list[MultiAppWorker] = []
    threads: list[threading.Thread] = []

    for i in range(1, num_files + 1):
        worker = MultiAppWorker(
            i,
            out_dir,
            d_min,
            d_max,
            include_payloads=include_payloads,
            log_callback=lambda msg: print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"),
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
    filepath = str(Path(str(args.output)).resolve())
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

    def progress_cb(cur_written: int, total: int, speed: float, cur_elapsed: float) -> None:
        pct = (cur_written / total) * 100 if total > 0 else 0
        written_mb = cur_written / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        print(
            f"\rProgress: {written_mb:.1f} / {total_mb:.1f} MB ({pct:.1f}%) | "
            f"Speed: {speed:.1f} MB/s | Elapsed: {cur_elapsed:.1f}s",
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
    filepath = str(Path(str(args.output)).resolve())
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
    """Runs Structured JSON / XML / Hybrid log generator in CLI mode."""
    filepath = str(Path(str(args.output)).resolve())
    fmt_mode = getattr(args, "format", "HYBRID").upper()
    multi_structure = getattr(args, "multi_structure", True)
    include_decoys = getattr(args, "decoys", True)
    long_lines = getattr(args, "long_lines", True)

    print("=== Structured JSON / XML / Hybrid Log Generator ===")
    print(f"Output File: {filepath}")
    print(
        f"Format: {fmt_mode} | Multi-structure: {multi_structure} | "
        f"Decoys: {include_decoys} | Long lines (>15KB): {long_lines}"
    )

    if args.live:
        print(f"Mode: Live Streaming (delay: {args.delay:.2f}s). Press Ctrl+C to stop.\n")
        stop_event = threading.Event()
        try:
            JsonLogGenerator.run_live(
                filepath=filepath,
                delay=args.delay,
                fmt_mode=fmt_mode,
                multi_structure=multi_structure,
                include_decoys=include_decoys,
                long_lines=long_lines,
                stop_event=stop_event,
                line_callback=lambda idx, line: print(f"[{idx}] {line[:120]}..."),
                log_callback=print,
            )
        except KeyboardInterrupt:
            stop_event.set()
            print("\nStructured streaming stopped.")
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
                fmt_mode=fmt_mode,
                multi_structure=multi_structure,
                include_decoys=include_decoys,
                long_lines=long_lines,
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

GUI_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if GUI_AVAILABLE:
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

    def _create_file_picker_row(
        parent: QWidget, default_filename: str, browse_slot: Callable[[], None]
    ) -> tuple[QLineEdit, QPushButton, QHBoxLayout]:
        """Tworzy wiersz wyboru pliku lub katalogu z polem tekstowym i przyciskiem Browse."""
        row_layout = QHBoxLayout()
        entry = QLineEdit(str(Path(default_filename).resolve()), parent)
        btn = QPushButton("Browse...", parent)
        btn.clicked.connect(browse_slot)
        row_layout.addWidget(entry)
        row_layout.addWidget(btn)
        return entry, btn, row_layout

    def _create_progress_and_action_controls(
        parent: QWidget,
        initial_status: str,
        start_text: str,
        stop_text: str,
        start_slot: Callable[[], None],
        stop_slot: Callable[[], None],
    ) -> tuple[QProgressBar, QLabel, QPushButton, QPushButton, QHBoxLayout]:
        """Tworzy wspólny pasek postępu, etykietę statusu oraz przyciski START / STOP dla zakładek generatora."""
        progress_bar = QProgressBar(parent)
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)

        lbl_status = QLabel(initial_status, parent)
        lbl_status.setStyleSheet("font-weight: bold; color: #2563eb;")

        act_layout = QHBoxLayout()
        btn_start = QPushButton(start_text, parent)
        btn_start.setStyleSheet(STYLE_BTN_PRIMARY)
        btn_start.clicked.connect(start_slot)

        btn_stop = QPushButton(stop_text, parent)
        btn_stop.setStyleSheet(STYLE_BTN_STOP)
        btn_stop.setEnabled(False)
        btn_stop.clicked.connect(stop_slot)

        act_layout.addWidget(btn_start)
        act_layout.addWidget(btn_stop)
        return progress_bar, lbl_status, btn_start, btn_stop, act_layout

    class TabSignals(QObject):
        """Universal signal hub for background GUI workers."""

        log_msg = Signal(str)
        progress_val = Signal(int, int)  # (current, total)
        progress_stats = Signal(int, int, float, float)  # (written_bytes, total_bytes, speed_mb, elapsed_s)
        finished = Signal()

    def _create_tab_signals(
        main_window: GeneratorMainWindow,
        on_finished: Callable[[], None],
        on_progress: Callable[[int, int], None] | None = None,
        on_stats: Callable[[int, int, float, float], None] | None = None,
    ) -> TabSignals:
        """Tworzy i podłącza obiekt sygnałów TabSignals dla zakładki generatora."""
        sig = TabSignals()
        sig.log_msg.connect(main_window.append_log)
        if on_progress is not None:
            sig.progress_val.connect(on_progress)
        if on_stats is not None:
            sig.progress_stats.connect(on_stats)
        sig.finished.connect(on_finished)
        return sig

    class MultiAppTab(QWidget):
        """Tab 1: Live concurrent multi-application load simulation."""

        def __init__(self, main_window: GeneratorMainWindow) -> None:
            super().__init__()
            self.main_window = main_window
            self.workers: list[MultiAppWorker] = []
            self.threads: list[threading.Thread] = []

            self.spin_files = QSpinBox(self)
            self.spin_min_delay = QDoubleSpinBox(self)
            self.spin_max_delay = QDoubleSpinBox(self)
            self.chk_payloads = QCheckBox("Inject complex XML/JSON & multi-structure payloads into live logs", self)
            self.entry_dir, self.btn_browse, dir_layout = _create_file_picker_row(
                self, "./simulation_logs", self.browse_dir
            )
            self.btn_start = QPushButton("START SIMULATION", self)
            self.btn_stop = QPushButton("STOP ALL", self)
            self.instances_container = QWidget(self)
            self.instances_wrap_layout = QVBoxLayout(self.instances_container)

            self._setup_layout(dir_layout)

        def _setup_layout(self, dir_layout: QHBoxLayout) -> None:
            layout = QVBoxLayout(self)

            cfg_group = QGroupBox("Multi-App Simulation Settings")
            form = QFormLayout()

            self.spin_files.setRange(1, 50)
            self.spin_files.setValue(5)
            form.addRow("Number of applications (APP-N):", self.spin_files)

            self.spin_min_delay.setRange(0.001, 10.0)
            self.spin_min_delay.setSingleStep(0.05)
            self.spin_min_delay.setValue(0.1)
            form.addRow("Min delay (s):", self.spin_min_delay)

            self.spin_max_delay.setRange(0.001, 20.0)
            self.spin_max_delay.setSingleStep(0.05)
            self.spin_max_delay.setValue(0.5)
            form.addRow("Max delay (s):", self.spin_max_delay)

            self.chk_payloads.setChecked(True)
            form.addRow("Structured payloads:", self.chk_payloads)
            form.addRow("Output directory:", dir_layout)

            cfg_group.setLayout(form)
            layout.addWidget(cfg_group)

            act_layout = QHBoxLayout()
            self.btn_start.setStyleSheet(STYLE_BTN_PRIMARY)
            self.btn_start.clicked.connect(self.start_generation)

            self.btn_stop.setStyleSheet(STYLE_BTN_STOP)
            self.btn_stop.setEnabled(False)
            self.btn_stop.clicked.connect(self.stop_generation)

            act_layout.addWidget(self.btn_start)
            act_layout.addWidget(self.btn_stop)
            layout.addLayout(act_layout)

            instances_group = QGroupBox("Instance Real-Time Control")
            inst_layout = QVBoxLayout()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(140)

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
            out_dir_path = Path(out_dir)
            if not out_dir_path.exists():
                try:
                    out_dir_path.mkdir(parents=True, exist_ok=True)
                except Exception as err:
                    self.main_window.append_log(f"ERROR: Cannot create directory {out_dir}: {err}")
                    return

            self.btn_start.setEnabled(False)
            self.spin_files.setEnabled(False)
            self.chk_payloads.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.main_window.append_log(">>> Starting Multi-App simulation...")

            num_files = self.spin_files.value()
            d_min = self.spin_min_delay.value()
            d_max = self.spin_max_delay.value()
            inc_payloads = self.chk_payloads.isChecked()

            self.workers = []
            self.threads = []

            for i in range(1, num_files + 1):
                worker = MultiAppWorker(
                    i,
                    out_dir,
                    d_min,
                    d_max,
                    include_payloads=inc_payloads,
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
                self.chk_payloads.setEnabled(True)
                self.clear_instances_ui()
                self.main_window.append_log(">>> All Multi-App workers stopped.")

            QTimer.singleShot(0, reset)

    class FastGenTab(QWidget):
        """Tab 2: High-speed large file benchmark generator."""

        def __init__(self, main_window: GeneratorMainWindow) -> None:
            super().__init__()
            self.main_window = main_window
            self.stop_event = threading.Event()
            self.signals = _create_tab_signals(main_window, on_finished=self.on_finished, on_stats=self.update_stats)

            self.entry_file, self.btn_browse, file_layout = _create_file_picker_row(
                self, "benchmark_fast.log", self.browse_file
            )
            self.spin_size = QDoubleSpinBox(self)
            self.combo_unit = QComboBox(self)
            self.spin_chunk = QSpinBox(self)
            self.chk_overwrite = QCheckBox("Overwrite existing file (uncheck to append)", self)
            (
                self.progress_bar,
                self.lbl_stats,
                self.btn_start,
                self.btn_stop,
                act_layout,
            ) = _create_progress_and_action_controls(
                self,
                "Ready to generate.",
                "START GENERATION",
                "STOP",
                self.start_fast_gen,
                self.stop_fast_gen,
            )

            self._setup_layout(file_layout, act_layout)

        def _setup_layout(self, file_layout: QHBoxLayout, act_layout: QHBoxLayout) -> None:
            layout = QVBoxLayout(self)
            group = QGroupBox("Fast File Generation Parameters")
            form = QFormLayout()

            form.addRow("Output file path:", file_layout)

            size_layout = QHBoxLayout()
            self.spin_size.setRange(0.01, 1000.0)
            self.spin_size.setValue(1.0)
            self.spin_size.setSingleStep(0.5)

            self.combo_unit.addItems(["GB", "MB"])
            size_layout.addWidget(self.spin_size)
            size_layout.addWidget(self.combo_unit)
            size_layout.addStretch()
            form.addRow("Target size:", size_layout)

            self.spin_chunk.setRange(1_000, 1_000_000)
            self.spin_chunk.setValue(100_000)
            self.spin_chunk.setSingleStep(10_000)
            form.addRow("Buffer chunk size (lines):", self.spin_chunk)

            self.chk_overwrite.setChecked(True)
            form.addRow("Write mode:", self.chk_overwrite)

            group.setLayout(form)
            layout.addWidget(group)
            layout.addWidget(self.progress_bar)
            layout.addWidget(self.lbl_stats)
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
                        progress_callback=lambda w, t, s, el: self.signals.progress_stats.emit(w, t, s, el),
                        log_callback=lambda msg: self.signals.log_msg.emit(msg),
                    )
                except Exception as err:
                    self.signals.log_msg.emit(f"FAST GENERATION ERROR: {err}")
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

        def __init__(self, main_window: GeneratorMainWindow) -> None:
            super().__init__()
            self.main_window = main_window
            self.stop_event = threading.Event()
            self.signals = _create_tab_signals(
                main_window, on_finished=self.on_finished, on_progress=self.update_progress
            )

            self.entry_file, self.btn_browse, file_layout = _create_file_picker_row(
                self, "test_target_logs.txt", self.browse_file
            )
            self.entry_phrase = QLineEdit("[TARGET]", self)
            self.spin_interval = QSpinBox(self)
            self.rb_live = QRadioButton("Continuous mode (Live stream with delay)", self)
            self.rb_batch = QRadioButton("Batch mode (Generate exact line count)", self)
            self.spin_delay = QDoubleSpinBox(self)
            self.spin_count = QSpinBox(self)
            (
                self.progress_bar,
                self.lbl_status,
                self.btn_start,
                self.btn_stop,
                act_layout,
            ) = _create_progress_and_action_controls(
                self, "Ready.", "START GENERATION", "STOP", self.start_gen, self.stop_gen
            )

            self._setup_layout(file_layout, act_layout)

        def _setup_layout(self, file_layout: QHBoxLayout, act_layout: QHBoxLayout) -> None:
            layout = QVBoxLayout(self)
            group = QGroupBox("Target [TARGET] Generator Settings")
            form = QFormLayout()

            form.addRow("Output file path:", file_layout)
            form.addRow("Target keyword/phrase:", self.entry_phrase)

            self.spin_interval.setRange(1, 1000)
            self.spin_interval.setValue(15)
            form.addRow("Inject target every N lines:", self.spin_interval)

            self.rb_live.setChecked(True)
            self.rb_live.toggled.connect(self.toggle_mode)

            mode_layout = QVBoxLayout()
            mode_layout.addWidget(self.rb_live)
            mode_layout.addWidget(self.rb_batch)
            form.addRow("Operation mode:", mode_layout)

            self.spin_delay.setRange(0.01, 10.0)
            self.spin_delay.setValue(0.3)
            self.spin_delay.setSingleStep(0.05)
            form.addRow("Delay per line (s):", self.spin_delay)

            self.spin_count.setRange(1, 10_000_000)
            self.spin_count.setValue(5000)
            self.spin_count.setSingleStep(1000)
            self.spin_count.setEnabled(False)
            form.addRow("Line count (batch mode):", self.spin_count)

            group.setLayout(form)
            layout.addWidget(group)
            layout.addWidget(self.progress_bar)
            layout.addWidget(self.lbl_status)
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
                    except Exception as err:
                        self.signals.log_msg.emit(f"TARGET LIVE ERROR: {err}")
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
                    except Exception as err:
                        self.signals.log_msg.emit(f"TARGET BATCH ERROR: {err}")
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
        """Tab 4: Structured JSON / XML / Hybrid Challenge log generator."""

        def __init__(self, main_window: GeneratorMainWindow) -> None:
            super().__init__()
            self.main_window = main_window
            self.stop_event = threading.Event()
            self.signals = _create_tab_signals(
                main_window, on_finished=self.on_finished, on_progress=self.update_progress
            )

            self.entry_file, self.btn_browse, file_layout = _create_file_picker_row(
                self, "sample_structured_challenge.log", self.browse_file
            )
            self.combo_format = QComboBox(self)
            self.chk_multi = QCheckBox("Multi-structure lines (Request + Response JSON/XML in a single log line)", self)
            self.chk_decoys = QCheckBox(
                "Inject parser decoys in log prefix (false '{', '[', '<unclosed>' before valid payload)", self
            )
            self.chk_long = QCheckBox(
                "Inject ultra-long lines (>15 KB) to test [truncated] view, horizontal scroll & FormatDialog", self
            )
            self.rb_batch = QRadioButton("Batch mode (Generate exact record count)", self)
            self.rb_live = QRadioButton("Continuous mode (Live stream JSON/XML)", self)
            self.spin_count = QSpinBox(self)
            self.spin_delay = QDoubleSpinBox(self)
            self.chk_overwrite = QCheckBox("Overwrite file (batch mode)", self)
            (
                self.progress_bar,
                self.lbl_status,
                self.btn_start,
                self.btn_stop,
                act_layout,
            ) = _create_progress_and_action_controls(
                self, "Ready.", "START GENERATION", "STOP", self.start_gen, self.stop_gen
            )

            self._setup_layout(file_layout, act_layout)

        def _setup_layout(self, file_layout: QHBoxLayout, act_layout: QHBoxLayout) -> None:
            layout = QVBoxLayout(self)
            group = QGroupBox("Structured JSON / XML & Challenge Generator Settings")
            form = QFormLayout()

            form.addRow("Output file path:", file_layout)

            self.combo_format.addItem("HYBRID — Mixed JSON + XML + Multi-Structure Challenge", "HYBRID")
            self.combo_format.addItem("JSON — Structured JSON / JSONL Payloads", "JSON")
            self.combo_format.addItem("XML — SOAP / Namespaces / CDATA / Comments Payloads", "XML")
            form.addRow("Payload format:", self.combo_format)

            challenge_layout = QVBoxLayout()
            self.chk_multi.setChecked(True)
            self.chk_decoys.setChecked(True)
            self.chk_long.setChecked(True)
            challenge_layout.addWidget(self.chk_multi)
            challenge_layout.addWidget(self.chk_decoys)
            challenge_layout.addWidget(self.chk_long)
            form.addRow("Challenge features:", challenge_layout)

            self.rb_batch.setChecked(True)
            self.rb_batch.toggled.connect(self.toggle_mode)

            mode_layout = QVBoxLayout()
            mode_layout.addWidget(self.rb_batch)
            mode_layout.addWidget(self.rb_live)
            form.addRow("Generation mode:", mode_layout)

            self.spin_count.setRange(1, 5_000_000)
            self.spin_count.setValue(2000)
            self.spin_count.setSingleStep(500)
            form.addRow("Record count (batch):", self.spin_count)

            self.spin_delay.setRange(0.01, 10.0)
            self.spin_delay.setValue(0.1)
            self.spin_delay.setSingleStep(0.05)
            self.spin_delay.setEnabled(False)
            form.addRow("Stream delay (s):", self.spin_delay)

            self.chk_overwrite.setChecked(True)
            form.addRow("Write options:", self.chk_overwrite)

            group.setLayout(form)
            layout.addWidget(group)
            layout.addWidget(self.progress_bar)
            layout.addWidget(self.lbl_status)
            layout.addLayout(act_layout)
            layout.addStretch()

        def browse_file(self) -> None:
            f, _ = QFileDialog.getSaveFileName(
                self,
                "Select Output File",
                filter="Log & Structured Files (*.log *.jsonl *.xml *.json);;All Files (*.*)",
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
                self.lbl_status.setText(f"Generated structured records: {current}")

        def start_gen(self) -> None:
            filepath = self.entry_file.text().strip()
            if not filepath:
                self.main_window.append_log("ERROR: Please specify output file path.")
                return

            fmt_mode = str(self.combo_format.currentData() or "HYBRID")
            multi_structure = self.chk_multi.isChecked()
            include_decoys = self.chk_decoys.isChecked()
            long_lines = self.chk_long.isChecked()

            self.stop_event.clear()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)

            if self.rb_batch.isChecked():
                count = self.spin_count.value()
                overwrite = self.chk_overwrite.isChecked()
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)
                self.lbl_status.setText(f"Batch ({fmt_mode}) generation in progress...")

                def worker() -> None:
                    try:
                        JsonLogGenerator.run_batch(
                            filepath=filepath,
                            count=count,
                            overwrite=overwrite,
                            fmt_mode=fmt_mode,
                            multi_structure=multi_structure,
                            include_decoys=include_decoys,
                            long_lines=long_lines,
                            stop_event=self.stop_event,
                            progress_callback=lambda cur, tot: self.signals.progress_val.emit(cur, tot),
                            log_callback=lambda msg: self.signals.log_msg.emit(msg),
                        )
                    except Exception as err:
                        self.signals.log_msg.emit(f"STRUCTURED BATCH ERROR: {err}")
                    finally:
                        self.signals.finished.emit()

            else:
                delay = self.spin_delay.value()
                self.progress_bar.setRange(0, 0)
                self.lbl_status.setText(f"Live ({fmt_mode}) streaming active...")

                def worker() -> None:
                    try:
                        JsonLogGenerator.run_live(
                            filepath=filepath,
                            delay=delay,
                            fmt_mode=fmt_mode,
                            multi_structure=multi_structure,
                            include_decoys=include_decoys,
                            long_lines=long_lines,
                            stop_event=self.stop_event,
                            line_callback=lambda cur, line: self.signals.progress_val.emit(cur, 0),
                            log_callback=lambda msg: self.signals.log_msg.emit(msg),
                        )
                    except Exception as err:
                        self.signals.log_msg.emit(f"STRUCTURED LIVE ERROR: {err}")
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
            self.setWindowTitle("Log Generator")
            self.resize(820, 760)

            self._auto_scroll: bool = True
            self._programmatic_scroll: bool = False

            # Ustawienie ikony okna aplikacji jeśli dostępna
            base_res_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            app_icon_path = base_res_dir / "assets" / "icon.png"
            if app_icon_path.exists():
                self.setWindowIcon(QIcon(str(app_icon_path)))

            self.tabs = QTabWidget(self)
            self.tab_multi_app = MultiAppTab(self)
            self.tab_fast_gen = FastGenTab(self)
            self.tab_target_gen = TargetGenTab(self)
            self.tab_json_gen = JsonGenTab(self)
            self.btn_autoscroll = QPushButton("Auto-scroll", self)
            self.console = QTextEdit(self)

            self._setup_layout()

        def _setup_layout(self) -> None:
            main_layout = QVBoxLayout(self)

            title_lbl = QLabel("🛠️ Log Generator")
            title_lbl.setStyleSheet("font-size: 15pt; font-weight: bold; padding: 4px;")
            main_layout.addWidget(title_lbl)

            self.tabs.addTab(self.tab_multi_app, "🔴 Multi-App Simulator (Live)")
            self.tabs.addTab(self.tab_fast_gen, "⚡ Fast File Gen (GB/MB)")
            self.tabs.addTab(self.tab_target_gen, "🎯 Target Logs [TARGET]")
            self.tabs.addTab(self.tab_json_gen, "📦 JSON / XML Challenge")

            main_layout.addWidget(self.tabs)

            console_header = QHBoxLayout()
            console_header.addWidget(QLabel("Event and Output Log:"))
            console_header.addStretch()

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

        def _on_scrollbar_range_changed(self, _min_val: int, _max_val: int) -> None:
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
        description="Log Generator Tool for Developers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage Examples:
  python log_generator.py                           # Starts Graphical User Interface (GUI)
  python log_generator.py fast --output test.log --size-gb 2
  python log_generator.py target --output filter.log --phrase "[TARGET]" --count 5000
  python log_generator.py json --output challenge.log --format hybrid --count 1000
  python log_generator.py json --output soap.log --format xml --count 500
  python log_generator.py live --apps 3 --output-dir ./sim_logs
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
        "--no-payloads", action="store_true", help="Disable complex XML/JSON payload injection in live logs"
    )
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

    # Subparser: json (obsługuje również xml i hybrid)
    parser_json = subparsers.add_parser(
        "json", aliases=["xml", "structured"], help="Structured JSON / XML / Hybrid Challenge log generator"
    )
    parser_json.add_argument("--output", type=str, default="sample_structured_challenge.log", help="Output file path")
    parser_json.add_argument(
        "--format",
        choices=["json", "xml", "hybrid"],
        default="hybrid",
        help="Payload format mode: json, xml, or hybrid (default: hybrid)",
    )
    parser_json.add_argument("--count", type=int, default=1000, help="Record count for batch mode")
    parser_json.add_argument("--live", action="store_true", help="Continuous live streaming mode")
    parser_json.add_argument("--delay", type=float, default=0.1, help="Delay in seconds for live mode")
    parser_json.add_argument("--indent", type=int, default=None, help="JSON indentation (default 1 line per record)")
    parser_json.add_argument(
        "--no-multi",
        dest="multi_structure",
        action="store_false",
        default=True,
        help="Disable multi-structure (Request + Response) per single line",
    )
    parser_json.add_argument(
        "--no-decoys",
        dest="decoys",
        action="store_false",
        default=True,
        help="Disable false-positive parser decoys ('{', '[', '<') in log prefix",
    )
    parser_json.add_argument(
        "--no-long-lines",
        dest="long_lines",
        action="store_false",
        default=True,
        help="Disable ultra-long (>15 KB) lines injection",
    )
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
        elif args.subcommand in ("json", "xml", "structured"):
            if args.subcommand == "xml" and getattr(args, "format", "hybrid") == "hybrid":
                args.format = "xml"
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
