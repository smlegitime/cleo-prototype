"""Tests for the sandbox-run report's per-label breakdown.

Two properties matter more than the formatting itself: a label that matched NOTHING must be visible
(silently missing reads as "that label doesn't exist", and a rule that matches nothing is the
failure most worth surfacing), and the line must not degrade into an unreadable run as labels grow.
"""

from src.api.reporters import _plural, _run_breakdown


def test_matched_labels_render_inline_with_counts():
    assert _run_breakdown({"health_misinfo": 12, "scam_newbie": 3}) == (
        "• `health_misinfo` ×12 · `scam_newbie` ×3"
    )


def test_zero_match_labels_are_named_not_omitted():
    out = _run_breakdown({"health_misinfo": 12, "spam": 0, "harassment": 0})
    assert out.splitlines() == [
        "• `health_misinfo` ×12",
        "• No matches for: `spam`, `harassment`",
    ]


def test_all_labels_zero_says_so_and_lists_them():
    out = _run_breakdown({"spam": 0, "harassment": 0})
    assert out == "• No test post matched any label: `spam`, `harassment`."


def test_single_zero_label_reads_naturally():
    assert _run_breakdown({"spam": 0}) == "• No test post matched any label: `spam`."


def test_many_matched_labels_get_one_bullet_each():
    """Past a handful, the inline ' · ' run wraps at arbitrary points in a chat client."""
    per_label = {f"label_{i}": i + 1 for i in range(6)}
    lines = _run_breakdown(per_label).splitlines()
    assert len(lines) == 6
    assert lines[0] == "• `label_0` ×1"
    assert lines[-1] == "• `label_5` ×6"


def test_the_inline_threshold_is_the_boundary():
    four = _run_breakdown({f"l{i}": 1 for i in range(4)})
    five = _run_breakdown({f"l{i}": 1 for i in range(5)})
    assert len(four.splitlines()) == 1
    assert len(five.splitlines()) == 5


def test_mixed_many_matched_and_some_zero():
    per_label = {**{f"l{i}": 1 for i in range(5)}, "quiet": 0}
    lines = _run_breakdown(per_label).splitlines()
    assert len(lines) == 6  # 5 matched bullets + the no-match line
    assert lines[-1] == "• No matches for: `quiet`"


def test_legacy_summaries_without_zero_entries_still_render():
    """Runs recorded before the executor reported zeros have matched-only dicts."""
    assert _run_breakdown({"a": 2}) == "• `a` ×2"
    assert _run_breakdown({}) == "• No posts matched any label."


def test_plural_helper():
    assert _plural(1, "signed label record") == "1 signed label record"
    assert _plural(0, "signed label record") == "0 signed label records"
    assert _plural(3, "label") == "3 labels"
