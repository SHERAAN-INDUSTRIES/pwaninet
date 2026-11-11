from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from calling.models import Call
from calling.services import expire


class Command(BaseCommand):
    help = 'Expire unanswered voice invitations. Run regularly from a scheduler.'

    def handle(self, *args, **options):
        count = 0
        for pk in Call.objects.filter(status='inviting', expires_at__lte=timezone.now()).values_list('pk', flat=True):
            with transaction.atomic():
                expire(Call.objects.select_for_update().get(pk=pk))
                count += 1
        self.stdout.write(f'Processed {count} expired invitations.')
