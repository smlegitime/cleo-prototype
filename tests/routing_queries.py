routing_queries = [
    # question 
    ("what is a labeler", "question", "labeler", "search_documentation", False),
    ("how does bluesky work", "question", "bluesky", "search_documentation", False),
    ("what is the AT Protocol", "question", "atproto", "search_documentation", False),
    ("how do moderation labels work", "question", "label", "search_documentation", False),

    # feedback 
    ("create a label for spam content", "feedback", None, "provide_feedback", False),
    ("add a label for misinformation", "feedback", None, "provide_feedback", False),
    ("modify the severity of my graphic violence label", "feedback", None, "provide_feedback", False),

    # summary 
    ("can you summarize what we discussed", "summary", None, "summarize_conversation", False),
    ("recap the conversation so far", "summary", None, "summarize_conversation", False),
    ("what are the main takeaways from this conversation", "summary", None, "summarize_conversation", False),

    # show_config 
    ("show me my current labeler config", "show_config", None, "draft_response", False),
    ("what labels do i have set up", "show_config", None, "draft_response", False),
    ("display my labeler configuration", "show_config", None, "draft_response", False),

    # generate_code 
    ("generate code from the current labeler configuration", "generate_code", None, "draft_response", False),
    ("write code for this labeler config", "generate_code", None, "draft_response", False),

    # violation 
    ("i hate all these people they should be banned", None, None, "draft_response", True),
    ("here's my social security number 123-45-6789", None, None, "draft_response", True),
    ("this community is full of idiots and morons", None, None, "draft_response", True),
]

