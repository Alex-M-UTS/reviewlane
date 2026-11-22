from reviewlane.classifier import classify_file, classify_pr, path_kind, skim_stats
from reviewlane.diffparse import changed_line_texts
from reviewlane.models import ChangeKind, FileChange, PullRequest

PATCH = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n"


def mk(old, new, path="app/core.py", patch=PATCH):
    return classify_file(FileChange(path=path, old_source=old, new_source=new, patch=patch, additions=1, deletions=1))


def test_formatting_only_is_proven_by_ast():
    c = mk("def f(a,b):\n  return a+b\n", "def f(a, b):\n    # explain\n    return a + b\n")
    assert c.kind is ChangeKind.FORMATTING


def test_docstring_only():
    c = mk("def f(a):\n    return a\n", "def f(a):\n    '''Return a.'''\n    return a\n")
    assert c.kind is ChangeKind.DOCS


def test_consistent_rename():
    c = mk("def f(a):\n    return a + 1\n", "def g(x):\n    return x + 1\n")
    assert c.kind is ChangeKind.RENAME


def test_rename_that_swaps_two_names_is_behavioral():
    # a->b and b->a is a consistent bijection structurally, but here it changes semantics
    # only if names collide with different roles; this case is a pure swap and must not be
    # reported as a one-sided rename when the structure differs.
    c = mk("def f(a, b):\n    return a - b\n", "def f(a, b):\n    return b - a\n")
    assert c.kind is ChangeKind.BEHAVIORAL


def test_renaming_a_builtin_is_not_a_safe_rename():
    c = mk("def f(x):\n    return len(x)\n", "def f(x):\n    return sum(x)\n")
    assert c.kind is ChangeKind.BEHAVIORAL


def test_behavioral_change_lists_symbols():
    c = mk("def f(a):\n    return a + 1\n\ndef g():\n    return 1\n",
           "def f(a):\n    return a + 2\n\ndef g():\n    return 1\n")
    assert c.kind is ChangeKind.BEHAVIORAL
    assert c.symbols == ["f (modified)"]


def test_added_and_removed_symbols():
    c = mk("def a():\n    pass\n", "def b():\n    pass\n\ndef c():\n    pass\n")
    assert "a (removed)" in c.symbols and "b (added)" in c.symbols


def test_path_kinds():
    assert path_kind("poetry.lock") is ChangeKind.GENERATED
    assert path_kind("tests/test_x.py") is ChangeKind.TEST
    assert path_kind("README.md") is ChangeKind.DOCS
    assert path_kind(".github/workflows/ci.yml") is ChangeKind.CONFIG
    assert path_kind("requirements-dev.txt") is ChangeKind.CONFIG
    assert path_kind("src/app.py") is None


def test_whitespace_collapse_without_sources():
    patch = "@@ -1,2 +1,1 @@\n-const a =\n-   1;\n+const a = 1;\n"
    c = classify_file(FileChange(path="web/app.js", patch=patch, additions=1, deletions=2))
    assert c.kind is ChangeKind.FORMATTING


def test_unparseable_python_falls_back_to_behavioral():
    c = mk("def f(:\n", "def f(a):\n    return a\n")
    assert c.kind is ChangeKind.BEHAVIORAL


def test_removed_line_starting_with_dashes_is_not_a_header():
    patch = "--- a/q.sql\n+++ b/q.sql\n@@ -1 +1 @@\n--- old comment\n+-- new comment\n"
    added, removed = changed_line_texts(patch)
    assert removed == ["-- old comment"] and added == ["-- new comment"]


def test_skim_stats():
    pr = PullRequest(files=[
        FileChange(path="a.py", additions=5, deletions=5, kind=ChangeKind.FORMATTING),
        FileChange(path="b.py", additions=10, deletions=0, kind=ChangeKind.BEHAVIORAL),
    ])
    assert skim_stats(pr.files) == (10, 20)
    assert classify_pr(PullRequest()).files == []
