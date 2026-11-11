from django.core.management.base import BaseCommand
from pwanimate.models import PwanimateConversation
from pwanimate.services.study_memory import StudyMemoryService


class Command(BaseCommand):
    help = 'Generate missing study summaries from existing owned conversations (bounded, explicit backfill).'
    def add_arguments(self, parser):
        parser.add_argument('--user-id', required=True, type=int)
        parser.add_argument('--limit', default=5, type=int)
    def handle(self, *args, **options):
        ids = PwanimateConversation.objects.filter(user_id=options['user_id'], sparkconversationdigest__isnull=True).order_by('-updated_at').values_list('pk', flat=True)[:max(1,min(options['limit'],20))]
        success = 0
        for pk in ids:
            try:
                success += bool(StudyMemoryService.refresh(pk))
            except Exception:
                self.stderr.write('Summary unavailable; transcript preserved and retry remains safe.')
        self.stdout.write(f'Updated {success} study summaries.')
