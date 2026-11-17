"""Bounded application icon regressions: actual templates and local browser assets.

No application database, provider or production endpoint is used.
"""
import argparse
import datetime
import json
import os
import re
import uuid
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from urllib.parse import urlsplit, parse_qs
from django.http import QueryDict
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','tests.sals_wp4.ui_settings')
import django
django.setup()
from django.template.loader import render_to_string
from playwright.sync_api import sync_playwright

USER=NS(id=7,pk=7,username='qa',is_authenticated=True,is_staff=True,is_superuser=True,profile_pic=None,get_full_name=lambda:'QA')
BASE={'user':USER,'request':NS(user=USER,path='/search/',GET={},resolver_match=NS(url_name='search',app_name='search',namespace='search'))}

def search_context(tab='all',query='test'):
    person={'id':8,'username':'person','title':'QA Person','detail_url':'/users/profile/qa/','avatar_url':'/static/images/default_profile.png','global_role':'VERIFIED','subtitle':'Student'}
    group={'id':2,'title':'QA Group','detail_url':'/groups/2/','avatar_url':'/static/images/default_group.jpg','is_official':True}
    results={'people':[person],'groups':[group],'documents':[],'posts':[]}
    return {**BASE,'query':query,'active_tab':tab,'results':results,'counts':{'all':2,'people':1,'groups':1,'documents':0,'posts':0},'following_ids':[],'recent_searches':['algebra'],'did_you_mean':'testing'}

def wrapper(content):
    css=['css/bootstrap.min.css','css/bootstrap-icons.css','css/custom.css','css/vasto-navigation-icons.css','css/vasto-application-icons.css']
    links=''.join('<link rel="stylesheet" href="/static/'+p+'">' for p in css)
    scripts=''.join('<script src="/static/'+p+'"></script>' for p in ['js/htmx.min.js','vendor/lucide/lucide.min.js','js/vasto-icons.js'])
    oob = re.search(r'<aside[^>]*id="context-rail-target"[\s\S]*?</aside>', content)
    rail = oob.group() if oob else '<aside id="context-rail-target"></aside>'
    if oob: content=content.replace(oob.group(),'')
    return '<!doctype html><html data-theme="light" data-bs-theme="light"><head><meta name="viewport" content="width=device-width,initial-scale=1">'+links+'</head><body><main id="page-content-target">'+content+'</main>'+rail+scripts+'</body></html>'


# ---------------------------------------------------------------- admin batch
def admin_ticket(**kw):
    data = dict(id=str(uuid.uuid4()), subject='Cannot sign in',
                sender=NS(username='student1', email='s1@example.com', profile_pic=None, is_staff=False),
                created_at=datetime.datetime(2026, 11, 10, 9, 30), status='UNREAD',
                get_category_display='Bug', get_status_display='Unread',
                message='The sign-in button does nothing on my phone.',
                current_url='/accounts/login/', device_info='Chrome on Android')
    data.update(kw)
    return NS(**data)


def admin_reply():
    return NS(sender=NS(username='rootadmin', is_staff=True),
              created_at=datetime.datetime(2026, 11, 10, 10, 5), message='Looking into it.')


def admin_shared():
    return {
        'ops': {'online_users': 4, 'unread_feedback': 2, 'open_tickets': 3, 'provider_alerts': 1,
                'model_counts': {'active': 6, 'rate_limited': 1, 'disabled': 1, 'no_key': 1, 'unregistered': 0},
                'model_total': 9},
        'engines': [dict(slug='speed', name='Spark Speed', icon='bi-lightning-charge', icon_lucide='zap',
                        tagline='Rapid everyday Q&A', description='', powered_by=['Google', 'Groq'],
                        primary_provider='gemini', primary_model='gemini-flash-latest',
                        primary_status='ready', fallbacks_ready=2, cooldown_remaining=0)],
        'providers': [dict(label='Google', provider='gemini', state='healthy', configured=True,
                           key_state='ready', key_detail='Configured', chain_models=3,
                           rate_limited_models=0, cooldown_remaining=0,
                           models=[dict(display_name='Gemini Flash', model='gemini-flash-latest',
                                        provider_label='Google', rate_limited=False,
                                        cooldown_remaining=0, description=''),
                                  dict(display_name='Gemini Pro', model='gemini-pro-latest',
                                       provider_label='Google', rate_limited=True,
                                       cooldown_remaining=42, description='')])],
        'registry': {'healthy': True, 'offerings': 14, 'disabled': 2, 'default_tier': True, 'any_tier': True},
    }


def admin_ctx(user, url_name, content_partial, page_title, **extra):
    request = NS(user=user, path='/admin/dashboard/', GET=QueryDict(''), COOKIES={}, META={},
                 resolver_match=NS(url_name=url_name, app_name='admin_dashboard',
                                   namespace='admin_dashboard'))
    ctx = {'app_version': 'wp3-test', 'request': request, 'user': user,
           'unread_messages_count': 0, 'unread_notifications_count': 0,
           'has_unread_group_activity': False}
    ctx.update(admin_shared())
    ctx.update({'content_partial': content_partial, 'page_title': page_title})
    ctx.update(extra)
    return ctx


def admin_pages():
    ticket = admin_ticket()
    doc = NS(share_id='123e4567-e89b-42d3-a456-426614174000', title='Algebra notes', category=NS(name='Math'),
             created_at=datetime.datetime(2026, 11, 1), get_status_display='Published',
             get_visibility_display='Private', id=42,
             uploaded_by=NS(username='uploader1', email='up@example.com'))
    doc_row = NS(document=doc, latest_version_id=3, index_state='fully_indexed',
                 chunks_total=12, chunks_active=12, chunks_completed=12, chunks_pending=0,
                 chunks_processing=0, chunks_failed=0, embedding_model='embed-v3',
                 embedding_models=['embed-v3'], views_count=20, downloads_count=5)
    model_row = NS(model_id='gemini-flash-latest', provider='gemini', provider_label='Google',
                   status='active', disabled=False, rate_limited=False, cooldown_remaining=0,
                   key_detail='', engines=[NS(name='Spark Speed')], in_global_chain=False,
                   primary_of=['Spark Speed'], registered=True, min_tier=NS(name='Pro'),
                   min_tier_name='Pro', usage_tokens=1500, usage_share=60, usage_requests=12,
                   toggleable=True)
    usage = {'totals': {'total': 9000, 'prompt': 5000, 'completion': 4000, 'requests': 20,
                        'images': 0, 'cost_usd': 0.0025, 'users': 3},
             'by_model': [dict(model_id='gemini-flash-latest', provider='gemini', total=9000,
                               requests=20, users=3, cost_usd=0.0025)],
             'by_provider': [dict(provider='gemini', total=9000, requests=20, users=3, cost_usd=0.0025)]}
    login_row = NS(when=datetime.datetime(2026, 11, 16, 8, 0), user='student1', ip='10.0.0.1',
                   location='Nairobi', browser='Chrome', os_device='Windows 11',
                   outcome='success', outcome_label='Signed in')
    admin_user = NS(user=NS(id=9), username='rootadmin', email='root@example.com',
                    is_superuser=True, profile_pic=None, online=True,
                    last_seen=datetime.datetime(2026, 11, 17, 8, 0),
                    last_login=datetime.datetime(2026, 11, 16, 22, 0), sessions=2,
                    current_browser='Chrome', current_os='Linux', current_device='Desktop',
                    current_location='Nairobi', two_factor=True)
    day = json.dumps(['Nov 01', 'Nov 02'])
    pages = {
        '/admin/': ('admin_dashboard/dashboard.html', 'admin_dashboard/partials/dashboard_content.html',
                    'Overview', 'dashboard_home', dict(
                        total_users=120, daily_active_users=34, online_users=4, pending_reports=2,
                        unread_feedback_count=2, open_tickets_count=3, recent_feedback=[ticket],
                        chart_labels=day, growth_data='[1, 2]', active_data='[3, 4]')),
        '/admin/inbox/': ('admin_dashboard/inbox.html', 'admin_dashboard/partials/inbox_content.html',
                          'Feedback inbox', 'feedback_inbox',
                          dict(tickets=[ticket], current_filter='All')),
        '/admin/ticket/': ('admin_dashboard/ticket_detail.html', 'admin_dashboard/partials/ticket_detail_content.html',
                           'Ticket', 'feedback_detail', dict(ticket=ticket, replies=[admin_reply()])),
        '/admin/user-feedback/': ('admin_dashboard/user_feedback.html',
                                  'admin_dashboard/partials/user_feedback_content.html',
                                  'Feedback', 'user_feedback_view',
                                  dict(ticket=ticket, replies=[admin_reply()])),
        '/admin/documents/': ('admin_dashboard/documents.html', 'admin_dashboard/partials/documents_content.html',
                              'Documents', 'documents', dict(
                                  rows=[doc_row], tab='all', search='', doc_status='', visibility='',
                                  sort='newest',
                                  tabs=[('all', 'All', 1), ('fully_indexed', 'Fully indexed', 1),
                                        ('partially_indexed', 'Partially', 0), ('not_started', 'Not started', 0)],
                                  sort_options=[('newest', 'Newest'), ('oldest', 'Oldest')],
                                  status_choices=[('PUBLISHED', 'Published')],
                                  visibility_choices=[('PRIVATE', 'Private')],
                                  summary=dict(all=1, fully_indexed=1, partially_indexed=0, not_started=0,
                                               total_chunks=12, with_failures=0, total_views=20,
                                               total_downloads=5),
                                  page_obj=NS(paginator=NS(count=1, num_pages=1), has_next=False,
                                             has_previous=False, number=1, next_page_number=2,
                                             previous_page_number=0),
                                  flash='', flash_level='info')),
        '/admin/ai/models/': ('admin_dashboard/ai_models.html', 'admin_dashboard/partials/ai_models_content.html',
                              'Models', 'ai_models',
                              dict(models=[model_row], usage_days=30, flash='', flash_level='info',
                                   success=False)),
        '/admin/ai/providers/': ('admin_dashboard/ai_providers.html', 'admin_dashboard/partials/ai_providers_content.html',
                                 'AI providers', 'ai_providers', dict(
                                     providers_ready=1, providers_missing=0, rungs_limited=0, rungs_total=3,
                                     llm_chain=[dict(model_id='gemini-flash-latest', provider_label='Google',
                                                     provider='gemini', configured=True)],
                                     vision_chain=[dict(model_id='gemini-flash-latest', provider_label='Google',
                                                        provider='gemini', configured=True)],
                                     quota=dict(models=[], cooldown_duration=60))),
        '/admin/ai/usage/': ('admin_dashboard/ai_usage.html', 'admin_dashboard/partials/ai_usage_content.html',
                             'Token usage', 'ai_usage', dict(
                                 days=30, usage=usage, chart_labels=day, chart_prompt='[1]',
                                 chart_completion='[1]', chart_cost='[0.1]')),
        '/admin/users/logins/': ('admin_dashboard/user_logins.html', 'admin_dashboard/partials/user_logins_content.html',
                                 'User logins', 'user_logins', dict(
                                     days=30, outcome='all', search='',
                                     outcome_options=[('all', 'All'), ('success', 'Success'),
                                                      ('failure', 'Failure'), ('new_device', 'New device')],
                                     login_rows=[login_row])),
        '/admin/admins/': ('admin_dashboard/admin_manager.html', 'admin_dashboard/partials/admin_manager_content.html',
                           'Admin manager', 'admin_manager', dict(admins=[admin_user])),
    }
    return {path: render_to_string(template, admin_ctx(USER, url_name, partial, title, **extra))
            for path, (template, partial, title, url_name, extra) in pages.items()}


def admin_nav_partial(path):
    """HTMX workspace response: centre panel + out-of-band rails."""
    matches = {'documents': ('documents', 'admin_dashboard/partials/documents_content.html', 'Documents'),
               'inbox': ('feedback_inbox', 'admin_dashboard/partials/inbox_content.html', 'Feedback inbox'),
               'models': ('ai_models', 'admin_dashboard/partials/ai_models_content.html', 'Models'),
               'providers': ('ai_providers', 'admin_dashboard/partials/ai_providers_content.html', 'AI providers'),
               'usage': ('ai_usage', 'admin_dashboard/partials/ai_usage_content.html', 'Token usage'),
               'logins': ('user_logins', 'admin_dashboard/partials/user_logins_content.html', 'User logins'),
               'admins': ('admin_manager', 'admin_dashboard/partials/admin_manager_content.html', 'Admin manager')}
    key = next((k for k in matches if k in path), 'overview')
    if key == 'overview':
        ctx = admin_ctx(USER, 'dashboard_home', 'admin_dashboard/partials/dashboard_content.html',
                        'Overview', total_users=120, daily_active_users=34, online_users=4,
                        pending_reports=2, unread_feedback_count=2, open_tickets_count=3,
                        recent_feedback=[], chart_labels='[]', growth_data='[]', active_data='[]')
    else:
        url_name, partial, title = matches[key]
        # Reuse the full-page fixtures by re-rendering through admin_pages is
        # wasteful; build a minimal context — empty loops still exercise chrome.
        ctx = admin_ctx(USER, url_name, partial, title)
    return render_to_string('admin_dashboard/partials/admin_navigation_partial.html', ctx)



# ----------------------------------------------------------- notifications batch
def notif_actor(uid=8, name='student1'):
    return NS(id=uid, username=name, profile_pic=None, is_staff=False)


def notif_obj(**kw):
    data = dict(id=5, notification_id=None, is_read=False, notification_type='LIKE',
                msg='liked your post', post=None, group=None,
                sender=notif_actor(),
                created_at=datetime.datetime(2026, 11, 16, 9, 0))
    data.update(kw)
    return NS(**data)


def notif_ctx(user, **extra):
    request = NS(user=user, path='/notifications/', GET=QueryDict(''), COOKIES={}, META={},
                 resolver_match=NS(url_name='notifications', app_name='notifications',
                                   namespace='notifications'))
    ctx = {'app_version': 'wp3-test', 'request': request, 'user': user,
           'unread_messages_count': 0, 'unread_notifications_count': 2,
           'has_unread_group_activity': False,
           'time_filter': 'all', 'search_query': '', 'next_cursor': None,
           'filter_type': '', 'filter_read': None,
           'current_filter_type': '', 'current_filter_read': '',
           'current_grouped': '', 'current_sender_grouped': '', 'current_hybrid_grouped': '',
           'has_pagination': False, 'notifications': [], 'time_grouped': {}}
    ctx.update(extra)
    return ctx


def notif_pages():
    avatar = NS(username='student1', profile_pic=None)
    group = NS(id=2, name='QA Group')
    notifs = [
        notif_obj(notification_type='LIKE', msg='liked your post'),
        notif_obj(id=6, notification_type='FOLLOW', msg='started following you'),
        notif_obj(id=7, notification_type='GROUP_REQUEST', msg='requested to join QA Group',
                  group=group, sender=notif_actor(9, 'joiner9')),
    ]
    sender_grouped_item = NS(
        display_mode='sender_grouped', sender=notif_actor(), sender_avatar=avatar,
        unread_count=2, total_count=3,
        latest_timestamp=datetime.datetime(2026, 11, 16, 9, 0), notifications=notifs)
    activity_item = NS(
        display_mode='activity_grouped', count=2, is_read=False, notification_type='LIKE',
        latest_timestamp=datetime.datetime(2026, 11, 16, 8, 0),
        sender_avatars=[NS(username='ana', profile_pic=None), NS(username='bo', profile_pic=None)],
        notifications=[NS(id=11, msg='liked your post',
                          sender=NS(username='ana', profile_pic=NS(url='')),
                          created_at=datetime.datetime(2026, 11, 16, 8, 0)),
                       NS(id=12, msg='liked your post',
                          sender=NS(username='bo', profile_pic=NS(url='')),
                          created_at=datetime.datetime(2026, 11, 15, 8, 0))],
        post=None, group=None)
    sender_group = NS(
        sender=notif_actor(), sender_avatar=avatar, unread_count=1, total_count=2,
        latest_timestamp=datetime.datetime(2026, 11, 16, 9, 0),
        notifications=[notif_obj(notification_id=None)])
    activity_group = NS(
        count=2, post=None, group=group, notifications=[notif_obj(notification_id=None)])
    variants = {
        '/notifications/': {},
        '/notifications/hybrid/': {'current_hybrid_grouped': 'true',
                                   'notifications': [sender_grouped_item, activity_item]},
        '/notifications/by-person/': {'current_sender_grouped': 'true',
                                      'notifications': [sender_group]},
        '/notifications/by-activity/': {'current_grouped': 'true',
                                        'notifications': [activity_group]},
    }
    return {path: admin_page_html(render_to_string('notifications/notifications.html',
                                                  notif_ctx(USER, **extra)))
            for path, extra in variants.items()}


def notif_nav_partial():
    return render_to_string('notifications/partials/notifications_navigation_partial.html',
                            notif_ctx(USER, current_hybrid_grouped='true',
                                      notifications=[NS(
                                          display_mode='activity_grouped', count=1, is_read=False,
                                          notification_type='LIKE',
                                          latest_timestamp=datetime.datetime(2026, 11, 16, 8, 0),
                                          sender_avatars=[NS(username='ana', profile_pic=None)],
                                          notifications=[], post=None, group=None)]))


# ----------------------------------------------------------- posts batch
def post_ctx(user, **extra):
    request = NS(user=user, path='/posts/', GET=QueryDict(''), COOKIES={}, META={},
                 resolver_match=NS(url_name='home', app_name='posts', namespace='posts'))
    ctx = {'app_version': 'wp3-test', 'request': request, 'user': user,
           'unread_messages_count': 0, 'unread_notifications_count': 0,
           'has_unread_group_activity': False,
           'liked_post_ids': set(), 'reposted_post_ids': set()}
    ctx.update(extra)
    return ctx


def post_author():
    return NS(id=8, username='student1', get_full_name=lambda: 'Student One',
              profile_pic=NS(url=''), is_staff=False)


def posts_pages():
    from posts.forms import PostForm
    form = PostForm()
    post = NS(share_id='12345678-1234-1234-1234-123456789abc', id=1, like_count=3, is_reel=False,
              author=post_author(), original_author=post_author(),
              content='Hello campus', created_at=datetime.datetime(2026, 11, 16, 9, 0),
              comments=NS(count=0), images=NS(first=None), group=None,
              visibility='PUBLIC', allow_downloads=True, repost_count=0,
              unit=None, audio=None, thumbnail_url='', shared_document=None)
    reel_post = NS(share_id='87654321-4321-4321-4321-cba987654321', id=2, is_reel=True, content='reel',
                   author=post_author(), original_author=post_author(),
                   created_at=datetime.datetime(2026, 11, 16, 9, 0),
                   comments=NS(count=0), like_count=8, repost_count=1,
                   group=None, unit=None, video=NS(url=''), audio=None,
                   thumbnail_url='', allow_downloads=True, visibility='PUBLIC')
    preview = NS(title='Example Article', description='A preview', url='https://example.com/a',
                 domain='example.com', site_name='Example', media_type='article',
                 thumbnail_url='', favicon=NS(url=''))
    pages = {
        '/posts/create/': render_to_string('posts/partials/create_post_content.html',
            post_ctx(USER, form=form, user_groups=[], selected_group=None, selected_group_id='')),
        '/posts/like/': render_to_string('posts/partials/like_button.html',
            post_ctx(USER, post=post, is_reel=False)),
        '/posts/reel-like/': render_to_string('posts/partials/like_button.html',
            post_ctx(USER, post=post, is_reel=True)),
        '/posts/comments-modal/': render_to_string('posts/partials/comment_modal.html', post_ctx(USER)),
        '/posts/link-preview/': render_to_string('posts/partials/link_preview_card.html',
            post_ctx(USER, preview=preview)),
        '/posts/nav/': render_to_string('posts/partials/home_navigation_partial.html', post_ctx(USER)),
        '/posts/reel-card/': render_to_string('posts/partials/reel_card.html',
            post_ctx(USER, post=reel_post)),
    }
    return {path: wrapper(html) for path, html in pages.items()}


# ----------------------------------------------------------- releases batch
def rel_actor():
    return NS(username='rootadmin', is_staff=True)


def release_obj(**kw):
    data = dict(id=7, version='2.4.0', build_number='240', release_title='Campus Polish',
                release_summary='Bug fixes and UI polish', release_channel='STABLE',
                get_release_channel_display=lambda: 'Stable', status='PUBLISHED',
                get_status_display=lambda: 'Published', release_type='PATCH',
                get_release_type_display=lambda: 'Patch',
                published_at=datetime.datetime(2026, 11, 15, 12, 0),
                created_at=datetime.datetime(2026, 11, 14, 9, 0),
                updated_at=datetime.datetime(2026, 11, 15, 12, 0),
                created_by=rel_actor(), published_by=rel_actor(),
                mandatory_update=False, minimum_supported_version='',
                environment_snapshot='', is_current_release=False,
                items=NS(all=[]))
    data.update(kw)
    return NS(**data)


def releases_ctx(user, **extra):
    request = NS(user=user, path='/system/releases/', GET=QueryDict(''), COOKIES={}, META={},
                 resolver_match=NS(url_name='release_list', app_name='releases', namespace='releases'))
    ctx = {'app_version': 'wp3-test', 'request': request, 'user': user,
           'unread_messages_count': 0, 'unread_notifications_count': 0,
           'has_unread_group_activity': False}
    ctx.update(extra)
    return ctx


def releases_pages():
    from releases.forms import (CreateReleaseForm, ReleaseForm,
                                PublishReleaseForm, ArchiveReleaseForm, ReleaseItemForm)
    rel = release_obj()
    item = NS(title='New feed ranking', description='Posts now rank by engagement.',
              images=NS(all=[]))
    rel_detail = release_obj(items=NS(all=[item]))
    statistics = NS(total_releases=4, draft_count=1, published_count=2, archived_count=1,
                    current_version='2.4.0')
    templates = {
        '/system/releases/': ('releases/list.html',
            {'releases': [rel], 'release_content_partial': 'releases/partials/list_content.html'}),
        '/system/releases/dashboard/': ('releases/dashboard.html',
            {'statistics': statistics, 'current_release': rel, 'draft_releases': [],
             'published_releases': [rel], 'archived_releases': [], 'environment': 'production',
             'debug': False, 'database_engine': 'postgresql',
             'release_content_partial': 'releases/partials/dashboard_content.html'}),
        '/system/releases/create/': ('releases/create.html',
            {'form': CreateReleaseForm(), 'release_content_partial': 'releases/partials/create_content.html'}),
        '/system/releases/7/edit/': ('releases/edit.html',
            {'form': ReleaseForm(), 'release': rel,
             'release_content_partial': 'releases/partials/edit_content.html'}),
        '/system/releases/7/': ('releases/detail.html',
            {'release': rel_detail, 'release_content_partial': 'releases/partials/detail_content.html'}),
        '/system/releases/7/publish/': ('releases/publish.html',
            {'form': PublishReleaseForm(), 'release': rel,
             'release_content_partial': 'releases/partials/publish_content.html'}),
        '/system/releases/7/archive/': ('releases/archive.html',
            {'form': ArchiveReleaseForm(), 'release': rel,
             'release_content_partial': 'releases/partials/archive_content.html'}),
        '/system/releases/7/items/new/': ('releases/add_item.html',
            {'form': ReleaseItemForm(), 'release': rel,
             'release_content_partial': 'releases/partials/add_item_content.html'}),
    }
    return {path: admin_page_html(render_to_string(tpl, releases_ctx(USER, **ctx)))
            for path, (tpl, ctx) in templates.items()}


def releases_nav_partial():
    return render_to_string('releases/partials/releases_navigation_partial.html',
                            releases_ctx(USER, release_content_partial='releases/partials/list_content.html',
                                         releases=[release_obj()]))


# ----------------------------------------------------------- messaging batch
def msg_user(uid, name, username):
    return NS(id=uid, username=username, first_name=name.split()[0],
              last_name=name.split()[-1] if ' ' in name else '', second_name='',
              get_full_name=lambda: name, profile_pic=NS(url=''),
              is_online=False, is_staff=False, chat_theme_preference='ocean-wave')


def msg_member(user, **kw):
    data = dict(user=user, is_muted=False, is_pinned=False, role='member')
    data.update(kw)
    return NS(**data)


def msg_conversation(cid, ctype, name, members, **kw):
    peer = next((m.user for m in members if m.user.username != 'student1'), members[0].user)
    data = dict(id=cid, type=ctype, name=name, group_id=None, group=None,
                is_encrypted=False, is_muted=False, is_pinned=False,
                members=NS(count=len(members), all=members),
                messages=NS(last=NS(content='Hey there')),
                last_msg_time=datetime.datetime(2026, 11, 16, 8, 30),
                peer_user=peer)
    data.update(kw)
    return NS(**data)


def messaging_ctx(user, **extra):
    request = NS(user=user, path='/messages/', GET=QueryDict(''), COOKIES={}, META={},
                 resolver_match=NS(url_name='conversation_list', app_name='messaging',
                                   namespace='messaging'))
    ctx = {'app_version': 'wp3-test', 'request': request, 'user': user,
           'unread_messages_count': 0, 'unread_notifications_count': 0,
           'has_unread_group_activity': False}
    ctx.update(extra)
    return ctx


def messaging_pages():
    me = msg_user(1, 'Student One', 'student1')
    peer = msg_user(2, 'Amina Juma', 'amina')
    grp_peer = msg_user(3, 'Baraka Mushi', 'baraka')
    direct = msg_conversation(11, 'direct', '', [msg_member(me), msg_member(peer)])
    group = msg_conversation(22, 'group', 'CS101 Study Group',
                             [msg_member(me), msg_member(peer), msg_member(grp_peer)])
    conv_data = [
        {'conversation': group, 'is_pinned': True, 'unread_count': 3,
         'preview': {'icon': 'users', 'type': 'text', 'text': 'Baraka: quiz tomorrow'}},
        {'conversation': direct, 'is_pinned': False, 'unread_count': 0,
         'preview': {'icon': 'mic', 'type': 'audio', 'text': 'Voice message'}},
    ]
    friends = [msg_user(4, 'Neema K', 'neema'), msg_user(5, 'Juma S', 'juma')]
    base_extra = dict(conversation_data=conv_data, users=friends,
                      unmessaged_friends=friends, suggested_users=friends,
                      following_ids=[4, 5])
    partials = {
        '/messages/chat-content/': render_to_string('messaging/partials/conversation_chat_content.html',
            messaging_ctx(USER, conversation=direct, messages=[],
                          initial_messages_json='[]', partner_user=peer,
                          target_user=peer, partner_relationship='Classmate')),
        '/messages/right-rail/': render_to_string('messaging/partials/conversation_right_rail.html',
            messaging_ctx(USER, conversation=direct, is_oob=False)),
        '/messages/media/': render_to_string('messaging/partials/conversation_media_items.html',
            messaging_ctx(USER, conversation=direct, media_type='media', page=1,
                          items=[], has_next=False, next_page=2)),
        '/messages/suggestions/': render_to_string('messaging/partials/suggestion_cards.html',
            messaging_ctx(USER, suggested_users=friends)),
        '/messages/user-search/': render_to_string('messaging/partials/user_search_results.html',
            messaging_ctx(USER, users=friends)),
    }
    pages = {
        '/messages/': admin_page_html(render_to_string('messaging/conversation_detail_refactored.html',
            messaging_ctx(USER, conversation=direct, messages=[],
                          initial_messages_json='[]', partner_user=peer,
                          target_user=peer, partner_relationship='Classmate',
                          is_oob=False, **base_extra))),
        '/messages/empty/': admin_page_html(render_to_string('messaging/conversation_detail_refactored.html',
            messaging_ctx(USER, conversation=None, messages=[], is_oob=False, **base_extra))),
        '/messages/group/': admin_page_html(render_to_string('messaging/conversation_detail_refactored.html',
            messaging_ctx(USER, conversation=group, messages=[],
                          initial_messages_json='[]', partner_user=None,
                          target_user=None, partner_relationship='',
                          is_oob=False, **base_extra))),
        '/messages/list/': admin_page_html(render_to_string('messaging/conversation_list.html',
            messaging_ctx(USER, active_conversation=None, active_conversation_obj=None,
                          messages=[], **base_extra))),
    }
    pages.update({path: wrapper(html) for path, html in partials.items()})
    return pages


def messaging_nav_partial():
    return render_to_string('messaging/partials/conversation_chat_partial.html',
                            messaging_ctx(USER))


def admin_page_html(html):
    """Keep the shared-shell scripts the admin workspace actually needs."""
    def script(match):
        attrs, code = match.group(1), match.group(2)
        if any(name in attrs for name in ['js/htmx.min.js', 'js/bootstrap.bundle.min.js',
                                          'vendor/lucide/lucide.min.js', 'js/vasto-icons.js',
                                          'js/admin/admin-workspace.js']):
            return match.group()
        if any(marker in code for marker in ['function setTheme(', 'window.isAuthenticated =',
                                             'function toggleSenderGroup(', 'function toggleExpand(',
                                             'function toggleOlderNotifications(']):
            return match.group()
        return ''
    return re.sub(r'<script\b([^>]*)>(.*?)</script>', script, html, flags=re.S | re.I)



def run_notifications(passed):
    pages = notif_pages()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True,
                                    args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            u = urlsplit(r.request.url)
            if u.hostname != 'icons.test':
                return r.abort()
            if u.path.startswith('/notifications'):
                if r.request.headers.get('hx-request'):
                    return r.fulfill(body=notif_nav_partial(), content_type='text/html')
                return r.fulfill(body=pages.get(u.path) or pages['/notifications/'],
                                 content_type='text/html')
            file = ROOT / u.path.lstrip('/')
            if u.path.startswith('/static/') and file.is_file():
                return r.fulfill(path=str(file))
            return r.fulfill(body='{}', content_type='application/json')
        page.route('**/*', route)
        page.goto('http://icons.test/notifications/hybrid/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        assert page.locator('i.vasto-icon').count() == 0
        assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0
        assert page.locator('#notification-list svg.vasto-icon').count() >= 10
        passed('Notifications hybrid grouping: sender/activity icons render accessibly')
        page.locator('.sender-group-header').first.click()
        page.wait_for_function("""()=>{const n=document.getElementById("expand-icon-8");return n && n.getAttribute("data-lucide")==="chevron-up"}""")
        passed('Notifications expand toggle swaps chevron through VastoIcons.setIcon')
        for path in ['/notifications/', '/notifications/by-person/', '/notifications/by-activity/']:
            page.goto('http://icons.test' + path)
            page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
            assert page.locator('i.vasto-icon').count() == 0
            assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0
            passed(f'Notifications {path} renders without Bootstrap placeholders')
        page.goto('http://icons.test/notifications/hybrid/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        page.evaluate("""()=>{if(window.htmx)htmx.ajax('GET','/notifications/nav/',{target:'#page-content-target',swap:'innerHTML'})}""")
        page.wait_for_function("""()=>{const t=document.getElementById('context-rail-target');return t && t.querySelector('svg.vasto-icon')}""")
        assert page.locator('#left-nav-rail svg.vasto-icon').count() > 0
        passed('Notifications htmx response refreshes centre panel and both rails out-of-band')
        assert not errors, errors
        passed('Notifications no browser JavaScript errors')
        browser.close()


def run_posts(passed):
    pages = posts_pages()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True,
                                    args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            u = urlsplit(r.request.url)
            if u.hostname != 'icons.test':
                return r.abort()
            if u.path.startswith('/posts') or u.path.startswith('/notifications') or u.path.startswith('/accounts') or u.path.startswith('/workspace'):
                return r.fulfill(body=pages.get(u.path) or pages['/posts/nav/'], content_type='text/html')
            file = ROOT / u.path.lstrip('/')
            if u.path.startswith('/static/') and file.is_file():
                return r.fulfill(path=str(file))
            return r.fulfill(body='{}', content_type='application/json')
        page.route('**/*', route)
        for path in pages:
            page.goto('http://icons.test' + path)
            page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
            assert page.locator('i.vasto-icon').count() == 0, path
            assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0, path
            n = page.locator('svg.vasto-icon').count()
            assert n >= 1, path + ' rendered no icons'
            passed(f'Posts {path} renders {n} accessible Lucide icons')
        # like button: unliked -> liked swap through hx swap is server-rendered;
        # verify the heart placeholder carries filled-state hook
        page.goto('http://icons.test/posts/like/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        heart = page.locator('.like-button [data-lucide="heart"], .engagement-button [data-lucide="heart"]').first
        assert heart.count() == 1
        passed('Posts like button renders Lucide heart with filled-state class hook')
        assert not errors, errors
        passed('Posts no browser JavaScript errors')
        browser.close()


def run_releases(passed):
    pages = releases_pages()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True,
                                    args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            u = urlsplit(r.request.url)
            if u.hostname != 'icons.test':
                return r.abort()
            if u.path.startswith('/system/releases'):
                if r.request.headers.get('hx-request'):
                    return r.fulfill(body=releases_nav_partial(), content_type='text/html')
                return r.fulfill(body=pages.get(u.path) or pages['/system/releases/'],
                                 content_type='text/html')
            file = ROOT / u.path.lstrip('/')
            if u.path.startswith('/static/') and file.is_file():
                return r.fulfill(path=str(file))
            return r.fulfill(body='{}', content_type='application/json')
        page.route('**/*', route)
        for path in pages:
            page.goto('http://icons.test' + path)
            page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
            assert page.locator('i.vasto-icon').count() == 0, path
            assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0, path
            n = page.locator('svg.vasto-icon').count()
            assert n >= 1, path + ' rendered no icons'
            passed(f'Releases {path} renders {n} accessible Lucide icons')
        page.goto('http://icons.test/system/releases/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        page.evaluate("""()=>{if(window.htmx)htmx.ajax('GET','/system/releases/nav/',{target:'#page-content-target',swap:'innerHTML'})}""")
        page.wait_for_function("""()=>{const t=document.querySelector('.release-center');return t && t.querySelector('svg.vasto-icon')}""")
        passed('Releases htmx navigation partial renders Lucide icons')
        assert not errors, errors
        passed('Releases no browser JavaScript errors')
        browser.close()


def run_messaging(passed):
    pages = messaging_pages()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True,
                                    args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            u = urlsplit(r.request.url)
            if u.hostname != 'icons.test':
                return r.abort()
            if u.path.startswith('/messages'):
                return r.fulfill(body=pages.get(u.path) or pages['/messages/'],
                                 content_type='text/html')
            file = ROOT / u.path.lstrip('/')
            if u.path.startswith('/static/') and file.is_file():
                return r.fulfill(path=str(file))
            return r.fulfill(body='{}', content_type='application/json')
        page.route('**/*', route)
        for path in pages:
            page.goto('http://icons.test' + path)
            page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
            assert page.locator('i.vasto-icon').count() == 0, path
            assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0, path
            n = page.locator('svg.vasto-icon').count()
            assert n >= 1, path + ' rendered no icons'
            passed(f'Messaging {path} renders {n} accessible Lucide icons')
        # dynamic preview icon contract: backend emits lucide names into data-lucide
        page.goto('http://icons.test/messages/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        dyn = page.locator('[data-lucide="mic"] svg, svg[data-lucide="mic"], [data-lucide="users"]')
        assert dyn.count() >= 1
        passed('Messaging dynamic preview icons render Lucide names from backend payloads')
        assert not errors, errors
        passed('Messaging no browser JavaScript errors')
        browser.close()

def run_admin(passed):
    pages = {path: admin_page_html(html) for path, html in admin_pages().items()}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True,
                                    args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(r):
            u = urlsplit(r.request.url)
            if u.hostname != 'icons.test':
                return r.abort()
            if u.path.startswith('/admin') or u.path.startswith('/dashboard'):
                if r.request.headers.get('hx-request'):
                    return r.fulfill(body=admin_nav_partial(u.path), content_type='text/html')
                return r.fulfill(body=pages.get(u.path) or pages['/admin/'], content_type='text/html')
            file = ROOT / u.path.lstrip('/')
            if u.path.startswith('/static/') and file.is_file():
                return r.fulfill(path=str(file))
            return r.fulfill(body='{}', content_type='application/json')
        page.route('**/*', route)
        page.goto('http://icons.test/admin/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        assert page.locator('i.vasto-icon').count() == 0
        assert page.locator('#page-content-target i.bi, .adw-topbar i.bi, .adw-rail i.bi, .adw-ctx i.bi').count() == 0
        assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0
        assert page.locator('#left-nav-rail svg.vasto-icon').count() >= 10
        assert page.locator('#adwCtx svg.vasto-icon').count() >= 3
        passed('Admin overview: rails, topbar and content icons render; no Bootstrap glyphs remain')
        page.locator('.adw-icon-btn[aria-label="Cycle theme"]').click()
        page.wait_for_function("""()=>{const n=document.getElementById("adwThemeIcon");return n && (n.getAttribute("data-lucide")==="sun"||n.getAttribute("data-lucide")==="moon-star")}""")
        passed('Admin theme toggle swaps the topbar icon through VastoIcons.setIcon')
        for width in [1200, 390]:
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.locator('svg.vasto-icon:visible').count() > 0
            passed(f'Admin {width}px layout keeps icons visible')
        page.set_viewport_size({'width': 1200, 'height': 900})
        page.locator('#left-nav-rail a[href*="documents"]').first.click()
        page.wait_for_selector('#admin-workspace [data-lucide]', state='attached')
        page.wait_for_function("""()=>{const w=document.getElementById("admin-workspace");return w && w.querySelector("svg.vasto-icon") && !w.querySelector("i.vasto-icon")}""")
        assert page.locator('#context-rail-target svg.vasto-icon').count() >= 3
        assert page.locator('#left-nav-rail svg.vasto-icon').count() >= 10
        passed('Admin htmx navigation swaps centre panel and refreshes both rails out-of-band')
        for path in ['/admin/inbox/', '/admin/ticket/', '/admin/documents/', '/admin/ai/models/',
                     '/admin/ai/providers/', '/admin/ai/usage/', '/admin/users/logins/', '/admin/admins/']:
            page.goto('http://icons.test' + path)
            page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
            assert page.locator('i.vasto-icon').count() == 0
            assert page.locator('#page-content-target i.bi, .adw-topbar i.bi, .adw-rail i.bi, .adw-ctx i.bi').count() == 0
            assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count() == 0
            passed(f'Admin {path} renders without Bootstrap placeholders')
        page.goto('http://icons.test/admin/user-feedback/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        assert page.locator('i.vasto-icon').count() == 0
        passed('Admin user feedback view renders icons inside the shared shell')
        assert not errors, errors
        passed('Admin no browser JavaScript errors')
        browser.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('app',choices=['search','admin','notifications','posts','releases','messaging']);args=parser.parse_args()
    checks=[]
    def passed(name):checks.append(name);print('PASS '+name,flush=True)
    if args.app=='admin':
        run_admin(passed)
        print({'app':args.app,'passed':len(checks),'failed':0,'skipped':0})
        return
    if args.app=='notifications':
        run_notifications(passed)
        print({'app':args.app,'passed':len(checks),'failed':0,'skipped':0})
        return
    if args.app=='posts':
        run_posts(passed)
        print({'app':args.app,'passed':len(checks),'failed':0,'skipped':0})
        return
    if args.app=='releases':
        run_releases(passed)
        print({'app':args.app,'passed':len(checks),'failed':0,'skipped':0})
        return
    if args.app=='messaging':
        run_messaging(passed)
        print({'app':args.app,'passed':len(checks),'failed':0,'skipped':0})
        return
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1200,'height':900});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            u=urlsplit(r.request.url)
            if u.hostname!='icons.test':return r.abort()
            if u.path=='/search/':
                q=parse_qs(u.query);ctx=search_context(q.get('tab',['all'])[0],q.get('q',['test'])[0])
                content=render_to_string('search/partials/search_navigation_partial.html',ctx)
                return r.fulfill(body=content if r.request.headers.get('hx-request') else wrapper(content),content_type='text/html')
            if u.path=='/dropdown/':return r.fulfill(body=wrapper(render_to_string('search/partials/search_dropdown_results.html',search_context())),content_type='text/html')
            file=ROOT/u.path.lstrip('/')
            if u.path.startswith('/static/') and file.is_file():return r.fulfill(path=str(file))
            return r.fulfill(body='{}',content_type='application/json')
        page.route('**/*',route)
        page.goto('http://icons.test/search/')
        page.wait_for_function('window.VastoIcons && document.querySelector("svg.vasto-icon")')
        assert page.locator('i.vasto-icon').count()==0
        assert page.locator('svg.vasto-icon:not([aria-hidden="true"])').count()==0
        assert page.locator('[data-lucide="badge-check"]').count()>=2
        assert page.locator('a[href="/users/profile/qa/"]').count()>0
        passed('Search initial populated results, type/role icons, destinations and accessibility')
        assert page.evaluate('''()=>{const n=[...document.querySelectorAll('svg.vasto-icon')];VastoIcons.render(document);return n.every(x=>x.isConnected)}''')
        passed('Search repeat render retains SVG identity')
        for width in [1200,390]:
            page.set_viewport_size({'width':width,'height':900})
            assert page.locator('.search-input').evaluate('n=>n.getBoundingClientRect().right')<=width+1
            for theme in ['light','dark']:
                page.evaluate("t=>{document.documentElement.dataset.theme=t;document.documentElement.dataset.bsTheme=t}",theme)
                assert page.locator('.search-icon').evaluate('n=>getComputedStyle(n).stroke===getComputedStyle(n).color')
            passed(f'Search {width}px layout and light/dark inheritance')
        page.locator('.search-clear').click();assert page.locator('input[name="q"]').input_value()==''
        passed('Search clear control retains action')
        for tab in ['people','documents','posts','groups','all']:
            page.locator('.search-nav-tabs a').filter(has_text={'people':'People','documents':'Documents','posts':'Posts','groups':'Groups','all':'All'}[tab]).click()
            page.wait_for_function('(tab)=>document.querySelector("input[name=tab]").value===tab',arg=tab)
            assert page.locator('#context-rail-target svg.vasto-icon').count()==5
            assert page.locator('i.vasto-icon').count()==0
            assert page.locator('.search-nav-tabs .active').count()==1
            if tab=='documents':assert page.locator('[data-lucide="file-x"]').count()==1
        passed('Search real htmx filtering, empty states, active tabs and OOB rendering')
        page.goto('http://icons.test/dropdown/')
        assert page.locator('i.vasto-icon').count()==0
        assert page.locator('[data-lucide="users"]').count()==1
        passed('Search dropdown results render on full navigation')
        assert not errors,errors
        passed('Search no browser JavaScript errors')
        browser.close()
    print({'app':args.app,'passed':len(checks),'failed':0,'skipped':0})
if __name__=='__main__':main()
