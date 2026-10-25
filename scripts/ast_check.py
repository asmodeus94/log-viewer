#!/usr/bin/env python3
"""scripts/ast_check.py — Szybka analiza AST (duplikaty kodu oraz nadpisywane zmienne lokalne)."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path


def _node_weight(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node))


def _stmt_has_cf(s: ast.stmt) -> bool:
    return any(
        isinstance(sub, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith))
        for sub in ast.walk(s)
    )


def _anon_dump(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return "Name"
    if isinstance(node, ast.Attribute):
        return f"Attr({_anon_dump(node.value)})"
    fields: list[str] = []
    for k, v in ast.iter_fields(node):
        if k in ("ctx", "type_comment"):
            continue
        if isinstance(v, list):
            inner = ",".join(_anon_dump(x) if isinstance(x, ast.AST) else repr(x) for x in v)
            fields.append(f"{k}=[{inner}]")
        elif isinstance(v, ast.AST):
            fields.append(f"{k}={_anon_dump(v)}")
        else:
            fields.append(f"{k}={v!r}")
    return f"{type(node).__name__}({','.join(fields)})"


def _record_dup(
    seen: dict[str, tuple[str, int, int, int]],
    key: str,
    loc: tuple[str, int, int, int],
    lines: int,
    raw_dups: list[tuple[tuple[str, int, int, int], tuple[str, int, int, int]]],
) -> None:
    if key in seen:
        prev = seen[key]
        if prev[0] != loc[0] or abs(prev[1] - loc[1]) >= lines:
            raw_dups.append((prev, loc))
    else:
        seen[key] = loc


def check_duplicated_code(files: list[Path], repo_root: Path) -> list[str]:
    """Wykrywa zduplikowane bloki instrukcji AST (odpowiednik inspekcji DuplicatedCode)."""
    blocks: list[tuple[str, list[ast.stmt]]] = []
    for p in files:
        rel = p.relative_to(repo_root).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            for field_name in ("body", "orelse", "finalbody"):
                stmts = getattr(node, field_name, None)
                if isinstance(stmts, list) and stmts:
                    clean = [
                        s
                        for s in stmts
                        if not (
                            isinstance(s, ast.Expr)
                            and isinstance(s.value, ast.Constant)
                            and isinstance(s.value.value, str)
                        )
                    ]
                    if clean:
                        blocks.append((rel, clean))

    seen_exact: dict[str, tuple[str, int, int, int]] = {}
    seen_anon: dict[str, tuple[str, int, int, int]] = {}
    raw_dups: list[tuple[tuple[str, int, int, int], tuple[str, int, int, int]]] = []

    for rel, stmts in blocks:
        exact_norms = [ast.dump(s) for s in stmts]
        anon_norms = [_anon_dump(s) for s in stmts]
        weights = [_node_weight(s) for s in stmts]
        cfs = [_stmt_has_cf(s) for s in stmts]
        n = len(stmts)
        for length in range(1, min(n + 1, 25)):
            for i in range(0, n - length + 1):
                w = sum(weights[i : i + length])
                if w < 65:
                    continue
                s0, s1 = stmts[i], stmts[i + length - 1]
                end_line = s1.end_lineno or s1.lineno
                lines = end_line - s0.lineno + 1
                if lines < 6:
                    continue
                loc = (rel, s0.lineno, end_line, w)
                _record_dup(seen_exact, "\n".join(exact_norms[i : i + length]), loc, lines, raw_dups)

                if w >= 85 and lines >= 10 and any(cfs[i : i + length]):
                    _record_dup(seen_anon, "\n".join(anon_norms[i : i + length]), loc, lines, raw_dups)

    # Odfiltruj podprzedziały zawierające się w większych duplikatach
    maximal: list[tuple[tuple[str, int, int, int], tuple[str, int, int, int]]] = []
    for a, b in sorted(raw_dups, key=lambda x: x[0][3], reverse=True):
        if any(
            ma[0] == a[0] and mb[0] == b[0] and ma[1] <= a[1] and a[2] <= ma[2] and mb[1] <= b[1] and b[2] <= mb[2]
            for ma, mb in maximal
        ):
            continue
        maximal.append((a, b))

    return [
        f"[DuplicatedCode] {a[0]}:{a[1]}-{a[2]} duplicates {b[0]}:{b[1]}-{b[2]} (AST weight={a[3]})" for a, b in maximal
    ]


def _has_load(nodes: Sequence[ast.AST], var: str) -> bool:
    for n in nodes:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Name) and sub.id == var and isinstance(sub.ctx, ast.Load):
                return True
            if isinstance(sub, ast.AugAssign) and isinstance(sub.target, ast.Name) and sub.target.id == var:
                return True
    return False


def _has_store_target(target: ast.AST, var: str) -> bool:
    return any(
        isinstance(sub, ast.Name) and sub.id == var and isinstance(sub.ctx, ast.Store) for sub in ast.walk(target)
    )


def _first_use_in_stmt_list(
    stmts: list[ast.stmt], var: str, loaded_on_fallthrough: bool, loaded_on_break: bool
) -> str | None:
    for idx, s in enumerate(stmts):
        rem = stmts[idx + 1 :]
        rem_loaded = loaded_on_fallthrough or _has_load(rem, var)
        res = _first_use_in_stmt(s, var, rem_loaded, loaded_on_break)
        if res is not None:
            return res
    return None


def _first_use_in_stmt(s: ast.stmt, var: str, loaded_on_fallthrough: bool, loaded_on_break: bool) -> str | None:
    if isinstance(s, (ast.Return, ast.Raise)):
        return "load" if _has_load([s], var) else "store"
    if isinstance(s, ast.Break):
        return "load" if loaded_on_break else "store"
    if isinstance(s, ast.Continue):
        return "load" if loaded_on_fallthrough else "store"
    if isinstance(s, ast.AugAssign):
        if _has_store_target(s.target, var) or _has_load([s.value, s.target], var):
            return "load"
        return None
    if isinstance(s, ast.Assign):
        if _has_load([s.value], var):
            return "load"
        for t in s.targets:
            if _has_store_target(t, var):
                return "store"
            if _has_load([t], var):
                return "load"
        return None
    if isinstance(s, ast.AnnAssign):
        if s.value and _has_load([s.value], var):
            return "load"
        if _has_store_target(s.target, var) and s.value is not None:
            return "store"
        return None
    if isinstance(s, (ast.With, ast.AsyncWith)):
        if _has_load(s.items, var):
            return "load"
        return _first_use_in_stmt_list(s.body, var, loaded_on_fallthrough, loaded_on_break)
    if isinstance(s, ast.Try):
        if _has_load(s.handlers, var) or _has_load(s.finalbody, var):
            return "load"
        if not loaded_on_fallthrough:
            return _first_use_in_stmt_list(s.body, var, False, loaded_on_break)
        return "load" if _has_load(s.body, var) else None
    if isinstance(s, ast.If):
        if _has_load([s.test], var):
            return "load"
        b_use = _first_use_in_stmt_list(s.body, var, loaded_on_fallthrough, loaded_on_break)
        e_use = _first_use_in_stmt_list(s.orelse, var, loaded_on_fallthrough, loaded_on_break) if s.orelse else None
        if b_use == "load" or e_use == "load":
            return "load"
        b_done = b_use == "store" or (not _has_load(s.body, var) and not loaded_on_fallthrough)
        e_done = (s.orelse and (e_use == "store" or (not _has_load(s.orelse, var) and not loaded_on_fallthrough))) or (
            not s.orelse and not loaded_on_fallthrough
        )
        if (b_use == "store" or e_use == "store") and b_done and e_done:
            return "store"
        return "load" if _has_load([s], var) else None
    if isinstance(s, (ast.For, ast.AsyncFor)):
        if _has_load([s.iter], var):
            return "load"
        if _has_store_target(s.target, var) and not loaded_on_fallthrough and not _has_load(s.orelse, var):
            return "store"
        b_use = _first_use_in_stmt_list(s.body, var, loaded_on_fallthrough, loaded_on_fallthrough)
        if b_use == "store" and not _has_load(s.orelse, var) and not loaded_on_fallthrough:
            return "store"
        return "load" if _has_load([s], var) else None
    return "load" if _has_load([s], var) else None


def check_dead_stores(files: list[Path], repo_root: Path) -> list[str]:
    """Wykrywa przypisania do zmiennych lokalnych nadpisywane przed pierwszym odczytem (PyUnusedLocal)."""
    issues: list[str] = []

    for p in files:
        rel = p.relative_to(repo_root).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)

        def scan_block(
            stmts: list[ast.stmt],
            fn_name: str,
            fallthrough_stmts: list[ast.stmt],
            loop_repeat_nodes: list[ast.stmt],
            break_nodes: list[ast.stmt],
            rel_path: str = rel,
        ) -> None:
            for idx, s in enumerate(stmts):
                rem = stmts[idx + 1 :]
                after_seq = rem + fallthrough_stmts
                assigned_vars: list[tuple[str, int]] = []
                if isinstance(s, ast.Assign):
                    for t in s.targets:
                        if isinstance(t, ast.Name) and not t.id.startswith("_"):
                            assigned_vars.append((t.id, s.lineno))
                elif (
                    isinstance(s, ast.AnnAssign)
                    and s.value is not None
                    and isinstance(s.target, ast.Name)
                    and not s.target.id.startswith("_")
                ):
                    assigned_vars.append((s.target.id, s.lineno))

                for var, lineno in assigned_vars:
                    if not after_seq:
                        continue
                    if (
                        _first_use_in_stmt_list(
                            after_seq,
                            var,
                            _has_load(loop_repeat_nodes, var),
                            _has_load(break_nodes, var),
                        )
                        == "store"
                    ):
                        issues.append(
                            f"[PyUnusedLocal] {rel_path}:{lineno} in '{fn_name}': "
                            f"initial value assigned to '{var}' is overwritten before being read"
                        )

                if isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
                    child_fallthrough: list[ast.stmt] = []
                    child_loop_repeat = [s] + after_seq + loop_repeat_nodes
                    child_break = after_seq + break_nodes
                else:
                    child_fallthrough = after_seq
                    child_loop_repeat = loop_repeat_nodes
                    child_break = break_nodes

                for field_name in ("body", "orelse", "finalbody"):
                    child_stmts = getattr(s, field_name, None)
                    if isinstance(child_stmts, list):
                        scan_block(child_stmts, fn_name, child_fallthrough, child_loop_repeat, child_break, rel_path)

        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scan_block(fn.body, fn.name, [], [], [])

    return issues


def run_ast_checks(repo_root: Path) -> list[str]:
    """Uruchamia wszystkie kontrole AST na kodzie źródłowym log_viewer oraz scripts."""
    files: list[Path] = []
    for folder in ("log_viewer", "scripts"):
        target_dir = repo_root / folder
        if target_dir.is_dir():
            files.extend(p for p in sorted(target_dir.rglob("*.py")) if not p.name.startswith("ui_"))
    return check_duplicated_code(files, repo_root) + check_dead_stores(files, repo_root)
