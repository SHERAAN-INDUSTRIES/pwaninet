from celery import shared_task
from pwanimate.services.study_memory import StudyMemoryService

@shared_task(bind=True, max_retries=2, default_retry_delay=30, queue='docs_queue')
def refresh_study_memory(self, conversation_id):
    try:
        return StudyMemoryService.refresh(conversation_id)
    except Exception as exc:
        raise self.retry(exc=exc)
