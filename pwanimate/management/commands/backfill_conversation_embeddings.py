"""Queue semantic indexing for existing Pwanimate conversation messages."""

from django.core.management.base import BaseCommand

from pwanimate.models import PwanimateMessage, PwanimateMessageEmbedding
from pwanimate.services.conversation import ConversationService


class Command(BaseCommand):
    help = "Queue embeddings for persisted Pwanimate conversation messages."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limit = max(1, options["limit"])
        message_ids = list(
            PwanimateMessage.objects.filter(
                conversation__user__isnull=False,
            ).exclude(
                id__in=PwanimateMessageEmbedding.objects.values("message_id"),
            ).order_by("id").values_list("id", flat=True)[:limit]
        )
        if options["dry_run"]:
            self.stdout.write(f"Would queue {len(message_ids)} conversation message embeddings.")
            return

        for message_id in message_ids:
            ConversationService.schedule_embedding(message_id)
        self.stdout.write(self.style.SUCCESS(f"Queued {len(message_ids)} conversation message embeddings."))
