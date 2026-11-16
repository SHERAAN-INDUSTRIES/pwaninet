from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from documents.access_policy import DocumentAccessPolicy
from documents.permissions import IsDocumentUploaderOrAdmin
from pwanimate.services.study_session import is_user_authorized_for_document
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService
from pwanimate.retrieval.adapters.pwaninet_search import pwaninetSearchAdapter
from pwanimate.retrieval.types import RetrievalRequest

class PolicyOfflineTests(SimpleTestCase):
    def test_all_roles_share_same_query_rule(self):
        for role in ['STUDENT','PRESIDENT','DELEGATE']:
            user=SimpleNamespace(pk=8,is_authenticated=True,is_staff=True,is_superuser=True,global_role=role)
            with patch('django.contrib.auth.get_user_model') as model:
                model.return_value.objects.filter.return_value.values.return_value.first.return_value=None
                sql=str(DocumentAccessPolicy.authorized_documents(user).query)
                self.assertIn('uploaded_by_id',sql);self.assertIn('visibility',sql)
                self.assertNotIn('PRESIDENT',sql)

    def test_scope_query_resolves_and_empty_filters_fail_closed(self):
        user=SimpleNamespace(pk=8,is_authenticated=True)
        with patch('django.contrib.auth.get_user_model') as model:
            model.return_value.objects.filter.return_value.values.return_value.first.return_value={'institution_id':2,'programme_id':3}
            query=DocumentAccessPolicy.authorized_documents(user)
            self.assertIn('is_active',str(query.query))
            self.assertEqual(list(DocumentAccessPolicy.authorized_documents(user,filters={'document_ids':[]})),[])
            self.assertEqual(list(DocumentAccessPolicy.authorized_documents(user,filters={'academic_units':[]})),[])

    def test_page_context_rechecks_shared_policy(self):
        with patch.object(DocumentAccessPolicy,'can_read',return_value=False) as check:
            self.assertFalse(is_user_authorized_for_document('actor','doc',require_ready=False))
            check.assert_called_once_with('actor','doc',require_ready=False)

    def test_vector_candidates_use_authorized_document_subquery(self):
        from documents.models import Document
        with patch.object(DocumentAccessPolicy,'authorized_documents',return_value=Document.objects.none()) as allowed:
            qs=DocumentSemanticRetrievalService().build_authorized_chunk_queryset(None,{'document_ids':[]})
            self.assertEqual(list(qs),[])
            allowed.assert_called_once_with(None,filters={'document_ids':[]})

    def test_lexical_forwards_scope_before_limit(self):
        search=Mock();search._search_documents.return_value=([],0)
        request=RetrievalRequest(query='matrix',user=None,filters={'academic_unit':'MAT101','category':'past_paper'},limit=3)
        pwaninetSearchAdapter(search_service=search).search_documents_lexical(request)
        self.assertEqual(search._search_documents.call_args.kwargs['filters'],request.filters)

    def test_read_permissions_delegate(self):
        with patch.object(DocumentAccessPolicy,'can_read',return_value=False):
            request=SimpleNamespace(method='GET',user=SimpleNamespace(is_authenticated=True,is_staff=True,is_superuser=True,pk=2))
            self.assertFalse(IsDocumentUploaderOrAdmin().has_object_permission(request,None,Mock()))
