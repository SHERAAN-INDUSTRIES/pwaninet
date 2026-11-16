"""Owner-only fact approval. All mutations serialize on the consent row."""
from django.db import transaction
from django.utils import timezone
from pwanimate.models import SparkMemoryPreference, SparkPersonalMemory, PwanimateMessage

CONSENT_VERSION = 1


def memory_scope(fact):
    # Reuse existing provenance/purpose, including pre-0.1 automatic rows.
    return 'academic' if fact.automatic or fact.purpose == 'academic_learning' else 'personal'


def preference_key(text):
    """Only exact supported preference facts have mutually exclusive values."""
    import re
    match = re.fullmatch(
        r'Preferred response (length): (?:concise|detailed)\.|'
        r'Preferred response (format): (?:bullet points|paragraphs)\.|'
        r'Preferred response (language): (?:English|Kiswahili)\.', text, re.I)
    return 'preference:' + next(value for value in match.groups() if value).lower() if match else ''


def enabled(pref):
    return pref.enabled and pref.consent_version == CONSENT_VERSION


def payload(fact):
    return {'id': str(fact.pk), 'text': fact.text, 'purpose': fact.purpose,
            'status': fact.status, 'version': fact.version,
            'conversation_id': str(fact.source_conversation_id) if fact.source_conversation_id else (str(fact.source_message.conversation_id) if fact.source_message_id else None),
            'scope': memory_scope(fact), 'category': fact.category, 'confidence': fact.confidence, 'importance': fact.importance,
            'automatic': fact.automatic, 'source_message_id': fact.source_message_id,
            'created_at': fact.created_at.isoformat(), 'updated_at': fact.updated_at.isoformat(),
            'last_accessed_at': fact.last_accessed_at.isoformat() if fact.last_accessed_at else None,
            'superseded_by': str(fact.superseded_by_id) if fact.superseded_by_id else None}


class PersonalMemoryService:
    @staticmethod
    def _delete_locked(pref, fact):
        """Caller holds the owner's preference lock; store no deleted fact text."""
        if memory_scope(fact) == 'academic' and fact.memory_key:
            cutoffs = dict(pref.extraction_cutoffs)
            cutoffs[fact.memory_key] = timezone.now().isoformat()
            pref.extraction_cutoffs = cutoffs
            pref.save(update_fields=['extraction_cutoffs'])
        fact.delete()

    @classmethod
    def delete(cls, user, fact_id):
        cls.preference(user)
        with transaction.atomic():
            pref = SparkMemoryPreference.objects.select_for_update().get(user=user)
            fact = SparkPersonalMemory.objects.filter(pk=fact_id, user=user).first()
            if fact is None:
                raise LookupError('Memory not found')
            cls._delete_locked(pref, fact)

    @staticmethod
    def preference(user):
        if not user or not user.is_authenticated: raise ValueError('Authentication required')
        return SparkMemoryPreference.objects.get_or_create(user=user)[0]

    @classmethod
    def set_enabled(cls, user, value):
        if type(value) is not bool: raise ValueError('enabled must be boolean')
        cls.preference(user)
        with transaction.atomic():
            pref = SparkMemoryPreference.objects.select_for_update().get(user=user)
            pref.enabled = value
            pref.consent_version = CONSENT_VERSION
            if not value:
                pref.automatic_extraction = False
                pref.automatic_enabled_at = None
            pref.revision += 1
            pref.save(update_fields=['enabled','consent_version','revision','automatic_extraction','automatic_enabled_at'])
        return pref

    @classmethod
    def set_automatic(cls, user, value):
        """Separate explicit consent to extracting non-sensitive academic statements."""
        if type(value) is not bool: raise ValueError('automatic_extraction must be boolean')
        cls.preference(user)
        with transaction.atomic():
            pref = SparkMemoryPreference.objects.select_for_update().get(user=user)
            if value and not enabled(pref): raise ValueError('Enable memory before automatic extraction')
            pref.automatic_extraction = value
            pref.automatic_enabled_at = timezone.now() if value else None
            pref.revision += 1
            pref.save(update_fields=['automatic_extraction', 'automatic_enabled_at', 'revision'])
        return pref

    @classmethod
    def propose(cls, user, text, source_message_id=None, category='semantic'):
        cls.preference(user)
        if not isinstance(text,str) or not text.strip() or len(text)>1000: raise ValueError('Fact must contain 1–1000 characters')
        if category not in {'semantic','episodic','learning'}: raise ValueError('Invalid category')
        from pwanimate.services.persistent_memory import normalize
        text = normalize(text)
        with transaction.atomic():
            pref=SparkMemoryPreference.objects.select_for_update().get(user=user)
            if not enabled(pref): raise ValueError('Enable personal memory before proposing a fact')
            source=None
            if source_message_id is not None:
                source=PwanimateMessage.objects.filter(pk=source_message_id,conversation__user=user).first()
                if source is None: raise ValueError('Source unavailable')
            existing = SparkPersonalMemory.objects.filter(user=user, text__iexact=text, status__in=['proposed','approved']).first()
            if existing: return existing
            return SparkPersonalMemory.objects.create(user=user,text=text,category=category,source_message=source,
                memory_key=preference_key(text),
                purpose='academic_learning' if category in {'learning', 'episodic'} else 'personalization',
                source_conversation=source.conversation if source else None,consent_revision=pref.revision)

    @classmethod
    def change(cls,user,fact_id,action,version,text=None):
        cls.preference(user)
        with transaction.atomic():
            pref=SparkMemoryPreference.objects.select_for_update().get(user=user)
            fact=SparkPersonalMemory.objects.select_for_update().filter(pk=fact_id,user=user).first()
            if fact is None: raise LookupError('Memory not found')
            if type(version) is not int or version != fact.version: raise ValueError('Memory version changed; review it again')
            if action=='delete':
                cls._delete_locked(pref, fact); return None
            if action=='edit':
                if not isinstance(text,str) or not text.strip() or len(text)>1000: raise ValueError('Invalid fact text')
                from pwanimate.services.persistent_memory import normalize
                fact.text=normalize(text); fact.status='proposed';fact.decided_at=None
                if memory_scope(fact) == 'academic':
                    fact.purpose = 'academic_learning'
                if fact.memory_key.startswith('preference:') or preference_key(fact.text):
                    fact.memory_key = preference_key(fact.text)
                fact.embedding=None; fact.embedding_model=''; fact.fingerprint=''; fact.automatic=False
                fact.consent_revision=pref.revision
            elif action in {'approve','reject'}:
                if (not enabled(pref) or fact.status!='proposed' or fact.consent_revision!=pref.revision):
                    raise ValueError('Consent changed; edit/review a fresh proposal before deciding')
                fact.status='approved' if action=='approve' else 'rejected'
                if action=='approve':
                    from pwanimate.services.persistent_memory import fingerprint
                    digest = fingerprint(fact.text)
                    from django.db.models import Q
                    approved = SparkPersonalMemory.objects.filter(user=user, status='approved').exclude(pk=fact.pk)
                    if approved.filter(Q(fingerprint=digest) | Q(text__iexact=fact.text)).exists():
                        raise ValueError('This fact is already approved')
                    fact.fingerprint=digest
                    if fact.memory_key:
                        # Older approved facts without keys are still recognized.
                        conflicts = Q(memory_key=fact.memory_key)
                        if fact.memory_key.startswith('preference:'):
                            from pwanimate.tools.domain.memory import PREFERENCES
                            conflicts |= Q(text__in=[value for value in PREFERENCES.values()
                                if preference_key(value) == fact.memory_key])
                        for stale in approved.filter(conflicts):
                            stale.status = 'superseded'
                            stale.superseded_by = fact
                            stale.version += 1
                            stale.save(update_fields=['status', 'superseded_by', 'version', 'updated_at'])
                fact.decided_at=timezone.now()
                if action=='reject': fact.text=''  # no indefinite rejected personal content
            elif action=='revoke':
                fact.status='revoked';fact.decided_at=timezone.now()
                fact.embedding=None;fact.embedding_model=''
            else: raise ValueError('Unknown memory action')
            fact.version+=1
            fact.save()
            return fact

    @classmethod
    def recall(cls,user,query,limit=6):
        if str(query or '').strip():
            from pwanimate.services.persistent_memory import PersistentMemoryService
            return PersistentMemoryService.retrieve(user, query, limit=limit)
        if not user or not user.is_authenticated: return []
        cls.preference(user)
        with transaction.atomic():
            pref=SparkMemoryPreference.objects.select_for_update().get(user=user)
            if not enabled(pref): return []
            # Small bounded approved-fact context, never raw transcript/vector fallback.
            facts=SparkPersonalMemory.objects.filter(user=user,status='approved').order_by('-updated_at')[:max(1,min(int(limit),12))]
            return [{'message_id':None,'conversation_id':None,'conversation_title':'Approved personal memory',
                'role':'user','content':f.text,'memory_id':str(f.pk),'match_type':'approved_fact','score':1.0}
                for f in facts]
