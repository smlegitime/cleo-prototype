# CLEO flow — stages and decision points

One-page recap for the start of a pilot session. Companion to `technical-pilot-run-sheet.md`.

```mermaid
flowchart TD
    S([Cold start — group joins the channel]) --> A["<b>purpose</b><br/>what is this labeler for?"]
    A -- "🗣️ community · goal · audience captured" --> B["<b>content</b><br/>which labels, and how does each behave?"]
    A -. "🗣️ label talk arrives early<br/>parked, not acted on" .-> A
    B --> V1{"👍🏾 <b>Label proposal</b><br/>staged by CLEO"}
    V1 -- approved --> C["<b>rules</b><br/>what words, patterns and<br/>account traits fire each label?"]
    C --> V2{"👍🏾 <b>Rules proposal</b>"}

    V2 -- approved --> PV["<b>PREVIEW</b> · screen at ?preview=id<br/>simulated badges on generated sample posts<br/>⚠️ account traits are NOT simulated here"]
    PV --> V3{"👍🏾 <b>Preview approved</b>"}

    V3 --> GEN["<b>GENERATE</b> ⚙️ automatic<br/>pull real Bluesky posts, enrich follower data,<br/>run the rules → rule-quality report"]
    GEN --> V4{"👍🏾 <b>Ship gate</b><br/>on the quality report"}

    V4 --> DEP["<b>DEPLOY</b> ⚙️ automatic<br/>build bundle → run it in the sandbox<br/>signed records, throwaway did:web<br/><i>no vote — machine pass/fail</i>"]

    DEP --> FORK{"Go live?<br/><i>optional</i>"}
    FORK -- "👍🏾 react" --> PRO["<b>PROVISION</b><br/>3 questions answered in chat:<br/>handle · custodian · appeals contact"]
    FORK -- "do nothing" --> GUIDE(["📘 <b>Maintenance guide</b> · ?guide=id<br/>stays private in the sandbox"])

    PRO --> V5{"👍🏾 <b>Confirm card</b><br/>on what CLEO heard"}
    V5 -- approved --> DONE(["✅ <b>Governance recorded</b><br/>end of what the app can do today"])

    PRO -- "🗣️ 'let's park this'" --> BACK["↩️ back to DEPLOY<br/>answers kept · fresh anchor posted"]
    BACK --> FORK
    GUIDE -. "whenever they're ready" .-> FORK

    PV -. "🗣️ change request" .-> C
    GEN -. "🗣️ rule edit → re-check" .-> GEN

    style GEN fill:#2d3748,color:#fff
    style DEP fill:#2d3748,color:#fff
    style DONE fill:#22543d,color:#fff
    style GUIDE fill:#2c5282,color:#fff
```

---

## Where the group actually decides

Six moments. Five are 👍🏾 reactions on a specific message; one is just talking.

| # | Decision | How | What it moves |
|---|---|---|---|
| 1 | Approve the labels | 👍🏾 on the proposal card | `purpose`/`content` → `rules` |
| 2 | Approve the rules | 👍🏾 on the rules card | `rules` → `complete`, opens **preview** |
| 3 | Approve the preview | 👍🏾 on the preview prompt | `preview` → `generate` |
| 4 | Ship gate | 👍🏾 on the quality report prompt | `generate` → `deploy` |
| 5 | Go live — **or not** | 👍🏾 to start · ignore it for the guide | `deploy` → `provision` |
| 6 | Confirm governance answers | 👍🏾 on the confirm card | commits the record |

**Threshold:** majority engages above 2 members. So **3 members → 2 approvals**; 4 → 3; 1–2 → any
single reaction passes. Extra reactions after the threshold are no-ops.

**Not a decision point:** the sandbox run. It's a machine pass/fail and flows straight through to the
report with no vote.

**Talking, not reacting:** the three provisioning answers, any change request during preview, a rule
edit during generate, and standing down from provisioning. CLEO listens for these in chat.

---

## What happens on its own

| Stage | CLEO does | Group sees |
|---|---|---|
| `generate` | pulls real posts, enriches follower data, runs the rules | corpus count + per-label quality report with examples |
| `deploy` | builds the bundle, runs it under a sandbox identity | bundle summary, then the run report: identity, posts, signed records, per-label counts |
| `provision` | derives handle candidates from the labeler's name | the three questions, with candidates offered |

---

## Two things to say out loud at the start

**Nothing goes public.** No Bluesky account is created, no handle is claimed, nothing is published —
at any stage, including provisioning. The sandbox identity is a throwaway `did:web` that isn't
served.

**Provisioning is where it stops.** Recording the three answers is the last thing the app can do.
Creating a real account needs email addresses this version doesn't collect and a step that isn't
built, and CLEO says so plainly at the end.
