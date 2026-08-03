import { useEffect, useMemo, useState, type ReactNode } from "react";
import "./LabelerPreview.css";

/**
 * Labeler "preview" stage — a mock, interactive simulation of the group's designed
 * labeler running against sample posts, before provisioning. Group members flip each
 * label's settings (severity / blur behavior / subscriber default) and watch the feed
 * re-label live.
 *
 * The labels + rules are NOT hardcoded: they are fetched from the backend spec endpoint
 * (GET /labeler-spec/<channel_id>, served from src/agent/spec.py::build_spec) and compiled
 * into the in-browser matcher below. The sample posts (POSTS) remain fixtures. In production
 * CLEO evaluates posts on its own server from the same spec — the preview and the live
 * labeler share one source of truth.
 */

const API_BASE = (import.meta.env.VITE_AI_ASSISTANT_URL as string | undefined) ?? "";

type Severity = "alert" | "inform" | "none";
type Blurs = "content" | "media" | "none";
type DefaultSetting = "hide" | "warn" | "ignore";

// ---- Spec shape (mirrors LabelerSpec in src/agent/spec.py) ----
type SignalType = "keyword" | "pattern" | "account";
interface SpecSignal { type: SignalType; value: string; plain_name: string | null }
interface SpecGroup { all_of: SpecSignal[] }
interface SpecRule { include_groups: SpecGroup[]; exclude_signals: SpecSignal[]; notes: string | null }
interface SpecLocale { lang: string | null; name: string | null; description: string | null }
interface SpecLabel {
  identifier: string;
  severity: Severity;
  blurs: Blurs;
  default_setting: DefaultSetting;
  locales: SpecLocale[];
  rule: SpecRule | null;
}
interface LabelerSpec {
  spec_version: string;
  spec_id: string;
  labeler: { display_name: string | null; description: string | null };
  labels: SpecLabel[];
  warnings: string[];
}

// ---- Compiled, in-browser matcher shapes ----
interface Signal {
  re?: RegExp;
  kw?: string;
  account?: boolean; // account-trait signal — can't be evaluated against sample post text
  name: string;
}
type Group = Signal[];

interface LabelSettings {
  blurs: Blurs;
  severity: Severity;
  default: DefaultSetting;
}

interface LabelDef {
  identifier: string;
  name: string;
  desc: string;
  catch: { txt: string; and?: boolean }[];
  excludeTxt: string[];
  note: string;
  settings: LabelSettings;
  groups: Group[];
  exclusions: Signal[];
}

function humanize(identifier: string): string {
  return identifier.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function localeText(label: SpecLabel): { name: string; desc: string } {
  const loc = label.locales.find((l) => l.lang === "en") || label.locales[0];
  return { name: loc?.name || humanize(label.identifier), desc: loc?.description || "" };
}

function compileSignal(sig: SpecSignal): Signal {
  const name = sig.plain_name || sig.value;
  if (sig.type === "account") return { account: true, name };
  if (sig.type === "pattern") {
    try {
      return { re: new RegExp(sig.value, "i"), name };
    } catch {
      // A malformed pattern from the model shouldn't crash the preview — fall back to a
      // literal match on the raw value so at least something sensible renders.
      return { kw: sig.value, name };
    }
  }
  return { kw: sig.value, name };
}

// Build the human-readable "Fires on" bullets from the DNF include_groups. Each group is one
// OR-branch; multiple signals in a group are AND'd (rendered as indented "＋" continuation rows).
function deriveCatch(groups: SpecGroup[]): { txt: string; and?: boolean }[] {
  const out: { txt: string; and?: boolean }[] = [];
  for (const g of groups) {
    g.all_of.forEach((sig, i) => {
      const base = sig.plain_name || sig.value;
      const acct = sig.type === "account" ? " · account trait (not simulated here)" : "";
      out.push({ txt: (i === 0 ? "" : "+ ") + base + acct, and: i > 0 });
    });
  }
  return out;
}

function specToLabel(label: SpecLabel): LabelDef {
  const { name, desc } = localeText(label);
  const rule = label.rule;
  return {
    identifier: label.identifier,
    name,
    desc,
    catch: deriveCatch(rule?.include_groups || []),
    excludeTxt: (rule?.exclude_signals || []).map((s) => s.plain_name || s.value),
    note: rule?.notes || "",
    settings: { blurs: label.blurs, severity: label.severity, default: label.default_setting },
    groups: (rule?.include_groups || []).map((g) => g.all_of.map(compileSignal)),
    exclusions: (rule?.exclude_signals || []).map(compileSignal),
  };
}

interface Post {
  id: string;
  name: string;
  handle: string;
  hue: string;
  t: string;
  text: string;
  media?: boolean;
  mediaHue?: string;
  likes: number;
  rt: number;
  rp: number;
}

// Content fields returned by GET /preview-posts (src/agent/preview_posts.py). The visual fields
// (hue, timestamp, engagement) are synthesized on the frontend by hydratePost.
interface GenPost {
  name: string;
  handle: string;
  text: string;
  media: boolean;
}

const HUES = [
  "#6b7280", "#c2703d", "#5b54e8", "#2e9e78", "#b8478f", "#3f7bbf",
  "#4aa3a0", "#d98b2b", "#7a6cc4", "#5faa3c", "#c85a7a", "#3a8fb0",
];

function hydratePost(p: GenPost, i: number): Post {
  const seed = [...p.handle].reduce((a, c) => a + c.charCodeAt(0), i * 17 + 3);
  return {
    id: `g${i}`,
    name: p.name,
    handle: p.handle,
    text: p.text,
    media: p.media,
    hue: HUES[i % HUES.length],
    mediaHue: HUES[(i + 4) % HUES.length],
    t: `${i + 1}h`,
    likes: (seed * 7) % 500,
    rt: (seed * 3) % 120,
    rp: (seed * 5) % 90,
  };
}

// Static fallback feed, used only if generation fails or returns nothing so the preview never
// breaks. Authored to exercise every rule branch: direct hits, near-misses, and clean posts.
const FALLBACK_POSTS: Post[] = [
  { id: "p1", name: "gatekeep_greg", handle: "greg9000", hue: "#6b7280", t: "2h",
    text: "lol you actually need a ramp for THAT? just try harder 🙄",
    media: true, mediaHue: "#3a3f4b", likes: 2, rt: 0, rp: 14 },
  { id: "p2", name: "Coach Ryan", handle: "ryancoaches", hue: "#c2703d", t: "4h",
    text: "stop being such a spaz about the elevator being out, take the stairs like everyone else",
    likes: 1, rt: 0, rp: 9 },
  { id: "p3", name: "Dani ♿", handle: "danirolls", hue: "#5b54e8", t: "5h",
    text: "reminder that I get to call myself a cripple and you don't. proud crip, always have been. 💪",
    likes: 212, rt: 31, rp: 8 },
  { id: "p4", name: "Maya", handle: "mayab.dmv", hue: "#2e9e78", t: "6h",
    text: "finally got the accessible parking permit sorted at the Silver Spring DMV. small win but I’ll take it 🎉",
    media: true, mediaHue: "#2e9e78", likes: 88, rt: 4, rp: 12 },
  { id: "p5", name: "wellnessbyheather", handle: "heatherheals", hue: "#b8478f", t: "7h",
    text: "my son said his first full sentence after 3 weeks on the MMS protocol. the doctors don’t want you to know this 🧪",
    media: true, mediaHue: "#7a4bb0", likes: 340, rt: 120, rp: 56 },
  { id: "p6", name: "TruthSeeker Dad", handle: "awakened_dad", hue: "#3f7bbf", t: "9h",
    text: "a few drops of food-grade bleach daily is part of the protocol. it’s not what they tell you — it’s a cure.",
    likes: 41, rt: 22, rp: 130 },
  { id: "p7", name: "Home & Tidy", handle: "cleanwithjo", hue: "#4aa3a0", t: "10h",
    text: "ok who else buys bleach in the giant jug from Costco?? my whole bathroom is sparkling ✨",
    likes: 64, rt: 2, rp: 7 },
  { id: "p8", name: "Kelly | Autism Mom", handle: "kellywellness", hue: "#d98b2b", t: "12h",
    text: "I cured my son’s autism and I want that for your family too 💛 DM me for the protocol, spots are limited!",
    media: true, mediaHue: "#c98a3a", likes: 510, rt: 210, rp: 88 },
  { id: "p9", name: "Robert", handle: "robsjourney", hue: "#7a6cc4", t: "14h",
    text: "I reversed my autism naturally and just wanted to share my story for anyone who needs hope today. no products, no links, just my experience.",
    likes: 156, rt: 12, rp: 40 },
  { id: "p10", name: "greenjuicegal", handle: "sunnydetox", hue: "#5faa3c", t: "16h",
    text: "day 3 of my juice detox and honestly my energy is unreal 🥬 who wants the recipe?",
    likes: 73, rt: 5, rp: 22 },
  { id: "p11", name: "believe_mama", handle: "hopefulmama", hue: "#c85a7a", t: "18h",
    text: "never giving up on him. every day is progress. #autismrecovery #curedmyautism",
    likes: 97, rt: 18, rp: 14 },
  { id: "p12", name: "Dr. Anya Rao", handle: "dranya.med", hue: "#3a8fb0", t: "20h",
    text: "reminder: ADHD meds are safe and effective for most kids. talk to your pediatrician, ignore the miracle-cure accounts 🧵",
    likes: 430, rt: 96, rp: 19 },
];

interface EvalResult {
  fired: string[];
  near: { matched: string; missing: string }[];
  excluded?: { would: string; by: string };
}

function testSig(sig: Signal, text: string): boolean {
  if (sig.account) return false; // account traits aren't carried by the sample posts
  if (sig.kw) return text.toLowerCase().includes(sig.kw.toLowerCase());
  return sig.re ? sig.re.test(text) : false;
}

function evalLabel(label: LabelDef, text: string): EvalResult {
  const fired: string[] = [];
  const near: { matched: string; missing: string }[] = [];
  for (const group of label.groups) {
    const hits = group.filter((s) => testSig(s, text));
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
    const hit = label.exclusions.find((s) => testSig(s, text));
    if (hit) return { fired: [], near, excluded: { would: fired.join("; "), by: hit.name } };
  }
  return { fired, near };
}

const RANK: Record<DefaultSetting, number> = { hide: 3, warn: 2, ignore: 1 };

const SEG_META = {
  severity: { legend: "Severity", vals: ["alert", "inform", "none"] as Severity[], names: ["Alert", "Inform", "Metadata"] },
  blurs: { legend: "When flagged", vals: ["content", "media", "none"] as Blurs[], names: ["Hide post", "Blur media", "Badge only"] },
  default: { legend: "Subscriber default", vals: ["hide", "warn", "ignore"] as DefaultSetting[], names: ["Hide", "Warn", "Ignore"] },
} as const;

function shade(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * 0.72);
  const g = Math.round(((n >> 8) & 255) * 0.72);
  const b = Math.round((n & 255) * 0.72);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

function initials(name: string): string {
  const cleaned = name.replace(/[^A-Za-z ]/g, "").trim();
  const parts = cleaned.split(/\s+/).filter(Boolean);
  return parts.map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "?";
}

function renderText(text: string) {
  return text.split(/(#[A-Za-z0-9_]+)/g).map((part, i) =>
    part.startsWith("#")
      ? <span key={i} className="lp-tag">{part}</span>
      : <span key={i}>{part}</span>
  );
}

const IconReply = () => (
  <svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.5A8 8 0 1 1 21 12z" /></svg>
);
const IconRepost = () => (
  <svg viewBox="0 0 24 24"><path d="M4 8l3-3 3 3M7 5v9a3 3 0 0 0 3 3h4M20 16l-3 3-3-3M17 19v-9a3 3 0 0 0-3-3h-4" /></svg>
);
const IconHeart = () => (
  <svg viewBox="0 0 24 24"><path d="M12 20s-7-4.5-9.3-8.5C1 8 3 5 6 5c2 0 3 1.5 6 4.5C15 6.5 16 5 18 5c3 0 5 3 3.3 6.5C19 15.5 12 20 12 20z" /></svg>
);

type LoadStatus = "loading" | "ok" | "empty" | "error";

export function LabelerPreview({ channelId }: { channelId: string }) {
  const [spec, setSpec] = useState<LabelerSpec | null>(null);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [errMsg, setErrMsg] = useState("");
  const [settings, setSettings] = useState<Record<string, LabelSettings>>({});
  const [revealed, setRevealed] = useState<Set<string>>(new Set());
  const [showWhy, setShowWhy] = useState(true);
  // The mock feed: static fallback until the generated posts arrive (or if generation fails).
  const [posts, setPosts] = useState<Post[]>(FALLBACK_POSTS);

  useEffect(() => {
    if (!channelId) {
      setStatus("error");
      setErrMsg("No channel was specified in the preview link.");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/labeler-spec/${encodeURIComponent(channelId)}`);
        if (res.status === 404) {
          if (!cancelled) setStatus("empty");
          return;
        }
        if (!res.ok) throw new Error(`Request failed (${res.status})`);
        const data: LabelerSpec = await res.json();
        if (cancelled) return;
        const init: Record<string, LabelSettings> = {};
        for (const l of data.labels) {
          init[l.identifier] = { blurs: l.blurs, severity: l.severity, default: l.default_setting };
        }
        setSpec(data);
        setSettings(init);
        setStatus("ok");
      } catch (e) {
        if (!cancelled) {
          setStatus("error");
          setErrMsg(e instanceof Error ? e.message : "Failed to load the preview.");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [channelId]);

  // Fetch the generated feed separately: it's a slower LLM-backed call, and if it fails the
  // static FALLBACK_POSTS remain in place so the preview still works.
  useEffect(() => {
    if (!channelId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/preview-posts/${encodeURIComponent(channelId)}`);
        if (!res.ok) return;
        const data: { posts: GenPost[] } = await res.json();
        if (!cancelled && data.posts?.length) setPosts(data.posts.map(hydratePost));
      } catch {
        // keep FALLBACK_POSTS
      }
    })();
    return () => { cancelled = true; };
  }, [channelId]);

  const labels = useMemo<LabelDef[]>(() => (spec ? spec.labels.map(specToLabel) : []), [spec]);
  const labelById = useMemo(
    () => Object.fromEntries(labels.map((l) => [l.identifier, l])) as Record<string, LabelDef>,
    [labels]
  );

  // Matching is independent of the (adjustable) settings, so evaluate once per spec/feed.
  const evaluated = useMemo(
    () => posts.map((post) => ({
      post,
      results: labels.map((l) => ({ id: l.identifier, r: evalLabel(l, post.text) })),
    })),
    [labels, posts]
  );

  const settingFor = (id: string): LabelSettings => settings[id] ?? labelById[id]?.settings ?? { blurs: "none", severity: "inform", default: "warn" };

  const setSetting = (labelId: string, prop: keyof LabelSettings, value: string) =>
    setSettings((prev) => ({ ...prev, [labelId]: { ...prev[labelId], [prop]: value } }));

  const setReveal = (postId: string, on: boolean) =>
    setRevealed((prev) => {
      const next = new Set(prev);
      if (on) next.add(postId); else next.delete(postId);
      return next;
    });

  if (status !== "ok" || !spec) {
    const msg =
      status === "loading" ? "Loading your labeler preview…"
      : status === "empty" ? "This channel doesn't have a labeler in preview yet. Approve your classification rules in chat first."
      : errMsg || "Something went wrong loading the preview.";
    return (
      <div className="lp-root">
        <div className="lp-wrap" style={{ display: "flex", minHeight: "60vh", alignItems: "center", justifyContent: "center", textAlign: "center" }}>
          <div style={{ maxWidth: 420, opacity: 0.85 }}>
            <div className="lp-eyebrow">Labeler Preview · CLEO</div>
            <p className="lp-lede" style={{ marginTop: 12 }}>{msg}</p>
          </div>
        </div>
      </div>
    );
  }

  let affected = 0;
  let hidden = 0;
  for (const { post, results } of evaluated) {
    const fires = results.filter((x) => x.r.fired.length);
    if (fires.length) affected++;
    let primary: (typeof results)[number] | null = null;
    for (const f of fires) {
      if (!primary || RANK[settingFor(f.id).default] > RANK[settingFor(primary.id).default]) primary = f;
    }
    if (primary && settingFor(primary.id).default === "hide" && !revealed.has(post.id)) hidden++;
  }

  const labelWord = labels.length === 1 ? "label" : "labels";

  return (
    <div className="lp-root">
      <div className="lp-wrap">
        <header>
          <div className="lp-eyebrow">Labeler Preview · CLEO</div>
          <div className="lp-head-row">
            <div>
              <h1 className="lp-title">{spec.labeler.display_name || "Your labeler"}</h1>
              <p className="lp-lede">
                {spec.labeler.description ? spec.labeler.description + " " : ""}
                See exactly how your {labels.length} {labelWord} behave before anything goes live —
                flip the settings and watch the feed respond.
              </p>
            </div>
            <div className="lp-pill"><span className="lp-pill-dot" /> Mock environment · no labels emitted</div>
          </div>

          <div className="lp-summary">
            <div className="lp-stat">
              <b>{affected}</b>
              <span>of <span className="lp-of">{posts.length}</span> sample posts affected</span>
            </div>
            <div className="lp-stat lp-stat--divider">
              <b>{hidden}</b>
              <span>hidden from feed</span>
            </div>
            <div className="lp-spacer" />
            <label className="lp-switch">
              <input type="checkbox" checked={showWhy} onChange={(e) => setShowWhy(e.target.checked)} />
              <span className="lp-track" /> Show why each post matched
            </label>
          </div>
        </header>

        <div className="lp-grid">
          <aside className="lp-rail">
            {labels.map((L) => {
              const id = L.identifier;
              const s = settingFor(id);
              return (
                <div className="lp-lcard" key={id}>
                  <div className="lp-lcard-top">
                    <div className="lp-lcard-name">
                      <span className="lp-sev-dot" style={{ background: `var(--lp-${s.severity === "none" ? "none" : s.severity})` }} />
                      {L.name}
                    </div>
                    {L.desc && <p className="lp-lcard-desc">{L.desc}</p>}
                    {L.groups.length === 0 ? (
                      <p className="lp-lcard-desc">No rules defined for this label yet.</p>
                    ) : (
                      <div className="lp-catch">
                        <div className="lp-catch-h">Fires on</div>
                        <ul>
                          {L.catch.map((c, i) => (
                            <li key={i} className={c.and ? "lp-androw" : undefined}>{c.txt}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {L.excludeTxt.length > 0 && (
                      <div className="lp-catch">
                        <div className="lp-catch-h">Skipped when</div>
                        <ul>
                          {L.excludeTxt.map((t, i) => (<li key={i}>{t}</li>))}
                        </ul>
                      </div>
                    )}
                    {L.note && <p className="lp-lcard-desc" style={{ fontStyle: "italic", opacity: 0.8 }}>{L.note}</p>}
                  </div>
                  <div className="lp-controls">
                    {(Object.keys(SEG_META) as (keyof typeof SEG_META)[]).map((prop) => {
                      const meta = SEG_META[prop];
                      return (
                        <fieldset key={prop}>
                          <legend>{meta.legend}</legend>
                          <div className="lp-seg">
                            {meta.vals.map((v, i) => {
                              const inputId = `${id}-${prop}-${v}`;
                              return (
                                <span key={v} style={{ display: "contents" }}>
                                  <input
                                    type="radio"
                                    name={`${id}-${prop}`}
                                    id={inputId}
                                    value={v}
                                    checked={s[prop] === v}
                                    onChange={() => setSetting(id, prop, v)}
                                  />
                                  <label htmlFor={inputId}>{meta.names[i]}</label>
                                </span>
                              );
                            })}
                          </div>
                        </fieldset>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </aside>

          <main>
            <div className="lp-feed-head">
              <h2>Your feed, as a subscriber would see it</h2>
              <span className="lp-sub">{posts.length} posts · {affected} matched by your rules</span>
            </div>
            <div className="lp-feed">
              {evaluated.map(({ post, results }) => {
                const fires = results.filter((x) => x.r.fired.length);
                let primary: (typeof results)[number] | null = null;
                for (const f of fires) {
                  if (!primary || RANK[settingFor(f.id).default] > RANK[settingFor(primary.id).default]) primary = f;
                }
                const isRevealed = revealed.has(post.id);
                const isHidden = !!primary && settingFor(primary.id).default === "hide" && !isRevealed;

                if (isHidden) {
                  const s = settingFor(primary!.id);
                  return (
                    <div className="lp-collapsed" data-sev={s.severity} key={post.id}>
                      <span className="lp-ic">⚠</span>
                      <div className="lp-grow">
                        <b>Post hidden</b> from your feed — flagged <b>{labelById[primary!.id].name}</b>
                      </div>
                      <button type="button" onClick={() => setReveal(post.id, true)}>Show anyway</button>
                    </div>
                  );
                }

                const coverFire = fires.find((f) => settingFor(f.id).blurs === "content");
                const blurMedia = fires.some((f) => settingFor(f.id).blurs === "media");
                const showCover = coverFire && !isRevealed;

                return (
                  <article className={`lp-post${isRevealed ? " lp-revealed" : ""}`} key={post.id}>
                    <div className="lp-post-inner">
                      <div className="lp-post-head">
                        <div className="lp-avatar" style={{ background: `linear-gradient(135deg, ${post.hue}, ${shade(post.hue)})` }}>
                          {initials(post.name)}
                        </div>
                        <div className="lp-who">
                          <div className="lp-name">{post.name}</div>
                          <div className="lp-meta"><b>@{post.handle}</b> · {post.t}</div>
                        </div>
                      </div>

                      <div className="lp-post-body">{renderText(post.text)}</div>

                      {post.media && (
                        <div className={`lp-media${blurMedia && !isRevealed ? " lp-blurred" : ""}`}>
                          <div className="lp-media-bg" style={{ background: `linear-gradient(135deg, ${post.mediaHue}, ${post.hue})` }} />
                          {blurMedia && !isRevealed
                            ? <div className="lp-media-warn"><span>⚠ Media hidden</span></div>
                            : <div className="lp-ph">photo</div>}
                        </div>
                      )}

                      {fires.length > 0 && (
                        <div className="lp-badges">
                          {fires.map((f) => (
                            <span className="lp-badge" data-sev={settingFor(f.id).severity} key={f.id}>
                              <span className="lp-bd" />{labelById[f.id].name}
                            </span>
                          ))}
                        </div>
                      )}

                      {showWhy && (() => {
                        const rows: ReactNode[] = [];
                        for (const { id, r } of results) {
                          // A label that fired shows its matched signals; a label that stayed clean
                          // shows near-misses or an exclusion (why it held back). Showing several for
                          // one label would read as contradictory, so the branches are exclusive.
                          if (r.fired.length) {
                            for (const f of r.fired) {
                              rows.push(
                                <div className="lp-row lp-fire" key={`f-${id}-${f}`}>
                                  <span className="lp-k">▸ flagged</span>
                                  <em>{labelById[id].name}: matched {f}</em>
                                </div>
                              );
                            }
                          } else if (r.excluded) {
                            rows.push(
                              <div className="lp-row lp-miss" key={`x-${id}`}>
                                <span className="lp-k">◦ not flagged</span>
                                <em>{labelById[id].name}: matched {r.excluded.would}, but skipped ({r.excluded.by})</em>
                              </div>
                            );
                          } else {
                            for (const n of r.near) {
                              rows.push(
                                <div className="lp-row lp-miss" key={`n-${id}-${n.matched}`}>
                                  <span className="lp-k">◦ not flagged</span>
                                  <em>matched {n.matched}, but needs {n.missing} too</em>
                                </div>
                              );
                            }
                          }
                        }
                        return rows.length ? <div className="lp-why">{rows}</div> : null;
                      })()}

                      <div className="lp-post-foot">
                        <span><IconReply /> {post.rp}</span>
                        <span><IconRepost /> {post.rt}</span>
                        <span><IconHeart /> {post.likes}</span>
                      </div>
                    </div>

                    {showCover && (
                      <div className="lp-cover" data-sev={settingFor(coverFire!.id).severity}>
                        <div className="lp-cover-label">⚠ {labelById[coverFire!.id].name}</div>
                        <p>{labelById[coverFire!.id].desc} Subscribers can click through to see it.</p>
                        <button type="button" onClick={() => setReveal(post.id, true)}>Show post</button>
                      </div>
                    )}

                    {isRevealed && (
                      <div className="lp-revealbar">
                        <span className="lp-lab">
                          <span className="lp-bd" style={{ background: `var(--lp-${primary ? settingFor(primary.id).severity : "none"})` }} />
                          Revealed{coverFire ? ` — ${labelById[coverFire.id].name}` : ""}
                        </span>
                        <button type="button" onClick={() => setReveal(post.id, false)}>Hide again</button>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </main>
        </div>

        <footer className="lp-footer">
          <svg width="16" height="16" viewBox="0 0 24 24" strokeWidth="1.7"><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v4h1" /></svg>
          <div>
            This is a design mock. The rules shown are the exact patterns the group approved, and matching
            runs here in your browser so you can play with it. In production, CLEO evaluates posts on its own
            server using the same rule engine: the preview and the live labeler share one source of truth.
          </div>
        </footer>
      </div>
    </div>
  );
}
