"""Backfill academic-unit tags on ready documents that have none.

Infers units from title/description/tags via
DocumentAcademicLinkService.auto_link_document so older uploads participate
in unit-scoped retrieval and academic ranking.

Usage:
    python manage.py backfill_document_academic_units [--dry-run] [--limit N]
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from documents.models import Document
from documents.academic.services import DocumentAcademicLinkService


class Command(BaseCommand):
    help = "Infer and link academic units for documents missing DocumentAcademicUnit rows."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report matches without writing.')
        parser.add_argument('--limit', type=int, default=0, help='Max documents to process (0 = all).')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']

        qs = (
            Document.objects
            .filter(status='ready', is_available=True)
            .annotate(unit_links=Count('academic_units'))
            .filter(unit_links=0)
            .select_related('uploaded_by')
            .prefetch_related('document_tags__tag')
            .order_by('id')
        )
        total = qs.count()
        self.stdout.write(f"Found {total} ready documents without academic-unit links.")
        if limit:
            qs = qs[:limit]

        tagged = 0
        unmatched = 0
        for doc in qs.iterator():
            if dry_run:
                parts = [doc.title or '', doc.description or '']
                parts.extend(dt.tag.name for dt in doc.document_tags.all())
                units = DocumentAcademicLinkService.infer_units_from_text(' '.join(parts))
                if units:
                    tagged += 1
                    self.stdout.write(f"  would tag {doc.id} '{doc.title[:60]}' -> {[u.code for u in units]}")
                else:
                    unmatched += 1
                continue
            linked = DocumentAcademicLinkService.auto_link_document(doc)
            if linked:
                tagged += 1
            else:
                unmatched += 1

        verb = 'Would tag' if dry_run else 'Tagged'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {tagged} document(s); {unmatched} had no inferable unit match."
        ))
