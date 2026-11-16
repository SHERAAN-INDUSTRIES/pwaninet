"""Read-only inspection by default; recovery/requeue require explicit options."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from pwanimate.models import SparkMemoryDispatch
from pwanimate.services.memory_dispatch import recover, valid

class Command(BaseCommand):
    help = 'Inspect delivery counts; --recover dispatches a bounded batch; --retry-failed UUID explicitly requeues one dead letter.'
    def add_arguments(self, parser):
        parser.add_argument('--recover', action='store_true')
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--retry-failed')
    def handle(self, *args, **options):
        if options['retry_failed']:
            with transaction.atomic():
                job = SparkMemoryDispatch.objects.select_for_update().filter(pk=options['retry_failed'], status='failed').first()
                if not job or not valid(job):
                    raise CommandError('Eligible failed request required; stale consent cannot be requeued.')
                job.status, job.attempts, job.next_attempt_at = 'pending', 0, timezone.now()
                job.lease_until, job.finished_at, job.last_error = None, None, 'operator_requeue'
                job.save()
        if options['recover']:
            self.stdout.write(str(recover(options['limit'])))
        self.stdout.write(str(list(SparkMemoryDispatch.objects.values('status').annotate(count=Count('pk')))))
        for row in SparkMemoryDispatch.objects.exclude(status__in=['completed','cancelled']).order_by('next_attempt_at').values(
                'id','status','attempts','next_attempt_at','lease_until','last_error')[:max(1,min(options['limit'],200))]:
            self.stdout.write(str(row))
