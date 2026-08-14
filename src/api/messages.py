from src.config import FRONTEND_URL

# Appended to the rules-approval confirmation at the preview handoff, with the group's live
# preview link. A function (not a constant) because the link is per-channel.
def preview_stage_intro(channel_id: str) -> str:
    return (
        "📋 *Next step: preview.*\n\n"
        "You'll be able to view your labels and rules in a preview screen, a live simulation of "
        "how each label behaves on sample posts, so the group can see exactly what gets flagged "
        "before anything goes live.\n\n"
        f"👉🏾 Open your preview: {FRONTEND_URL}/?preview={channel_id}"
    )

# Sent automatically right after a labeler config is approved and the group enters the
# 'rules' stage, so they don't have to ask what comes next.
RULES_STAGE_INTRO = (
    "📋 *Next step: classification rules.*\n\n"
    "Now that your labels are approved, we'll define the rules that decide when each label "
    "gets applied: the specific words or phrases, text patterns, and account traits to look "
    "for. (The labeler matches on those signals; it can't judge tone or meaning.)\n\n"
    "When you're ready, mention @CLEO or react 🤖 to any message and I'll draft a starting "
    "set of rules for each label for the group to review and adjust."
)

# Posted right after the preview link, as the anchor for the preview-approval vote: reacting to
# THIS message advances the lifecycle preview -> generate (materialize + sandbox-test the labeler).
PREVIEW_APPROVAL_PROMPT = (
    "👍🏾 When your group has reviewed the preview and is happy with it, react to *this* message to "
    "approve. I'll then build your labeler and test it in a private sandbox before it ever goes "
    "live. No Bluesky account gets created yet.\n\n"
    "Want to change something first? Just tell me what to adjust and we'll update the rules before "
    "you approve."
)

# Appended to a proposal card once a revised proposal replaces it. Votes on the old card are inert
# from that moment (see voting.superseded_entries); this is what says so in the channel, since the
# card itself stays in the scroll looking just as approvable as it did before.
SUPERSEDED_NOTE = "— ⤵️ *Replaced by a revised version below. Approve that one instead.*"

# Replied when someone approves a superseded card anyway. Without it the reaction just does
# nothing, which reads as a broken button rather than a deliberate no-op.
SUPERSEDED_VOTE_MSG = (
    "That version was replaced by a revised one further down. Your 👍🏾 there won't count. "
    "React to the newest proposal instead."
)

# Replied when an approval IS counted but the card still needs more. Same failure as the superseded
# case and a much more common one: a first 👍🏾 of two changes nothing the group can see — the card
# stays amber and carries no count — so the vote that is working looks identical to the vote that
# was ignored. The running tally exists in state; this is the only thing that volunteers it.
VOTE_PROGRESS_MSG = (
    "👍🏾 Counted — that's {approved} of {needed}. {remaining} more and this is approved."
)

# Posted when someone joining changes how many 👍🏾 a card needs. The figure CLEO quotes is refreshed
# once per agent run while enforcement recomputes it live on every reaction, so between a join and
# the next run the two disagree — and the stale number is already in the scroll, under cards the
# group can still vote on. Saying it out loud is what stops a group reacting once to a card they
# were told a single 👍🏾 would carry.
THRESHOLD_CHANGED_MSG = (
    "👥 There are {voting_count} of you voting now, so a card needs {needed} 👍🏾 to be approved. "
    "Anything further up that says otherwise is out of date."
)

# Sent once the preview is approved and the group advances to generate (bundle + sandbox test).
# The irreversible provisioning step (real account/identity) comes later, only after the sandbox run.
SANDBOX_STAGE_INTRO = (
    "🛠️ *Next step: building and testing your labeler.*\n\n"
    "Your preview is approved. I'll now assemble the labeler and run it in a private sandbox over "
    "real posts, so your group can see how it behaves before anything goes live. Nothing is "
    "published to Bluesky and no account is created at this stage."
)

# Posted after the generate stage sources real posts to test the rules against.
CORPUS_SOURCED_MSG = (
    "📥 Pulled {n} real Bluesky posts related to your labels to test the rules against. "
    "Checking how the rules behave on them next."
)
CORPUS_FAILED_MSG = (
    "⚠️ I couldn't pull posts to test the rules against just now. I'll retry. If it keeps failing "
    "it's usually a credentials or network issue on my side, not a problem with your labeler."
)

# Posted right after the rule-quality report, as the anchor for the ship gate: reacting to THIS
# message advances the lifecycle generate -> deploy and materializes the sandbox bundle. The quality
# report itself stays informational (no vote on it) — this is the single explicit "go", reusing the
# same reaction the preview approval uses so the group already knows the gesture.
DEPLOY_APPROVAL_PROMPT = (
    "👍🏾 Happy with how the rules behave? React to *this* message and I'll package your labeler into "
    "a bundle ready for the sandbox run. Still nothing published to Bluesky and no account created.\n\n"
    "Want to tweak a rule first? Tell me what to change and we'll re-check before you approve."
)

# Sent when the ship gate is approved and the lifecycle advances generate -> deploy.
BUNDLE_STAGE_INTRO = (
    "📦 *Assembling your labeler…* Packaging the approved labels and rules into a bundle for the "
    "sandbox run. One moment."
)

# Reported after the bundle is materialized, as a bridge into the sandbox run that follows immediately.
BUNDLE_READY_MSG = (
    "✅ *Labeler bundle assembled*. {label_phrase}, {rules} with matching rules. Now starting it "
    "in a private sandbox over the test posts…"
)
BUNDLE_FAILED_MSG = (
    "⚠️ I couldn't assemble the labeler bundle just now — that's on my side, not your labeler. "
    "I'll look into it."
)

# Reported after the sandbox executor runs the bundle end-to-end. This is the labeler running as a
# real service (identity + signed records), NOT just the earlier rules check — so it leads with the
# operational facts. The did:web is a sandbox placeholder (nothing served/subscribable yet), so the
# copy deliberately does not tell the group to subscribe to it in a Bluesky client.
SANDBOX_RUN_REPORT = (
    "🧪 *Sandbox run complete. Your labeler works end to end.*\n\n"
    "• Identity: `{did}`  (a throwaway sandbox identity. No Bluesky account, nothing published, "
    "fully reversible)\n"
    "• Ran over {total} test posts and emitted {record_phrase}\n"
    "{breakdown}\n\n"
    "This is the real labeler service running and signing labels so you "
    "can see it behave before any permanent identity exists."
)
SANDBOX_RUN_FAILED_MSG = (
    "⚠️ Your labeler didn't start cleanly in the sandbox. Nothing was published — this is a build or "
    "runtime issue on my side, not a problem with your rules. I'll look into it."
)

# Catch-all for UNHANDLED errors in the background stage tasks (see reporters._report_unexpected).
# Every expected failure above has its own specific copy; this is what gets posted when something
# nobody anticipated goes wrong. Deliberately promises no retry — the stage gates are reaction-
# anchored and already committed, so "give me a nudge" would be a lie. It says the two things a
# group actually needs (nothing published, nothing lost) and leaves recovery to a human.
UNEXPECTED_ERROR_MSG = (
    "⚠️ Something went wrong on my side and I couldn't finish that step. Nothing was published and "
    "nothing your group has built was lost. Your labeler is exactly as it was. This is a bug "
    "rather than anything you did, and it's been logged."
)
# The fork after the sandbox run, posted as two anchors the group votes between. Both carry the
# same weight. Whichever reaches a majority first decides, and the other is closed (see
# voting.process_guide_choice). Two messages rather than one because a single card with two
# reactions cannot express "we chose"; each path needs its own thing to react to.
#
# Framing note: going live is genuinely OPTIONAL, so neither message is written as the default.
PATH_CHOICE_INTRO = (
    "🔀 *Your labeler works. Two ways to go from here, the group picks.*\n\n"
    "React 👍🏾 to whichever of the next two messages your group wants. Whichever gets a majority "
    "first is the path we take, and I'll close the other one. Nothing here is permanent: you can "
    "switch later by asking me."
)

# Path A: stop at the sandbox and read the guide. Advances nothing; the channel stays at `deploy`.
GUIDE_PATH_PROMPT = (
    "📘 *Option 1: keep it in the private sandbox, and show me the guide.*\n\n"
    "Your labeler keeps running here where only your group sees it. I'll give you a short guide to "
    "running and maintaining it: what the rules are doing, how to change them, and what going "
    "live would involve if you decide you want it later.\n\n"
    "👍🏾 React to *this* message for the guide."
)

# Path B: the real go-live gate. Reacting registers pending_provision_approval and advances
# deploy -> provision, which opens the governance questions.
GO_LIVE_PATH_PROMPT = (
    "🚀 *Option 2: find out what going live would involve.*\n\n"
    "Going live would give your labeler a permanent, public Bluesky identity that people outside "
    "your group can subscribe to. Before any of that, your group needs to settle three things: "
    "what it's called on Bluesky, who holds the account, and who hears from someone who thinks "
    "they were labeled unfairly.\n\n"
    "👍🏾 React to *this* message and I'll put those three questions to the group. Answering them "
    "creates nothing and publishes nothing. It's a conversation, and you can stop at any point."
)

# Appended to the path the group did not take. Distinct from SUPERSEDED_NOTE: nothing replaced this
# card and it was never wrong — the group chose the other option.
#
# One note per path, because the two ways back are not the same. Going live needs a fresh vote, so
# that note names the phrase that reopens it. The guide needs no vote at all — it's a link — so
# that note just hands it over rather than promising a reopening that would have to be built.
# Named for the card each one lands on, not the path that won.
GUIDE_PATH_CLOSED_NOTE = (
    "— ⤵️ *The group chose going live, so this option is closed. The guide is here whenever you "
    "want it: {guide_url}*"
)
GO_LIVE_PATH_CLOSED_NOTE = (
    "— ⤵️ *The group chose the guide, so this option is closed. Changed your mind? Say "
    "`@CLEO go live` and I'll put it back to the group.*"
)

# Posted when the guide path wins. `{guide_url}` is filled at the callsite.
GUIDE_CHOSEN_MSG = (
    "📘 *Here's your guide:* {guide_url}\n\n"
    "It covers what your rules are doing, how to change them, and what going live would involve. "
    "Your labeler keeps running in the private sandbox in the meantime.\n\n"
    "Changed your mind? Say `@CLEO go live` and I'll put the going-live questions to the group."
)

# Posted when someone reopens the go-live path after the group took the guide. Doubles as a fresh
# anchor, since the original was committed and can never fire again.
GO_LIVE_REOPENED_MSG = (
    "🚀 *Reopening the going-live path.*\n\n" + GO_LIVE_PATH_PROMPT.split("\n\n", 1)[1]
)

# Posted when the group opens the go-live gate (deploy -> provision). Asks all three questions at
# once rather than interrogating one at a time — a group chat answers out of order anyway, and the
# extractor takes whatever lands. States up front that nothing irreversible happens here, and names
# the email requirement WITHOUT asking for an address (there's no mechanism to take one).
def provision_stage_intro(candidates: list[str]) -> str:
    options = "\n".join(f"  {i}. `@{c}`" for i, c in enumerate(candidates, start=1))
    return (
        "📝 *Three things to settle.* Nothing here creates an account or publishes anything. This "
        "is your group deciding, and I'll write the answers down.\n\n"
        "*1. What should the labeler be called on Bluesky?* This is the name people subscribe to. "
        f"Some suggestions based on your labeler's name:\n{options}\n"
        "Pick one or tell me something else. (I can't reserve it yet, so it's a preference, not a "
        "claim.)\n\n"
        "*2. Who holds the account?* Name one person as custodian. They'd hold the account on the "
        "group's behalf. They are a caretaker, not an owner, and the group can swap them out at any time. "
        "A second person as backup is a good idea.\n\n"
        "*3. Who hears appeals?* When someone thinks they were labeled unfairly, who do they talk "
        "to? Can be a person or a group.\n\n"
        "✉️ *Both of those roles will need an email address*. The custodian's becomes the account's "
        "recovery address, and the appeals contact's is the one you'd publish so people can reach a "
        "human. I'm not collecting addresses here, so don't post them in the channel. Just worth "
        "agreeing now whose they'd be.\n\n"
        "Answer in any order, and take as long as you like."
    )


# The confirm card: what CLEO heard, staged for approval. Reacting commits it to the governance
# record. `{answers}` and `{remaining}` are filled at the callsite.
GOVERNANCE_CONFIRM_CARD = (
    "📋 *Here's what I've got:*\n\n{answers}\n\n"
    "👍🏾 React to *this* message to confirm. Got something wrong? Just say so and I'll correct it."
)

# Appended to the confirm card when answers are still missing.
GOVERNANCE_REMAINING_MSG = "\n\nStill to settle: {remaining}."

# Posted when the group asks to stop the going-live questions. Doubles as a FRESH go-live anchor
# (the original one is marked committed and can never fire again), so "whenever you're ready" is a
# real offer and not a figure of speech. `{kept}` and `{guide_url}` are filled at the callsite.
PROVISION_STOOD_DOWN_MSG = (
    "👍🏾 *No problem — parked.* Your labeler is exactly as it was: private, in the sandbox, nothing "
    "published.{kept}\n\n"
    "👍🏾 React to *this* message whenever your group wants to pick the questions back up. There's no "
    "deadline and nothing expires.\n\n"
    "📘 Your guide has the whole picture, including anything you'd still need: {guide_url}"
)

# The reassurance slotted into PROVISION_STOOD_DOWN_MSG when answers were already banked.
PROVISION_KEPT_ANSWERS_MSG = " I've kept what you'd already settled:\n\n{answers}"

# Posted once every collected answer is recorded and approved. This is the end of what the system
# can do today, and says so — the alternative is leaving a group waiting for a next step that never
# comes. Deliberately does NOT promise a timeline for deployment.
GOVERNANCE_COMPLETE_MSG = (
    "✅ *All three settled.* Your group's answers are recorded:\n\n{answers}\n\n"
    "That's as far as I can take it. Creating the actual Bluesky account needs the two email "
    "addresses and a step I can't run yet, so your labeler stays private in the sandbox for now — "
    "unchanged, and nothing published.\n\n"
    "📘 Your answers are written into your guide, so nothing is lost: {guide_url}"
)