"""
Default-setting policy for labels.

default_setting is a subscriber's *default preference* for a label, not a description
of what the label does. This version pins it to 'warn' for every label, so the label is
always on by default and what subscribers actually see is decided entirely by the
blurs x severity combination (see BEHAVIOR_TABLE in prompts.py).

The model does not supply default_setting (it is not in the finalize_proposal schema);
this module is where the field gets stamped onto a proposal.
"""

# The only default_setting this version emits. See module docstring.
PINNED_DEFAULT_SETTING = "warn"


def pin_default_setting(labels: list[dict]) -> list[dict]:
    """Stamp default_setting='warn' onto every label. Input labels are not mutated."""
    pinned: list[dict] = []
    for label in labels or []:
        label = dict(label)
        label["default_setting"] = PINNED_DEFAULT_SETTING
        pinned.append(label)
    return pinned
