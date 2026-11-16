"""Shared, whole-request recall cues; never authorization to resume or write."""
import re

_RECENT_LEARNING = re.compile(
    r"(?:spark[,! ]+)?(?:what (?:were we|was i|did we|did i) "
    r"(?:study(?:ing)?|learn(?:ing)?|cover(?:ing)?|discuss(?:ing)?|devour(?:ing)?|work(?:ing)? on|do(?:ing)?)"
    r"(?: (?:last time|last session|previously|in (?:our|my|the) (?:last|previous) (?:chat|conversation|lesson|session)))?"
    r"|where did we (?:stop|leave off)|what was (?:our|my) last (?:topic|lesson|session))"
    r"[.!? ]*", re.I)


def is_recent_learning_recall(query):
    return bool(_RECENT_LEARNING.fullmatch(str(query or '').strip()))
