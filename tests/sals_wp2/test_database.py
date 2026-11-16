"""Run only through the disposable WP0 PostgreSQL runner."""
from django.test import TestCase
from django.contrib.auth import get_user_model
from documents.models import Category, Document
from documents.access_policy import DocumentAccessPolicy
from pwanimate.services.study_session import is_user_authorized_for_document
from pwanimate.retrieval.services.document_retrieval import DocumentSemanticRetrievalService

class DocumentIsolationTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.owner=U.objects.create_user(username='wp2-owner')
        self.other=U.objects.create_user(username='wp2-other')
        category=Category.objects.create(code='wp2-notes',name='Notes')
        self.private=Document.objects.create(title='Private',uploaded_by=self.owner,category=category,visibility='private',status='ready')
        self.public=Document.objects.create(title='Public',uploaded_by=self.owner,category=category,visibility='public',status='ready')

    def test_private_is_owner_only_even_for_superuser_and_executives(self):
        self.assertTrue(DocumentAccessPolicy.can_read(self.owner,self.private))
        for role in ['PRESIDENT','DELEGATE']:
            self.other.global_role=role;self.other.is_staff=True;self.other.is_superuser=True;self.other.save()
            self.assertFalse(DocumentAccessPolicy.can_read(self.other,self.private))
            self.assertFalse(is_user_authorized_for_document(self.other,self.private))
            self.assertTrue(DocumentAccessPolicy.can_read(self.other,self.public))

    def test_visibility_revocation_is_live(self):
        self.assertTrue(DocumentAccessPolicy.can_read(self.other,self.public))
        Document.objects.filter(pk=self.public.pk).update(visibility='private')
        self.assertFalse(DocumentAccessPolicy.can_read(self.other,self.public))

    def test_unavailable_and_empty_scope(self):
        self.private.is_available=False;self.private.save()
        self.assertFalse(DocumentAccessPolicy.can_read(self.owner,self.private))
        self.assertFalse(DocumentAccessPolicy.authorized_documents(self.owner,filters={'document_ids':[]}).exists())

    def test_undeclared_institution_denies_restricted_resources(self):
        self.public.visibility='restricted';self.public.save()
        self.assertFalse(DocumentAccessPolicy.can_read(self.other,self.public))

    def test_enrollment_revocation_blocks_programme_fallback(self):
        from documents.models import (Institution, School, Department, Programme, AcademicUnit,
                                      ProgrammeUnit, StudentUnitEnrollment, DocumentAcademicUnit)
        inst=Institution.objects.create(name='WP2 College',slug='wp2-college')
        school=School.objects.create(name='WP2 School',code='WP2',slug='wp2-school',institution=inst)
        department=Department.objects.create(name='WP2 Department',code='WP2D',slug='wp2-department',school=school)
        programme=Programme.objects.create(name='WP2 Programme',code='WP2P',slug='wp2-programme',department=department)
        unit=AcademicUnit.objects.create(code='MAT 101',name='Matrices',slug='wp2-matrices')
        ProgrammeUnit.objects.create(programme=programme,academic_unit=unit)
        enrollment=StudentUnitEnrollment.objects.create(user=self.other,academic_unit=unit,is_active=True)
        self.other.institution=inst;self.other.programme=programme;self.other.save()
        self.public.visibility='restricted';self.public.save()
        DocumentAcademicUnit.objects.create(document=self.public,academic_unit=unit)
        self.assertTrue(DocumentAccessPolicy.can_read(self.other,self.public))
        enrollment.is_active=False;enrollment.save()
        self.assertFalse(DocumentAccessPolicy.can_read(self.other,self.public))
        self.assertFalse(DocumentAccessPolicy.authorized_documents(self.other,filters={'category':'unrelated'}).exists())
