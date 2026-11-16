"""
Unified Search Service for vasto.
Orchestrates normalized search across People, Documents, Posts, and Groups
with PostgreSQL Full-Text Search (FTS), Trigram similarity fallbacks,
post privacy invariants, and engagement rank damping.
"""

import math
import logging
from typing import Dict, Any, List, Optional, Tuple
from django.db.models import Q, F, Value, IntegerField, FloatField, Case, When, Count, Prefetch, Exists, OuterRef
from django.db.models.functions import Concat
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity

logger = logging.getLogger(__name__)


class UnifiedSearchService:
    """Service providing unified search across Users, Documents, Posts, and Groups."""

    def search(
        self,
        query: str,
        active_tab: str = 'all',
        user=None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Execute unified search across domains according to active tab.

        Canonical Return Contract:
        {
            "query": query,
            "active_tab": active_tab,
            "counts": {"all": 0, "people": 0, "documents": 0, "posts": 0, "groups": 0},
            "results": {
                "people": [...],
                "documents": [...],
                "posts": [...],
                "groups": [...]
            },
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "has_next": False,
                "has_previous": False,
                "total_count": 0
            }
        }
        """
        query = (query or "").strip()
        active_tab = (active_tab or "all").lower()
        if active_tab not in ("all", "people", "documents", "posts", "groups"):
            active_tab = "all"

        page = max(1, int(page or 1))
        page_size = max(1, int(page_size or 20))
        offset = (page - 1) * page_size

        counts = {
            "all": 0,
            "people": 0,
            "documents": 0,
            "posts": 0,
            "groups": 0
        }
        results = {
            "people": [],
            "documents": [],
            "posts": [],
            "groups": []
        }

        if not query:
            return {
                "query": query,
                "active_tab": active_tab,
                "counts": counts,
                "results": results,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_pages": 1,
                    "has_next": False,
                    "has_previous": False,
                    "total_count": 0
                }
            }

        try:
            if active_tab == 'all':
                # Fetch top 4 preview items per domain for composite overview
                people_items, people_count = self._search_people(query, user, limit=4, offset=0)
                doc_items, doc_count = self._search_documents(query, user, limit=4, offset=0)
                post_items, post_count = self._search_posts(query, user, limit=4, offset=0)
                group_items, group_count = self._search_groups(query, user, limit=4, offset=0)

                counts["people"] = people_count
                counts["documents"] = doc_count
                counts["posts"] = post_count
                counts["groups"] = group_count
                counts["all"] = people_count + doc_count + post_count + group_count

                results["people"] = people_items
                results["documents"] = doc_items
                results["posts"] = post_items
                results["groups"] = group_items

                pagination = {
                    "page": 1,
                    "page_size": page_size,
                    "total_pages": 1,
                    "has_next": False,
                    "has_previous": False,
                    "total_count": counts["all"]
                }
            else:
                # Dedicated domain tab with full pagination
                if active_tab == 'people':
                    items, total = self._search_people(query, user, limit=page_size, offset=offset)
                    results['people'] = items
                    counts['people'] = total
                    # Background counts for other tab badges
                    counts['documents'] = self._count_documents(query)
                    counts['posts'] = self._count_posts(query, user)
                    counts['groups'] = self._count_groups(query)
                elif active_tab == 'documents':
                    items, total = self._search_documents(query, user, limit=page_size, offset=offset)
                    results['documents'] = items
                    counts['documents'] = total
                    counts['people'] = self._count_people(query, user)
                    counts['posts'] = self._count_posts(query, user)
                    counts['groups'] = self._count_groups(query)
                elif active_tab == 'posts':
                    items, total = self._search_posts(query, user, limit=page_size, offset=offset)
                    results['posts'] = items
                    counts['posts'] = total
                    counts['people'] = self._count_people(query, user)
                    counts['documents'] = self._count_documents(query)
                    counts['groups'] = self._count_groups(query)
                elif active_tab == 'groups':
                    items, total = self._search_groups(query, user, limit=page_size, offset=offset)
                    results['groups'] = items
                    counts['groups'] = total
                    counts['people'] = self._count_people(query, user)
                    counts['documents'] = self._count_documents(query)
                    counts['posts'] = self._count_posts(query, user)

                counts['all'] = counts['people'] + counts['documents'] + counts['posts'] + counts['groups']
                active_total = counts[active_tab]
                total_pages = max(1, math.ceil(active_total / page_size)) if active_total > 0 else 1

                pagination = {
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_previous": page > 1,
                    "total_count": active_total
                }

        except Exception as exc:
            logger.error("Unified search execution error for query '%s': %s", query, exc, exc_info=True)
            pagination = {
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "has_next": False,
                "has_previous": False,
                "total_count": 0
            }

        return {
            "query": query,
            "active_tab": active_tab,
            "counts": counts,
            "results": results,
            "pagination": pagination
        }

    # --------------------------------------------------------------------------
    # 1. People Adapter
    # --------------------------------------------------------------------------
    def search_people_models(
        self,
        query: str = '',
        user=None,
        connection_type: Optional[str] = None,
        profile_username: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[Any, str, str, str, bool, Optional[str]]:
        """
        Search or list people returning paginated User models for the People Modal.
        Supports connection types (followers, following, pinches_sent, pinches_received)
        and global people search with PostgreSQL FTS, Trigram typo-tolerance, and role boosts.
        """
        from django.shortcuts import get_object_or_404
        from django.core.paginator import Paginator
        from users.models import User, Follow, Pinch, GlobalRole

        query = (query or '').strip()
        page = max(1, int(page or 1))
        page_size = max(1, int(page_size or 20))

        list_type = 'all'
        empty_message = 'No users found.'
        empty_icon = 'search'

        if connection_type and profile_username:
            profile_user = get_object_or_404(User, username=profile_username)
            if connection_type == 'followers':
                follower_ids = Follow.objects.filter(followed=profile_user).values_list('follower_id', flat=True)
                qs = User.objects.filter(id__in=follower_ids)
                list_type = 'followers'
                empty_message = 'No followers yet.'
            elif connection_type == 'following':
                following_ids = Follow.objects.filter(follower=profile_user).values_list('followed_id', flat=True)
                qs = User.objects.filter(id__in=following_ids)
                list_type = 'following'
                empty_message = 'Not following anyone yet.'
            elif connection_type == 'pinches_sent':
                pinch_ids = Pinch.objects.filter(pinch_user=profile_user).values_list('pinched_user_id', flat=True)
                qs = User.objects.filter(id__in=pinch_ids)
                list_type = 'pinches_sent'
                empty_message = 'No pinches sent yet.'
            elif connection_type == 'pinches_received':
                pinch_ids = Pinch.objects.filter(pinched_user=profile_user).values_list('pinch_user_id', flat=True)
                qs = User.objects.filter(id__in=pinch_ids)
                list_type = 'pinches_received'
                empty_message = 'No pinches received yet.'
            else:
                qs = User.objects.filter(is_active=True)
        elif query:
            qs = User.objects.filter(is_active=True)
            if user and user.is_authenticated:
                qs = qs.exclude(id=user.id)
            list_type = 'search'
            empty_message = f'No results for "{query}"'
        else:
            qs = User.objects.filter(is_active=True)
            if user and user.is_authenticated:
                qs = qs.exclude(id=user.id)
            qs = qs.select_related('course', 'year').order_by('?')[:20]
            list_type = 'suggested'
            empty_message = 'No users available'
            empty_icon = 'people'

            paginator = Paginator(qs, page_size)
            users_page = paginator.get_page(page)
            return users_page, list_type, empty_message, empty_icon, users_page.has_next(), None

        if query:
            role_boost = Case(
                When(global_role=GlobalRole.PRESIDENT, then=Value(10)),
                When(global_role=GlobalRole.DELEGATE, then=Value(7)),
                When(global_role=GlobalRole.VERIFIED, then=Value(5)),
                default=Value(0),
                output_field=IntegerField()
            )

            search_query = SearchQuery(query, config='english')
            fts_qs = qs.annotate(
                relevance_rank=SearchRank(F('search_vector'), search_query),
                role_boost=role_boost
            ).filter(search_vector=search_query)

            if fts_qs.exists():
                ordered_qs = fts_qs.order_by('-role_boost', '-relevance_rank', 'username').select_related('course', 'year')
            else:
                full_name = Concat('first_name', Value(' '), 'last_name')
                rev_name = Concat('last_name', Value(' '), 'first_name')
                trgm_sim = (
                    TrigramSimilarity('username', query) * 1.3 +
                    TrigramSimilarity(full_name, query) +
                    TrigramSimilarity(rev_name, query)
                )
                trgm_qs = qs.annotate(
                    relevance_rank=trgm_sim,
                    role_boost=role_boost
                ).filter(relevance_rank__gte=0.15)
                if trgm_qs.exists():
                    ordered_qs = trgm_qs.order_by('-role_boost', '-relevance_rank', 'username').select_related('course', 'year')
                else:
                    ordered_qs = qs.filter(
                        Q(username__icontains=query) |
                        Q(first_name__icontains=query) |
                        Q(last_name__icontains=query)
                    ).select_related('course', 'year').order_by('username')
        else:
            ordered_qs = qs.select_related('course', 'year').order_by('-id')

        paginator = Paginator(ordered_qs, page_size)
        users_page = paginator.get_page(page)

        next_url = None
        if users_page.has_next():
            url_params = []
            if query:
                url_params.append(f"q={query}")
            if connection_type:
                url_params.append(f"connection_type={connection_type}")
            if profile_username:
                url_params.append(f"profile_username={profile_username}")
            url_params.append(f"page={users_page.next_page_number()}")
            next_url = f"?{'&'.join(url_params)}"

        return users_page, list_type, empty_message, empty_icon, users_page.has_next(), next_url

    def _search_people(self, query: str, user, limit: int, offset: int) -> Tuple[List[Dict[str, Any]], int]:
        from users.models import User, GlobalRole

        qs = User.objects.filter(is_active=True)
        if user and user.is_authenticated:
            qs = qs.exclude(id=user.id)

        role_boost = Case(
            When(global_role=GlobalRole.PRESIDENT, then=Value(10)),
            When(global_role=GlobalRole.DELEGATE, then=Value(7)),
            When(global_role=GlobalRole.VERIFIED, then=Value(5)),
            default=Value(0),
            output_field=IntegerField()
        )

        search_query = SearchQuery(query, config='english')
        fts_qs = qs.annotate(
            relevance_rank=SearchRank(F('search_vector'), search_query),
            role_boost=role_boost
        ).filter(search_vector=search_query)

        total = fts_qs.count()
        if total > 0:
            ordered_qs = fts_qs.order_by('-role_boost', '-relevance_rank', 'username')
            records = list(ordered_qs.select_related('course', 'year')[offset:offset + limit])
        else:
            # Trigram fallback: word-order invariant matching on username & full name
            full_name = Concat('first_name', Value(' '), 'last_name')
            rev_name = Concat('last_name', Value(' '), 'first_name')
            trgm_sim = (
                TrigramSimilarity('username', query) * 1.3 +
                TrigramSimilarity(full_name, query) +
                TrigramSimilarity(rev_name, query)
            )
            trgm_qs = qs.annotate(
                relevance_rank=trgm_sim,
                role_boost=role_boost
            ).filter(relevance_rank__gte=0.15)
            total = trgm_qs.count()
            if total > 0:
                records = list(trgm_qs.order_by('-role_boost', '-relevance_rank', 'username').select_related('course', 'year')[offset:offset + limit])
            else:
                fallback_qs = qs.filter(
                    Q(username__icontains=query) |
                    Q(first_name__icontains=query) |
                    Q(last_name__icontains=query)
                )
                total = fallback_qs.count()
                records = list(fallback_qs.select_related('course', 'year').order_by('username')[offset:offset + limit])

        items = []
        for u in records:
            headline = getattr(u, 'headline', '') or ''
            if not headline and getattr(u, 'course', None):
                course_name = u.course.name if hasattr(u.course, 'name') else str(u.course)
                year_lvl = f" · Year {u.year.level}" if getattr(u, 'year', None) else ""
                headline = f"{course_name}{year_lvl}"

            avatar_url = '/static/images/default-avatar.png'
            if getattr(u, 'profile_pic', None) and hasattr(u.profile_pic, 'url') and u.profile_pic.name:
                avatar_url = u.profile_pic.url

            items.append({
                "id": u.id,
                "type": "person",
                "title": u.get_full_name() or u.username,
                "subtitle": f"@{u.username}",
                "username": u.username,
                "avatar_url": avatar_url,
                "headline": headline,
                "global_role": getattr(u, 'global_role', 'NORMAL'),
                "detail_url": f"/users/user/{u.username}/",
                "relevance_rank": float(getattr(u, 'relevance_rank', 0.0) or 0.0),
                "obj": u,
            })

        return items, total

    def _count_people(self, query: str, user) -> int:
        _, count = self._search_people(query, user, limit=1, offset=0)
        return count

    # --------------------------------------------------------------------------
    # 2. Documents Adapter
    # --------------------------------------------------------------------------
    def _apply_student_interest_boost(self, queryset, user):
        """Apply tiered soft relevance boost for documents matching student's enrolled units, level, and programme."""
        try:
            if not user or not getattr(user, 'is_authenticated', False):
                return queryset.annotate(
                    academic_relevance=Value(0.0, output_field=FloatField()),
                    boosted_rank=F('relevance_rank'),
                )

            from documents.academic.models import ProgrammeUnit
            from documents.academic.services import StudentAcademicEnrollmentService
            from documents.documents.models import DocumentAcademicUnit

            programme, academic_level, _, _ = StudentAcademicEnrollmentService.resolve_student_programme_and_level(user)
            active_enrollments = StudentAcademicEnrollmentService.get_active_enrollments(user)
            enrolled_unit_ids = [enr.academic_unit_id for enr in active_enrollments]
            enrolled_unit_codes = [enr.academic_unit.code for enr in active_enrollments if getattr(enr, 'academic_unit', None)]

            programme_level_unit_ids: List[int] = []
            programme_unit_ids: List[int] = []
            if programme:
                programme_unit_ids = list(
                    ProgrammeUnit.objects.filter(programme=programme)
                    .values_list('academic_unit_id', flat=True)
                )
                if academic_level:
                    programme_level_unit_ids = list(
                        ProgrammeUnit.objects.filter(programme=programme, academic_level=academic_level)
                        .values_list('academic_unit_id', flat=True)
                    )

            if not enrolled_unit_ids and not programme_unit_ids:
                return queryset.annotate(
                    academic_relevance=Value(0.0, output_field=FloatField()),
                    boosted_rank=F('relevance_rank'),
                )

            when_clauses_boost = []
            when_clauses_rel = []

            if enrolled_unit_ids:
                enrolled_q = Exists(
                    DocumentAcademicUnit.objects.filter(
                        document_id=OuterRef('document_id'),
                        academic_unit_id__in=enrolled_unit_ids,
                    )
                )
                for code in enrolled_unit_codes:
                    enrolled_q |= Q(academic_unit_codes__contains=code)
                when_clauses_boost.append(When(enrolled_q, then=Value(0.65)))
                when_clauses_rel.append(When(enrolled_q, then=Value(1.0)))

            if programme_level_unit_ids:
                prog_lvl_q = Exists(
                    DocumentAcademicUnit.objects.filter(
                        document_id=OuterRef('document_id'),
                        academic_unit_id__in=programme_level_unit_ids,
                    )
                )
                when_clauses_boost.append(When(prog_lvl_q, then=Value(0.40)))
                when_clauses_rel.append(When(prog_lvl_q, then=Value(0.75)))

            if programme_unit_ids:
                prog_q = Exists(
                    DocumentAcademicUnit.objects.filter(
                        document_id=OuterRef('document_id'),
                        academic_unit_id__in=programme_unit_ids,
                    )
                )
                when_clauses_boost.append(When(prog_q, then=Value(0.20)))
                when_clauses_rel.append(When(prog_q, then=Value(0.5)))

            queryset = queryset.annotate(
                academic_relevance=Case(
                    *when_clauses_rel,
                    default=Value(0.0),
                    output_field=FloatField(),
                ),
                boosted_rank=F('relevance_rank') + Case(
                    *when_clauses_boost,
                    default=Value(0.0),
                    output_field=FloatField(),
                ),
            )
            return queryset
        except Exception as exc:
            logger.warning("Failed to apply student interest boost: %s", exc)
            return queryset.annotate(
                academic_relevance=Value(0.0, output_field=FloatField()),
                boosted_rank=F('relevance_rank'),
            )

    def _build_document_queryset(
        self,
        query: str = '',
        user=None,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = 'relevance',
    ):
        """Build and filter DocumentSearchIndex queryset with FTS, Trigram, and academic filters."""
        from documents.search.models import DocumentSearchIndex

        filters = filters or {}
        base_qs = DocumentSearchIndex.objects.filter(
            document__status='ready',
            document__is_available=True,
        )

        from documents.access_policy import DocumentAccessPolicy
        base_qs = base_qs.filter(document_id__in=DocumentAccessPolicy.authorized_documents(user, filters=filters).values('pk'))

        level = filters.get('academic_level')
        if level:
            base_qs = base_qs.filter(
                Q(document__academic_units__academic_level__id=level) |
                Q(document__academic_units__academic_level__level=level)
            )

        semester = filters.get('semester')
        if semester:
            sem_str = str(semester).strip()
            if sem_str in ('1', '2', '3'):
                base_qs = base_qs.filter(
                    Q(document__academic_units__semester__id=semester) |
                    Q(document__academic_units__semester__number=int(sem_str))
                )
            else:
                base_qs = base_qs.filter(document__academic_units__semester__id=semester)

        academic_year = filters.get('academic_year')
        if academic_year:
            base_qs = base_qs.filter(document__academic_units__academic_year__id=academic_year)

        programme = filters.get('programme')
        if programme:
            from documents.academic.models import ProgrammeUnit
            p_unit_ids = ProgrammeUnit.objects.filter(programme_id=programme).values_list('academic_unit_id', flat=True)
            base_qs = base_qs.filter(document__academic_units__academic_unit_id__in=p_unit_ids)

        school = filters.get('school')
        if school:
            base_qs = base_qs.filter(
                document__academic_units__academic_unit__programme_units__programme__department__school_id=school
            )

        department = filters.get('department')
        if department:
            base_qs = base_qs.filter(
                document__academic_units__academic_unit__programme_units__programme__department_id=department
            )

        file_type = filters.get('file_type')
        if file_type:
            ft_str = str(file_type).lower()
            base_qs = base_qs.filter(file_types__contains=[ft_str])

        tags = filters.get('tags')
        if tags:
            base_qs = base_qs.filter(tag_slugs__contains=tags)

        query = (query or '').strip()
        has_query = bool(query)

        if has_query:
            search_query = SearchQuery(query, config='english', search_type='websearch')
            fts_qs = base_qs.annotate(
                relevance_rank=SearchRank(F('search_vector'), search_query)
            ).filter(search_vector=search_query)

            fts_qs = self._apply_student_interest_boost(fts_qs, user)

            total = fts_qs.count()
            if total > 0:
                qs = fts_qs
            else:
                from django.contrib.postgres.search import TrigramWordSimilarity
                title_sim = TrigramWordSimilarity(Value(query), 'title')
                text_sim = TrigramWordSimilarity(Value(query), 'searchable_text')
                trgm_qs = base_qs.annotate(
                    title_sim=title_sim,
                    text_sim=text_sim,
                    relevance_rank=title_sim * 1.5 + text_sim
                ).filter(Q(title_sim__gte=0.25) | Q(text_sim__gte=0.25))
                trgm_qs = self._apply_student_interest_boost(trgm_qs, user)
                total = trgm_qs.count()
                qs = trgm_qs
        else:
            qs = base_qs
            total = qs.count()

        # Sorting logic
        sort_field = '-created_at'
        if sort_by == 'relevance':
            if has_query:
                sort_field = '-boosted_rank'
            else:
                sort_field = '-created_at'
        elif sort_by == 'newest':
            sort_field = '-created_at'
        elif sort_by == 'oldest':
            sort_field = 'created_at'
        elif sort_by == 'downloads':
            sort_field = '-download_count'
        elif sort_by == 'rating':
            sort_field = '-rating_average'
        elif sort_by == 'trending':
            sort_field = '-trending_score'
        elif sort_by == 'popular':
            sort_field = '-popularity_score'

        if has_query and sort_by == 'relevance':
            ordered_qs = qs.order_by(sort_field, '-relevance_rank', '-download_count', '-created_at')
        else:
            ordered_qs = qs.order_by(sort_field)

        return ordered_qs, total

    def search_documents_models(
        self,
        query: str = '',
        user=None,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = 'relevance',
        page: int = 1,
        per_page: int = 20,
    ) -> Tuple[List[Any], int]:
        """
        Search documents returning hydrated Document ORM models.
        Suitable for direct consumption by the Document Repository search page and DocumentSelector.
        """
        ordered_qs, total = self._build_document_queryset(
            query=query,
            user=user,
            filters=filters,
            sort_by=sort_by
        )

        offset = (page - 1) * per_page
        records = list(ordered_qs[offset:offset + per_page])
        doc_ids = [idx.document_id for idx in records]

        if not doc_ids:
            return [], total

        from documents.models import (
            Document, DocumentVersion, DocumentFile,
            DocumentAcademicUnit, DocumentTag, DocumentAuthor
        )
        docs_qs = (
            Document.objects
            .filter(id__in=doc_ids)
            .select_related('category', 'analytics', 'uploaded_by')
            .prefetch_related(
                Prefetch(
                    'versions',
                    queryset=DocumentVersion.objects.select_related('created_by').prefetch_related(
                        Prefetch('files', queryset=DocumentFile.objects.all())
                    )
                ),
                Prefetch(
                    'academic_units',
                    queryset=DocumentAcademicUnit.objects.select_related(
                        'academic_unit', 'academic_level', 'semester', 'academic_year'
                    )
                ),
                Prefetch(
                    'document_tags',
                    queryset=DocumentTag.objects.select_related('tag')
                ),
                Prefetch(
                    'authors',
                    queryset=DocumentAuthor.objects.all()
                ),
            )
        )

        docs_by_id = {d.id: d for d in docs_qs}
        documents = [docs_by_id[did] for did in doc_ids if did in docs_by_id]
        return documents, total

    def get_document_suggestions(self, query: str, limit: int = 10) -> List[str]:
        """Get autocomplete suggestions for documents based on query."""
        if not query or len(query.strip()) < 2:
            return []
        query = query.strip()
        import re
        from django.contrib.postgres.search import TrigramWordSimilarity
        from documents.search.models import DocumentSearchIndex
        try:
            suggestions = DocumentSearchIndex.objects.filter(
                document__status='ready',
                document__is_available=True,
                document__visibility='public',
                document__is_ai_generated=False,
            ).annotate(
                similarity=TrigramWordSimilarity(Value(query), 'title')
            ).filter(similarity__gte=0.22).order_by('-similarity')[:limit]
            results = []
            for s in suggestions:
                raw = s.title
                t = re.sub(r'___PDFDrive\.com___.*$', '', raw, flags=re.IGNORECASE)
                t = re.sub(r'__PDFDrive.*$', '', t, flags=re.IGNORECASE)
                t = re.sub(r'\.(pdf|docx|pptx|txt|epub)$', '', t, flags=re.IGNORECASE)
                t = re.sub(r'[_\-]+', ' ', t).strip()
                t = re.sub(r'\s+', ' ', t)
                if t and t not in results:
                    results.append(t)
            if not results:
                raw_matches = list(DocumentSearchIndex.objects.filter(
                    document__status='ready',
                    document__is_available=True,
                    document__visibility='public',
                    document__is_ai_generated=False,
                    title__icontains=query
                ).values_list('title', flat=True)[:limit])
                results = [re.sub(r'[_\-]+', ' ', m).strip() for m in raw_matches]
            return results
        except Exception as exc:
            logger.warning("get_document_suggestions error: %s", exc)
            return list(DocumentSearchIndex.objects.filter(
                document__status='ready',
                document__is_available=True,
                document__visibility='public',
                document__is_ai_generated=False,
                title__icontains=query
            ).values_list('title', flat=True)[:limit])

    def get_document_did_you_mean(self, query: str) -> Optional[str]:
        """Get spelling correction suggestion for document searches using fuzzy matching."""
        if not query or len(query.strip()) < 3:
            return None
        query = query.strip()
        import re
        from django.contrib.postgres.search import TrigramWordSimilarity
        from documents.search.models import DocumentSearchIndex
        try:
            best = DocumentSearchIndex.objects.filter(
                document__status='ready',
                document__is_available=True,
                document__visibility='public',
                document__is_ai_generated=False,
            ).annotate(
                sim=TrigramWordSimilarity(Value(query), 'title')
            ).filter(sim__gte=0.22).order_by('-sim').first()

            if not best:
                best = DocumentSearchIndex.objects.filter(
                    document__status='ready',
                    document__is_available=True,
                    document__visibility='public',
                    document__is_ai_generated=False,
                ).annotate(
                    sim=TrigramWordSimilarity(Value(query), 'searchable_text')
                ).filter(sim__gte=0.25).order_by('-sim').first()

            if best:
                raw = best.title
                # Clean up scraping artifacts and filenames
                t = re.sub(r'___PDFDrive\.com___.*$', '', raw, flags=re.IGNORECASE)
                t = re.sub(r'__PDFDrive.*$', '', t, flags=re.IGNORECASE)
                t = re.sub(r'\.(pdf|docx|pptx|txt|epub)$', '', t, flags=re.IGNORECASE)
                t = re.sub(r'[_\-]+', ' ', t).strip()
                t = re.sub(r'\s+', ' ', t)
                if t and t.lower() != query.lower():
                    return t
        except Exception as exc:
            logger.warning("get_document_did_you_mean error: %s", exc)
        return None

    def _search_documents(self, query: str, user, limit: int, offset: int, filters=None) -> Tuple[List[Dict[str, Any]], int]:
        ordered_qs, total = self._build_document_queryset(query=query, user=user, filters=filters)
        records = list(ordered_qs[offset:offset + limit])

        doc_ids = [idx.document_id for idx in records]
        if doc_ids:
            from documents.models import Document, DocumentVersion, DocumentFile, DocumentAcademicUnit
            docs_qs = (
                Document.objects
                .filter(id__in=doc_ids)
                .select_related('category', 'analytics', 'uploaded_by')
                .prefetch_related(
                    Prefetch(
                        'versions',
                        queryset=DocumentVersion.objects.filter(is_latest=True).prefetch_related(
                            Prefetch('files', queryset=DocumentFile.objects.all())
                        )
                    ),
                    Prefetch(
                        'academic_units',
                        queryset=DocumentAcademicUnit.objects.select_related('academic_unit')
                    ),
                )
            )
            docs_by_id = {d.id: d for d in docs_qs}
        else:
            docs_by_id = {}

        items = []
        for idx in records:
            doc = docs_by_id.get(idx.document_id, idx.document)
            unit_sub = ", ".join(idx.academic_unit_names[:2]) if idx.academic_unit_names else (idx.category_name or "")
            ext = idx.file_types[0].upper() if idx.file_types else 'PDF'

            thumb_url = None
            latest = doc.latest_version
            if latest:
                first_f = latest.files.first()
                if first_f and first_f.preview_path:
                    thumb_url = f"/media/{first_f.preview_path}"

            effective_rank = getattr(idx, 'boosted_rank', None)
            if effective_rank is None:
                effective_rank = getattr(idx, 'relevance_rank', 0.0)
            items.append({
                "id": doc.id,
                "type": "document",
                "title": doc.title,
                "subtitle": unit_sub,
                "category_display": idx.category_name or (doc.category.name if getattr(doc, 'category', None) else "Document"),
                "file_type": ext,
                "thumbnail_url": thumb_url,
                "detail_url": f"/documents/document/{doc.id}/",
                "download_url": f"/documents/document/{doc.id}/download/",
                "download_count": idx.download_count,
                "rating_average": float(idx.rating_average or 0.0),
                "snippet": doc.description[:140] if doc.description else "",
                "relevance_rank": float(effective_rank or 0.0),
                "academic_relevance": float(getattr(idx, 'academic_relevance', 0.0) or 0.0),
                "obj": doc,
            })

        return items, total

    def _count_documents(self, query: str) -> int:
        _, count = self._search_documents(query, None, limit=1, offset=0)
        return count

    # --------------------------------------------------------------------------
    # 3. Posts Adapter
    # --------------------------------------------------------------------------
    def _search_posts(self, query: str, user, limit: int, offset: int) -> Tuple[List[Dict[str, Any]], int]:
        from posts.models import Post, HiddenPost, AuthorPreference
        from groups.models import Group, Membership, MembershipStatus, PostVisibility

        base_filter = Q()
        from users.services.privacy import visible_posts_for
        visible_post_ids = visible_posts_for(user).values_list('id', flat=True)
        base_filter &= Q(id__in=visible_post_ids)
        # Exclude failed videos
        base_filter &= ~Q(video_status=Post.VIDEO_STATUS_FAILED)

        # Privacy invariants
        if user and user.is_authenticated:
            hidden_ids = HiddenPost.objects.filter(user=user).values_list('post_id', flat=True)
            if hidden_ids:
                base_filter &= ~Q(id__in=hidden_ids)

            blocked_authors = AuthorPreference.objects.filter(user=user, preference='none').values_list('author_id', flat=True)
            if blocked_authors:
                base_filter &= ~Q(author_id__in=blocked_authors)

            # Group visibility check
            user_group_ids = Membership.objects.filter(user=user, status=MembershipStatus.APPROVED).values_list('group_id', flat=True)
            created_group_ids = Group.objects.filter(created_by=user).values_list('id', flat=True)
            allowed_groups = set(user_group_ids) | set(created_group_ids)

            base_filter &= (
                Q(group__isnull=True) |
                Q(group__post_visibility=PostVisibility.EVERYONE) |
                Q(group_id__in=allowed_groups)
            )
        else:
            base_filter &= (
                Q(group__isnull=True) |
                Q(group__post_visibility=PostVisibility.EVERYONE)
            )

        search_query = SearchQuery(query, config='english')
        fts_qs = Post.objects.filter(base_filter).annotate(
            relevance_rank=SearchRank(F('search_vector'), search_query)
        ).filter(search_vector=search_query)

        total = fts_qs.count()
        if total > 0:
            candidate_posts = list(fts_qs.order_by('-relevance_rank', '-id')[offset:offset + limit])
        else:
            trgm_sim = TrigramSimilarity('content', query)
            trgm_qs = Post.objects.filter(base_filter).annotate(
                relevance_rank=trgm_sim
            ).filter(relevance_rank__gte=0.15)
            total = trgm_qs.count()
            candidate_posts = list(trgm_qs.order_by('-relevance_rank', '-id')[offset:offset + limit])

        post_ids = [p.id for p in candidate_posts]
        if post_ids:
            from posts.models import PostImage
            hydrated_qs = (
                Post.objects
                .filter(id__in=post_ids)
                .select_related(
                    'author',
                    'group',
                    'repost_of',
                    'repost_of__author',
                    'shared_document',
                    'shared_document__analytics',
                )
                .prefetch_related(
                    Prefetch('images', queryset=PostImage.objects.all()),
                    'comments',
                    'likes',
                )
                .annotate(
                    like_count_annotated=Count('likes', distinct=True),
                    comment_count_annotated=Count('comments', distinct=True),
                    repost_count_annotated=Count('repost_children', distinct=True),
                )
            )
            posts_by_id = {p.id: p for p in hydrated_qs}
        else:
            posts_by_id = {}

        items = []
        for cand in candidate_posts:
            p = posts_by_id.get(cand.id, cand)
            l_cnt = getattr(p, 'like_count_annotated', None)
            if l_cnt is None:
                l_cnt = p.likes.count() if hasattr(p, 'likes') else 0
            c_cnt = getattr(p, 'comment_count_annotated', None)
            if c_cnt is None:
                c_cnt = p.comments.count() if hasattr(p, 'comments') else 0

            # Engagement rank damping formula
            score = float(getattr(cand, 'relevance_rank', 0.0) or 0.0) * (1.0 + math.log(l_cnt + c_cnt + 1) * 0.15)

            avatar_url = '/static/images/default-avatar.png'
            if getattr(p.author, 'profile_pic', None) and hasattr(p.author.profile_pic, 'url') and p.author.profile_pic.name:
                avatar_url = p.author.profile_pic.url

            content_text = p.content or ""
            title = content_text[:80] + ("..." if len(content_text) > 80 else "")

            items.append({
                "id": p.id,
                "type": "post",
                "title": title or f"Post by {p.author.username}",
                "author": {
                    "id": p.author.id,
                    "name": p.author.get_full_name() or p.author.username,
                    "username": p.author.username,
                    "avatar_url": avatar_url,
                },
                "snippet": content_text[:160],
                "has_video": bool(p.video),
                "video_status": getattr(p, 'video_status', None),
                "hls_url": p.get_hls_url if hasattr(p, 'get_hls_url') else None,
                "like_count": l_cnt,
                "comment_count": c_cnt,
                "created_at": p.created_at.strftime('%b %d, %Y') if p.created_at else '',
                "detail_url": f"/post/{p.share_id}/",
                "relevance_rank": float(score),
                "obj": p,
            })

        items.sort(key=lambda x: x["relevance_rank"], reverse=True)
        return items, total

    def _count_posts(self, query: str, user) -> int:
        _, count = self._search_posts(query, user, limit=1, offset=0)
        return count

    # --------------------------------------------------------------------------
    # 4. Groups Adapter
    # --------------------------------------------------------------------------
    def search_groups_models(
        self,
        query: str = '',
        user=None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Any], int]:
        """
        Search active groups returning annotated Group ORM models.
        Suitable for Groups Dashboard and Global Search.
        """
        from groups.models import Group

        member_cnt = Count('memberships', filter=Q(memberships__status='APPROVED'), distinct=True)
        base_qs = Group.objects.annotate(member_count=member_cnt)

        # Privacy scoping: if user is authenticated, include public groups + user's joined/created groups.
        if user and getattr(user, 'is_authenticated', False):
            base_qs = base_qs.filter(
                Q(join_policy='open') |
                Q(memberships__user=user, memberships__status='APPROVED') |
                Q(created_by=user)
            ).distinct()

        query = (query or '').strip()
        if query:
            search_query = SearchQuery(query, config='english')
            fts_qs = base_qs.annotate(
                relevance_rank=SearchRank(F('search_vector'), search_query)
            ).filter(search_vector=search_query)

            total = fts_qs.count()
            if total > 0:
                groups = list(fts_qs.order_by('-relevance_rank', '-member_count', '-id')[offset:offset + limit])
            else:
                trgm_sim = TrigramSimilarity('name', query) + TrigramSimilarity('description', query) * 0.5
                trgm_qs = base_qs.annotate(
                    relevance_rank=trgm_sim
                ).filter(relevance_rank__gte=0.2)
                total = trgm_qs.count()
                if total > 0:
                    groups = list(trgm_qs.order_by('-relevance_rank', '-member_count', '-id')[offset:offset + limit])
                else:
                    fallback_qs = base_qs.filter(
                        Q(name__icontains=query) |
                        Q(description__icontains=query)
                    )
                    total = fallback_qs.count()
                    groups = list(fallback_qs.order_by('-member_count', '-id')[offset:offset + limit])
        else:
            total = base_qs.count()
            groups = list(base_qs.order_by('-member_count', '-id')[offset:offset + limit])

        return groups, total

    def _search_groups(self, query: str, user, limit: int, offset: int) -> Tuple[List[Dict[str, Any]], int]:
        groups, total = self.search_groups_models(query=query, user=user, limit=limit, offset=offset)

        items = []
        for g in groups:
            photo = getattr(g, 'get_photo_url', None)
            if callable(photo):
                photo = photo()
            items.append({
                "id": g.id,
                "type": "group",
                "title": g.name,
                "subtitle": f"{getattr(g, 'member_count', 0)} members",
                "avatar_url": photo or '/static/images/default_group.jpg',
                "detail_url": f"/groups/{g.id}/",
                "is_official": getattr(g, 'is_official', False),
                "relevance_rank": float(getattr(g, 'relevance_rank', 0.0) or 0.0),
                "obj": g,
            })

        return items, total

    def _count_groups(self, query: str) -> int:
        _, count = self._search_groups(query, None, limit=1, offset=0)
        return count

    def search_group_posts_queryset(self, group, query: str, user=None):
        """
        Reuse Unified Search Engine to filter posts within a specific group.
        Applies PostgreSQL Full-Text Search with Trigram and text similarity fallbacks,
        author select_related, and media prefetches.
        """
        from posts.models import Post, HiddenPost, AuthorPreference, PostImage
        from users.services.privacy import visible_posts_for
        query = (query or "").strip()
        base_filter = Q(group=group) & ~Q(video_status=Post.VIDEO_STATUS_FAILED)
        base_filter &= Q(id__in=visible_posts_for(user).values_list('id', flat=True))

        if user and user.is_authenticated:
            hidden_ids = HiddenPost.objects.filter(user=user).values_list('post_id', flat=True)
            if hidden_ids:
                base_filter &= ~Q(id__in=hidden_ids)
            blocked_authors = AuthorPreference.objects.filter(user=user, preference='none').values_list('author_id', flat=True)
            if blocked_authors:
                base_filter &= ~Q(author_id__in=blocked_authors)

        if not query:
            qs = Post.objects.filter(base_filter).order_by('-created_at')
        else:
            search_query = SearchQuery(query, config='english')
            fts_qs = Post.objects.filter(base_filter).annotate(
                relevance_rank=SearchRank(F('search_vector'), search_query)
            ).filter(search_vector=search_query)

            if fts_qs.exists():
                qs = fts_qs.order_by('-relevance_rank', '-id')
            else:
                trgm_sim = TrigramSimilarity('content', query)
                trgm_qs = Post.objects.filter(base_filter).annotate(
                    relevance_rank=trgm_sim
                ).filter(relevance_rank__gte=0.15)
                if trgm_qs.exists():
                    qs = trgm_qs.order_by('-relevance_rank', '-id')
                else:
                    qs = Post.objects.filter(
                        base_filter & (
                            Q(content__icontains=query) |
                            Q(author__username__icontains=query) |
                            Q(author__first_name__icontains=query) |
                            Q(author__last_name__icontains=query)
                        )
                    ).order_by('-created_at')

        return qs.select_related(
            'author', 'group', 'unit', 'repost_of', 'repost_of__author',
            'shared_document', 'shared_document__analytics'
        ).prefetch_related(
            Prefetch('images', queryset=PostImage.objects.all()),
            'comments', 'likes'
        ).annotate(
            like_count_annotated=Count('likes', distinct=True),
            comment_count_annotated=Count('comments', distinct=True),
            repost_count_annotated=Count('repost_children', distinct=True),
        )
