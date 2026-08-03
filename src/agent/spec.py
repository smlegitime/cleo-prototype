"""
Labeler spec — the single serialized source of truth that ties the group's
approved design (labeler config + classification rules) to every downstream
stage: preview, bundle generation, sandbox deploy, provisioning, and maintenance.

The graph deliberates and produces two artifacts in `BrainstormingAgentState`:
`labeler_config` (a `LabelerDeclaration`) and `classification_rules`
(a `dict[label_identifier, ClassificationRule]`). Those live only in the
LangGraph checkpoint. `build_spec` folds them into one deterministic, JSON-
serializable document — `labeler.spec.json` — that leaves the graph and is
read by:

  * the preview UI (renders each label's rule against sample posts),
  * the code generator (compiles the matching logic),
  * the deployer (ships whatever codegen produced),
  * the maintenance stage (diffs the deployed spec_id against the current one
    to know whether a redeploy is owed).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal, TypedDict

from src.agent.state import (
    ClassificationRule,
    ClassificationSignal,
    LabelerDeclaration,
    LabelValueDefinition,
    Locale,
    SignalGroup,
)

# Bump when the spec's shape changes in a way downstream consumers must branch on. This
# is the schema version, different from the `spec_id` (which is the content hash of
# one particular labeler's design).
SPEC_VERSION = "1.0"

# Defaults mirror the field defaults on LabelValueDefinition in state.py.
_DEFAULT_BLURS: Literal["content", "media", "none"] = "none"
_DEFAULT_SEVERITY: Literal["alert", "inform", "none"] = "inform"
_DEFAULT_DEFAULT_SETTING: Literal["hide", "warn", "ignore"] = "warn"


# ---- Spec shape ----
# Each label carries its display settings AND its rule.

class SpecSignal(TypedDict):
    type: Literal["keyword", "pattern", "account"]
    value: str
    plain_name: str | None


class SpecGroup(TypedDict):
    all_of: list[SpecSignal]


class SpecRule(TypedDict):
    include_groups: list[SpecGroup]
    exclude_signals: list[SpecSignal]
    notes: str | None


class SpecLabel(TypedDict):
    identifier: str
    severity: Literal["alert", "inform", "none"]
    blurs: Literal["content", "media", "none"]
    default_setting: Literal["hide", "warn", "ignore"]
    locales: list[Locale]
    rule: SpecRule | None  # None = label approved but no rule derived yet


class SpecLabeler(TypedDict):
    display_name: str | None
    description: str | None


class LabelerSpec(TypedDict):
    spec_version: str
    spec_id: str         # "sha256:..." content hash, excludes generated_at + warnings
    generated_at: str    # ISO-8601 UTC; informational, NOT part of spec_id
    labeler: SpecLabeler
    labels: list[SpecLabel]
    warnings: list[str]  # diagnostics, NOT part of spec_id


# ---- Serializer functions ----

def _signal(sig: ClassificationSignal) -> SpecSignal:
    return {
        "type": sig["type"],
        "value": sig["value"],
        "plain_name": sig.get("plain_name"),
    }


def _rule(rule: ClassificationRule) -> SpecRule:
    return {
        "include_groups": [
            {"all_of": [_signal(s) for s in (group.get("all_of") or [])]}
            for group in (rule.get("include_groups") or [])
        ],
        "exclude_signals": [_signal(s) for s in (rule.get("exclude_signals") or [])],
        "notes": rule.get("notes"),
    }


def _label(defn: LabelValueDefinition, rule: ClassificationRule | None) -> SpecLabel:
    return {
        "identifier": defn.get("identifier") or "",
        "severity": defn.get("severity") or _DEFAULT_SEVERITY,
        "blurs": defn.get("blurs") or _DEFAULT_BLURS,
        "default_setting": defn.get("default_setting") or _DEFAULT_DEFAULT_SETTING,
        "locales": list(defn.get("locales") or []),
        "rule": _rule(rule) if rule else None,
    }


def _content_hash(labeler: SpecLabeler, labels: list[SpecLabel]) -> str:
    """Stable hash of the *design*, independent of dict ordering or timestamp.

    Only the labeler block and labels (with their rules) are inputs tothe hash, never
    `generated_at`, `spec_id`, or `warnings`. Two runs over the same approved
    design therefore produce the same `spec_id`, which is what lets the
    maintenance stage detect "nothing changed, no redeploy needed".
    """
    canonical = json.dumps(
        {"labeler": labeler, "labels": labels},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_spec(
    labeler_config: LabelerDeclaration | None,
    classification_rules: dict[str, ClassificationRule] | None,
) -> LabelerSpec:
    """Fold the group's approved config + rules into one deterministic spec.

    Pure function. Pass the values straight out of graph state:

        state = graph.get_state(config).values
        spec = build_spec(state.get("labeler_config"), state.get("classification_rules"))

    Rules are joined to labels by `label_identifier`. Mismatches don't surface as `warnings` 
    instead of raising so the caller (or the preview) can show them
    rather than silently dropping a label or a rule:
      * a label with no rule yet -> label emitted with rule=None
      * a rule for an unknown label id  -> warning, rule omitted from output
    """
    config = labeler_config or {}
    rules = dict(classification_rules or {})
    label_defs = list(config.get("labels") or [])

    warnings: list[str] = []
    labels: list[SpecLabel] = []
    seen_ids: set[str] = set()

    for defn in label_defs:
        ident = defn.get("identifier") or ""
        if ident:
            seen_ids.add(ident)
        rule = rules.get(ident)
        if rule is None:
            warnings.append(f"label '{ident or '(unnamed)'}' has no classification rule yet")
        labels.append(_label(defn, rule))

    for rule_id in rules:
        if rule_id not in seen_ids:
            warnings.append(f"rule references unknown label '{rule_id}'. Omitted from spec")

    labeler: SpecLabeler = {
        "display_name": config.get("display_name"),
        "description": config.get("description"),
    }

    return {
        "spec_version": SPEC_VERSION,
        "spec_id": _content_hash(labeler, labels),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labeler": labeler,
        "labels": labels,
        "warnings": warnings,
    }


def spec_to_json(spec: LabelerSpec, *, indent: int | None = 2) -> str:
    """Render a spec as JSON text for writing to `labeler.spec.json` or an API body."""
    return json.dumps(spec, indent=indent, ensure_ascii=False)
