#!/usr/bin/env python3
"""scripts/ast_check.py — Szybka analiza AST (duplikaty kodu, nadpisywane zmienne lokalne, pułapki typów IDE oraz konwencje testów)."""

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


def _is_any_annotation(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name) and node.id == "Any":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "Any"


def _is_generic_qt_annotation(node: ast.AST | None) -> str | None:
    """Sprawdza, czy adnotacja to generyczny QObject / QWidget (lub QObject | None / QWidget | None)."""
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id in ("QObject", "QWidget", "object"):
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in ("QObject", "QWidget"):
        return node.attr
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_generic_qt_annotation(node.left) or _is_generic_qt_annotation(node.right)
    return None


def _is_none_constant(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_optional_annotation(node: ast.AST | None) -> bool:
    """Sprawdza, czy adnotacja typu zawiera unię z None (np. `QObject | None`)."""
    if node is None:
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return (
            _is_none_constant(node.left)
            or _is_none_constant(node.right)
            or _is_optional_annotation(node.left)
            or _is_optional_annotation(node.right)
        )
    return False


def _is_getattr_none_call(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 3
        and _is_none_constant(node.args[2])
    )


def _walk_same_scope(stmts: list[ast.stmt]) -> list[ast.AST]:
    """Zwraca wszystkie węzły AST wewnątrz listy instrukcji z pominięciem zagnieżdżonych funkcji i klas."""
    result: list[ast.AST] = []
    stack: list[ast.AST] = list(stmts)
    while stack:
        cur = stack.pop()
        result.append(cur)
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            stack.append(child)
    return result


def _is_named_expr(node: ast.AST, var_name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == var_name


def _has_isinstance_narrowing(scope_nodes: list[ast.AST], var_name: str) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "isinstance"
        and len(n.args) >= 2
        and _is_named_expr(n.args[0], var_name)
        for n in scope_nodes
    )


def _is_self_attr_expr(node: ast.AST, attr_name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == attr_name
    )


def _has_self_attr_narrowing(scope_nodes: list[ast.AST], attr_name: str) -> bool:
    """Sprawdza, czy w danym zakresie występuje zawężenie typu dla `self.<attr>` (np. `if self.attr:` lub `if self.attr is not None:`)."""
    for n in scope_nodes:
        if isinstance(n, (ast.If, ast.Assert)):
            for test_sub in ast.walk(n.test):
                if _is_self_attr_expr(test_sub, attr_name):
                    return True
    return False


def _collect_optional_self_attrs(cls: ast.ClassDef) -> dict[str, int]:
    """Zbiera atrybuty klasy i instancji (`self.<attr>`) zadeklarowane z adnotacją `T | None`."""
    opt_attrs: dict[str, int] = {}
    for item in cls.body:
        if (
            isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and _is_optional_annotation(item.annotation)
        ):
            opt_attrs[item.target.id] = item.lineno
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
            for sub in _walk_same_scope(item.body):
                if (
                    isinstance(sub, ast.AnnAssign)
                    and isinstance(sub.target, ast.Attribute)
                    and isinstance(sub.target.value, ast.Name)
                    and sub.target.value.id == "self"
                    and _is_optional_annotation(sub.annotation)
                ):
                    opt_attrs[sub.target.attr] = sub.lineno
    return opt_attrs


_SAFE_OPTIONAL_CALLS = frozenset({"isinstance", "getattr", "setattr", "hasattr", "id", "print", "bool", "repr", "str"})


def check_ide_type_pitfalls(files: list[Path], repo_root: Path) -> list[str]:
    """Wykrywa konstrukcje powodujące ostrzeżenia PyTypeChecker / PyUnresolvedReferences w IntelliJ IDEA.

    W szczególności:
    1. Parametry o sygnaturze `param: Any = None` używane w ciele funkcji — IntelliJ wnioskuje typ `Any | None`.
    2. Zmienne lokalne `x: Any = None` lub `x = getattr(..., None)` bez `isinstance(x, ...)`, na których
       wykonywane jest `len(x)`, indeksowanie `x[...]` lub wywołanie metody `x.method()` po ponownym przypisaniu.
    3. Przypisania do atrybutów `x.attr = ...` na zmiennych zadeklarowanych jako generyczny `QObject`, `QWidget` lub `object`.
    4. Wywołania `Signal("qint64")` ze stałymi znakowymi zamiast typów Pythona (np. `Signal(int)`).
    5. Przekazywanie atrybutu `self.<attr>` zadeklarowanego jako `T | None` bezpośrednio jako argument wywołania
       po przypisaniu `self.<attr> = ...` (IntelliJ nie zawęża typu `self.<attr>` bez zmiennej lokalnej lub `if`).
    """
    issues: list[str] = []

    for p in files:
        rel = p.relative_to(repo_root).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)

        # Sprawdź wywołania Signal("...") w całym pliku
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Signal":
                for arg_node in node.args:
                    if isinstance(arg_node, ast.Constant) and isinstance(arg_node.value, str):
                        issues.append(
                            f"[PyTypeChecker] {rel}:{node.lineno}: Signal({arg_node.value!r}) uses string literal "
                            f"instead of Python type (e.g. int, str, object)"
                        )

        # 5. Sprawdź w klasach przekazywanie `self.<attr>: T | None` po przypisaniu bez zmiennej lokalnej
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            opt_self_attrs = _collect_optional_self_attrs(cls)
            if not opt_self_attrs:
                continue
            for item in cls.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                m_nodes = _walk_same_scope(item.body)
                assigned_non_none: dict[str, int] = {}
                for n in m_nodes:
                    if isinstance(n, ast.Assign) and not _is_none_constant(n.value):
                        for t in n.targets:
                            if (
                                isinstance(t, ast.Attribute)
                                and isinstance(t.value, ast.Name)
                                and t.value.id == "self"
                                and t.attr in opt_self_attrs
                            ):
                                assigned_non_none.setdefault(t.attr, n.lineno)
                for attr_name, assign_line in assigned_non_none.items():
                    if _has_self_attr_narrowing(m_nodes, attr_name):
                        continue
                    decl_line = opt_self_attrs[attr_name]
                    for n in m_nodes:
                        if isinstance(n, ast.Call) and n.lineno >= assign_line:
                            call_name = (
                                n.func.id
                                if isinstance(n.func, ast.Name)
                                else (n.func.attr if isinstance(n.func, ast.Attribute) else "call")
                            )
                            if call_name in _SAFE_OPTIONAL_CALLS:
                                continue
                            all_call_args = list(n.args) + [kw.value for kw in n.keywords]
                            if any(_is_self_attr_expr(arg, attr_name) for arg in all_call_args):
                                issues.append(
                                    f"[PyTypeChecker] {rel}:{n.lineno} in '{cls.name}.{item.name}': "
                                    f"passing optional attribute 'self.{attr_name}' (declared as 'T | None' at line {decl_line}) "
                                    f"directly to '{call_name}()'; assign to a local variable first"
                                )

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            scope_nodes = _walk_same_scope(fn.body)
            any_none_vars: dict[str, int] = {}
            generic_qt_vars: dict[str, tuple[str, int]] = {}

            # 1. Sprawdź parametry z domyślną wartością None i adnotacją Any lub generycznym QObject/QWidget
            pos_args = fn.args.posonlyargs + fn.args.args
            num_defaults = len(fn.args.defaults)
            for idx, arg in enumerate(pos_args):
                def_idx = idx - (len(pos_args) - num_defaults)
                if def_idx >= 0 and _is_none_constant(fn.args.defaults[def_idx]) and _is_any_annotation(arg.annotation):
                    any_none_vars[arg.arg] = arg.lineno
                qt_base = _is_generic_qt_annotation(arg.annotation)
                if qt_base is not None:
                    generic_qt_vars[arg.arg] = (qt_base, arg.lineno)

            for idx, arg in enumerate(fn.args.kwonlyargs):
                kw_def = fn.args.kw_defaults[idx]
                if kw_def is not None and _is_none_constant(kw_def) and _is_any_annotation(arg.annotation):
                    any_none_vars[arg.arg] = arg.lineno
                qt_base = _is_generic_qt_annotation(arg.annotation)
                if qt_base is not None:
                    generic_qt_vars[arg.arg] = (qt_base, arg.lineno)

            # 2. Sprawdź zmienne lokalne `x: Any = None`, `x = getattr(..., None)` oraz `x: QObject | None = ...`
            for node in scope_nodes:
                if (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and not node.target.id.startswith("_")
                ):
                    if _is_any_annotation(node.annotation) and _is_none_constant(node.value):
                        any_none_vars[node.target.id] = node.lineno
                    qt_base = _is_generic_qt_annotation(node.annotation)
                    if qt_base is not None:
                        generic_qt_vars[node.target.id] = (qt_base, node.lineno)
                elif isinstance(node, ast.Assign) and _is_getattr_none_call(node.value):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and not t.id.startswith("_"):
                            any_none_vars[t.id] = node.lineno

            # 3. Sprawdź niebezpieczne użycia zmiennych `Any | None` bez zawężenia isinstance(...)
            for var_name, decl_line in any_none_vars.items():
                if _has_isinstance_narrowing(scope_nodes, var_name):
                    continue

                for node in scope_nodes:
                    # Wywołanie len(var_name)
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "len"
                        and len(node.args) == 1
                        and _is_named_expr(node.args[0], var_name)
                    ):
                        issues.append(
                            f"[PyTypeChecker] {rel}:{node.lineno} in '{fn.name}': "
                            f"'len({var_name})' on 'Any | None' variable (declared at line {decl_line}) "
                            f"without isinstance() narrowing"
                        )
                    # Indeksowanie var_name[...]
                    elif (
                        isinstance(node, ast.Subscript)
                        and _is_named_expr(node.value, var_name)
                        and isinstance(node.ctx, ast.Load)
                    ):
                        issues.append(
                            f"[PyUnresolvedReferences] {rel}:{node.lineno} in '{fn.name}': "
                            f"indexing '{var_name}[...]' on 'Any | None' variable (declared at line {decl_line}) "
                            f"without isinstance() narrowing"
                        )
                    # Dostęp do atrybutu/metody var_name.attr
                    elif (
                        isinstance(node, ast.Attribute)
                        and _is_named_expr(node.value, var_name)
                        and isinstance(node.ctx, ast.Load)
                    ):
                        issues.append(
                            f"[PyUnresolvedReferences] {rel}:{node.lineno} in '{fn.name}': "
                            f"attribute access '{var_name}.{node.attr}' on 'Any | None' variable "
                            f"(declared at line {decl_line}); use a specific type union 'T | None' instead of 'Any'"
                        )

            # 4. Sprawdź przypisania atrybutów `var.attr = ...` na generycznych `QObject`/`QWidget`/`object`
            for var_name, (qt_base, decl_line) in generic_qt_vars.items():
                if _has_isinstance_narrowing(scope_nodes, var_name):
                    continue
                for node in scope_nodes:
                    if (
                        isinstance(node, ast.Attribute)
                        and _is_named_expr(node.value, var_name)
                        and isinstance(node.ctx, ast.Store)
                    ):
                        issues.append(
                            f"[PyUnresolvedReferences] {rel}:{node.lineno} in '{fn.name}': "
                            f"setting attribute '{var_name}.{node.attr}' on generic '{qt_base}' "
                            f"(declared at line {decl_line}); annotate with concrete class (e.g. 'LogTab | None')"
                        )

    return issues


def _collect_self_attrs(stmts: list[ast.stmt]) -> list[tuple[str, int]]:
    """Zbiera wszystkie przypisania `self.<attr> = ...` w danej liście instrukcji metody."""
    found: list[tuple[str, int]] = []
    for node in _walk_same_scope(stmts):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for sub in ast.walk(t):
                    if (
                        isinstance(sub, ast.Attribute)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "self"
                        and isinstance(sub.ctx, ast.Store)
                    ):
                        found.append((sub.attr, node.lineno))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Attribute)
            and isinstance(node.target.value, ast.Name)
            and node.target.value.id == "self"
        ):
            found.append((node.target.attr, node.lineno))
    return found


def check_attributes_outside_init(files: list[Path], repo_root: Path) -> list[str]:
    """Wykrywa atrybuty instancji `self.<attr>` definiowane poza metodą `__init__` (PyAttributeOutsideInit)."""
    issues: list[str] = []

    for p in files:
        rel = p.relative_to(repo_root).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)

        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue

            declared_attrs: set[str] = set()
            methods_by_name: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
            methods: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

            for item in cls.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name):
                            declared_attrs.add(t.id)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    declared_attrs.add(item.target.id)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item)
                    methods_by_name.setdefault(item.name, item)
                    for dec in item.decorator_list:
                        if (isinstance(dec, ast.Name) and dec.id == "property") or (
                            isinstance(dec, ast.Attribute) and dec.attr == "setter"
                        ):
                            declared_attrs.add(item.name)

            if "__init__" not in methods_by_name:
                continue

            # Zbierz atrybuty z __init__ oraz prywatnych metod pomocniczych _* wywoływanych z __init__
            init_helpers: set[str] = {"__init__", "__new__", "__post_init__", "setupUi", "retranslateUi"}
            queue = ["__init__"]
            while queue:
                cur_m_name = queue.pop()
                m_node = methods_by_name.get(cur_m_name)
                if m_node is None:
                    continue
                for attr_name, _ in _collect_self_attrs(m_node.body):
                    declared_attrs.add(attr_name)
                for sub in _walk_same_scope(m_node.body):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "self"
                        and sub.func.attr.startswith("_")
                        and sub.func.attr not in init_helpers
                        and sub.func.attr in methods_by_name
                    ):
                        init_helpers.add(sub.func.attr)
                        queue.append(sub.func.attr)

            for method in methods:
                if method.name in init_helpers:
                    continue
                for attr_name, lineno in _collect_self_attrs(method.body):
                    if attr_name not in declared_attrs:
                        issues.append(
                            f"[PyAttributeOutsideInit] {rel}:{lineno} in '{cls.name}.{method.name}': "
                            f"instance attribute 'self.{attr_name}' defined outside '__init__'"
                        )

    return issues


def _collect_function_local_names(fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> dict[str, int]:
    """Zbiera wszystkie nazwy parametrów i zmiennych lokalnych zdefiniowanych bezpośrednio w zakresie funkcji."""
    names: dict[str, int] = {}
    pos_args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
    for arg in pos_args:
        if arg.arg not in ("self", "cls") and not arg.arg.startswith("_"):
            names.setdefault(arg.arg, arg.lineno)
    if fn.args.vararg and not fn.args.vararg.arg.startswith("_"):
        names.setdefault(fn.args.vararg.arg, fn.args.vararg.lineno)
    if fn.args.kwarg and not fn.args.kwarg.arg.startswith("_"):
        names.setdefault(fn.args.kwarg.arg, fn.args.kwarg.lineno)

    if isinstance(fn, ast.Lambda):
        return names

    for node in _walk_same_scope(fn.body):
        if isinstance(node, (ast.Assign, ast.For, ast.AsyncFor)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store) and not sub.id.startswith("_"):
                        names.setdefault(sub.id, node.lineno)
        elif (
            isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and not node.target.id.startswith("_")
        ):
            names.setdefault(node.target.id, node.lineno)
        elif isinstance(node, ast.ExceptHandler) and node.name and not node.name.startswith("_"):
            names.setdefault(node.name, node.lineno)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    for sub in ast.walk(item.optional_vars):
                        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store) and not sub.id.startswith("_"):
                            names.setdefault(sub.id, node.lineno)

    return names


def check_shadowing_names(files: list[Path], repo_root: Path) -> list[str]:
    """Wykrywa przesłanianie nazw z zewnętrznych zakresów funkcyjnych lub modułowych (PyShadowingNames)."""
    issues: list[str] = []

    for p in files:
        rel = p.relative_to(repo_root).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)

        def visit_scope(
            stmts: list[ast.stmt],
            outer_names: dict[str, int],
            scope_label: str,
            rel_path: str = rel,
        ) -> None:
            for node in _walk_same_scope(stmts):
                # Sprawdź zagnieżdżone funkcje i wyrażenia lambda
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        local_names = _collect_function_local_names(child)
                        child_label = "<lambda>" if isinstance(child, ast.Lambda) else child.name
                        for var_name, var_line in local_names.items():
                            if var_name in outer_names:
                                issues.append(
                                    f"[PyShadowingNames] {rel_path}:{var_line} in '{scope_label}.{child_label}': "
                                    f"shadows name '{var_name}' from outer scope (line {outer_names[var_name]})"
                                )
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            merged = {**outer_names, **local_names}
                            visit_scope(child.body, merged, f"{scope_label}.{child.name}", rel_path)

        for top_node in ast.walk(tree):
            if isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Uruchamiamy dla każdej funkcji/metody względem jej własnego zakresu
                fn_locals = _collect_function_local_names(top_node)
                visit_scope(top_node.body, fn_locals, top_node.name)

    # Deduplikacja zgłoszeń (gdy funkcja jest zagnieżdżona wielokrotnie)
    seen: set[str] = set()
    unique_issues: list[str] = []
    for iss in issues:
        if iss not in seen:
            seen.add(iss)
            unique_issues.append(iss)
    return unique_issues


def _handler_terminates(stmts: list[ast.stmt]) -> bool:
    """Sprawdza, czy blok except bezwarunkowo kończy działanie (raise, return, break, continue, sys.exit)."""
    for s in stmts:
        if isinstance(s, (ast.Raise, ast.Return, ast.Break, ast.Continue)):
            return True
        if (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Call)
            and (
                (isinstance(s.value.func, ast.Name) and s.value.func.id in ("exit", "quit"))
                or (
                    isinstance(s.value.func, ast.Attribute)
                    and isinstance(s.value.func.value, ast.Name)
                    and s.value.func.value.id == "sys"
                    and s.value.func.attr == "exit"
                )
            )
        ):
            return True
    return False


def _collect_defined_names_in_block(stmts: list[ast.stmt]) -> set[str]:
    """Zbiera wszystkie nazwy definiowane w bloku instrukcji (przypisania, importy, funkcje, klasy)."""
    defined: set[str] = set()
    for node in _walk_same_scope(stmts):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        defined.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
    return defined


def check_try_except_imports(files: list[Path], repo_root: Path) -> list[str]:
    """Wykrywa importy w bloku `try ... except ImportError`, które nie są zdefiniowane w bloku `except` (PyUnusedImports)."""
    issues: list[str] = []

    for p in files:
        rel = p.relative_to(repo_root).as_posix()
        content = p.read_text(encoding="utf-8")
        lines = content.splitlines()
        tree = ast.parse(content, filename=rel)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue

            # Zbierz importowane symbole z bloku try (o ile instrukcja import nie ma własnego # noinspection PyUnusedImports)
            imported_symbols: list[tuple[str, int]] = []
            for stmt in node.body:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    prev_line = lines[stmt.lineno - 2].strip() if stmt.lineno >= 2 else ""
                    if "noinspection PyUnusedImports" in prev_line:
                        continue
                    for alias in stmt.names:
                        if alias.name == "*":
                            continue
                        bound_name = alias.asname or alias.name.split(".")[0]
                        imported_symbols.append((bound_name, stmt.lineno))

            if not imported_symbols:
                continue

            for handler in node.handlers:
                if _handler_terminates(handler.body):
                    continue
                defined_in_except = _collect_defined_names_in_block(handler.body)
                for sym_name, lineno in imported_symbols:
                    if sym_name not in defined_in_except:
                        issues.append(
                            f"[PyUnusedImports] {rel}:{lineno}: '{sym_name}' in the try block with "
                            f"'except ImportError' should also be defined in the except block"
                        )

    return issues


def check_test_conventions(tests_dir: Path, repo_root: Path) -> list[str]:
    """Weryfikuje konwencje w plikach testowych.

    W tym: obecność komentarza wyciszającego '# noinspection PyPackageRequirements'
    przed importami biblioteki pytest, zapobiegając fałszywym ostrzeżeniom inspekcji IDE.
    """
    issues: list[str] = []
    if not tests_dir.is_dir():
        return issues

    for py_file in sorted(tests_dir.rglob("*.py")):
        rel = py_file.relative_to(repo_root).as_posix()
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError as e:
            issues.append(f"[FileReadError] {rel}: {e}")
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Wykrywanie import pytest lub from pytest import ...
            if (
                stripped == "import pytest"
                or stripped.startswith("import pytest ")
                or stripped.startswith("from pytest import")
            ):
                # Sprawdź poprzedzające niepuste linie pod kątem wyciszenia
                has_noinspect = False
                prev_idx = i - 1
                while prev_idx >= 0:
                    prev_line = lines[prev_idx].strip()
                    if not prev_line:
                        prev_idx -= 1
                        continue
                    if "noinspection PyPackageRequirements" in prev_line:
                        has_noinspect = True
                    break

                if not has_noinspect:
                    issues.append(
                        f"[PyPackageRequirements] {rel}:{i + 1}: 'import pytest' should be preceded by "
                        f"'# noinspection PyPackageRequirements' to prevent IDE inspection warnings"
                    )

    return issues


def run_ast_checks(repo_root: Path) -> list[str]:
    """Uruchamia wszystkie kontrole AST i konwencji na kodzie źródłowym i testach."""
    files: list[Path] = []
    for folder in ("log_viewer", "scripts", "dev-scripts"):
        target_dir = repo_root / folder
        if target_dir.is_dir():
            files.extend(p for p in sorted(target_dir.rglob("*.py")) if not p.name.startswith("ui_"))

    return (
        check_duplicated_code(files, repo_root)
        + check_dead_stores(files, repo_root)
        + check_ide_type_pitfalls(files, repo_root)
        + check_attributes_outside_init(files, repo_root)
        + check_shadowing_names(files, repo_root)
        + check_try_except_imports(files, repo_root)
        + check_test_conventions(repo_root / "tests", repo_root)
    )
