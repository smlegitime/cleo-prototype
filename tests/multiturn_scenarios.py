from dataclasses import dataclass, field

@dataclass
class MultiTurnScenario:
    scenario_id: str
    description: str
    messages: list[str]
    rubric: str = field(default="")


scenarios: list[MultiTurnScenario] = [
    MultiTurnScenario(
        scenario_id="mt-01-single-question",
        description=(
            "Baseline: single-turn Q&A about Bluesky. "
            "Exercises: router -> validate_and_classify -> search_documentation -> draft_response."
        ),
        messages=["what is bluesky"],
        rubric="The answer should correctly explain what Bluesky is, a decentralized social network.",
    ),
    MultiTurnScenario(
        scenario_id="mt-02-search-then-summary",
        description=(
            "Ask about labelers, then request a summary. "
            "Exercises: search_documentation on turn 1, summarize_conversation on turn 2. "
            "The summary must reference information from the search result."
        ),
        messages=[
            "how do labelers work in bluesky",
            "summarize what you just explained",
        ],
        rubric="The summary should accurately recap the explanation of labelers from turn 1.",
    ),
    MultiTurnScenario(
        scenario_id="mt-03-feedback-then-modify",
        description=(
            "Create a label via feedback, then modify it in a second turn. "
            "Exercises: provide_feedback on both turns, verifying the feedback agent "
            "carries context across turns. "
            "The modified proposal must reflect the changes requested in turn 2."
        ),
        messages=[
            "create a spam label with severity inform and blurs none",
            "change the severity from inform to alert",
        ],
        rubric=(
            "The response should acknowledge the prior spam label and indicate that "
            "its severity has been changed to alert."
        ),
    ),
    MultiTurnScenario(
        scenario_id="mt-04-feedback-then-show-config",
        description=(
            "Create a label via feedback, then ask to view the configuration. "
            "Exercises: provide_feedback -> draft_response, then show_config -> draft_response. "
            "The shown config should reflect the label created in turn 1."
        ),
        messages=[
            "create a spam label with severity inform and blurs none",
            "show me my current labeler configuration",
        ],
        rubric="The shown configuration should reference the spam label created in turn 1.",
    ),
    MultiTurnScenario(
        scenario_id="mt-05-three-turn-feedback-lifecycle",
        description=(
            "Build labeler configuration across three feedback turns: create spam label, "
            "add harassment label, then show config. "
            "Exercises: provide_feedback twice, then draft_response for show_config. "
            "Verifies the config accumulates both labels."
        ),
        messages=[
            "create a spam label with severity inform and blurs none",
            "add a harassment label with severity alert and blurs content",
            "show me the full configuration",
        ],
        rubric=(
            "The configuration should include both a spam label and a harassment label."
        ),
    ),
    MultiTurnScenario(
        scenario_id="mt-06-search-feedback-summary",
        description=(
            "Mixed scenario: search a topic, create a label, then request a summary. "
            "Exercises all three sub-graphs: search_documentation, provide_feedback, "
            "and summarize_conversation. "
            "The summary must cover both the informational Q&A and the label creation."
        ),
        messages=[
            "what are labels in atproto",
            "create a label for misinformation with severity alert",
            "summarize everything we've discussed",
        ],
        rubric=(
            "The summary should mention both the explanation of AT Protocol labels "
            "and the creation of a misinformation label."
        ),
    ),
    MultiTurnScenario(
        scenario_id="mt-07-feedback-then-violation",
        description=(
            "Multi-turn violation handling: first turn creates a label via feedback, "
            "second turn contains a community guidelines violation. "
            "Exercises: provide_feedback -> draft_response, then draft_response (violation path). "
            "The violation response should still acknowledge the prior conversation context."
        ),
        messages=[
            "create a spam label for my community",
            "fuck this labeler it's useless and you're all morons",
        ],
        rubric=(
            "The response should address the violation while still acknowledging the "
            "label creation context from turn 1."
        ),
    ),
    MultiTurnScenario(
        scenario_id="mt-08-full-lifecycle",
        description=(
            "Longest scenario covering all sub-graph paths: search, feedback, "
            "modify feedback, then show config. "
            "Exercises: search_documentation, provide_feedback (x2), draft_response (show_config). "
            "Comprehensive test of state accumulation across 4 turns."
        ),
        messages=[
            "what are labels in bluesky",
            "create a label for spam with severity inform",
            "change the severity to alert and blurs to content",
            "show me the full config",
        ],
        rubric=(
            "The final configuration should reference the spam label with alert severity "
            "and content blur, reflecting the modifications made in turn 3."
        ),
    ),
]
