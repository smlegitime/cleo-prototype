"""Offline tests for spec-agnostic query derivation + account enrichment (no network)."""

from unittest.mock import MagicMock, patch

from src.agent.lifecycle.corpus import (
    _account_traits,
    enrich_accounts,
    search_posts,
    spec_uses_account_signals,
)
from src.agent.lifecycle.queries import corpus_key, derive_queries


def _sig(type_, value, plain=None):
    return {"type": type_, "value": value, "plain_name": plain}


def _label(identifier, include_groups=None, exclude=None, name=None):
    return {
        "identifier": identifier,
        "severity": "alert",
        "blurs": "none",
        "default_setting": "warn",
        "locales": [{"lang": "en", "name": name, "description": None}] if name else [],
        "rule": None if include_groups is None else {
            "include_groups": include_groups,
            "exclude_signals": exclude or [],
            "notes": None,
        },
    }


def _spec(labels, display_name=None):
    return {
        "spec_version": "1",
        "spec_id": "sha256:test",
        "generated_at": "2026-07-23T00:00:00Z",
        "labeler": {"display_name": display_name, "description": None},
        "labels": labels,
        "warnings": [],
    }


def _qs(queries):
    return {q["q"] for q in queries}


def test_keyword_and_hashtag_signals_become_trigger_queries():
    spec = _spec([_label("misinfo", [{"all_of": [_sig("keyword", "chelation")]},
                                      {"all_of": [_sig("keyword", "#curedmyautism")]}])])
    qs = derive_queries(spec)
    assert {"chelation", "#curedmyautism"} <= _qs(qs)
    assert all(q["bucket"] == "trigger" for q in qs if q["q"] in {"chelation", "#curedmyautism"})


def test_pattern_mines_literal_and_never_emits_raw_regex():
    spec = _spec([_label("x", [{"all_of": [_sig("pattern", r"\bcure\b", "a cure word")]}])])
    qs = _qs(derive_queries(spec))
    assert "cure" in qs                 # mined literal, not the plain_name
    assert "a cure word" not in qs      # plain_name only used as a fallback
    assert r"\bcure\b" not in qs        # raw regex is never a query


def test_pattern_mines_alternation_branches():
    pat = r"\b(dm\s+me|link\s+in\s+(bio|profile)|shop\s+now|affiliate)\b"
    spec = _spec([_label("x", [{"all_of": [_sig("pattern", pat, "sales pitch")]}])])
    qs = _qs(derive_queries(spec))
    assert {"dm me", "shop now", "affiliate"} <= qs
    assert "link in bio" in qs and "link in profile" in qs  # nested branch expands


def test_pattern_mines_mandatory_spine_through_optional_obfuscation():
    # The obfuscated slur pattern: optional [-_*] separators are skipped, letters recovered.
    pat = r"\br[-_*]?e[-_*]?t[-_*]?a[-_*]?r[-_*]?d(s|ed)?\b"
    spec = _spec([_label("x", [{"all_of": [_sig("pattern", pat, "the r-slur")]}])])
    qs = _qs(derive_queries(spec))
    assert "retard" in qs
    assert "the r-slur" not in qs


def test_pattern_whitespace_becomes_single_space():
    spec = _spec([_label("x", [{"all_of": [_sig("pattern", r"chlorine\s+dioxide", "cd")]}])])
    assert "chlorine dioxide" in _qs(derive_queries(spec))


def test_pure_scaffold_pattern_falls_back_to_plain_name():
    # All-stopword alternation (harassment scaffold) mines nothing contentful -> plain_name used.
    pat = r"\b(you'?re|they'?re|he'?s|she'?s|what|such)\s+(a\s+)?"
    spec = _spec([_label("x", [{"all_of": [_sig("pattern", pat, "attacking language")]}])])
    qs = _qs(derive_queries(spec))
    assert "attacking language" in qs
    assert "what" not in qs and "such" not in qs


def test_account_signals_are_not_searchable():
    spec = _spec([_label("x", [{"all_of": [_sig("account", "account_age_days < 30", "a new account")]}])])
    # account traits never appear in post text -> no trigger query from them
    assert not any(q["bucket"] == "trigger" for q in derive_queries(spec))


def test_exclude_signals_included_as_triggers():
    spec = _spec([_label("x", [{"all_of": [_sig("keyword", "detox")]}],
                         exclude=[_sig("keyword", "satire")])])
    qs = _qs(derive_queries(spec))
    assert "detox" in qs and "satire" in qs


def test_labeler_and_label_names_become_context_queries():
    spec = _spec([_label("x", [{"all_of": [_sig("keyword", "detox")]}], name="Health Misinfo")],
                 display_name="Wellness Watch")
    ctx = {q["q"] for q in derive_queries(spec) if q["bucket"] == "context"}
    assert {"Wellness Watch", "Health Misinfo"} <= ctx


def test_dedup_is_case_insensitive_and_trigger_wins():
    spec = _spec([_label("x", [{"all_of": [_sig("keyword", "Detox")]}], name="detox")],
                 display_name=None)
    qs = derive_queries(spec)
    detox = [q for q in qs if q["q"].lower() == "detox"]
    assert len(detox) == 1
    assert detox[0]["bucket"] == "trigger"  # trigger seen first, context dupe dropped


def test_overlong_and_tiny_queries_are_dropped():
    spec = _spec([_label("x", [{"all_of": [
        _sig("keyword", "a"),  # too short
        _sig("keyword", "one two three four five six seven"),  # too many words
        _sig("keyword", "valid term"),
    ]}])])
    qs = _qs(derive_queries(spec))
    assert "valid term" in qs
    assert "a" not in qs
    assert "one two three four five six seven" not in qs


def test_ruleless_label_yields_no_trigger_queries():
    spec = _spec([_label("approved_but_no_rule", include_groups=None, name="Some Label")])
    qs = derive_queries(spec)
    assert not any(q["bucket"] == "trigger" for q in qs)
    assert "Some Label" in _qs(qs)  # still contributes a context query


def test_corpus_key_is_stable_across_rule_restructuring_with_same_terms():
    # Same searchable terms, different DNF structure -> same corpus key (posts can be reused).
    flat = _spec([_label("x", [{"all_of": [_sig("keyword", "detox")]},
                               {"all_of": [_sig("keyword", "chelation")]}])])
    grouped = _spec([_label("x", [{"all_of": [_sig("keyword", "chelation"), _sig("keyword", "detox")]}])])
    assert corpus_key(flat) == corpus_key(grouped)


def test_corpus_key_changes_when_terms_change():
    a = _spec([_label("x", [{"all_of": [_sig("keyword", "detox")]}])])
    b = _spec([_label("x", [{"all_of": [_sig("keyword", "bleach")]}])])
    assert corpus_key(a) != corpus_key(b)


# --- account-trait enrichment ---

def test_spec_uses_account_signals_detects_include_exclude_and_none():
    inc = _spec([_label("x", [{"all_of": [_sig("account", "follower_count < 5")]}])])
    exc = _spec([_label("x", [{"all_of": [_sig("keyword", "detox")]}],
                         exclude=[_sig("account", "has_avatar == false")])])
    none = _spec([_label("x", [{"all_of": [_sig("keyword", "detox")]}])])
    assert spec_uses_account_signals(inc)
    assert spec_uses_account_signals(exc)
    assert not spec_uses_account_signals(none)


def test_account_traits_derived_from_profile():
    prof = {"createdAt": "2020-01-01T00:00:00Z", "followersCount": 3, "followsCount": 100,
            "postsCount": 7, "avatar": "https://x/a.jpg", "description": "hi"}
    t = _account_traits(prof)
    assert (t["follower_count"], t["following_count"], t["post_count"]) == (3, 100, 7)
    assert t["has_avatar"] is True and t["has_description"] is True
    assert isinstance(t["account_age_days"], int) and t["account_age_days"] > 1000


def test_account_traits_absent_fields_are_none_or_false():
    t = _account_traits({"description": "   "})   # blank bio, no avatar, no createdAt/counts
    assert t["has_avatar"] is False and t["has_description"] is False
    assert t["account_age_days"] is None
    assert t["follower_count"] is None and t["post_count"] is None


def _apost(handle):
    return {"handle": handle, "text": "t", "account": None}


def test_enrich_accounts_populates_by_handle():
    posts = [_apost("a.bsky.social"), _apost("b.bsky.social"), _apost(None)]
    profiles = [
        {"handle": "a.bsky.social", "followersCount": 2, "createdAt": "2020-01-01T00:00:00Z"},
        {"handle": "b.bsky.social", "followersCount": 9},
    ]
    with patch("src.agent.lifecycle.corpus.get_profiles", return_value=profiles) as gp:
        enrich_accounts(posts, access_jwt="jwt", client=MagicMock())
    gp.assert_called_once()
    assert posts[0]["account"]["follower_count"] == 2
    assert posts[1]["account"]["follower_count"] == 9
    assert posts[2]["account"] is None            # handle-less post stays unenriched


def test_enrich_accounts_tolerates_failed_batch():
    posts = [_apost("a.bsky.social")]
    with patch("src.agent.lifecycle.corpus.get_profiles", side_effect=RuntimeError("500")):
        enrich_accounts(posts, access_jwt="jwt", client=MagicMock())
    assert posts[0]["account"] is None            # logged + skipped, not crashed


# --- corpus is restricted to English (no language signal, so keep cross-language FPs out of testing) ---

def _mock_client():
    client = MagicMock()
    client.get.return_value = MagicMock(json=lambda: {"posts": []})
    return client


def test_search_posts_defaults_to_english():
    client = _mock_client()
    search_posts("detox", access_jwt="jwt", client=client)
    assert client.get.call_args.kwargs["params"].get("lang") == "en"


def test_search_posts_passes_explicit_lang():
    client = _mock_client()
    search_posts("detox", access_jwt="jwt", client=client, lang="fr")
    assert client.get.call_args.kwargs["params"]["lang"] == "fr"


def test_search_posts_omits_lang_when_falsy():
    client = _mock_client()
    search_posts("detox", access_jwt="jwt", client=client, lang="")
    assert "lang" not in client.get.call_args.kwargs["params"]
