import json
from unittest.mock import MagicMock
from django.test import SimpleTestCase, override_settings
from pwanimate.ai.gateway.types import LLMRequest, ChatMessage
from pwanimate.ai.gateway.router import LLMRouter
from pwanimate.ai.gateway.gateway import AIGateway
from pwanimate.ai.gateway.quota_tracker import QuotaTracker
from pwanimate.ai.providers.compatible import CompatibleLLMProvider, PROVIDERS, MODELS
from pwanimate.ai.exceptions import AIProviderAPIError, AIProviderConfigurationError

@override_settings(PWANIMATE_EXPANSION_ENABLED={'zai': True},
    PWANIMATE_EXPANSION_MODELS={'zai': ['glm-4.7-flash', 'glm-4.5-flash']})
class Contracts(SimpleTestCase):
    def setUp(self):
        self.session = MagicMock()
        self.response = self.session.post.return_value
        self.response.status_code = 200
        self.response.headers = {}
        self.provider = CompatibleLLMProvider('zai', api_key='placeholder', session=self.session)
        self.req = LLMRequest(messages=[ChatMessage('user', 'Say OK')], max_tokens=8)

    def answer(self, finish='stop'):
        return {'choices': [{'message': {'content': 'OK'}, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}

    def test_nonstream_models(self):
        for model in MODELS['zai']:
            self.req.model = model
            self.response.json.return_value = self.answer()
            result = self.provider.generate(self.req)
            self.assertEqual((result.model, result.total_tokens, result.finish_reason), (model, 3, 'stop'))
            self.assertEqual(self.session.post.call_args.args[0], PROVIDERS['zai'][1] + '/chat/completions')
            self.assertEqual(self.session.post.call_args.kwargs['json']['model'], model)
            self.assertFalse(self.session.post.call_args.kwargs['allow_redirects'])

    def test_stream_terminal(self):
        for finish, expected in [('stop', 'stop'), (None, 'unknown'), ('length', 'length')]:
            self.response.iter_lines.return_value = [
                ('data: ' + json.dumps({'choices': [{'delta': {'content': 'OK'}, 'finish_reason': finish}]})).encode(),
                b'data: [DONE]']
            tokens = []
            result = self.provider.generate_stream(self.req, tokens.append)
            self.assertEqual(tokens, ['OK'])
            self.assertEqual(result.finish_reason, expected)
            self.assertEqual(result.metadata['terminal_finish_received'], bool(finish))

    def test_rejections_and_cooldown(self):
        for status in [429, 503, 401, 402]:
            self.response.status_code = status
            self.response.headers = {'Retry-After': '90'}
            try:
                self.provider.generate(self.req)
                self.fail('accepted rejection')
            except Exception as exc:
                self.assertEqual(exc.status_code, status)
                tracker = QuotaTracker(cooldown=60)
                tracker.record_failure('zai', 'glm-4.7-flash', exc)
                self.assertTrue(tracker.is_rate_limited('zai', 'glm-4.7-flash'))
                self.assertGreaterEqual(tracker.get_cooldown_remaining('zai', 'glm-4.7-flash'), 89)

    def test_malformed(self):
        for data in [{}, {'choices': []}, {'choices': [{}]}]:
            self.response.json.return_value = data
            with self.assertRaises(AIProviderAPIError):
                self.provider.generate(self.req)
            self.response.close.assert_called()

    def test_unsupported_and_missing(self):
        for task in ['tool', 'rag', 'memory_extraction']:
            self.req.task = task
            self.assertFalse(self.provider.eligible(self.req, 'glm-4.7-flash'))
        self.assertFalse(CompatibleLLMProvider('zai', api_key='').is_available())
        self.assertFalse(CompatibleLLMProvider('deepseek', api_key='placeholder').eligible(self.req, 'deepseek-flash'))
        self.session.post.assert_not_called()

    def test_partial_never_cascades(self):
        def lines():
            yield b'data: {"choices":[{"delta":{"content":"Partial answer"}}]}'
            raise ConnectionError('disconnected')
        self.response.iter_lines.side_effect = lines
        router = LLMRouter(providers={'zai': self.provider}, default_provider='zai')
        gateway = AIGateway(router=router, quota_tracker=QuotaTracker())
        tokens = []
        result = gateway.generate_stream(self.req, tokens.append)
        self.assertEqual(result.finish_reason, 'interrupted')
        self.assertEqual(''.join(tokens), 'Partial answer')
        self.assertEqual(self.session.post.call_count, 1)

    @override_settings(PWANIMATE_TASK_FALLBACK_CHAINS={'general': [{'provider':'zai','model':'glm-4.5-flash'}]})
    def test_task_order(self):
        router = LLMRouter(providers={'zai': self.provider}, default_provider='zai')
        self.assertEqual(router.resolve_fallback_chain(self.req)[0]['model'], 'glm-4.5-flash')

    def test_all_provider_contracts_and_entitlement(self):
        enabled = {p: True for p in PROVIDERS}
        models = {p: list(ids) for p, ids in MODELS.items()}
        entitlements = {p: {'free_only_confirmed': True, 'promotional_balance_confirmed': True,
            'models': list(ids), 'expires_at': '2099-01-01T00:00:00+00:00'} for p, ids in MODELS.items()}
        with override_settings(PWANIMATE_EXPANSION_ENABLED=enabled,
                PWANIMATE_EXPANSION_MODELS=models, PWANIMATE_EXPANSION_ENTITLEMENTS=entitlements):
            for name, ids in MODELS.items():
                adapter = CompatibleLLMProvider(name, api_key='placeholder', session=self.session)
                self.response.json.return_value = self.answer()
                for model in ids:
                    self.req.model = model
                    self.assertTrue(adapter.eligible(self.req, model))
                    result = adapter.generate(self.req)
                    self.assertEqual((result.provider, result.model), (name, model))
                    self.assertEqual(self.session.post.call_args.args[0], PROVIDERS[name][1] + '/chat/completions')
            entitlements['deepseek']['promotional_balance_confirmed'] = False
            adapter = CompatibleLLMProvider('deepseek', api_key='placeholder')
            self.assertFalse(adapter.eligible(self.req, 'deepseek-flash'))

    def test_independent_cascade_and_exhaustion(self):
        from pwanimate.ai.exceptions import AIProviderRateLimitError
        enabled = {p: True for p in PROVIDERS}
        models = {p: list(ids) for p, ids in MODELS.items()}
        entitlements = {p: {'free_only_confirmed': True, 'models': list(ids),
            'expires_at': '2099-01-01T00:00:00+00:00'} for p, ids in MODELS.items()}
        names = ['gemini', 'groq', 'zai', 'alibaba', 'tencent']
        providers = {p: MagicMock(provider_name=p, model_name='test-model') for p in names[:2]}
        for provider in providers.values():
            provider.is_available.return_value = True
            provider.eligible.return_value = True
            provider.generate.side_effect = AIProviderAPIError('overload', status_code=503)
        for name in names[2:]:
            session = MagicMock()
            session.post.return_value.status_code = 503
            session.post.return_value.headers = {}
            providers[name] = CompatibleLLMProvider(name, api_key='placeholder', session=session)
        chain = [{'provider': p, 'model': PROVIDERS[p][2] if p in PROVIDERS else 'test-model'} for p in names]
        with override_settings(PWANIMATE_EXPANSION_ENABLED=enabled, PWANIMATE_EXPANSION_MODELS=models,
                PWANIMATE_EXPANSION_ENTITLEMENTS=entitlements, PWANIMATE_TASK_FALLBACK_CHAINS={'general':chain}):
            tracker = QuotaTracker()
            gateway = AIGateway(router=LLMRouter(providers=providers, default_provider='gemini'), quota_tracker=tracker)
            with self.assertRaises(AIProviderAPIError):
                gateway.generate(self.req)
            for candidate in chain:
                self.assertTrue(tracker.is_rate_limited(candidate['provider'], candidate['model']))
            with self.assertRaises(AIProviderRateLimitError):
                gateway.generate(self.req)
            for name in names[2:]:
                self.assertEqual(providers[name].session.post.call_count, 1)

    def test_each_independent_transition_succeeds(self):
        from pwanimate.ai.gateway.types import LLMResponse
        names = ['gemini', 'groq', 'zai', 'alibaba', 'tencent', 'openrouter']
        for first, second in zip(names, names[1:]):
            providers = {}
            for name in [first, second]:
                provider = MagicMock(provider_name=name, model_name='contract-model')
                provider.is_available.return_value = True
                provider.eligible.return_value = True
                providers[name] = provider
            providers[first].generate.side_effect = AIProviderAPIError('overload', status_code=503)
            providers[second].generate.return_value = LLMResponse('OK', second, 'contract-model', finish_reason='stop')
            chain = [{'provider': n, 'model': 'contract-model'} for n in [first, second]]
            with override_settings(PWANIMATE_TASK_FALLBACK_CHAINS={'general': chain}):
                gateway = AIGateway(router=LLMRouter(providers=providers, default_provider=first), quota_tracker=QuotaTracker())
                result = gateway.generate(self.req)
                self.assertEqual(result.provider, second)
                self.assertTrue(result.metadata['fallback_used'])
                self.assertEqual(providers[first].generate.call_count, 1)

    def test_timeout_and_network_are_sanitized(self):
        import requests
        from pwanimate.ai.exceptions import AIProviderTimeoutError
        for exc, expected in [(requests.Timeout('sensitive request'), AIProviderTimeoutError),
                              (requests.ConnectionError('sensitive request'), AIProviderAPIError)]:
            self.session.post.side_effect = exc
            with self.assertRaises(expected) as caught:
                self.provider.generate(self.req)
            self.assertNotIn('sensitive request', str(caught.exception))

    def test_expired_quota_is_ineligible(self):
        with override_settings(PWANIMATE_EXPANSION_ENABLED={'alibaba':True},
                PWANIMATE_EXPANSION_MODELS={'alibaba':['qwen3.8-flash']},
                PWANIMATE_EXPANSION_ENTITLEMENTS={'alibaba': {'free_only_confirmed':True,
                    'models':['qwen3.8-flash'], 'expires_at':'2020-01-01T00:00:00+00:00'}}):
            provider = CompatibleLLMProvider('alibaba', api_key='placeholder', session=self.session)
            self.assertFalse(provider.eligible(self.req, 'qwen3.8-flash'))
            self.session.post.assert_not_called()

    @override_settings(PWANIMATE_TASK_FALLBACK_CHAINS={'general': [{'provider':'zai','model':'glm-4.5-flash'}]})
    def test_engine_chain_retains_precedence(self):
        self.req.metadata = {'fallback_chain': [{'provider':'zai','model':'glm-4.7-flash'}]}
        router = LLMRouter(providers={'zai': self.provider}, default_provider='zai')
        self.assertEqual(router.resolve_fallback_chain(self.req), [{'provider':'zai','model':'glm-4.7-flash'}])

    def test_retry_after_http_date(self):
        from datetime import datetime, timezone, timedelta
        from email.utils import format_datetime
        tracker = QuotaTracker(cooldown=1)
        exc = AIProviderAPIError('overload', status_code=503,
            details={'retry_after': format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120))})
        tracker.record_failure('zai', 'glm-4.7-flash', exc)
        self.assertGreater(tracker.get_cooldown_remaining('zai', 'glm-4.7-flash'), 118)

    @override_settings(ZAI_API_KEY='placeholder')
    def test_enabled_zai_in_engine_and_default_fallbacks(self):
        from pwanimate.ai.engines import get_engine, _provider_configured
        from pwanimate.ai.gateway.types import LLMResponse
        self.assertTrue(_provider_configured('zai'))
        self.assertNotIn('zai', [c['provider'] for c in get_engine('vision')['fallback_chain']])
        for slug in ['speed', 'coda', 'scholar']:
            chain = get_engine(slug)['fallback_chain']
            zai = [c for c in chain if c['provider'] == 'zai']
            self.assertEqual([c['model'] for c in zai], ['glm-4.7-flash', 'glm-4.5-flash'])
            self.assertLess(chain.index(zai[0]), next(i for i,c in enumerate(chain) if c['provider']=='openrouter'))
        primary = MagicMock(provider_name='gemini', model_name='contract-primary')
        primary.is_available.return_value = True
        primary.eligible.return_value = True
        primary.generate.side_effect = AIProviderAPIError('overload', status_code=503)
        self.response.json.return_value = self.answer()
        for engine_chain in [None, get_engine('speed')['fallback_chain']]:
            self.req.metadata = {'fallback_chain':engine_chain} if engine_chain else {}
            router = LLMRouter(providers={'gemini':primary, 'zai':self.provider}, default_provider='gemini')
            result = AIGateway(router=router, quota_tracker=QuotaTracker()).generate(self.req)
            self.assertEqual((result.provider, result.model), ('zai', 'glm-4.7-flash'))
            self.assertTrue(result.metadata['fallback_used'])
            self.req.task = 'rag'
            self.assertFalse(any(c['provider']=='zai' for c in router.resolve_fallback_chain(self.req)))
            self.req.task = 'general'

    def test_zai_chain_requires_key_and_allowlist(self):
        from pwanimate.ai.providers.compatible import with_zai_fallbacks
        original = [{'provider':'gemini','model':'primary'}, {'provider':'mock','model':'unavailable'}]
        for overrides in [{'ZAI_API_KEY':''}, {'ZAI_API_KEY':'placeholder','PWANIMATE_EXPANSION_ENABLED':{}},
                          {'ZAI_API_KEY':'placeholder','PWANIMATE_EXPANSION_MODELS':{'zai':['glm-5.3']}}]:
            with override_settings(**overrides):
                self.assertEqual(with_zai_fallbacks(original), original)
        with override_settings(ZAI_API_KEY='placeholder'):
            once = with_zai_fallbacks(original)
            self.assertEqual(with_zai_fallbacks(once), once)
