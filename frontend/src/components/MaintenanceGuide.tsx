import { useEffect, useState, type ReactNode } from "react";

/**
 * Standalone maintenance-guide screen (route `?guide=<channel_id>` in main.tsx). The opt-out path:
 * a group that keeps its labeler in the private sandbox instead of going live gets a short,
 * lightly-tailored guide to running and maintaining it. Content comes from
 * GET /maintenance-guide/<channel_id> (src/agent/maintenance_guide.py) — curated sections with a few
 * spec-derived slots — so this component only renders; it never invents copy. Read-only in v1.
 */

const API_BASE = (import.meta.env.VITE_AI_ASSISTANT_URL as string | undefined) ?? "";

interface GuideSection { id: string; title: string; body: string }
type Readiness = "sandbox" | "partial" | "live";
interface Guide {
  labeler_name: string;
  label_count: number;
  labels: string[];
  mode: string;
  readiness: Readiness;
  outstanding: string[];
  sections: GuideSection[];
}

// How far along the group is on going live. `readiness` is server-computed; the badge just names it.
const READINESS_LABEL: Record<Readiness, string> = {
  sandbox: "sandbox",
  partial: "sandbox · going live in progress",
  live: "live on Bluesky",
};

type LoadStatus = "loading" | "ok" | "error";

// Light inline formatter for the curated copy: `*bold*` -> <strong>, `` `code` `` -> <code>.
// Newlines are preserved by white-space: pre-wrap on the container.
function fmt(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*([^*]+)\*|`([^`]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<strong key={key++}>{m[1]}</strong>);
    else out.push(<code key={key++} className="mg-code">{m[2]}</code>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function MaintenanceGuide({ channelId }: { channelId: string }) {
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [guide, setGuide] = useState<Guide | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/maintenance-guide/${encodeURIComponent(channelId)}`);
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as Guide;
        if (!cancelled) { setGuide(data); setStatus("ok"); }
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();
    return () => { cancelled = true; };
  }, [channelId]);

  return (
    <div className="mg-root">
      <style>{CSS}</style>
      <div className="mg-wrap">
        {status === "loading" && <p className="mg-muted">Loading your guide…</p>}
        {status === "error" && (
          <p className="mg-muted">
            Couldn't load the guide for this channel. It may not have an approved labeler yet.
          </p>
        )}
        {status === "ok" && guide && (
          <>
            <header className="mg-header">
              <div className="mg-eyebrow">Maintenance guide</div>
              <h1 className="mg-title">{guide.labeler_name}</h1>
              <span className="mg-badge">
                {guide.mode} · {READINESS_LABEL[guide.readiness] ?? "sandbox"}
              </span>
            </header>
            {guide.sections.map((s) => (
              <section key={s.id} className="mg-section">
                <h2 className="mg-h2">{s.title}</h2>
                <p className="mg-body">{fmt(s.body)}</p>
              </section>
            ))}
            {/* No maintenance promise here: CLEO builds labelers, it doesn't run them (see the
                SCOPE note in src/agent/maintenance_guide.py). The footer points at what deploying
                would ask of the group, which is the part CLEO does own. */}
            <footer className="mg-footer">
              {guide.readiness === "live"
                ? "Your labeler is public. Changing a rule or a label means re-testing and redeploying it."
                : "Nothing here is permanent. If your group decides to deploy, these are the answers you'd be asked for."}
            </footer>
          </>
        )}
      </div>
    </div>
  );
}

const CSS = `
.mg-root { min-height: 100vh; color: #e6e8eb;
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.mg-wrap { max-width: 680px; margin: 0 auto; padding: 48px 20px 80px; }
.mg-muted { color: #8b94a3; padding: 40px 0; }
.mg-header { border-bottom: 1px solid #22262e; padding-bottom: 20px; margin-bottom: 8px; }
.mg-eyebrow { text-transform: uppercase; letter-spacing: .08em; font-size: 12px; color: #8b94a3; }
.mg-title { font-size: 28px; font-weight: 700; margin: 6px 0 10px; }
.mg-badge { display: inline-block; font-size: 12px; color: #a8b0bd; background: #1a1e26;
  border: 1px solid #2a2f39; border-radius: 999px; padding: 3px 10px; }
.mg-section { padding: 22px 0; border-bottom: 1px solid #191d24; }
.mg-h2 { font-size: 17px; font-weight: 650; margin: 0 0 8px; color: #f2f4f7; }
.mg-body { margin: 0; color: #cfd4dc; white-space: pre-wrap; }
.mg-code { background: #1a1e26; border: 1px solid #2a2f39; border-radius: 5px;
  padding: 1px 5px; font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.mg-footer { margin-top: 28px; padding-top: 20px; border-top: 1px solid #22262e;
  color: #a8b0bd; font-size: 14px; }
`;
