"""
Tests for the Spark engine registry, engine-aware routing, and branded surfaces.

Covers decisions D6/D8 in docs/local-models-design.md:
- engines are the user-facing branded picker entries (provider models are plumbing),
- hidden engines behave as if they do not exist,
- engine selection carries an ordered request-level fallback chain into the gateway,
- quota-status serves the branded engine list alongside legacy models[].
"""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from unittest import mock

from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
from pwanimate.ai.engines import (
    UnknownSparkEngineError,
    get_engine,
    get_engines_status,
    identify_engine,
    list_engines,
)
from pwanimate.ai.gateway import AIGateway, ChatMessage, LLMRequest, LLMRouter
from pwanimate.ai.gateway.quota_tracker import get_quota_tracker
from pwanimate.ai.providers import MockLLMProvider
from pwanimate.orchestrator import PwanimateOrchestrator
from pwanimate.orchestrator.types import OrchestrationRequest, OrchestrationResponse
from pwanimate.retrieval import UnifiedRetrievalService
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService


class FakeQuotaTracker:
    """Minimal stand-in exposing the tracker surface engines.py consumes."""

    def __init__(self, limited=None, cooldowns=None):
        self.limited = set(limited or [])
        self.cooldowns = cooldowns or {}

    @staticmethod
    def _key(provider, model):
        return f"{provider}:{model}"

    def is_rate_limited(self, provider, model=None):
        return self._key(provider, model) in self.limited

    def get_cooldown_remaining(self, provider, model=None):
        return self.cooldowns.get(self._key(provider, model), 0.0)


class SparkEngineRegistryTests(SimpleTestCase):

    def test_default_engines_are_selectable(self):
        slugs = [engine["slug"] for engine in list_engines()]
        self.assertEqual(slugs, ["speed", "coda", "scholar", "vision"])
        for engine in list_engines():
            self.assertTrue(engine["fallback_chain"])
            self.assertTrue(engine["name"].startswith("Spark"))
            self.assertIn("tagline", engine)
            self.assertIn("description", engine)

    def test_default_chains_have_interleaved_resilience_ladders(self):
        """The three resilience-ladder engines: a long multi-provider ladder,
        no duplicate (provider, model) rungs, all three real providers
        represented, and a final mock:unavailable rung for friendly-error
        degradation. (Vision keeps a lean, image-capable-only chain.)"""
        for slug in ("speed", "coda", "scholar"):
            chain = get_engine(slug)["fallback_chain"]
            self.assertGreaterEqual(len(chain), 8, slug)
            self.assertEqual(chain[-1], {"provider": "mock", "model": "unavailable"}, slug)
            rungs = [(c["provider"], c["model"]) for c in chain[:-1]]
            self.assertEqual(len(rungs), len(set(rungs)), f"duplicate rung in {slug}")
            self.assertEqual(
                {provider for provider, _ in rungs},
                {"gemini", "groq", "openrouter"},
                f"{slug} must interleave all three real providers",
            )

    def test_vision_engine_leads_with_our_own_account(self):
        """Spark Vision leads with Cloudflare Workers AI.

        The free tier may only depend on capacity we control, and the
        Workers AI allocation belongs to our account -- unlike a Gemini or
        Groq free key, which a provider can revoke at any time. Every rung
        must be image-capable."""
        slugs = [engine["slug"] for engine in list_engines()]
        self.assertIn("vision", slugs)
        vision = get_engine("vision")
        self.assertFalse(vision.get("hidden", False))
        chain = vision["fallback_chain"]
        self.assertEqual(chain[0]["provider"], "cloudflare")
        self.assertTrue(chain[0]["model"].startswith("@cf/"))
        # The paid providers remain as backups behind it.
        self.assertIn("gemini", [rung["provider"] for rung in chain])
        self.assertEqual(chain[-1], {"provider": "groq", "model": "qwen/qwen3.8-27b"})

    def test_every_vision_rung_is_image_capable(self):
        """A rung that cannot read an image would silently drop the turn."""
        from pwanimate.ai.capabilities import provider_supports_images

        vision = get_engine("vision")
        for rung in vision["fallback_chain"]:
            self.assertTrue(
                provider_supports_images(rung["provider"], rung["model"], 1),
                f"{rung['provider']}:{rung['model']} cannot read images",
            )

    def test_get_engine_normalizes_and_rejects_bad_slugs(self):
        self.assertEqual(get_engine(" SPEED ")["slug"], "speed")
        self.assertEqual(get_engine(" VISION ")["slug"], "vision")
        for bad in ("does-not-exist", "", None):
            with self.assertRaises(UnknownSparkEngineError):
                get_engine(bad)

    @override_settings(PWANIMATE_ENGINE_OVERRIDES={"speed": {"name": "Spark Turbo"}})
    def test_engine_override_settings_merge(self):
        speed = get_engine("speed")
        self.assertEqual(speed["name"], "Spark Turbo")
        # Untouched keys survive the merge.
        self.assertTrue(speed["fallback_chain"])
        self.assertEqual(speed["slug"], "speed")

    @override_settings(
        PWANIMATE_SPARK_ENGINES=[
            {"slug": "only", "name": "Only Engine", "fallback_chain": [{"provider": "mock", "model": "m"}]}
        ]
    )
    def test_full_registry_replacement(self):
        self.assertEqual([engine["slug"] for engine in list_engines()], ["only"])

    def test_engine_metadata_contract(self):
        request = OrchestrationRequest(
            query="hi",
            engine="speed",
            fallback_chain=[{"provider": "gemini", "model": "g"}],
        )
        meta = request.engine_metadata()
        self.assertEqual(meta["engine"], "speed")
        self.assertEqual(meta["fallback_chain"], [{"provider": "gemini", "model": "g"}])

        plain = OrchestrationRequest(query="hi")
        self.assertEqual(plain.engine_metadata(), {})


# Pin tiny chains so these tests exercise the rollup's accounting logic,
# decoupled from how far the curated resilience ladders grow over time.
_ENGINE_CHAIN_PINS = {
    "speed": {"fallback_chain": [
        {"provider": "gemini", "model": "gemini-3.8-flash"},
        {"provider": "groq", "model": "openai/gpt-oss-120b"},
        {"provider": "openrouter", "model": "google/gemini-flash-latest"},
    ]},
    "coda": {"fallback_chain": [
        {"provider": "groq", "model": "openai/gpt-oss-120b"},
        {"provider": "gemini", "model": "gemini-3.8-flash"},
        {"provider": "openrouter", "model": "google/gemini-flash-latest"},
    ]},
    "scholar": {"fallback_chain": [
        {"provider": "gemini", "model": "gemini-3.8-flash"},
        {"provider": "openrouter", "model": "google/gemini-flash-latest"},
    ]},
}


@override_settings(PWANIMATE_ENGINE_OVERRIDES=_ENGINE_CHAIN_PINS)
class EngineStatusRollupTests(SimpleTestCase):

    @staticmethod
    def _primary_key(slug):
        return f"{get_engine(slug)['fallback_chain'][0]['provider']}:{get_engine(slug)['fallback_chain'][0]['model']}"

    @override_settings(
        PWANIMATE_GEMINI_API_KEY="g-key",
        PWANIMATE_GROQ_API_KEY="",
        PWANIMATE_OPENROUTER_API_KEY="o-key",
        PWANIMATE_CLOUDFLARE_API_TOKEN="cf-token",
        PWANIMATE_CLOUDFLARE_ACCOUNT_ID="cf-account",
    )
    def test_status_rollup_with_healthy_primary(self):
        by_slug = {e["slug"]: e for e in get_engines_status(FakeQuotaTracker())}

        speed = by_slug["speed"]
        self.assertTrue(speed["is_configured"])
        self.assertFalse(speed["rate_limited"])
        self.assertEqual(speed["primary_status"], "ready")
        # groq key missing → only openrouter remains as a ready fallback.
        self.assertEqual(speed["fallbacks_ready"], 1)
        self.assertIn("Google", speed["powered_by"])

        # Coda's primary is groq (unconfigured here): engine still serves via
        # fallbacks, and primary_status pinpoints WHY it isn't using the primary.
        coda = by_slug["coda"]
        self.assertTrue(coda["is_configured"])
        self.assertTrue(coda["rate_limited"])
        self.assertEqual(coda["primary_status"], "unconfigured")
        self.assertEqual(coda["fallbacks_ready"], 2)

        # Vision leads with Cloudflare Workers AI: configured here → primary
        # ready, and both the Cloudflare backup and Gemini are ready
        # fallbacks. Groq is unconfigured in this test.
        vision = by_slug["vision"]
        self.assertTrue(vision["is_configured"])
        self.assertEqual(vision["primary_status"], "ready")
        self.assertEqual(vision["fallbacks_ready"], 2)
        self.assertIn("Cloudflare", vision["powered_by"])

    @override_settings(
        PWANIMATE_GEMINI_API_KEY="g-key",
        PWANIMATE_GROQ_API_KEY="q-key",
        PWANIMATE_OPENROUTER_API_KEY="o-key",
    )
    def test_status_primary_rate_limited_reports_fallback(self):
        primary_key = self._primary_key("speed")
        tracker = FakeQuotaTracker(
            limited={primary_key},
            cooldowns={primary_key: 42.0},
        )
        speed = {e["slug"]: e for e in get_engines_status(tracker)}["speed"]
        self.assertTrue(speed["is_configured"])
        self.assertTrue(speed["rate_limited"])
        self.assertEqual(speed["primary_status"], "rate_limited")
        self.assertEqual(speed["cooldown_remaining"], 42.0)
        self.assertEqual(speed["fallbacks_ready"], 2)

    @override_settings(
        PWANIMATE_GEMINI_API_KEY="",
        PWANIMATE_GROQ_API_KEY="",
        PWANIMATE_OPENROUTER_API_KEY="",
    )
    def test_status_nothing_configured(self):
        speed = {e["slug"]: e for e in get_engines_status(FakeQuotaTracker())}["speed"]
        self.assertFalse(speed["is_configured"])
        self.assertTrue(speed["rate_limited"])
        self.assertEqual(speed["primary_status"], "unconfigured")
        self.assertEqual(speed["fallbacks_ready"], 0)

    @override_settings(
        PWANIMATE_ENGINE_OVERRIDES={"scholar": {"fallback_chain": [
            {"provider": "gemini", "model": "gemini-3.8-flash"},
            {"provider": "mock", "model": "unavailable"},
        ]}},
        PWANIMATE_GEMINI_API_KEY="",
    )
    def test_status_mock_rung_never_props_up_indicator(self):
        """The friendly-error rung is not a real model: with every real
        provider unconfigured, the engine must report unavailable even
        though mock:unavailable would answer."""
        scholar = {e["slug"]: e for e in get_engines_status(FakeQuotaTracker())}["scholar"]
        self.assertFalse(scholar["is_configured"])
        self.assertEqual(scholar["fallbacks_ready"], 0)
        self.assertNotIn("Dev", scholar["powered_by"])


class EngineFallbackChainRoutingTests(SimpleTestCase):

    @staticmethod
    def _router():
        return LLMRouter(
            providers={"mock": MockLLMProvider(), "gemini": MockLLMProvider()},
            default_provider="mock",
        )

    def test_request_metadata_chain_drives_candidates(self):
        chain = [
            {"provider": "mock", "model": "engine-primary"},
            {"provider": "gemini", "model": "engine-secondary"},
        ]
        request = LLMRequest(
            messages=[ChatMessage(role="user", content="hi")],
            provider="mock",
            model="engine-primary",
            metadata={"engine": "coda", "fallback_chain": chain},
        )
        candidates = self._router().resolve_fallback_chain(request)
        pairs = [(c["provider"], c["model"]) for c in candidates]
        # Engine primary leads; engine chain follows; the global settings
        # chain is NOT mixed in when the request carries its own chain.
        self.assertEqual(pairs, [("mock", "engine-primary"), ("gemini", "engine-secondary")])

    def test_default_chain_still_used_without_engine_metadata(self):
        request = LLMRequest(
            messages=[ChatMessage(role="user", content="hi")],
            provider="mock",
        )
        candidates = self._router().resolve_fallback_chain(request)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["provider"], "mock")


class SparkEngineAPITests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="engine_student",
            email="engine@vasto.local",
            password="testpassword123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_quota_status_includes_branded_engines(self):
        resp = self.client.get(reverse("pwanimate_api:quota_status"))
        self.assertEqual(resp.status_code, 200)

        engines = resp.json()["quota_status"]["engines"]
        self.assertEqual([e["slug"] for e in engines], ["speed", "coda", "scholar", "vision"])
        for engine in engines:
            for key in ("name", "icon", "tagline", "description", "powered_by",
                        "is_configured", "rate_limited", "fallbacks_ready", "primary_status"):
                self.assertIn(key, engine)
            self.assertTrue(engine["name"].startswith("Spark"))
        # Legacy models[] remains for compatibility during the transition.
        self.assertIn("models", resp.json()["quota_status"])

    def test_chat_rejects_unknown_engine(self):
        resp = self.client.post(
            reverse("pwanimate_api:chat"),
            {"message": "hello", "engine": "does-not-exist"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_chat_accepts_visible_vision_engine(self):
        """Build③: vision is selectable (interim Gemini multimodal route)."""
        with mock.patch("pwanimate.api.views.PwanimateOrchestrator") as orch_cls:
            orch_cls.return_value.run.return_value = OrchestrationResponse(answer="ok")
            resp = self.client.post(
                reverse("pwanimate_api:chat"),
                {"message": "read this image", "engine": "vision"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["answer"], "ok")


class IdentifyEngineTests(SimpleTestCase):
    """Brand-level attribution: a serving (provider, model) maps back to an
    engine name so the UI can credit Speed/Coda/Scholar/Vision instead of
    leaking raw model identities (Build③ privacy rule)."""

    def test_primary_match_wins(self):
        primary = get_engine("speed")["fallback_chain"][0]
        self.assertEqual(
            identify_engine(primary["provider"], primary["model"]),
            "Spark Speed",
        )

    def test_non_primary_rung_attributes_to_an_engine(self):
        name = identify_engine("groq", "qwen/qwen3.8-27b")
        self.assertTrue(name is not None and name.startswith("Spark"), name)

    def test_unknown_and_mock_never_attribute(self):
        self.assertIsNone(identify_engine("someprovider", "some-model"))
        self.assertIsNone(identify_engine("mock", "unavailable"))
        self.assertIsNone(identify_engine(None, None))
        self.assertIsNone(identify_engine("gemini", None))


class SparkEngineChatRoutingTests(TestCase):
    """End-to-end: an engine selection resolves to primary provider/model
    and carries engine + chain into gateway metadata."""

    def setUp(self):
        get_quota_tracker().clear()
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="engine_router_student",
            email="router@vasto.local",
            password="testpassword123",
        )
        self.client.force_authenticate(user=self.user)

        mock_emb = MockEmbeddingProvider(dimensions=768)
        doc_service = DocumentSemanticRetrievalService(embedding_provider=mock_emb)
        retrieval_service = UnifiedRetrievalService(document_service=doc_service)

        self.gateway = AIGateway(
            router=LLMRouter(providers={"mock": MockLLMProvider()}, default_provider="mock")
        )
        self.orchestrator = PwanimateOrchestrator(
            retrieval_service=retrieval_service,
            gateway=self.gateway,
        )
        from pwanimate.api.views import PwanimateChatView
        PwanimateChatView.orchestrator = self.orchestrator

        # Spy on the gateway to capture what the router receives.
        self.captured = {}
        original_generate = self.gateway.generate

        def spy(llm_request):
            self.captured["metadata"] = dict(llm_request.metadata or {})
            self.captured["provider"] = llm_request.provider
            self.captured["model"] = llm_request.model
            return original_generate(llm_request)

        self.gateway.generate = spy

    def tearDown(self):
        from pwanimate.api.views import PwanimateChatView
        PwanimateChatView.orchestrator = None
        get_quota_tracker().clear()

    @override_settings(
        PWANIMATE_ENGINE_OVERRIDES={
            "coda": {"fallback_chain": [{"provider": "mock", "model": "mock-code-1"}]}
        }
    )
    def test_engine_selection_routes_primary_and_carries_chain(self):
        resp = self.client.post(
            reverse("pwanimate_api:chat"),
            {"message": "fix my C pointer bug", "engine": "coda"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.captured["provider"], "mock")
        self.assertEqual(self.captured["model"], "mock-code-1")
        self.assertEqual(self.captured["metadata"].get("engine"), "coda")
        self.assertEqual(
            self.captured["metadata"].get("fallback_chain"),
            [{"provider": "mock", "model": "mock-code-1"}],
        )

    def test_auto_mode_carries_no_engine_metadata(self):
        resp = self.client.post(
            reverse("pwanimate_api:chat"),
            {"message": "hello there"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("engine", self.captured["metadata"])
        self.assertNotIn("fallback_chain", self.captured["metadata"])
