# ---- Valid configs -----------------------------------------------------------

valid_full = {
    "display_name": "Safety Moderation",
    "description": "Labels harmful content on the network",
    "labels": [
        {
            "identifier": "hate_speech",
            "blurs": "content",
            "severity": "alert",
            "default_setting": "hide",
        },
        {
            "identifier": "spam",
            "blurs": "media",
            "severity": "inform",
            "default_setting": "warn",
            "locales": [
                {"lang": "en", "name": "Spam", "description": "Unwanted promotional content"},
            ],
        },
    ],
}

valid_minimal_labels = {
    "display_name": "Minimal Labeler",
    "labels": [
        {"identifier": "verified"},
    ],
}

valid_empty_labels = {
    "display_name": "Empty Labeler",
    "description": "A labeler with no labels yet",
}

valid_none_config = {}

valid_single_label_with_locales = {
    "display_name": "I18n Labeler",
    "labels": [
        {
            "identifier": "nsfw",
            "blurs": "media",
            "severity": "alert",
            "default_setting": "warn",
            "locales": [
                {"lang": "en", "name": "NSFW", "description": "Not safe for work"},
                {"lang": "fr", "name": "PNSP", "description": "Pas sûr pour le travail"},
            ],
        },
    ],
}

# ---- Invalid configs -----------------------------------------------------------

# Invalid enum value for blurs
malformed_bad_blurs = {
    "labels": [{"identifier": "test", "blurs": "everything"}],
}

# Wrong type for severity
malformed_wrong_type_severity = {
    "labels": [{"identifier": "test", "severity": 42}],
}

# Invalid identifier format (not snake_case)
malformed_bad_identifier = {
    "labels": [{"identifier": "Bad-ID!"}],
}

# Labels is not a list
malformed_labels_wrong_type = {
    "labels": "not_an_array",
}

# Missing required identifier in label
malformed_missing_identifier = {
    "labels": [{"blurs": "content"}],
}

# Identifier has wrong type
malformed_identifier_wrong_type = {
    "labels": [{"identifier": 123}],
}

# Locales is wrong type
malformed_locales_wrong_type = {
    "labels": [
        {
            "identifier": "test",
            "locales": "not_an_array",
        },
    ],
}

# display_name is not a string
malformed_display_name_wrong_type = {
    "display_name": 42,
}

# Invalid default_setting enum
malformed_bad_default_setting = {
    "labels": [{"identifier": "test", "default_setting": "banana"}],
}

# Not a dict at all
malformed_not_a_dict = "this is a string, not a config"

examples = [
    # Valid
    {"inputs": {"labeler_config": valid_full},                    "outputs": {"is_valid": True}},
    {"inputs": {"labeler_config": valid_minimal_labels},          "outputs": {"is_valid": True}},
    {"inputs": {"labeler_config": valid_empty_labels},            "outputs": {"is_valid": True}},
    {"inputs": {"labeler_config": valid_none_config},             "outputs": {"is_valid": True}},
    {"inputs": {"labeler_config": valid_single_label_with_locales}, "outputs": {"is_valid": True}},
    # Invalid
    {"inputs": {"labeler_config": malformed_bad_blurs},           "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_wrong_type_severity}, "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_bad_identifier},      "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_labels_wrong_type},   "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_missing_identifier},  "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_identifier_wrong_type}, "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_locales_wrong_type},  "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_display_name_wrong_type}, "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_bad_default_setting}, "outputs": {"is_valid": False}},
    {"inputs": {"labeler_config": malformed_not_a_dict},          "outputs": {"is_valid": False}},
]
