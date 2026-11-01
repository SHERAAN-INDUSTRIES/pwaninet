"""
Popularity-first re-embed driver (design doc §6 step 3, checklist item 5).

Enqueues Celery embedding tasks for every document version whose chunks are
not yet embedded with the target model, **most popular documents first**, so
the retrievable corpus recovers with the most useful documents first while the
rest migrate in the background.

Why popularity-first: retrieval filters chunks by the active model stamp, so
the moment the provider flips, chunks still on the old model become
invisible to semantic search (lexical/FTS still finds them). Migrating the
highest-traffic documents first keeps answer quality high for the traffic
that matters while the tail is still being converted.

Usage:
    python manage.py reembed_corpus --dry-run            # plan only
    python manage.py reembed_corpus                       # run it (waves of N)
    python manage.py reembed_corpus --wait --concurrency 4
    python manage.py reembed_corpus --model multilingual-e5-base --dry-run

The work itself is done by the existing `embed_document_version` Celery task
(asynchronous, retrying) — this command only orders and enqueues, and never
blocks the Django request path.
"""

import time

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from documents.engagement.models import DocumentAnalytics
from documents.models import Document, DocumentVersion
from pwanimate.ai.embeddings.factory import get_embedding_provider
from pwanimate.models import DocumentChunk


class Command(BaseCommand):
    help = "Popularity-first re-embed driver: enqueues Celery embedding tasks for chunks not yet on the active model."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            default=None,
            help="Target model stamp (default: the active provider's model name).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the plan (documents, chunk counts, estimate) without enqueuing.",
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=4,
            help="Embedding tasks in flight at once (waves). The worker has a single inference slot.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max documents to process (0 = all).",
        )
        parser.add_argument(
            "--wait",
            action="store_true",
            help="Block until the enqueued wave is embedded (the Django server is never involved).",
        )
        parser.add_argument(
            "--poll-seconds",
            type=int,
            default=5,
            help="Progress poll interval when --wait is set.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=7200,
            help="Seconds to wait for a wave before giving up (default 2h).",
        )
        parser.add_argument(
            "--wave-timeout",
            type=int,
            default=600,
            help="Seconds to wait for one wave before re-enqueuing it (a killed worker loses its task).",
        )
        parser.add_argument(
            "--wave-retries",
            type=int,
            default=2,
            help="How many times a stalled wave is re-enqueued before moving on.",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Also re-embed chunks from superseded versions (wasteful; off by default).",
        )

    # ------------------------------------------------------------------ utils
    def _target_model(self, override):
        if override:
            return override
        try:
            return get_embedding_provider().get_model_name()
        except Exception as exc:  # noqa: BLE001 - the provider may be unconfigured
            self.stderr.write(self.style.WARNING(f"Could not resolve active provider: {exc}"))
            return None

    def _chunks_needing_work(self, target, include_inactive):
        """Exactly the task's selection: anything not (completed AND on target)."""
        needing = DocumentChunk.objects.exclude(
            embedding_status="completed", embedding_model=target
        )
        if not include_inactive:
            # Keep chunks that are still retrievable plus anything that has
            # never been embedded (pending/failed); drop superseded chunks.
            needing = needing.filter(Q(is_active=True) | ~Q(embedding_status="completed"))
        return needing

    def _popularity_map(self):
        return dict(
            DocumentAnalytics.objects.values_list("document_id", "popularity_score")
        )

    def _plan(self, target, include_inactive, limit):
        """Ordered list of (version, document, chunk_count) — popular first."""
        needing = self._chunks_needing_work(target, include_inactive)
        per_version = (
            needing.values("document_version_id")
            .annotate(n=Count("id"))
            .filter(n__gt=0)
        )
        counts = {row["document_version_id"]: row["n"] for row in per_version}
        if not counts:
            return []

        versions = {
            v.id: v
            for v in DocumentVersion.objects.select_related("document").filter(
                id__in=counts.keys()
            )
        }
        popularity = self._popularity_map()
        created = {
            d.id: d.created_at
            for d in Document.objects.filter(
                id__in={v.document_id for v in versions.values() if v}
            )
        }

        plan = []
        for version_id, chunk_count in counts.items():
            version = versions.get(version_id)
            if version is None:
                continue
            document = version.document
            plan.append(
                {
                    "version_id": version_id,
                    "document_id": document.id,
                    "title": (document.title or "")[:60],
                    "chunks": chunk_count,
                    "popularity": popularity.get(document.id, 0.0) or 0.0,
                    "created_at": created.get(document.id),
                }
            )

        # Popularity first; ties broken by chunk count (bigger win first), then
        # newest document. Popularity is the signal that protects answer
        # quality during the transition.
        plan.sort(
            key=lambda item: (
                -float(item["popularity"] or 0.0),
                -item["chunks"],
            )
        )
        if limit:
            plan = plan[:limit]
        return plan

    # ---------------------------------------------------------------- command
    def handle(self, *args, **options):
        target = self._target_model(options["model"])
        if not target:
            self.stderr.write(self.style.ERROR("No target model resolved; pass --model."))
            return

        plan = self._plan(target, options["include_inactive"], options["limit"])
        total_chunks = sum(item["chunks"] for item in plan)

        self.stdout.write(f"target model stamp : {target}")
        self.stdout.write(f"documents to migrate: {len(plan)}")
        self.stdout.write(f"chunks to re-embed  : {total_chunks}")
        if not plan:
            self.stdout.write(self.style.SUCCESS("Nothing to do — corpus is already on the target model."))
            return

        self.stdout.write("")
        self.stdout.write(f"{'#':>3}  {'pop':>8}  {'chunks':>6}  title")
        for index, item in enumerate(plan[:20], start=1):
            self.stdout.write(
                f"{index:>3}  {float(item['popularity'] or 0.0):>8.1f}  {item['chunks']:>6}  {item['title']}"
            )
        if len(plan) > 20:
            self.stdout.write(f"     ... and {len(plan) - 20} more documents")

        if options["dry_run"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Dry run — nothing enqueued."))
            return

        # Import lazily so --dry-run never touches the broker.
        from pwanimate.tasks.embeddings import embed_document_version

        wave_size = max(1, int(options["concurrency"]))
        wave_timeout = max(30, int(options["wave_timeout"]))
        wave_retries = max(0, int(options["wave_retries"]))
        enqueued = 0
        deadline = time.monotonic() + int(options["timeout"])

        for start in range(0, len(plan), wave_size):
            wave = plan[start:start + wave_size]
            wave_version_ids = [item["version_id"] for item in wave]
            attempt = 0

            while True:
                for item in wave:
                    embed_document_version.delay(version_id=item["version_id"])
                    enqueued += 1
                self.stdout.write(
                    f"enqueued {enqueued}/{len(plan)} documents "
                    f"(wave of {len(wave)} queued, next in flight)"
                )

                if not options["wait"]:
                    break

                # Wait for this wave's chunks to land on the target model. A
                # worker that dies mid-task takes its task with it, so a
                # stalled wave is re-enqueued rather than waited on forever.
                wave_deadline = min(deadline, time.monotonic() + wave_timeout)
                completed = False
                while time.monotonic() < wave_deadline:
                    remaining = self._chunks_needing_work(
                        target, options["include_inactive"]
                    ).filter(document_version_id__in=wave_version_ids).count()
                    if remaining == 0:
                        completed = True
                        break
                    time.sleep(max(1, int(options["poll_seconds"])))

                if completed or attempt >= wave_retries:
                    if not completed:
                        self.stderr.write(
                            self.style.WARNING(
                                f"Wave {wave_version_ids} still incomplete after "
                                f"{attempt + 1} attempt(s) — moving on (it stays queued "
                                "for the next run)."
                            )
                        )
                    break

                attempt += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Wave {wave_version_ids} stalled — re-enqueueing (attempt {attempt + 1})."
                    )
                )

        self.stdout.write("")
        remaining = self._chunks_needing_work(target, options["include_inactive"]).count()
        self.stdout.write(f"enqueued {enqueued} documents; {remaining} chunks still need embedding")
        if remaining == 0:
            self.stdout.write(self.style.SUCCESS("Corpus fully migrated to the target model."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Remaining chunks are queued in Celery (or failed) — re-run this command to resume."
                )
            )
