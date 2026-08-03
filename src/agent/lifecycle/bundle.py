"""
Bundle materializer. The `deploy`-stage entry step that turns an approved spec into an
on-disk, addressable payload the sandbox executor can run.

Per the deployment architecture: `labeler.spec.json` is the whole per-labeler payload, 
and the interpreter (labeler-engine) is shared, not copied into each bundle. 
"Materialize a bundle" therefore means: serialize the spec next to a small self-describing
manifest, in a directory keyed by the spec's content digest so the same approved design always
lands at the same path.

    bundles/<channel_id>/<spec-digest>/
        labeler.spec.json   # spec_to_json(spec) — the payload downstream stages read
        manifest.json        # metadata ABOUT the bundle (see BundleManifest)

The directory is content-addressed by `spec_id` (which excludes `generated_at`), so re-materializing
an unchanged design is idempotent at the path level and the sandbox executor can locate a bundle from
`(channel_id, spec_id)` alone via `bundle_dir_for`. This module does pure filesystem work — it does
not touch the graph, the network, or the interpreter; the caller builds the spec (see
`src/agent/spec.py::build_spec`) and hands it in.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from src.agent.spec import LabelerSpec, spec_to_json

# Bump when the on-disk bundle LAYOUT changes in a way the executor must branch on. Distinct from
# the spec's own `spec_version` (the shape of labeler.spec.json) and from `spec_id` (a design hash).
BUNDLE_FORMAT = "1.0"

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Where bundles are written. Overridable so tests and alternate deployments don't collide with the
# repo-root default. Per-channel runtime artifacts, gitignored like the checkpoint DB.
BUNDLES_DIR = Path(os.environ.get("LABELER_BUNDLES_DIR", str(_REPO_ROOT / "bundles")))

# The shared interpreter entry the deployed/sandbox runtime wraps. Recorded (not copied) in the
# manifest so a bundle is self-describing about which engine build it expects.
ENGINE_ENTRY_REL = "labeler-engine/dist/batch.js"

SPEC_FILENAME = "labeler.spec.json"
MANIFEST_FILENAME = "manifest.json"


class BundleManifest(TypedDict):
    bundle_format: str
    channel_id: str
    spec_id: str
    spec_version: str
    created_at: str # ISO-8601 UTC; when this bundle was materialized
    spec_file: str # relative name of the spec payload within the bundle dir
    engine: dict # {"entry": <repo-relative path>, "present": bool}
    label_count: int # total approved labels in the spec
    rule_count: int # labels carrying a rule (only these can fire)
    warnings: list[str] # carried from build_spec (rule-less labels, orphaned rules)


class BundleHandle(TypedDict):
    spec_id: str
    spec_version: str
    bundle_dir: str # absolute path to the materialized bundle directory
    spec_path: str # absolute path to labeler.spec.json
    manifest_path: str # absolute path to manifest.json
    created_at: str


def _safe_digest(spec_id: str) -> str:
    """Turn a `sha256:<hex>` spec_id into a filesystem-safe directory name.

    The colon in `sha256:...` is not portable in paths, so strip the algorithm prefix and use the
    hex digest alone. Falls back to a full sanitization for any unexpected shape.
    """
    if spec_id.startswith("sha256:"):
        return spec_id[len("sha256:"):]
    return spec_id.replace(":", "-").replace("/", "-")


def bundle_dir_for(channel_id: str, spec_id: str, *, base_dir: Path | None = None) -> Path:
    """Deterministic bundle directory for a `(channel_id, spec_id)` pair.

    Pure path derivation — does not touch the filesystem. The sandbox executor uses this to locate
    a bundle materialized earlier without re-deriving the layout convention.
    """
    root = base_dir if base_dir is not None else BUNDLES_DIR
    return root / channel_id / _safe_digest(spec_id)


def materialize_bundle(
    spec: LabelerSpec,
    channel_id: str,
    *,
    base_dir: Path | None = None,
) -> BundleHandle:
    """Write `spec` + a manifest into a content-addressed bundle directory and return a handle.

    Idempotent at the path level: re-materializing an unchanged design (same `spec_id`) targets the
    same directory and overwrites its files. The written `generated_at`/`created_at` timestamps will
    differ run-to-run, but the addressable identity (`spec_id`, the directory) does not — which is
    what lets downstream stages compare "is the deployed bundle the current design?".

    Does no network or interpreter work; the engine is referenced by path in the manifest, never
    copied. Raises OSError if the directory can't be created or written.
    """
    bundle_dir = bundle_dir_for(channel_id, spec["spec_id"], base_dir=base_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    labels = spec.get("labels") or []
    engine_entry = _REPO_ROOT / ENGINE_ENTRY_REL
    created_at = datetime.now(timezone.utc).isoformat()

    manifest: BundleManifest = {
        "bundle_format": BUNDLE_FORMAT,
        "channel_id": channel_id,
        "spec_id": spec["spec_id"],
        "spec_version": spec.get("spec_version", ""),
        "created_at": created_at,
        "spec_file": SPEC_FILENAME,
        "engine": {"entry": ENGINE_ENTRY_REL, "present": engine_entry.exists()},
        "label_count": len(labels),
        "rule_count": sum(1 for l in labels if l.get("rule")),
        "warnings": list(spec.get("warnings") or []),
    }

    spec_path = bundle_dir / SPEC_FILENAME
    manifest_path = bundle_dir / MANIFEST_FILENAME
    spec_path.write_text(spec_to_json(spec), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "spec_id": spec["spec_id"],
        "spec_version": manifest["spec_version"],
        "bundle_dir": str(bundle_dir),
        "spec_path": str(spec_path),
        "manifest_path": str(manifest_path),
        "created_at": created_at,
    }
