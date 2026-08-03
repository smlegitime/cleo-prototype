# Pilot scenario — The Sourdough & Home Baking Collective

A scripted run for the technical pilot (see `technical-pilot-run-sheet.md`). Three participants,
one channel, cold start through to a recorded governance record.

**Coverage rows exercised:** 1 (happy path), 2 (majority vote — 3 members means 2 of 3 are needed),
9 (guide at the partial tier). Variant B at the end covers row 8 (stand-down → resume).

---

## Validation notes

Checked against the current build. Three things in the draft didn't match what the app does; they're
corrected in the script below and flagged inline with **⚠️**.

| Draft said | Reality | Where |
|---|---|---|
| At preview, "the spam got hidden… I checked that a normal link-in-bio from an account with followers stayed clean" | **The preview cannot show this label at all.** The in-browser matcher returns `false` for account-trait signals (`LabelerPreview.tsx:219`), and since a group is `all_of`, the whole AND-rule never fires. The UI annotates it "account trait (not simulated here)". Sample posts carry no follower data to check against. | Preview beat |
| Preview approval → straight to the sandbox run | There's a whole **`generate` stage between them**: real posts are fetched, a rule-quality report is posted, and a *second* approval (the ship gate) is required before anything is bundled. | Added as its own beat |
| Three approvals needed | `_threshold_met`: majority engages above 2 members, so **2 of 3 passes**. The third reaction is a no-op. | Noted at each vote |

Everything else validated as supported: positive/`inform` labels, `blurs='content'` for the hidden
one, keyword-OR rules, an AND-group combining a text signal with `follower_count < 10`, and account
enrichment of the real-post corpus (`corpus.enrich_accounts`, which only runs when the spec actually
uses account signals).

**Watch during the run:** whether the model picks `severity='inform'` for *Outside Our Community*.
The group frames it as "not harmful, just not us," which argues for `inform` + `blurs='content'`, but
the prompt associates `alert` with hiding. Either is defensible — note which it chose.

---

## Community context

A Bluesky-based home baking community, roughly 300 people, centered on sourdough, naturally-leavened
bread, and home milling. Mostly hobbyists who trade starter, troubleshoot crumb, and post bake
photos. The group wants a labeler that marks posts against its own community standards — not to warn
about danger, but to signal what does and doesn't fit the group's values.

**Dev1** — moderator, long-time baker, started the weekly bake-along
**Dev2** — runs a small local bakery, tech-comfortable, wants stricter rules
**Dev3** — newer hobbyist, cautious about over-labeling, worried about catching genuine posts

---

## Part 1 — Purpose

> **Dev1:** okay we've put this off long enough. we keep saying our community has a certain way of doing things, and new folks have no way of knowing what that is. let's actually encode it.

> **Dev3:** agreed, but carefully. I want this to help people understand our norms, not slap a scarlet letter on everyone who's new.

> **Dev2:** two things I'd want marked. posts that fit our "from-scratch" ethos so people can find the good stuff. And the drive-by product spam that has nothing to do with our culture.

> **Dev1:** yeah those are the two. one's a positive marker, one's a "this isn't us" marker.

*[CLEO asks what community it's for, what norms they want to encode, and who will subscribe.]*

> **Dev1:** first one is our core value — this group is about naturally-leavened, from-scratch baking. home-milled flour, wild starter, the slow way. we want a positive label that marks posts that exemplify that, so newcomers can see "this is what we're about."

> **Dev3:** I like that framing. it's aspirational, not punishing. but I don't want it to feel like a purity contest — a beginner's first lopsided loaf is still "us" even if it's not perfect.

> **Dev2:** the second thing is the dropshipper spam. accounts show up posting "PROFESSIONAL DUTCH OVEN 60% OFF LINK IN BIO," "DM me for artisan banneton wholesale" — brand-new accounts blasting product links. that's the opposite of our culture. it's not against a safety rule, it's just categorically not what this community is.

> **Dev1:** right. one marks "this fits our standard," the other marks "this falls outside it."

---

## Part 2 — Labels

*[CLEO explains that a label can be an informational badge that stays visible on the post, or can
cover the post behind a click-through, and asks how they want each handled.]*

> **Dev3:** the from-scratch one should just be a visible badge. it's a signal, a little "this exemplifies our values" marker. nothing hidden — we want people to see it.

> **Dev1:** definitely visible. it's a badge of belonging, not a warning.

> **Dev2:** the out-of-bounds spam — hide that. it's categorically not us, nobody needs it in the feed. put it behind a click-through.

> **Dev1:** yeah, hide the spam.

> **Dev3:** works for me.

*[CLEO stages a proposal: `from_scratch_craft` (visible badge, nothing covered) and
`outside_our_community` (post covered behind a click-through). Group reacts 👍🏾 to approve.]*

> **⚠️ Only 2 of the 3 reactions are needed.** Have Dev1 and Dev3 react first and confirm the
> proposal commits before Dev2 reacts — Dev2's reaction should be a visible no-op. That's coverage
> row 2. For row 3, have two people react simultaneously on a later gate instead.

---

## Part 3 — Rules

*[CLEO moves to classification rules, noting the labeler matches on words, patterns, and account
traits — it can't judge whether a bake is genuinely from-scratch in spirit, only what the text says.]*

> **Dev1:** for the from-scratch marker — the vocabulary is pretty consistent. "wild yeast," "naturally leavened," "home-milled," "fresh-milled flour," "levain," "no commercial yeast," "100% whole grain," that kind of language. people describing the slow process.

> **Dev2:** "starter" and "sourdough" alone are too broad — everyone says those. it's the from-scratch vocabulary that marks the real thing.

> **Dev3:** and please — this is a positive label, so worst case it's just missing from a post that deserved it. I'd rather it under-mark than slap the badge on something ironically.

> **Dev1:** that's right. keep it to the real from-scratch vocabulary.

> **Dev2:** now the out-of-community spam. same tell every time: a product-pitch phrase plus it's a brand-new account with basically no followers. "link in bio," "DM for wholesale," "60% off," "limited stock" — plus a follower count near zero. actual members who mention a link have hundreds of followers. it's the combination that marks it as not-us.

> **Dev3:** yeah, the combination feels safe. an established member sharing a link won't get caught because they've got followers.

> **Dev1:** so it's the sales phrase AND the low follower count together. either alone would catch real people.

*[CLEO drafts both rulesets — `from_scratch_craft` firing on the from-scratch vocabulary as separate
alternatives, `outside_our_community` firing only when a sales-pitch signal and a follower-count
signal match the same post. It notes the follower threshold is a blunt proxy.]*

> **Dev2:** what should the follower cutoff be?

> **Dev1:** our real newbies still follow like 20-30 people back within a day. the spam accounts follow nobody. under 10 feels safe?

> **Dev3:** under 10 works. low enough it won't catch a real person who's actually here.

> **Dev1:** good. sales phrase plus under 10 followers.

*[Group approves the rules. CLEO confirms two active rules and posts the preview link.]*

---

## Part 4 — Preview

> **⚠️ Corrected from the draft.** The preview matcher cannot evaluate account traits, so
> `outside_our_community` will show **zero matches** and its account signal will be annotated
> "account trait (not simulated here)". Do not script Dev3 confirming the spam got hidden — it
> can't. Whether the group notices this on their own is worth recording.

> **Dev3:** the from-scratch badges landed where I'd expect — the wild yeast and home-milled posts got marked, the plain "sourdough" ones didn't.

> **Dev2:** the spam label isn't showing anything though. it says the account trait isn't simulated here?

> **Dev1:** so we can't see that one until it runs against real posts?

*[CLEO explains the preview only has sample post text, with no account data behind it, so the
follower-count half of that rule can't be checked here — it gets tested in the next step against
real posts.]*

> **Dev2:** fine, as long as it gets checked somewhere.

> **Dev1:** from-scratch markers look right. approving.

> **Dev3:** approved.

---

## Part 5 — Rule-quality check *(added — missing from the draft)*

*[CLEO pulls real Bluesky posts related to the labels and runs the rules over them, posting a
per-label breakdown with examples that fired, then a ship-gate message.]*

> **⚠️ This is the first point where the account rule is genuinely exercised** — the corpus is
> enriched with follower counts via `getProfiles`, which only happens because the spec uses account
> signals. Expect `outside_our_community` to fire rarely or not at all: real spam accounts under 10
> followers are sparse in a topic search. **A `0/N` here is a valid result, not a failure.**

> **Dev2:** from-scratch fired on a good chunk of them, that looks right.

> **Dev3:** the out-of-community one is zero out of everything. is that broken or is there just no spam in the sample?

*[CLEO reports the counts as measured; the group decides whether to proceed.]*

> **Dev1:** probably just none in the pull. the rule looks right, let's go.

> **Dev2:** approving.

*[Ship gate approved → bundle assembled.]*

---

## Part 6 — Sandbox run

*[CLEO assembles the bundle and runs it end to end under a throwaway `did:web` sandbox identity,
reporting the identity, how many posts it ran over, how many signed records it emitted, and a
per-label breakdown.]*

> **Check the breakdown formatting here.** A label that fired zero times must be named explicitly —
> either "No matches for: `outside_our_community`" or, if nothing fired at all, "No test post
> matched any label: …". A label silently missing from the list is a bug.

> **Dev1:** okay so it actually ran and signed things. and nothing's public yet?

*[CLEO confirms nothing is published, then posts the go-live fork: a reaction to start the
governance questions, or a link to the maintenance guide to stay in the sandbox.]*

> **Dev3:** let me look at that guide first.

*[Dev3 opens `?guide=<channel_id>` — the sandbox-tier variant, showing what going live would take.
Coverage row 9.]*

> **Dev3:** okay it lists what we'd need — a name, someone to hold the account, someone for appeals. and it says emails aren't collected here.

> **Dev1:** let's do the questions at least, we can stop whenever.

*[Dev1 and Dev2 react 👍🏾 to the go-live message → `deploy` → `provision`.]*

---

## Part 7 — Provisioning *(new)*

*[CLEO posts the three questions with handle candidates derived from the labeler's display name, and
states plainly that nothing here creates an account or publishes anything.]*

> The candidates are computed from the display name. If the labeler is named **Sourdough Collective
> Standards**, expect exactly:
> `sourdough-collective-standards.bsky.social`, `…-mod.bsky.social`, `…-labels.bsky.social`.
> A different display name gives different candidates — check they're sensible rather than assuming.

> **Dev2:** the plain one, first option. the -mod suffix makes it sound like a moderation account and this is mostly a positive badge.

> **Dev1:** agreed, first one.

> **Dev3:** on the custodian — that's whoever holds the account if we ever make it real? I'd say Dev1, she started the bake-along and she's the one who'd still be here in a year.

> **Dev1:** I can do that. what does it actually mean?

*[CLEO explains the custodian holds the account on the group's behalf — a caretaker, not an owner,
replaceable by the group at any time — and that they'd need an email address for it, which is not
being collected here.]*

> **Dev1:** fine by me. and it says they need an email — we're not giving one now, right?

> **Dev2:** right, it said don't post addresses in the channel.

> **Dev3:** should we have a backup? if Dev1 goes on holiday and something breaks.

> **Dev1:** Dev2 as backup then.

> **Dev2:** sure.

> **Dev1:** appeals — if someone gets the "outside our community" label and thinks it's wrong, who do they talk to? that should be the mod team, not one person. we've got three mods.

> **Dev3:** the mod team, yeah. and we'd need to put a contact somewhere public so people can actually reach us.

*[CLEO posts a confirm card with everything it heard, and what's still outstanding if anything.]*

> **Dev1:** that's all correct. approving.

> **Dev3:** approved.

*[Threshold met at 2 of 3 → governance record committed. CLEO reports that all three are settled,
and says plainly that this is as far as it can take them: creating the real account needs the email
addresses and a step it can't run yet, so the labeler stays private in the sandbox, and their
answers are written into the guide.]*

> **Dev2:** so nothing happened on Bluesky at all.

> **Dev1:** nothing. it just wrote down what we decided.

*[Dev2 reopens `?guide=<channel_id>` — now the **partial/answered** tier, with the handle, custodian,
backup and appeals contact filled in, and the email requirement still listed as not-collected.]*

**End of run.** Export transcript and state before clearing (run sheet §9).

---

## Variant B — stand-down *(coverage row 8)*

Run Parts 1–6 as above, then instead of answering:

> **Dev1:** actually, let's park this. we should talk to the rest of the mods before we name anyone.

> **Dev3:** yeah, not today.

*[CLEO should acknowledge, keep anything already settled, return the channel to `deploy`, and post a
**new** go-live message that itself works as the anchor to pick things back up.]*

> **Verify two things:** any answers already approved are echoed back rather than lost, and reacting
> to the *new* message actually re-opens the questions. The original anchor is committed and can
> never fire again, so if the fresh one doesn't work, "pick it up whenever" is a dead offer.

Then have Dev1 and Dev2 react to the new message and complete Part 7 — that's stand-down *and*
resume in one run.
