"""Study-material inventory helpers for Spark's per-turn academic context."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from django.db import models

logger = logging.getLogger(__name__)


def enrolled_unit_rows(user: Any) -> List[Dict[str, Any]]:
    """Enrolled academic units as {id, code, name} dicts."""
    rows: List[Dict[str, Any]] = []
    try:
        for enrollment in user.get_enrolled_units():
            au = getattr(enrollment, 'academic_unit', None)
            if au is not None:
                rows.append({
                    'id': au.id,
                    'code': getattr(au, 'code', '') or '',
                    'name': getattr(au, 'name', '') or '',
                })
    except Exception:  # noqa: BLE001 - enrollment lookup must never break a turn
        return []
    return rows


def unit_document_inventory(user: Any, max_units: int = 12) -> List[Dict[str, Any]]:
    """
    Count authorized, indexed documents per enrolled unit so Spark knows which
    units actually have notes to revise from. Returns [] on any failure —
    inventory is advisory context, never a hard dependency.
    """
    units = enrolled_unit_rows(user)
    if not units:
        return []
    try:
        from documents.models import DocumentAcademicUnit
        from pwanimate.retrieval.services.document_retrieval import (
            DocumentSemanticRetrievalService,
        )

        authorized_doc_ids = (
            DocumentSemanticRetrievalService()
            .build_authorized_chunk_queryset(user=user)
            .values('document_id')
            .distinct()
        )
        unit_ids = [u['id'] for u in units]
        counts = (
            DocumentAcademicUnit.objects
            .filter(academic_unit_id__in=unit_ids, document_id__in=authorized_doc_ids)
            .values('academic_unit_id')
            .annotate(document_count=models.Count('document', distinct=True))
        )
        count_by_unit = {row['academic_unit_id']: row['document_count'] for row in counts}
        inventory = []
        for unit in units[:max_units]:
            count = count_by_unit.get(unit['id'], 0)
            inventory.append({
                'code': unit['code'],
                'name': unit['name'],
                'indexed_documents': count,
            })
        return inventory
    except Exception:  # noqa: BLE001
        logger.debug('unit_document_inventory failed for user %s', getattr(user, 'id', None), exc_info=True)
        return []


def saved_document_inventory(user: Any, max_docs: int = 15) -> List[Dict[str, Any]]:
    """
    Documents the student has deliberately saved (owned collections, e.g. the
    'Spark Offline' shelf). Injected into Spark's per-turn context so saved
    notes are referenceable without a search round-trip.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    try:
        from documents.collections.models import CollectionItem
        from documents.models import Document

        doc_ids = (
            CollectionItem.objects
            .filter(collection__owner=user)
            .values_list('document_id', flat=True)
            .distinct()[:200]
        )
        documents = (
            Document.objects
            .filter(id__in=list(doc_ids), status='ready', is_available=True)
            .prefetch_related('academic_units__academic_unit')
            .order_by('-updated_at')[:max_docs]
        )
        saved = []
        for doc in documents:
            unit_links = [u for u in doc.academic_units.all() if getattr(u, 'academic_unit', None)]
            saved.append({
                'title': doc.title,
                'share_id': str(doc.share_id),
                'units': [{'code': u.academic_unit.code, 'name': u.academic_unit.name} for u in unit_links[:3]],
            })
        return saved
    except Exception:  # noqa: BLE001
        logger.debug('saved_document_inventory failed for user %s', getattr(user, 'id', None), exc_info=True)
        return []
