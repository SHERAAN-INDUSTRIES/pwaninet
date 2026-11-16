"""Serialize bounded academic evidence without cutting JSON in mid-record."""
import json


def bounded_academic_json(records, limit=18000):
    data = json.loads(json.dumps(records, default=str))
    encode = lambda: json.dumps(data, ensure_ascii=True)
    if len(encode()) <= limit:
        return encode()
    # Preserve identity, scope and latest completed activity before verbose history.
    core = {'id','unit_id','unit_name','unit_code','semester_id','term','read_only',
        'historical_read','write_authorization','mastery_assessed','last_covered_concept',
        'clarification_required','units'}
    for record in data:
        for key in list(record):
            if key not in core:
                del record[key]
        record['history_truncated'] = True
        latest = record.get('last_covered_concept')
        if isinstance(latest, dict):
            record['last_covered_concept'] = {k: latest[k] for k in (
                'topic','session_id','kind','phase','completed_at','message_id','evidence_semantics') if k in latest}
    def bounded(value):
        if isinstance(value, str):
            return value[:500]
        if isinstance(value, list):
            return [bounded(v) for v in value[:8]]
        if isinstance(value, dict):
            return {k:bounded(v) for k,v in value.items()}
        return value
    data = bounded(data)
    while len(encode()) > limit and data:
        data.pop()
    return encode()
