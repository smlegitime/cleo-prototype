# Technical pilot — run sheet

Operational document for the lab-internal technical pilot of CLEO. Bring it to the session.

---

## 1. What this pilot is for

**One question: does the machinery survive a real group end-to-end without anyone rescuing it?**

Participants are lab-internal. Sessions are facilitated and synchronous. The exit criterion is
robustness (i.e., nothing crashed, no operator intervention).

### Explicitly out of scope

- **Whether the labelers are any good.** Lab people authoring rules in domains they already
  understand would write good rules and we'd learn nothing about the authoring gap. This pilot
  cannot tell us whether a group's labeler works; that question belongs to whatever comes next.
- **Usability.** We're not measuring confusion, only breakage.
- **Multi-group concurrency.** See §7.

---

## 2. Pass / fail

A run **passes** if every stage the group reached completed without operator intervention.

| | |
|---|---|
| **Rescue** (fails the run) | Poking CLEO with an extra message to make it respond · re-reacting because a vote didn't register · restarting the server · `scripts/rewind_lifecycle.py` · hand-editing checkpoint state · anything done to unblock a stall |
| **Not rescue** (fine) | Participating normally as a group member · answering CLEO's questions · post-run cleanup with the Clear button / `clear-channel` |

### The stall rule

**When it stalls, the run is already failed.** Record it, then rescue freely to keep exercising the
later stages. The finding is already banked and there's nothing to gain by ending early.

Write this down before the session, because the instinct in the room is to nudge it and quietly not
count the nudge. The facilitator is usually also a participant — participating is fine, unsticking
is not.

---

## 3. Prerequisites (once, before run 1)

- [ ] **Tag the build.** Every run goes against one tag. If something must be patched mid-pilot,
      branch, re-tag, and record which runs used which tag — otherwise the results describe several
      different systems.
- [ ] **Logs are durable.** The catch-all notice (§5) puts a marker in the channel, but the
      traceback only exists in the server log. Render's are ephemeral — ship them somewhere or run
      against a local instance you keep. Reconstructing a stall from memory afterwards is miserable.
- [ ] **Engine is built.** `labeler-engine/dist` is gitignored. The Docker image builds it in stage
      1; a local/manual deploy needs `scripts/build_labeler_engine.sh` or
      `tsc -p labeler-engine/tsconfig.build.json`. Without it, `generate` and `deploy` both fail.
- [ ] **`node` on PATH** (or `NODE_BIN` set) wherever the server runs.
- [ ] **Stream webhook** points at `<backend>/new-message`
      (`scripts/register_stream_webhook.py`).
- [ ] **Env**: `BSKY_HANDLE` / `BSKY_APP_PASSWORD` valid (corpus fetch), `FRONTEND_URL` and
      `VITE_AI_ASSISTANT_URL` cross-wired, `PROTECTED_CHANNELS` includes any demo channel you don't
      want wiped.
- [ ] **Autodeploy off.** `render.yaml` sets `autoDeploy: true`, so any push to the tracked branch
      redeploys mid-pilot and breaks the tag pin above. Turn it off, or point the service at a
      branch you won't touch.
- [ ] **Export gate configured.** Generate a token, set it on the server, mirror it locally:
      ```bash
      openssl rand -hex 32                      # paste into Render → Environment → CLEO_ADMIN_TOKEN
      ```
      Saving env vars on Render **restarts the service** — do this before run 1, never between runs.
      Then, in the shell you'll export from:
      ```bash
      export CLEO_BACKEND=https://<your-backend>.onrender.com
      export CLEO_ADMIN_TOKEN=<the same value>
      ```
      Verify before you need it: `curl -sS -o /dev/null -w '%{http_code}\n' \`
      `-H "Authorization: Bearer $CLEO_ADMIN_TOKEN" "$CLEO_BACKEND/export-state/dev2"` → `200`
      (or `404` if that channel has no state yet). `401` = token mismatch. `503` = not set on the
      server.
- [ ] **Smoke run.** One solo pass through to a recorded governance record before involving anyone.

---

## 4. Per-run setup

- [ ] Fresh channel, or clear an existing one (Clear button / `POST /clear-channel/{id}`).
      `PROTECTED_CHANNELS` will refuse protected ones.
- [ ] **Add the run's channel id to `STREAM_CHANNELS`** and redeploy. It's an allowlist: a join
      code that isn't on it is refused with a 404, so participants can't reach the channel until
      it's listed. Pre-create the channel in Stream so you can confirm the roster before the session.
- [ ] **Send the group its link:** `$CLEO_FRONTEND/?c=<channel_id>`. That code is the only thing
      routing them to the right channel — everyone in the group needs the same one, and it should
      not be shared with a different group. Participants who lose the link can type the code into
      the onboarding form instead.
- [ ] Note participant count. `_threshold_met` engages majority voting when non-AI members
      **exceed** `MAJORITY_THRESHOLD = 2` — so **3+ members** means a majority is required
      (2 of 3), and 1–2 members means any single approval passes. With fewer than 3, the vote
      paths in §6 don't get tested.
- [ ] Start the run log (§8). Note the start time; the flow is meant to fit 30–40 minutes.

---

## 5. What a failure looks like

| Symptom | Meaning |
|---|---|
| A specific ⚠️ message (`couldn't pull posts`, `couldn't assemble the bundle`, `didn't start cleanly in the sandbox`) | **Handled** failure. Expected path, has its own copy. Note it; not automatically a fail. |
| `⚠️ Something went wrong on my side…` | **Unhandled** failure. Grab the server traceback — this is the one worth investigating. |
| Channel goes quiet and stays quiet | Stall. Could be a dead background task whose reporter also failed, or a webhook that never arrived. Check the server log before touching anything. |
| A reaction produces no response | Vote didn't register, or the anchor was already committed. Note which message was reacted to. |

Handled failures are fine to trigger deliberately (§6) — the point is to see whether the copy reads
acceptably, not to avoid them.

---

## 6. Coverage matrix

A single happy-path run tells you very little. Tick each at least once across the pilot; note the
run ID where it was covered.

| # | Path | Run | Notes |
|---|---|---|---|
| 1 | Full happy path, cold start → governance recorded | | The baseline |
| 2 | Vote below threshold, then met | | Needs 3+ members (2 of 3); exercises `MAJORITY_THRESHOLD` |
| 3 | Two people react within a second | | The race `_vote_locks` exists for |
| 4 | Change request during `preview` | | Re-derives rules, re-stamps `spec_id`, stays in preview |
| 5 | Rule edit while in `generate` | | Triggers quality re-run + a fresh ship-gate anchor |
| 6 | Opt-out fork → maintenance guide | | The half that never enters provision |
| 7 | Provision: partial answers, resume later | | Answers must persist across a gap |
| 8 | Provision: stand-down → resume | | Back edge to `deploy`; the fresh anchor must fire |
| 9 | Guide viewed at each tier (sandbox / partial) | | `?guide=<channel_id>` |
| 10 | **Inject:** bad `BSKY_APP_PASSWORD` | | Expect the corpus-failure message |
| 11 | **Inject:** rename `labeler-engine/dist` | | Expect the quality / sandbox failure messages |

Rows 10–11 are deliberate. They're the only check that a handled failure reads acceptably to a group
rather than as a dead end.

---

## 7. Known blind spots

Record these as untested rather than rediscovering them live:

- **Multi-group concurrency.** Facilitated synchronous means one channel at a time. Untested:
  shared `BSKY_HANDLE` rate limits across simultaneous `generate` stages (`fetch_corpus` makes up to
  ~32 `searchPosts` calls), one SQLite checkpoint under parallel writes, one instance under load.
- **Rule quality.** See §1.
- **Async/remote use.** Every failure mode here is being observed by someone watching in real time.
- **Long-lived channels.** Runs are short and cleared between sessions; nothing tests a channel that
  has accumulated weeks of history.

---

## 8. Run log template

Copy per run.

```
Run ID:            R__
Date / time:       
Build tag:         
Channel ID:        
Participants:      __ non-AI members (names)
Facilitator:       

Coverage rows targeted:   #__, #__

Stage timings (first message → each milestone)
  setup complete (rules approved):     __:__
  preview approved:                    __:__
  quality report posted:               __:__
  sandbox run reported:                __:__
  governance recorded / opted out:     __:__
  TOTAL:                               __:__

Outcome:      PASS / FAIL
If FAIL, the first intervention:
  stage:            
  what happened:    
  channel message:  (specific ⚠️ / generic ⚠️ / silence)
  server traceback: (attach)

Handled failures seen (not necessarily failures of the run):

Rough edges worth noting (not pass/fail):

Artifacts captured (see §9):
  [ ] transcript   exports/channel_<id>_<ts>.json + .txt
  [ ] state        exports/state_<id>_<ts>.json
  [ ] server log slice
```

---

## 9. After each run

**Export before clearing.** `/clear-channel` deletes the checkpoint thread (`adelete_thread`) and it
is not recoverable. It is also deliberately **ungated**, so the in-app Clear button keeps working —
which means an accidental press between the run and the export loses it. Export first, every time.

Set `RUN=` to the channel id, then run all three:

```bash
RUN=pilot-r01
mkdir -p exports

# 1. Transcript — talks to Stream, not to your backend. Reads STREAM_* from the repo .env.
#    Note the channel comes from the env var, NOT an argument; it defaults to "general".
STREAM_CHANNEL_ID=$RUN \
  conda run -n bsky-coll-eng python scripts/export_channel.py --output-dir exports/

# 2. State — the checkpoint lives on Render's disk at /data/checkpoints.sqlite, unreachable
#    from here, so it comes over HTTPS. curl does NOT read .env; the vars must be exported
#    (see §3), or run:  set -a; source .env; set +a
curl -sS -f -H "Authorization: Bearer $CLEO_ADMIN_TOKEN" \
  "$CLEO_BACKEND/export-state/$RUN" \
  -o "exports/state_${RUN}_$(date +%Y%m%dT%H%M%S).json"

# 3. Only now, clear.
curl -sS -X POST "$CLEO_BACKEND/clear-channel/$RUN"
```

`-f` on the state export matters: without it a 401 or 503 body gets written to the file and looks
like a successful export until you open it.

Locally (no Render), step 2 is the script instead — same output shape:

```bash
conda run -n bsky-coll-eng python scripts/export_checkpoints.py $RUN --out exports/state_$RUN.json
```

- [ ] Both exports written and non-empty **before** clearing.
- [ ] File anything unhandled with its traceback.
- [ ] Update the coverage matrix.
- [ ] Clear the channel for the next run.
