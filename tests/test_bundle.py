"""Tests for the bundle materializer (pure filesystem; writes into a tmp base_dir)."""

import json

from src.agent.lifecycle.bundle import (
    BUNDLE_FORMAT,
    MANIFEST_FILENAME,
    SPEC_FILENAME,
    bundle_dir_for,
    materialize_bundle,
)
from src.agent.spec import build_spec

# A design with one ruled label and one rule-less label, so rule_count and warnings are exercised.
LABELER_CONFIG = {
    "display_name": "Test Labeler",
    "description": "a labeler for tests",
    "labels": [
        {"identifier": "misinfo", "severity": "alert", "blurs": "content", "locales": []},
        {"identifier": "no_rule", "severity": "inform", "blurs": "none", "locales": []},
    ],
}
RULES = {
    "misinfo": {
        "include_groups": [{"all_of": [{"type": "keyword", "value": "cure", "plain_name": None}]}],
        "exclude_signals": [],
        "notes": None,
    }
}


def _spec():
    return build_spec(LABELER_CONFIG, RULES)


def test_materialize_writes_spec_and_manifest(tmp_path):
    spec = _spec()
    handle = materialize_bundle(spec, "chan-1", base_dir=tmp_path)

    bundle_dir = tmp_path / "chan-1" / spec["spec_id"].removeprefix("sha256:")
    assert bundle_dir.is_dir()
    assert handle["bundle_dir"] == str(bundle_dir)
    assert (bundle_dir / SPEC_FILENAME).exists()
    assert (bundle_dir / MANIFEST_FILENAME).exists()


def test_spec_file_roundtrips_to_same_spec_id(tmp_path):
    spec = _spec()
    materialize_bundle(spec, "chan-1", base_dir=tmp_path)
    written = json.loads((tmp_path / "chan-1" / spec["spec_id"].removeprefix("sha256:") / SPEC_FILENAME).read_text())
    assert written["spec_id"] == spec["spec_id"]


def test_manifest_shape_and_counts(tmp_path):
    spec = _spec()
    handle = materialize_bundle(spec, "chan-1", base_dir=tmp_path)
    manifest = json.loads(open(handle["manifest_path"]).read())

    assert manifest["bundle_format"] == BUNDLE_FORMAT
    assert manifest["channel_id"] == "chan-1"
    assert manifest["spec_id"] == spec["spec_id"]
    assert manifest["spec_file"] == SPEC_FILENAME
    assert manifest["label_count"] == 2
    assert manifest["rule_count"] == 1  # only 'misinfo' carries a rule
    assert isinstance(manifest["engine"]["present"], bool)
    assert manifest["engine"]["entry"].endswith("batch.js")
    # the rule-less label surfaces as a build_spec warning, carried into the manifest
    assert any("no_rule" in w for w in manifest["warnings"])


def test_directory_is_content_addressed_and_path_safe(tmp_path):
    spec = _spec()
    handle = materialize_bundle(spec, "chan-1", base_dir=tmp_path)
    # colon from 'sha256:...' must not appear in the on-disk directory name
    leaf = handle["bundle_dir"].rsplit("/", 1)[-1]
    assert ":" not in leaf
    assert bundle_dir_for("chan-1", spec["spec_id"], base_dir=tmp_path) == tmp_path / "chan-1" / leaf


def test_rematerialize_same_spec_is_idempotent_at_path(tmp_path):
    spec = _spec()
    h1 = materialize_bundle(spec, "chan-1", base_dir=tmp_path)
    h2 = materialize_bundle(spec, "chan-1", base_dir=tmp_path)
    assert h1["bundle_dir"] == h2["bundle_dir"]
    # no duplicate sibling directories were created for the same design
    assert len(list((tmp_path / "chan-1").iterdir())) == 1


def test_different_channels_isolate_bundles(tmp_path):
    spec = _spec()
    a = materialize_bundle(spec, "chan-a", base_dir=tmp_path)
    b = materialize_bundle(spec, "chan-b", base_dir=tmp_path)
    assert a["bundle_dir"] != b["bundle_dir"]
    assert (tmp_path / "chan-a").is_dir() and (tmp_path / "chan-b").is_dir()
