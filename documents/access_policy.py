"""Live document read policy. Collection membership is not a file-sharing grant."""
from django.db.models import Q


class DocumentAccessPolicy:
    @staticmethod
    def authorized_documents(user, *, require_ready=True, filters=None):
        from documents.models import Document, StudentUnitEnrollment, ProgrammeUnit, AcademicUnit
        qs = Document.objects.all()
        public = Q(visibility='public', is_ai_generated=False)
        if user and getattr(user, 'is_authenticated', False):
            allowed = public | Q(uploaded_by_id=user.pk)
            # Re-query current account scope: stale context snapshots cannot retain access.
            from django.contrib.auth import get_user_model
            scope = get_user_model().objects.filter(pk=user.pk).values('institution_id','programme_id').first()
            if scope and scope['institution_id']:
                institution = scope['institution_id']
                units = AcademicUnit.objects.filter(
                    Q(programme_units__programme__department__school__institution_id=institution)
                    | Q(programme_units__programme__department__school__faculty__institution_id=institution))
                inactive = StudentUnitEnrollment.objects.filter(user_id=user.pk,is_active=False).values('academic_unit_id')
                active = StudentUnitEnrollment.objects.filter(user_id=user.pk,is_active=True).values('academic_unit_id')
                programme = ProgrammeUnit.objects.filter(programme_id=scope['programme_id']).values('academic_unit_id')
                entitled = units.filter(Q(pk__in=active)|Q(pk__in=programme)).exclude(pk__in=inactive)
                allowed |= Q(visibility='restricted',is_ai_generated=False,
                             academic_units__academic_unit_id__in=entitled.values('pk'))
        else:
            allowed = public
        qs = qs.filter(allowed)
        if require_ready:
            qs = qs.filter(status='ready',is_available=True)
        else:
            # Only the owner may inspect non-ready/unavailable metadata.
            owner = Q(uploaded_by_id=user.pk) if user and getattr(user,'is_authenticated',False) else Q(pk__in=[])
            qs = qs.filter(Q(status='ready',is_available=True)|owner)
        filters = filters or {}
        for key in ('document_ids','academic_units'):
            if key in filters:
                field = 'pk__in' if key == 'document_ids' else 'academic_units__academic_unit_id__in'
                qs = qs.filter(**{field: filters[key] or []})
        if 'document_id' in filters: qs = qs.filter(pk=filters['document_id'])
        if 'academic_unit' in filters or 'unit' in filters:
            raw = str(filters.get('academic_unit',filters.get('unit')) or '').strip()
            if raw.isdigit():
                qs = qs.filter(academic_units__academic_unit_id=int(raw))
            else:
                canonical = AcademicUnit.format_canonical_code(raw) if raw else ''
                qs = qs.filter(Q(academic_units__academic_unit__code__iexact=canonical)|Q(academic_units__academic_unit__code__iexact=raw)) if raw else qs.none()
        if 'category' in filters:
            category = str(filters['category'] or '')
            match = Q(category__code=category)
            if category.isdigit(): match |= Q(category_id=int(category))
            qs = qs.filter(match) if category else qs.none()
        return qs.distinct()

    @classmethod
    def can_read(cls,user,document,*,require_ready=True):
        return bool(document and not getattr(document,'is_deleted',False)
                    and cls.authorized_documents(user,require_ready=require_ready).filter(pk=document.pk).exists())
