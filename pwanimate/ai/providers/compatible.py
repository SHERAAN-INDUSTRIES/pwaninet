"""Conservative shared transport for the independent expansion providers.

No SDK retries, raw error bodies, headers, or credentials enter response metadata.
"""
import json
from django.conf import settings
import requests
from pwanimate.ai.gateway.base import BaseLLMProvider
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.ai.exceptions import (AIProviderAPIError, AIProviderAuthenticationError,
    AIProviderConfigurationError, AIProviderRateLimitError, AIProviderTimeoutError)
from pwanimate.ai.providers.groq import GroqLLMProvider

PROVIDERS = {
    'zai': ('ZAI_API_KEY', 'https://api.z.ai/api/paas/v4', 'glm-4.7-flash'),
    'alibaba': ('ALIBABA_DASHSCOPE_API_KEY', 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', 'qwen3.8-flash'),
    'tencent': ('TENCENT_TOKENHUB_API_KEY', 'https://tokenhub-intl.tencentcloudmaas.com/v1', 'hy3'),
    'deepseek': ('DEEPSEEK_API_KEY', 'https://api.deepseek.com', 'deepseek-flash'),
}
# Exact catalog IDs checked 2026-11-14. Advanced capabilities remain gated
# pending model-specific validation; provider-wide claims are insufficient.
MODELS = {
    'zai': {'glm-4.7-flash': 'free', 'glm-4.5-flash': 'free'},
    'alibaba': {'qwen3.8-flash': 'quota', 'qwen3.7-plus': 'quota'},
    'tencent': {'hy3': 'quota', 'hy4': 'quota'},
    'deepseek': {'deepseek-flash': 'paid', 'deepseek-v4-pro': 'paid'},
}
BASIC_TASKS = {'general', 'conversational', 'summary', 'summarization'}


def with_zai_fallbacks(chain):
    """Insert explicitly enabled free text models before aggregator fallback.

    Eligibility remains the router/adapter's responsibility for each task.
    Existing explicitly positioned candidates retain their precedence.
    """
    result = list(chain)
    if not getattr(settings, 'PWANIMATE_EXPANSION_ENABLED', {}).get('zai', False):
        return result
    if not getattr(settings, 'ZAI_API_KEY', '').strip():
        return result
    enabled = getattr(settings, 'PWANIMATE_EXPANSION_MODELS', {}).get('zai', [])
    existing = {(c.get('provider'), c.get('model')) for c in result if isinstance(c, dict)}
    additions = [{'provider': 'zai', 'model': model} for model in MODELS['zai']
                 if model in enabled and ('zai', model) not in existing]
    index = next((i for i, c in enumerate(result) if isinstance(c, dict)
                  and c.get('provider') in {'openrouter', 'mock'}), len(result))
    result[index:index] = additions
    return result


class CompatibleLLMProvider(BaseLLMProvider):
    def __init__(self, provider_name, *, api_key=None, session=None):
        self.provider_name = provider_name
        key, self.base_url, self.model_name = PROVIDERS[provider_name]
        self.api_key = getattr(settings, key, '') if api_key is None else api_key
        self.session = session or requests.Session()

    def is_available(self):
        return bool(self.api_key and getattr(settings, 'PWANIMATE_EXPANSION_ENABLED', {}).get(self.provider_name, False))

    def capabilities(self, model):
        return {'streaming': True, 'tools': False, 'json': False, 'image': False,
                'tasks': sorted(BASIC_TASKS), 'pricing_verified': '2026-11-14',
                'cost_class': MODELS[self.provider_name].get(model),
                'list_price_per_m_usd': ({'input_cache_miss_peak': '0.30', 'output_peak': '1.20'}
                    if model == 'deepseek-flash' else {'input_cache_miss_peak': '1.32', 'output_peak': '3.96'}
                    if model == 'deepseek-v4-pro' else None),
                'input_price_per_m_usd': 0 if MODELS[self.provider_name].get(model) == 'free' else None,
                'output_price_per_m_usd': 0 if MODELS[self.provider_name].get(model) == 'free' else None}

    def eligible(self, request, model):
        kind = MODELS[self.provider_name].get(model)
        enabled = getattr(settings, 'PWANIMATE_EXPANSION_MODELS', {}).get(self.provider_name, [])
        if not self.is_available() or not kind or model not in enabled:
            return False
        # Paid support remains fail-closed until accounting can reserve a budget
        # atomically across workers. A boolean alone must never authorize spend.
        if kind in {'quota', 'paid'}:
            ent = getattr(settings, 'PWANIMATE_EXPANSION_ENTITLEMENTS', {}).get(self.provider_name, {})
            from django.utils import timezone
            try:
                from datetime import datetime
                expiry = datetime.fromisoformat(ent.get('expires_at', ''))
                valid = expiry > timezone.now()
            except (ValueError, TypeError):
                valid = False
            if not (valid and ent.get('free_only_confirmed') is True and model in ent.get('models', [])
                    and (kind != 'paid' or ent.get('promotional_balance_confirmed') is True)):
                return False
        if request.task not in BASIC_TASKS:
            return False
        meta = request.metadata or {}
        if meta.get('required_capability') or meta.get('tools') or meta.get('response_format'):
            return False
        return not (request.attachments or any(m.attachments for m in request.messages))

    def _run(self, request, on_token=None):
        model = request.model or self.model_name
        if not self.eligible(request, model):
            raise AIProviderConfigurationError('Model is disabled, unentitled, or lacks required capability.', provider=self.provider_name)
        # Reuse existing bounded context/message construction, without Groq transport.
        payload = GroqLLMProvider._build_payload(self, request, model)
        if on_token is not None:
            payload['stream'] = True
            payload['stream_options'] = {'include_usage': True}
        resp = None
        try:
            resp = self.session.post(self.base_url + '/chat/completions', json=payload,
                headers={'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'},
                timeout=(getattr(settings, 'PWANIMATE_LLM_CONNECT_TIMEOUT', 5),
                         getattr(settings, 'PWANIMATE_LLM_TIMEOUT', 30)), stream=on_token is not None,
                allow_redirects=False)
            status = resp.status_code
            if status != 200:
                cls = AIProviderAuthenticationError if status in (401, 403) else AIProviderRateLimitError if status == 429 else AIProviderAPIError
                retry = resp.headers.get('Retry-After')
                raise cls('Provider request rejected.', provider=self.provider_name, status_code=status,
                          details={'retry_after': retry} if retry else {})
            if on_token is None:
                return self._parse(resp.json(), request, model)
            parts, usage, finish, stream_id = [], {}, None, None
            for line in resp.iter_lines():
                line = line.decode('utf-8') if isinstance(line, bytes) else line
                if not line or not line.startswith('data:'):
                    continue
                raw = line[5:].strip()
                if raw == '[DONE]':
                    break
                chunk = json.loads(raw)
                if chunk.get('error'):
                    raise ValueError('stream error')
                stream_id = chunk.get('id') or stream_id
                usage = chunk.get('usage') or usage
                for choice in chunk.get('choices', [])[:1]:
                    piece = (choice.get('delta') or {}).get('content')
                    if piece:
                        parts.append(piece)
                        on_token(piece)
                    finish = choice.get('finish_reason') or finish
            return self._parse({'id': stream_id, 'usage': usage, 'choices': [
                {'message': {'content': ''.join(parts)}, 'finish_reason': finish}]}, request, model)
        except requests.Timeout:
            raise AIProviderTimeoutError('Provider timed out.', provider=self.provider_name) from None
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise AIProviderAPIError('Provider transport or response invalid.', provider=self.provider_name) from None
        finally:
            if resp is not None:
                resp.close()

    def _parse(self, data, request, model):
        choice = data['choices'][0]
        content = choice['message'].get('content') or ''
        if not isinstance(content, str) or not content.strip():
            raise AIProviderAPIError('Provider returned no text.', provider=self.provider_name)
        usage = data.get('usage') or {}
        finish = choice.get('finish_reason')
        return LLMResponse(content=content, provider=self.provider_name, model=model,
            finish_reason=finish, prompt_tokens=usage.get('prompt_tokens'),
            completion_tokens=usage.get('completion_tokens'), total_tokens=usage.get('total_tokens'),
            citations=list(request.context.citations) if request.context else [],
            metadata={'provider_finish_reason': finish, 'terminal_finish_received': bool(finish),
                      'provider_request_id': data.get('id'), 'usage_reported': bool(usage),
                      'usage': usage, 'reported_model': data.get('model')})

    def generate(self, request):
        return self._run(request)

    def generate_stream(self, request, on_token):
        return self._run(request, on_token)
