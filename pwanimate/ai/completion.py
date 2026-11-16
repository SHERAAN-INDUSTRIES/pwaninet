"""Shared response termination contract. Unknown is never successful completion."""


def normalize_finish_reason(reason):
    value = str(reason or '').strip().lower()
    return {'max_tokens': 'length', 'max_output_tokens': 'length',
            'end_turn': 'stop', 'eos': 'stop', 'stop_sequence': 'stop',
            'safety': 'content_filter', 'recitation': 'content_filter'}.get(value, value or 'unknown')


def is_complete(reason, content):
    return normalize_finish_reason(reason) == 'stop' and bool(str(content or '').strip())


def needs_continuation(reason, content):
    return normalize_finish_reason(reason) in {'length', 'interrupted'} and bool(str(content or '').strip())
