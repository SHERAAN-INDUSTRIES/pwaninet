"""Cheap conservative strategy hints for the existing orchestrator.

A decision never selects/creates a session or grants tool/document access.
Unknown/mixed work retains the existing planner and authorization paths.
"""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class ExecutionStrategy:
    intent: str
    direct: bool = False
    uncertain: bool = False

_RECALL = re.compile(r"\b(?:what (?:did (?:i|we) study|have i (?:studied|covered|learned))|what topics have i completed|what were we discussing|do you remember my|my (?:study history|progress)|where did we stop)\b", re.I)
_RESUME = re.compile(r"\b(?:resume|continue (?:my |the |our )?(?:previous |paused )?(?:lesson|exercise|session)|another hint|same question)\b", re.I)
_COMPLEX = re.compile(r"\b(?:research|revision plan|study plan|compare\b[^.!?]{0,100}\b(?:papers|documents|textbooks|pdfs)|analy[sz]e\b[^.!?]{0,100}\b(?:and|plan)|recent changes|latest)\b", re.I)
_DOCUMENT = re.compile(r"\b(?:page\s+\d+|(?:this|current|the)\s+page|my\s+(?:java\s+)?pdf|(?:my|this|the|highlighted|selected)\s+(?:document|paragraph)|in my (?:document|textbook)|textbook)\b", re.I)
_REASON = re.compile(r"\b(?:calculat\w*|solve|determinant|differentiat\w*|integrat\w*|debug|fix (?:this|my) .*(?:code|method)|derive)\b", re.I)
_EXPLAIN = re.compile(r"\b(?:explain|teach me|what is|what are|define|difference between|how does|describe)\b", re.I)
# A recall mixed with a new operation must not suppress that operation.
_OPERATION = re.compile(r"\b(?:and\s+(?:then\s+)?(?:explain|teach|search|resume|create|solve)|send|remind|schedule|save|delete|search)\b", re.I)


def is_platform_action(text):
    """Execution hint only; tools still validate source, consent and approval."""
    if re.search(r"```|\b(?:how to|how do|how can|don't|do not|document says|pretend|example transcript)\b", text, re.I):
        return False
    return bool(re.search(r'\b(?:send|message|text|follow|notify|remind|save|create|build|draft|set|add|update|schedule|reschedule|change|move|shift|remove|delete|cancel|complete|finish|mark)\b', text, re.I)
        and re.search(r'\b(?:messages?|notifications?|notify|reminders?|alarms?|plans?|study blocks?|study sessions?|follow)\b|@\w+', text, re.I))


def is_platform_lookup(text):
    return bool(re.search(r'\bmy\s+(?:saved\s+)?(?:programme|program|profile|timetable|notifications?|schedule|reminders?|plans?|study blocks?|unread chats?)\b', text, re.I))


def select_strategy(request):
    from pwanimate.services.casual_context import is_greeting
    from pwanimate.services.recall_intent import is_recent_learning_recall
    text = request.query.strip()
    if is_greeting(text):
        return ExecutionStrategy('casual', direct=True)
    if is_platform_action(text):
        return ExecutionStrategy('action')
    if is_platform_lookup(text):
        return ExecutionStrategy('platform')
    if _COMPLEX.search(text):
        return ExecutionStrategy('complex')
    if _RESUME.search(text):
        return ExecutionStrategy('continuation', uncertain=True)
    if _RECALL.search(text) or is_recent_learning_recall(text):
        if _OPERATION.search(text):
            return ExecutionStrategy('mixed', uncertain=True)
        return ExecutionStrategy('recall', direct=True)
    if _DOCUMENT.search(text) and (request.context_resources or request.attachments):
        return ExecutionStrategy('document')
    if request.context_resources or request.attachments or request.sources:
        return ExecutionStrategy('contextual', uncertain=True)
    if _REASON.search(text):
        return ExecutionStrategy('reasoning', direct=True)
    if _EXPLAIN.search(text) and not _OPERATION.search(text):
        return ExecutionStrategy('explanation', direct=True)
    return ExecutionStrategy('uncertain', uncertain=True)
