"""Spoken-language detection and requirement matching.

Detects when a posting *requires* a language (not just mentions it), so a role
that needs Japanese can be flagged when the candidate didn't list Japanese.
"""
import re

# canonical language -> alias regex
_LANG = {
    "English": r"english",
    "Spanish": r"spanish|español|castilian",
    "French": r"french|français",
    "German": r"german|deutsch",
    "Japanese": r"japanese|日本語|nihongo",
    "Mandarin": r"mandarin|chinese|普通话|中文",
    "Cantonese": r"cantonese",
    "Korean": r"korean|한국어",
    "Portuguese": r"portuguese|português",
    "Italian": r"italian|italiano",
    "Dutch": r"dutch|nederlands",
    "Russian": r"russian",
    "Arabic": r"arabic",
    "Hindi": r"hindi",
    "Hebrew": r"hebrew",
    "Polish": r"polish",
    "Swedish": r"swedish",
    "Turkish": r"turkish",
    "Vietnamese": r"vietnamese",
    "Thai": r"thai",
    "Indonesian": r"indonesian|bahasa",
    "Tagalog": r"tagalog|filipino",
}

# qualifier words that, near a language, signal a real requirement
_QUAL = (r"fluen\w+|proficien\w+|nativ\w+|bilingual|trilingual|multilingual|"
         r"speak\w*|spoken|verbal|written|command of|knowledge of|"
         r"business[- ]?level|professional working|conversational|"
         r"required|preferred|a plus|must (?:speak|have)")

_LANG_RE = {}
for canon, alias in _LANG.items():
    # qualifier within ~40 chars before, or requirement word within ~30 after
    before = rf"(?:{_QUAL})\b[^.\n]{{0,40}}?\b(?:{alias})\b"
    after = rf"\b(?:{alias})\b[^.\n]{{0,30}}?(?:{_QUAL}|language)"
    _LANG_RE[canon] = re.compile(f"(?:{before})|(?:{after})", re.I)


def parse_spoken(text: str):
    """Parse a user's free-text languages box into a canonical set."""
    if not text:
        return set()
    spoken = set()
    for canon, alias in _LANG.items():
        if re.search(rf"\b(?:{alias})\b", text, re.I):
            spoken.add(canon)
    return spoken


def required_languages(text: str):
    """Languages a posting appears to require."""
    if not text:
        return set()
    return {canon for canon, rx in _LANG_RE.items() if rx.search(text)}
