"""Classify file changes so reviewers know what can be skimmed and what needs judgment.

For Python files with both versions available we *prove* a change is formatting-only,
docstring-only, or a consistent rename by comparing ASTs. That is deliberately not an
LLM call: a proof is cheaper and more trustworthy than a guess.
"""
from __future__ import annotations

import ast
import builtins
import copy
import re
from pathlib import PurePosixPath

from .diffparse import changed_line_texts
from .models import SKIMMABLE, ChangeKind, FileChange, PullRequest

_LOCKFILES = {
    "poetry.lock", "uv.lock", "pipfile.lock", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "cargo.lock", "gemfile.lock", "go.sum", "composer.lock",
}
_GENERATED_RE = re.compile(r"(_pb2(_grpc)?\.pyi?$|\.min\.(js|css)$|\.snap$|/generated/|\.generated\.)")
_DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
_CONFIG_EXT = {".yml", ".yaml", ".toml", ".json", ".ini", ".cfg", ".env"}
_CONFIG_NAMES = {"dockerfile", "makefile", ".gitignore", "setup.cfg", "tox.ini"}
_BUILTINS = set(dir(builtins))


def path_kind(path: str) -> ChangeKind | None:
    """Classify purely from the file path, or None if it looks like ordinary code."""
    p = PurePosixPath(path.lower())
    name = p.name
    if name in _LOCKFILES or _GENERATED_RE.search("/" + path.lower()):
        return ChangeKind.GENERATED
    if re.match(r"requirements.*\.txt$", name):
        return ChangeKind.CONFIG
    if p.suffix in _DOC_EXT or "docs" in p.parts:
        return ChangeKind.DOCS
    if (
        "tests" in p.parts or "test" in p.parts
        or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
    ):
        return ChangeKind.TEST
    if p.suffix in _CONFIG_EXT or name in _CONFIG_NAMES or ".github" in p.parts:
        return ChangeKind.CONFIG
    return None


# ---------- Python AST analysis ----------

_ID_FIELDS = {
    ast.Name: "id", ast.arg: "arg", ast.FunctionDef: "name",
    ast.AsyncFunctionDef: "name", ast.ClassDef: "name",
}


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _skeleton(tree: ast.AST) -> str:
    t = copy.deepcopy(tree)
    for n in ast.walk(t):
        f = _ID_FIELDS.get(type(n))
        if f:
            setattr(n, f, "_")
    return ast.dump(t)


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
    return names


def _consistent_rename(old: ast.AST, new: ast.AST) -> bool:
    """True if `new` is `old` with identifiers renamed by a consistent bijection."""
    pairs: dict[str, str] = {}
    for x, y in zip(ast.walk(old), ast.walk(new)):
        f = _ID_FIELDS.get(type(x))
        if not f:
            continue
        ox, oy = getattr(x, f), getattr(y, f)
        if pairs.setdefault(ox, oy) != oy:
            return False
    if len(set(pairs.values())) != len(pairs):
        return False
    renamed = {o for o, n in pairs.items() if o != n}
    if not renamed:
        return False
    protected = _BUILTINS | _imported_names(old) | _imported_names(new)
    return not (renamed & protected) and not ({pairs[r] for r in renamed} & protected)


def _symbol_map(tree: ast.Module) -> dict[str, str]:
    """Qualified name -> AST dump for top-level defs/classes and their methods."""
    out: dict[str, str] = {}
    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = ast.dump(node)
        elif isinstance(node, ast.ClassDef):
            out[node.name] = ast.dump(node)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = ast.dump(sub)
        else:
            module_level.append(ast.dump(node))
    out["module-level code"] = "|".join(module_level)
    return out


def changed_symbols(old_src: str | None, new_src: str | None) -> list[str]:
    """Human-readable list of functions/classes that were added, removed or modified."""
    try:
        old_map = _symbol_map(ast.parse(old_src)) if old_src else {}
        new_map = _symbol_map(ast.parse(new_src)) if new_src else {}
    except SyntaxError:
        return []
    result: list[str] = []
    for name in sorted(set(old_map) | set(new_map)):
        o, n = old_map.get(name), new_map.get(name)
        if o == n:
            continue
        if name == "module-level code" and not (o or n):
            continue
        state = "added" if o is None else "removed" if n is None else "modified"
        result.append(f"{name} ({state})")
    # A class marked modified purely because a method changed is noise; keep methods only.
    methods = {s.split(" ")[0] for s in result if "." in s.split(" ")[0]}
    owners = {m.split(".")[0] for m in methods}
    return [s for s in result if not (s.split(" ")[0] in owners and "(modified)" in s)]


def _classify_python(change: FileChange) -> tuple[ChangeKind, str] | None:
    if change.old_source is None or change.new_source is None:
        return None
    try:
        old, new = ast.parse(change.old_source), ast.parse(change.new_source)
    except SyntaxError:
        return None
    if ast.dump(old) == ast.dump(new):
        return ChangeKind.FORMATTING, "AST identical: whitespace/comments only (verified)"
    if ast.dump(_strip_docstrings(copy.deepcopy(old))) == ast.dump(_strip_docstrings(copy.deepcopy(new))):
        return ChangeKind.DOCS, "Only docstrings changed (verified by AST)"
    o2, n2 = _strip_docstrings(copy.deepcopy(old)), _strip_docstrings(copy.deepcopy(new))
    if _skeleton(o2) == _skeleton(n2) and _consistent_rename(o2, n2):
        return ChangeKind.RENAME, "Consistent identifier rename (verified by AST; check callers elsewhere)"
    return None


def _collapse(lines: list[str]) -> str:
    return " ".join(" ".join(line.split()) for line in lines).strip()


def classify_file(change: FileChange) -> FileChange:
    """Fill in kind/reason/symbols on a FileChange (mutates and returns it)."""
    pk = path_kind(change.path)
    if pk is ChangeKind.GENERATED:
        change.kind, change.reason = pk, "Lockfile or generated artifact"
        return change

    if change.path.endswith(".py"):
        verdict = _classify_python(change)
        if verdict:
            change.kind, change.reason = verdict
            return change
        change.symbols = changed_symbols(change.old_source, change.new_source)

    added, removed = changed_line_texts(change.patch)
    if (added or removed) and _collapse(added) == _collapse(removed):
        change.kind, change.reason = ChangeKind.FORMATTING, "Same text after collapsing whitespace"
        return change

    if pk is not None:
        change.kind, change.reason = pk, f"Path indicates {pk.value}"
        return change

    change.kind = ChangeKind.BEHAVIORAL
    change.reason = (
        "Code changed" if change.path.endswith(".py") and change.old_source is not None
        else "Code changed (no semantic parser or source available; treated as behavioral)"
    )
    return change


def classify_pr(pr: PullRequest) -> PullRequest:
    for f in pr.files:
        classify_file(f)
    return pr


def skim_stats(files: list[FileChange]) -> tuple[int, int]:
    """Return (skimmable_changed_lines, total_changed_lines)."""
    total = sum(f.changed_lines for f in files)
    skim = sum(f.changed_lines for f in files if f.kind in SKIMMABLE)
    return skim, total
