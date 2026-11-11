"""Spark diagnostics with an explicit legacy-placeholder quarantine option."""
import json
from collections import Counter
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from pwanimate.ai.embeddings.factory import get_embedding_provider
from pwanimate.models import DocumentChunk
from pwanimate.services.reconciliation import DocumentReconciliationService, classify_version_health
from documents.academic.models import ProgrammeTimetable, TimetableStatus
from documents.academic.timetable_service import is_starter_schedule


class Command(BaseCommand):
    help = 'Report Spark health; records change only with --quarantine-placeholders.'

    def add_arguments(self, parser):
        parser.add_argument('--quarantine-placeholders', action='store_true', help='Mark only exact legacy synthetic schedules failed; retain all source images and data.')
        parser.add_argument('--probe', action='store_true', help='Also test a real query embedding.')

    def handle(self, *args, **options):
        quarantined = 0
        if options['quarantine_placeholders']:
            with transaction.atomic():
                for timetable in ProgrammeTimetable.objects.select_for_update().filter(is_active=True):
                    if is_starter_schedule(timetable.schedule_data):
                        timetable.is_active = False
                        timetable.status = TimetableStatus.FAILED
                        timetable.save(update_fields=['is_active', 'status', 'updated_at'])
                        quarantined += 1
        provider = get_embedding_provider()
        versions = DocumentReconciliationService().get_annotated_versions_queryset()
        inventory = Counter(classify_version_health(v.chunk_count, v.completed_count,
            v.pending_count, v.failed_count, bool(v.file_count)) for v in versions)
        result = {'embedding': {'provider': type(provider).__name__, 'model': provider.get_model_name(),
                  'dimensions': provider.get_dimensions(), 'configured': provider.is_configured()},
                  'versions': dict(inventory),
                  'chunks': list(DocumentChunk.objects.values('embedding_model', 'embedding_status', 'is_active').annotate(count=Count('id'))),
                  'synthetic_active_timetables': sum(is_starter_schedule(t.schedule_data)
                      for t in ProgrammeTimetable.objects.filter(is_active=True))}
        if options['quarantine_placeholders']:
            result['quarantined_placeholders'] = quarantined
        if options['probe']:
            try:
                import time
                start = time.monotonic()
                vector = provider.embed_query('Explain photosynthesis')
                result['embedding']['probe'] = 'ok' if len(vector) == provider.get_dimensions() else 'wrong_dimensions'
                result['embedding']['probe_ms'] = round((time.monotonic() - start) * 1000, 1)
            except Exception as exc:
                result['embedding']['probe'] = type(exc).__name__
        self.stdout.write(json.dumps(result, indent=2))
