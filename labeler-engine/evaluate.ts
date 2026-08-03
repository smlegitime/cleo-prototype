/**
 * The labeling decision — `(subject, spec) -> labels that fire`.
 *
 * This is the ONLY implementation of that function. It is called from three places:
 *
 *   1. the preview UI, over the mock feed from `src/agent/lifecycle/preview_posts.py`, which is
 *      what a group looks at and votes to approve;
 *   2. the deployed labeler, over every post off Jetstream, feeding LabelerServer.createLabel;
 *   3. behavioral golden tests (`spec + post corpus -> expected labels`).
 *
 * "The preview and the live labeler behave identically" is therefore not a claim anyone
 * has to verify — there is one function and it cannot diverge from itself. Nothing in
 * here may import React, touch the DOM, or perform I/O; that is what keeps it shareable.
 *
 * Rules are in disjunctive normal form (see SpecRule): a label fires when ANY
 * include_group has ALL of its signals matching, unless an exclude_signal also matches.
 */

import type { SpecLabel, SpecSignal } from "./spec.js";

/** A signal with its matcher prepared once at spec-load time rather than per subject. */
export interface CompiledSignal {
  /** Compiled matcher for `pattern` signals. */
  re?: RegExp;
  /** Literal text for `keyword` signals, and for patterns that failed to compile. */
  kw?: string;
  /** Parsed predicate for `account` signals — evaluated against the subject's account, never its
   * text. Absent (so the signal never fires) when the value couldn't be parsed. */
  acct?: AccountPredicate;
  /** Human-readable name (`plain_name` if the model supplied one), used in match traces. */
  name: string;
}

export type CompiledGroup = CompiledSignal[];

export interface CompiledLabel {
  identifier: string;
  groups: CompiledGroup[];
  exclusions: CompiledSignal[];
}

/**
 * Author metadata an `account` signal is evaluated against. Fields mirror the grammar in
 * `signal_validation.py` (the Python authoring-side validator) — keep the two in lockstep. Any
 * field may be absent when the enrichment step (see corpus.py) couldn't resolve it; a predicate
 * over an absent field does not fire.
 */
export interface AccountTraits {
  account_age_days?: number | null;
  follower_count?: number | null;
  following_count?: number | null;
  post_count?: number | null;
  has_avatar?: boolean;
  has_description?: boolean;
}

/** A compiled `account` signal: one comparison `<field> <op> <threshold>`. */
export interface AccountPredicate {
  field: keyof AccountTraits;
  op: string;
  threshold: number | boolean;
}

/**
 * What a rule is evaluated against.
 *
 * `text` is always present. `account` carries author metadata for account-trait signals and is
 * absent for subjects that don't come with an author (the mock preview feed, for one) — an account
 * predicate over an absent `account` block does not fire, the same benign no-match as before.
 */
export interface Subject {
  text: string;
  account?: AccountTraits;
}

export interface EvalResult {
  /** Descriptions of each include_group that fired, e.g. "cortisol + detox". */
  fired: string[];
  /** Partially-satisfied AND-groups, for the preview's "why didn't this match?" trace. */
  near: { matched: string; missing: string }[];
  /** Set when a group fired but an exclusion suppressed it. */
  excluded?: { would: string; by: string };
}

/**
 * Keyword matching is case-insensitive substring containment.
 *
 * This is a real semantic choice and this is the single site that makes it: today the
 * keyword "art" fires on "heart". Switching to word-boundary matching means changing
 * this function and re-running the golden fixtures — nothing else.
 */
function matchesKeyword(kw: string, text: string): boolean {
  return text.toLowerCase().includes(kw.toLowerCase());
}

// Account-signal grammar — the runtime mirror of signal_validation.py's ACCOUNT_FIELDS /
// ACCOUNT_OPERATORS. The Python side validates at authoring time; this side evaluates at match
// time. They MUST agree on fields, operators, and boolean parsing (see the shared-fixture tests).
const NUMERIC_ACCOUNT_FIELDS = new Set([
  "account_age_days", "follower_count", "following_count", "post_count",
]);
const BOOLEAN_ACCOUNT_FIELDS = new Set(["has_avatar", "has_description"]);
const ACCOUNT_OPERATORS = new Set(["<", "<=", ">", ">=", "==", "!="]);

/** Parse an account signal's `"<field> <op> <threshold>"` value, or null if it doesn't conform. */
function parseAccountValue(value: string): AccountPredicate | null {
  const parts = value.trim().split(/\s+/);
  if (parts.length !== 3) return null;
  const [field, op, rawThreshold] = parts;
  if (!ACCOUNT_OPERATORS.has(op)) return null;
  if (NUMERIC_ACCOUNT_FIELDS.has(field)) {
    const n = Number(rawThreshold);
    if (Number.isNaN(n)) return null;
    return { field: field as keyof AccountTraits, op, threshold: n };
  }
  if (BOOLEAN_ACCOUNT_FIELDS.has(field)) {
    const t = rawThreshold.toLowerCase();
    if (t !== "true" && t !== "false") return null;
    if (op !== "==" && op !== "!=") return null; // boolean fields: only ==/!= (mirrors validator)
    return { field: field as keyof AccountTraits, op, threshold: t === "true" };
  }
  return null; // unknown field
}

function compare(a: number | boolean, op: string, b: number | boolean): boolean {
  switch (op) {
    case "==": return a === b;
    case "!=": return a !== b;
    case "<": return (a as number) < (b as number);
    case "<=": return (a as number) <= (b as number);
    case ">": return (a as number) > (b as number);
    case ">=": return (a as number) >= (b as number);
    default: return false;
  }
}

/** Evaluate an account predicate against a subject's traits. Absent traits / field => no fire. */
function testAccount(pred: AccountPredicate, traits: AccountTraits | undefined): boolean {
  if (!traits) return false;
  const actual = traits[pred.field];
  if (actual === undefined || actual === null) return false;
  return compare(actual, pred.op, pred.threshold);
}

export function compileSignal(sig: SpecSignal): CompiledSignal {
  const name = sig.plain_name || sig.value;
  // An unparseable account value compiles to a signal with no matcher, so it never fires (rather
  // than being mistaken for a keyword match on its literal text). Valid values are guaranteed by
  // signal_validation.py at authoring time; this guards stale/hand-edited specs.
  if (sig.type === "account") {
    const acct = parseAccountValue(sig.value);
    return acct ? { acct, name } : { name };
  }
  if (sig.type === "pattern") {
    try {
      return { re: new RegExp(sig.value, "i"), name };
    } catch {
      // A malformed pattern from the model shouldn't crash the caller — fall back to a
      // literal match on the raw value so at least something sensible happens.
      return { kw: sig.value, name };
    }
  }
  return { kw: sig.value, name };
}

/** Prepare one spec label for matching. Call once per spec load, not per subject. */
export function compileLabel(label: SpecLabel): CompiledLabel {
  const rule = label.rule;
  return {
    identifier: label.identifier,
    groups: (rule?.include_groups || []).map((g) => g.all_of.map(compileSignal)),
    exclusions: (rule?.exclude_signals || []).map(compileSignal),
  };
}

export function testSignal(sig: CompiledSignal, subject: Subject): boolean {
  if (sig.acct) return testAccount(sig.acct, subject.account);
  if (sig.kw) return matchesKeyword(sig.kw, subject.text);
  return sig.re ? sig.re.test(subject.text) : false;
}

/** Evaluate one label, returning the full trace (what fired, what nearly did, what was suppressed). */
export function evaluateLabel(label: CompiledLabel, subject: Subject): EvalResult {
  const fired: string[] = [];
  const near: { matched: string; missing: string }[] = [];
  for (const group of label.groups) {
    const hits = group.filter((s) => testSignal(s, subject));
    if (hits.length === group.length && group.length > 0) {
      fired.push(group.map((s) => s.name).join(" + "));
    } else if (hits.length > 0 && group.length > 1) {
      near.push({
        matched: hits.map((s) => s.name).join(", "),
        missing: group.filter((s) => !hits.includes(s)).map((s) => s.name).join(", "),
      });
    }
  }
  // Exclusions win: a matching exclude signal suppresses the label even if a group fired.
  if (fired.length) {
    const hit = label.exclusions.find((s) => testSignal(s, subject));
    if (hit) return { fired: [], near, excluded: { would: fired.join("; "), by: hit.name } };
  }
  return { fired, near };
}

/**
 * The production entry point: which labels apply to this subject.
 *
 * The deployed labeler calls this per post and emits one `createLabel` per identifier
 * returned. Traces are discarded here; use `evaluateLabel` when you need to explain a
 * decision (the preview does, and an audit log eventually will).
 */
export function evaluate(labels: CompiledLabel[], subject: Subject): string[] {
  return labels
    .filter((label) => evaluateLabel(label, subject).fired.length > 0)
    .map((label) => label.identifier);
}
