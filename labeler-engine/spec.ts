/**
 * TypeScript mirror of `labeler.spec.json` — the document produced by
 * `src/agent/spec.py::build_spec` once a group has approved its labels and rules.
 *
 * Keep these types in lockstep with the `Spec*` TypedDicts in that module; it is the
 * authoritative definition. `SPEC_VERSION` there is bumped whenever the shape changes
 * in a way consumers must branch on.
 */

export type Severity = "alert" | "inform" | "none";
export type Blurs = "content" | "media" | "none";
export type DefaultSetting = "hide" | "warn" | "ignore";
export type SignalType = "keyword" | "pattern" | "account";

export interface SpecSignal {
  type: SignalType;
  value: string;
  plain_name: string | null;
}

/** One OR-branch of a rule. Every signal in `all_of` must match (AND). */
export interface SpecGroup {
  all_of: SpecSignal[];
}

/** Rules are in disjunctive normal form: OR over `include_groups`, each an AND. */
export interface SpecRule {
  include_groups: SpecGroup[];
  exclude_signals: SpecSignal[];
  notes: string | null;
}

export interface SpecLocale {
  lang: string | null;
  name: string | null;
  description: string | null;
}

export interface SpecLabel {
  identifier: string;
  severity: Severity;
  blurs: Blurs;
  default_setting: DefaultSetting;
  locales: SpecLocale[];
  /** null = the group approved this label but no rule has been derived yet. */
  rule: SpecRule | null;
}

export interface LabelerSpec {
  spec_version: string;
  /** "sha256:..." content hash of the design; excludes generated_at and warnings. */
  spec_id: string;
  generated_at: string;
  labeler: { display_name: string | null; description: string | null };
  labels: SpecLabel[];
  /** Diagnostics from build_spec (orphaned rules, rule-less labels). Not matching input. */
  warnings: string[];
}
