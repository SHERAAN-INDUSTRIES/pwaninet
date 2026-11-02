import datetime
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from urllib.parse import urlencode
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout
from django.http import JsonResponse, HttpResponse
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.contrib.auth.tokens import default_token_generator
from django.db.models import Q
from users.models import User, Follow, DeviceAccount, Pinch, UserSession, Block, HiddenAuthor, PrivacyLevel, PRIVACY_CHOICES, HeroShowcaseSet
from users.models import TrustedDevice, BlockedDevice, DeviceVerificationEvent, DeviceVerificationStatus
from posts.models import Post, Like, Repost
from users.forms import PwaniSignupForm, PwaniAuthenticationForm, ProfileUpdateForm
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.db import transaction
from notifications.models import NotificationObject
from notifications.services.notification_service import invalidate_unread_count_cache
from users.services.device_service import get_or_create_device_id, hash_device_id
from users.services.email_verification_service import send_verification_email, verify_email_token
from pwaninet.utils.htmx import htmx_location_response


def _existing_direct_conversation_id(viewer, other_user):
    """Return existing 1-1 conversation id or None. Never creates."""
    if not viewer or not other_user or viewer.pk == other_user.pk:
        return None
    try:
        from messaging.models import Conversation
        conv = Conversation.get_direct_conversation_between(viewer, other_user)
        return conv.id if conv else None
    except Exception:
        return None
from .serializers import (
    UserSerializer, UserPublicSerializer, FollowSerializer, PinchSerializer,
    DeviceAccountSerializer, UserUpdateSerializer, NotificationPreferencesSerializer
)
import logging
from django.contrib.auth.views import LoginView, PasswordResetView, PasswordResetConfirmView
from django.core.cache import cache
from .filters import UserFilter, FollowFilter

logger = logging.getLogger('users.auth')


def _safe_login_next(request, candidate):
    """Return a same-host continuation URL, rejecting unsafe redirects."""
    from django.utils.http import url_has_allowed_host_and_scheme

    candidate = (candidate or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ''


def _login_url_with_next(request, candidate, **extra_params):
    """Build a login URL while safely retaining an optional continuation."""
    params = {key: value for key, value in extra_params.items() if value is not None}
    safe_next = _safe_login_next(request, candidate)
    if safe_next:
        params['next'] = safe_next
    url = reverse('login')
    return f'{url}?{urlencode(params)}' if params else url


def get_client_ip(request):
    """Safely extract the client IP address from the request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def register_view(request):
    """
    Handle user registration with robust validation, detailed logging,
    and clear user-facing error feedback.
    """
    if request.user.is_authenticated:
        return redirect('posts:home')

    # Signup can be entered while completing an OAuth authorization. Keep that
    # same-host continuation through account creation and the subsequent login.
    next_url = _safe_login_next(
        request, request.POST.get('next') or request.GET.get('next')
    )

    ref_token = request.GET.get('ref') or request.session.get('invite_token')
    inviter_username = request.session.get('inviter_username')
    if ref_token and not inviter_username:
        try:
            from users.models import PlatformInvite, User
            from users.services.invite_service import decrypt_invite_token
            inv = PlatformInvite.objects.select_related('inviter').filter(token=ref_token).first()
            if inv:
                inviter_username = inv.inviter.username
            else:
                decrypted = decrypt_invite_token(ref_token)
                if decrypted:
                    u_id, _ = decrypted
                    inviter_user = User.objects.get(id=u_id)
                    inviter_username = inviter_user.username
            if inviter_username:
                request.session['invite_token'] = ref_token
                request.session['inviter_username'] = inviter_username
        except Exception:
            pass

    if request.method == 'POST':
        ip = get_client_ip(request)
        submitted_username = request.POST.get('username', '').strip()
        msg_attempt = f"[AUTH-REGISTER] Registration attempt received from IP={ip} for username='{submitted_username}'"
        print(msg_attempt, flush=True)
        logger.info(msg_attempt)

        form = PwaniSignupForm(request.POST)
        try:
            if form.is_valid():
                user = form.save()
                msg_success = f"[AUTH-REGISTER] SUCCESS: User created. id={user.id}, username='{user.username}', IP={ip}"
                print(msg_success, flush=True)
                logger.info(msg_success)

                # Process invite conversion if user joined via invite
                if ref_token:
                    try:
                        from users.services.invite_service import process_invite_conversion
                        process_invite_conversion(ref_token, user)
                        request.session.pop('invite_token', None)
                        request.session.pop('inviter_username', None)
                        request.session.pop('inviter_id', None)
                    except Exception as e:
                        logger.warning(f"Error processing invite conversion: {e}")

                request.session['registered_username'] = user.email or user.username
                messages.success(request, f"Welcome to vasto, @{user.username}! Your account has been created successfully. Sign in using your username or email.")
                return redirect(_login_url_with_next(request, next_url))
            else:
                msg_warn = f"[AUTH-REGISTER] Validation failed for username='{submitted_username}', IP={ip}. Errors: {form.errors.as_json()}"
                print(msg_warn, flush=True)
                logger.warning(msg_warn)
                messages.error(request, "Please correct the errors highlighted below.")
        except Exception as e:
            msg_err = f"[AUTH-REGISTER] Unexpected exception for username='{submitted_username}': {e}"
            print(msg_err, flush=True)
            logger.error(msg_err, exc_info=True)
            messages.error(request, "An unexpected server error occurred during account creation. Please try again.")
    else:
        form = PwaniSignupForm()

    return render(request, 'users/register.html', {
        'form': form,
        'next': next_url,
        'inviter_username': inviter_username,
        'invite_token': ref_token,
        'google_login_enabled': bool(settings.GOOGLE_LOGIN_ENABLED and settings.GOOGLE_WEB_CLIENT_ID),
        'google_web_client_id': settings.GOOGLE_WEB_CLIENT_ID,
        'google_login_uri': request.build_absolute_uri(reverse('users:google_redirect')),
    })


class PwaniLoginView(LoginView):
    """
    Custom LoginView providing:
    1. Structured logging of authentication attempts and failures.
    2. Intelligent diagnostic error messages to help users (e.g. distinguishing
       non-existent accounts from wrong passwords).
    3. Seamless redirection and session cleanup for newly registered users.
    4. Phase 3: 2FA challenge interception for enrolled users.
    """
    template_name = 'registration/login.html'
    form_class = PwaniAuthenticationForm

    def get_initial(self):
        initial = super().get_initial()
        reg_identifier = self.request.session.get('registered_username')
        if reg_identifier:
            initial['username'] = reg_identifier
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['hero_set'] = HeroShowcaseSet.objects.filter(is_active=True).order_by('?').first()
        from django.conf import settings as _settings

        context['google_login_enabled'] = bool(
            _settings.GOOGLE_LOGIN_ENABLED and _settings.GOOGLE_WEB_CLIENT_ID
        )
        context['google_web_client_id'] = _settings.GOOGLE_WEB_CLIENT_ID
        next_url = _safe_login_next(
            self.request, context.get('next') or self.request.GET.get('next')
        )
        context['next'] = next_url
        context['google_login_uri'] = self.request.build_absolute_uri(
            reverse('users:google_redirect')
        )
        # GIS requires login_uri to exactly match a configured Google redirect URI.
        # Keep the authorization URL server-side; the GIS state field carries only
        # a signed, short-lived reference, never Workspaces OAuth parameters.
        context['google_login_state'] = _make_google_login_state(next_url)
        return context

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            next_url = _safe_login_next(request, request.GET.get('next'))
            return redirect(next_url or 'posts:home')
        # If user has an active, unexpired 2FA challenge and didn't explicitly request a fresh login, auto-resume
        if request.method == 'GET' and request.GET.get('fresh') != '1' and request.session.get('_2fa_pending_user_id'):
            import time as _time
            pending_ts = request.session.get('_2fa_pending_ts', 0)
            if int(_time.time()) - pending_ts <= _2FA_CHALLENGE_TIMEOUT:
                return redirect('login_2fa_challenge')
        elif request.method == 'GET' and request.GET.get('fresh') == '1':
            _clear_2fa_pending(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        from users.services import two_factor_service
        import time as _time
        user = form.get_user()
        ip = get_client_ip(self.request)
        msg = f"[AUTH-LOGIN] SUCCESS: User '{user.username}' (id={user.id}) credentials verified from IP={ip}"
        print(msg, flush=True)
        logger.info(msg)

        # Phase 3: if user has 2FA enabled, do NOT log them in yet.
        # Stash their pk in the session and redirect to the 2FA challenge page.
        if two_factor_service.is_two_factor_enabled(user):
            self.request.session.pop('_google_pending_link', None)
            self.request.session.pop('_google_pending_link_sub', None)
            self.request.session['_2fa_pending_user_id'] = user.pk
            self.request.session['_2fa_pending_ts'] = int(_time.time())
            self.request.session['_2fa_backend'] = getattr(user, 'backend', 'users.backends.CaseInsensitiveAuthBackend')
            next_url = (self.request.POST.get('next', '') or self.request.GET.get('next', '')
                        or self.get_success_url())
            self.request.session['_2fa_next'] = (
                _safe_login_next(self.request, next_url) or reverse('posts:home')
            )
            device_id = self.request.POST.get('device_id')
            if device_id:
                self.request.session['_2fa_device_id'] = device_id
            logger.info(
                f"[SECURITY-2FA] CHALLENGE: Redirecting user '{user.username}' to 2FA challenge (IP={ip})"
            )
            return redirect('login_2fa_challenge')

        # No 2FA enrolled — log in immediately.
        self.request.session.pop('registered_username', None)
        self.request.session.pop('_google_pending_link', None)
        self.request.session.pop('_google_pending_link_sub', None)
        # Signal the base template to show a one-time 2FA enrollment nudge.
        self.request.session['_show_2fa_nudge'] = True
        return super().form_valid(form)

    def form_invalid(self, form):
        username = self.request.POST.get('username', '').strip()
        ip = get_client_ip(self.request)
        msg_fail = f"[AUTH-LOGIN] FAILED: Login failed for identifier='{username}' from IP={ip}"
        print(msg_fail, flush=True)
        logger.warning(msg_fail)

        context_extra = {
            'attempted_username': username,
            'login_error_message': 'Invalid username, email, or password. Please check your credentials and try again.',
        }

        return self.render_to_response(self.get_context_data(form=form, **context_extra))


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: 2FA login challenge
# ──────────────────────────────────────────────────────────────────────────────

_2FA_CHALLENGE_TIMEOUT = 900      # seconds before pending session expires (15 min for seamless app switching)
_2FA_MAX_ATTEMPTS = 5             # wrong codes before the challenge is revoked


def login_2fa_challenge_view(request):
    """
    Second-factor challenge shown after credentials are verified for a 2FA-enrolled user.

    Session keys managed:
      _2fa_pending_user_id  – pk of the verified-but-not-yet-logged-in user
      _2fa_pending_ts       – unix timestamp when the pending session was created
      _2fa_next             – URL to redirect to after successful 2FA
      _2fa_attempts         – running count of failed attempts in this challenge

    GET  → render the TOTP / recovery-code input form.
    POST → validate code, complete login on success, or re-render with error on failure.
    """
    import time as _time

    # ── Guard: must have a pending 2FA session ────────────────────────────────
    pending_user_id = request.session.get('_2fa_pending_user_id')
    pending_ts = request.session.get('_2fa_pending_ts', 0)

    if not pending_user_id:
        messages.error(request, 'Your session has expired. Please log in again.')
        return redirect('login')

    if int(_time.time()) - pending_ts > _2FA_CHALLENGE_TIMEOUT:
        next_url = _safe_login_next(request, request.session.get('_2fa_next', ''))
        _clear_2fa_pending(request)
        messages.error(request, 'Your authentication session expired. Please log in again.')
        return redirect(_login_url_with_next(request, next_url))

    try:
        pending_user = User.objects.get(pk=pending_user_id)
    except User.DoesNotExist:
        next_url = _safe_login_next(request, request.session.get('_2fa_next', ''))
        _clear_2fa_pending(request)
        return redirect(_login_url_with_next(request, next_url))

    ip = get_client_ip(request)

    if request.method == 'GET':
        return render(request, 'registration/login_2fa.html', {
            'username': pending_user.username,
        })

    # ── POST: validate the submitted code ─────────────────────────────────────
    from users.services import two_factor_service, recovery_code_service

    code = request.POST.get('code', '').strip()
    attempts = request.session.get('_2fa_attempts', 0)

    # Rate-limit
    if attempts >= _2FA_MAX_ATTEMPTS:
        next_url = _safe_login_next(request, request.session.get('_2fa_next', ''))
        _clear_2fa_pending(request)
        request.session.pop('_google_pending_link', None)
        request.session.pop('_google_pending_link_sub', None)
        msg = (
            f"[SECURITY-2FA] LOCKOUT: 2FA challenge exceeded {_2FA_MAX_ATTEMPTS} attempts "
            f"for user '{pending_user.username}' from IP={ip}"
        )
        logger.warning(msg)
        messages.error(request, 'Too many incorrect attempts. Please log in again.')
        return redirect(_login_url_with_next(request, next_url))

    # Try TOTP first, then recovery code
    used_recovery_code = False
    if two_factor_service.verify_totp(pending_user, code):
        verified = True
    elif recovery_code_service.consume_recovery_code(pending_user, code):
        verified = True
        used_recovery_code = True
    else:
        verified = False

    if not verified:
        attempts += 1
        request.session['_2fa_attempts'] = attempts
        remaining = _2FA_MAX_ATTEMPTS - attempts
        logger.warning(
            f"[SECURITY-2FA] FAILED: Invalid 2FA code for user '{pending_user.username}' "
            f"from IP={ip} (attempt {attempts}/{_2FA_MAX_ATTEMPTS})"
        )
        return render(request, 'registration/login_2fa.html', {
            'username': pending_user.username,
            'error': 'Invalid code. Please try again.',
            'remaining_attempts': remaining,
        })

    # ── Success: complete the login ───────────────────────────────────────────
    next_url = _safe_login_next(request, request.session.get('_2fa_next', ''))
    device_id = request.POST.get('device_id') or request.session.get('_2fa_device_id')
    backend = request.session.get('_2fa_backend', 'users.backends.CaseInsensitiveAuthBackend')
    if device_id:
        request.device_id = device_id

    _clear_2fa_pending(request)

    from django.contrib.auth import login as auth_login
    auth_login(request, pending_user, backend=backend)

    # Complete a deferred Google-account link (stored before the 2FA challenge).
    pending_link = request.session.pop('_google_pending_link', None)
    request.session.pop('_google_pending_link_sub', None)
    if pending_link and pending_link.get('sub'):
        try:
            from users.models import GoogleAccount

            GoogleAccount.objects.get_or_create(
                sub=pending_link['sub'],
                defaults={
                    'user': pending_user,
                    'email': pending_link.get('email', ''),
                    'email_verified': bool(pending_link.get('email_verified', False)),
                    'hosted_domain': pending_link.get('hosted_domain', ''),
                    'link_method': pending_link.get('link_method', 'explicit_link'),
                },
            )
            logger.info(
                f"[AUTH-GOOGLE] LINKED: Google sub={pending_link['sub'][:8]}... to user "
                f"'{pending_user.username}' after 2FA"
            )
        except Exception as exc:
            logger.error(f"[AUTH-GOOGLE] Deferred link failed for '{pending_user.username}': {exc}")

    logger.info(
        f"[AUTH-LOGIN] SUCCESS: User '{pending_user.username}' (id={pending_user.pk}) "
        f"completed 2FA and is now logged in from IP={ip}"
        + (" [RECOVERY CODE]" if used_recovery_code else "")
    )

    if used_recovery_code:
        remaining_codes = recovery_code_service.get_remaining_recovery_codes_count(pending_user)
        if remaining_codes == 0:
            messages.warning(
                request,
                'You used your last recovery code. Please generate new recovery codes immediately to avoid being locked out.'
            )
        else:
            messages.warning(
                request,
                f'You signed in with a recovery code ({remaining_codes} remaining). '
                'Generate new codes now if you have lost access to your authenticator app.'
            )

    return redirect(next_url or 'posts:home')


def _clear_2fa_pending(request):
    """Remove all pending 2FA challenge keys from the session."""
    for key in ('_2fa_pending_user_id', '_2fa_pending_ts', '_2fa_next', '_2fa_attempts', '_2fa_device_id', '_2fa_backend'):
        request.session.pop(key, None)



# ──────────────────────────────────────────────────────────────────────────────
# Phase 4: 2FA & Single-Use Recovery Code Account Recovery / Password Reset
# ──────────────────────────────────────────────────────────────────────────────

_PW_RECOVERY_TIMEOUT = 900       # 15 minutes for 2FA step (allows seamless app switching)
_PW_RESET_GRANT_TIMEOUT = 900    # 15 minutes for setting new password
_PW_RECOVERY_MAX_ATTEMPTS = 5    # Max incorrect 2FA codes


def password_recovery_identify_view(request):
    """
    Step 1 of Account Recovery: Look up user by username and check for active 2FA.
    Does NOT require or use email. Auto-resumes in-progress recovery if app reloaded.
    """
    if request.user.is_authenticated:
        return redirect('posts:home')

    if request.method == 'GET':
        if request.GET.get('fresh') == '1':
            _clear_pw_recovery_pending(request)
            _clear_pw_reset_grant(request)
        else:
            import time as _time
            grant_user_id = request.session.get('_pw_reset_grant_user_id')
            grant_ts = request.session.get('_pw_reset_grant_ts', 0)
            if grant_user_id and (int(_time.time()) - grant_ts <= _PW_RESET_GRANT_TIMEOUT):
                return redirect('users:password_recovery_set_new')

            pending_user_id = request.session.get('_pw_recovery_user_id')
            pending_ts = request.session.get('_pw_recovery_ts', 0)
            if pending_user_id and (int(_time.time()) - pending_ts <= _PW_RECOVERY_TIMEOUT):
                return redirect('users:password_recovery_verify')

        return render(request, 'registration/password_recovery_identify.html')

    ip = get_client_ip(request)
    cache_key = f"vasto:ratelimit:pwidentify:{ip}"
    attempts = cache.get(cache_key, 0)
    if attempts >= 10:
        logger.warning(f"[SECURITY-RATELIMIT] BLOCKED: Password recovery lookup rate limit exceeded for IP={ip}")
        return render(request, 'registration/password_recovery_identify.html', {
            'error': _('Too many recovery attempts. Please wait 5 minutes before trying again.'),
        }, status=429)

    cache.set(cache_key, attempts + 1, timeout=300)

    username = request.POST.get('username', '').strip()
    if not username:
        return render(request, 'registration/password_recovery_identify.html', {
            'error': _('Please enter your username.'),
        })

    from users.services import two_factor_service

    target_user = User.objects.filter(username__iexact=username).first()
    if not target_user:
        logger.warning(f"[SECURITY-RECOVERY] FAILED: Recovery requested for non-existent username='{username}' from IP={ip}")
        return render(request, 'registration/password_recovery_identify.html', {
            'attempted_username': username,
            'error': _('No account found with that username. Please verify and try again.'),
        })

    if not two_factor_service.is_two_factor_enabled(target_user):
        logger.warning(f"[SECURITY-RECOVERY] BLOCKED: Recovery requested for unenrolled user '{target_user.username}' from IP={ip}")
        return render(request, 'registration/password_recovery_identify.html', {
            'attempted_username': username,
            'error': _('Two-Factor Authentication is not enabled for this account. Self-service recovery requires 2FA or a recovery code. Please contact support.'),
        })

    # Stash pending recovery in session
    import time as _time
    _clear_pw_recovery_pending(request)
    _clear_pw_reset_grant(request)

    request.session['_pw_recovery_user_id'] = target_user.pk
    request.session['_pw_recovery_ts'] = int(_time.time())
    request.session['_pw_recovery_attempts'] = 0

    logger.info(f"[SECURITY-RECOVERY] INITIATED: 2FA password recovery initiated for user '{target_user.username}' from IP={ip}")
    return redirect('users:password_recovery_verify')


def password_recovery_verify_view(request):
    """
    Step 2 of Account Recovery: Verify identity via 6-digit TOTP code or single-use recovery code.
    """
    if request.user.is_authenticated:
        return redirect('posts:home')

    import time as _time

    if request.method == 'GET' and request.GET.get('fresh') != '1':
        grant_user_id = request.session.get('_pw_reset_grant_user_id')
        grant_ts = request.session.get('_pw_reset_grant_ts', 0)
        if grant_user_id and (int(_time.time()) - grant_ts <= _PW_RESET_GRANT_TIMEOUT):
            return redirect('users:password_recovery_set_new')

    pending_user_id = request.session.get('_pw_recovery_user_id')
    pending_ts = request.session.get('_pw_recovery_ts', 0)

    if not pending_user_id or (int(_time.time()) - pending_ts > _PW_RECOVERY_TIMEOUT):
        _clear_pw_recovery_pending(request)
        messages.error(request, _('Your recovery session expired. Please start over.'))
        return redirect('users:password_reset')

    try:
        pending_user = User.objects.get(pk=pending_user_id)
    except User.DoesNotExist:
        _clear_pw_recovery_pending(request)
        return redirect('users:password_reset')

    if request.method == 'GET':
        return render(request, 'registration/password_recovery_verify.html', {
            'username': pending_user.username,
        })

    ip = get_client_ip(request)
    attempts = request.session.get('_pw_recovery_attempts', 0)
    if attempts >= _PW_RECOVERY_MAX_ATTEMPTS:
        _clear_pw_recovery_pending(request)
        logger.warning(f"[SECURITY-RECOVERY] LOCKOUT: Recovery challenge exceeded {_PW_RECOVERY_MAX_ATTEMPTS} attempts for user '{pending_user.username}' from IP={ip}")
        messages.error(request, _('Too many incorrect verification attempts. Please start over.'))
        return redirect('users:password_reset')

    code = request.POST.get('code', '').strip()
    from users.services import two_factor_service, recovery_code_service

    used_recovery_code = False
    if two_factor_service.verify_totp(pending_user, code):
        verified = True
    elif recovery_code_service.consume_recovery_code(pending_user, code):
        verified = True
        used_recovery_code = True
    else:
        verified = False

    if not verified:
        attempts += 1
        request.session['_pw_recovery_attempts'] = attempts
        remaining = _PW_RECOVERY_MAX_ATTEMPTS - attempts
        logger.warning(f"[SECURITY-RECOVERY] FAILED: Invalid 2FA/recovery code for user '{pending_user.username}' from IP={ip} (attempt {attempts}/{_PW_RECOVERY_MAX_ATTEMPTS})")
        return render(request, 'registration/password_recovery_verify.html', {
            'username': pending_user.username,
            'error': _('Invalid verification code. Please check and try again.'),
            'remaining_attempts': remaining,
        })

    # Identity Verified! Issue single-use reset grant
    import secrets
    _clear_pw_recovery_pending(request)
    grant_token = secrets.token_urlsafe(32)

    request.session['_pw_reset_grant_user_id'] = pending_user.pk
    request.session['_pw_reset_grant_ts'] = int(_time.time())
    request.session['_pw_reset_grant_token'] = grant_token

    logger.info(
        f"[SECURITY-RECOVERY] VERIFIED: User '{pending_user.username}' verified identity via "
        f"{'Recovery Code' if used_recovery_code else 'TOTP'} from IP={ip}. Reset grant issued."
    )
    return redirect('users:password_recovery_set_new')


def password_recovery_set_new_view(request):
    """
    Step 3 of Account Recovery: Set new password after verified 2FA / Recovery Code challenge.
    """
    if request.user.is_authenticated:
        return redirect('posts:home')

    import time as _time

    grant_user_id = request.session.get('_pw_reset_grant_user_id')
    grant_ts = request.session.get('_pw_reset_grant_ts', 0)
    grant_token = request.session.get('_pw_reset_grant_token')

    if not grant_user_id or not grant_token or (int(_time.time()) - grant_ts > _PW_RESET_GRANT_TIMEOUT):
        _clear_pw_reset_grant(request)
        messages.error(request, _('Your password reset authorization has expired. Please start over.'))
        return redirect('users:password_reset')

    try:
        user = User.objects.get(pk=grant_user_id)
    except User.DoesNotExist:
        _clear_pw_reset_grant(request)
        return redirect('users:password_reset')

    if request.method == 'GET':
        return render(request, 'registration/password_recovery_set_new.html', {
            'username': user.username,
        })

    new_password = request.POST.get('new_password', '')
    confirm_password = request.POST.get('confirm_password', '')

    errors = []
    if not new_password:
        errors.append(_('Please enter a new password.'))
    elif new_password != confirm_password:
        errors.append(_('The two password fields did not match.'))
    else:
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        try:
            validate_password(new_password, user=user)
        except ValidationError as e:
            errors.extend(e.messages)

    if errors:
        return render(request, 'registration/password_recovery_set_new.html', {
            'username': user.username,
            'errors': errors,
        })

    # Update password
    user.set_password(new_password)
    user.save(update_fields=['password'])

    _clear_pw_reset_grant(request)
    ip = get_client_ip(request)
    logger.info(f"[SECURITY-RECOVERY] SUCCESS: Password successfully reset for user '{user.username}' from IP={ip}")

    messages.success(request, _('Your password has been reset successfully. Please log in with your new password.'))
    return redirect('login')


def _clear_pw_recovery_pending(request):
    for key in ('_pw_recovery_user_id', '_pw_recovery_ts', '_pw_recovery_attempts'):
        request.session.pop(key, None)


def _clear_pw_reset_grant(request):
    for key in ('_pw_reset_grant_user_id', '_pw_reset_grant_ts', '_pw_reset_grant_token'):
        request.session.pop(key, None)




def load_academic_levels(request):
    """HTMX endpoint to load academic levels for a programme."""
    programme_id = request.GET.get('programme_id')
    from documents.academic.models import AcademicLevel
    levels = AcademicLevel.objects.filter(is_active=True).order_by('level')
    return render(request, 'users/partials/academic_level_options.html', {'levels': levels})


def load_academic_years(request):
    """HTMX endpoint to load academic years."""
    from documents.academic.models import AcademicYear
    years = AcademicYear.objects.all().order_by('-code')
    return render(request, 'users/partials/academic_year_options.html', {'years': years})


def load_semesters(request):
    """HTMX endpoint to load semesters for an academic year."""
    academic_year_id = request.GET.get('academic_year_id')
    from documents.academic.models import Semester
    if academic_year_id:
        semesters = Semester.objects.filter(academic_year_id=academic_year_id).order_by('number')
    else:
        semesters = Semester.objects.none()
    return render(request, 'users/partials/semester_options.html', {'semesters': semesters})


@login_required
def load_programmes_by_institution(request):
    """HTMX endpoint returning programme <option>s scoped to an institution.

    Institution scope is derived via Programme → Department → School (→ Faculty) → Institution.
    Unknown or inactive institutions yield an empty list (never an error page
    for HTMX swaps, never unscoped data).
    """
    from documents.academic.models import Institution, Programme
    from django.db.models import Q

    institution_id = request.GET.get('institution_id') or request.GET.get('institution')
    selected_programme_id = request.GET.get('selected_programme_id') or request.GET.get('programme')
    institution = None
    programmes = Programme.objects.none()
    if institution_id:
        try:
            institution = Institution.objects.filter(
                pk=institution_id, is_active=True).first()
        except (ValueError, TypeError):
            institution = None
        if institution is not None:
            programmes = Programme.objects.filter(
                is_active=True
            ).filter(
                Q(department__school__institution=institution) |
                Q(department__school__faculty__institution=institution)
            ).distinct().order_by('name')
    return render(request, 'users/partials/programme_options.html', {
        'programmes': programmes,
        'institution': institution,
        'selected_programme_id': selected_programme_id,
    })


@login_required
@require_http_methods(["POST"])
def save_institution_setup(request):
    """Persist the optional institution setup step of onboarding.

    All identifiers are re-validated server-side via InstitutionSetupForm
    (institution scope derived through the hierarchy, forged/inactive IDs
    rejected). Changing the institution clears an incompatible programme;
    clearing/changing the programme deactivates stale auto-synced unit
    enrollments while preserving custom electives/retakes. Everything may
    be left unset (skip).
    """
    from users.forms import InstitutionSetupForm

    form = InstitutionSetupForm(request.POST)
    from documents.academic.models import Institution as _Institution

    def _setup_extra_context():
        try:
            institutions = _Institution.objects.filter(is_active=True).order_by('name')
        except Exception:
            institutions = []
        return {'institutions': institutions, 'setup_programmes': []}

    if not form.is_valid():
        status = 400
        if request.headers.get('HX-Request'):
            ctx = {'setup_form': form, 'setup_saved': False}
            ctx.update(_setup_extra_context())
            return render(request, 'users/onboarding/partials/step_institution.html', ctx, status=status)
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}" if field != '__all__' else str(error))
        return redirect('users:onboarding')

    user = request.user
    institution = form.cleaned_data.get('institution')
    programme = form.cleaned_data.get('programme')
    academic_level = form.cleaned_data.get('academic_level')

    institution_changed = user.institution_id != (institution.pk if institution else None)
    programme_changed = user.programme_id != (programme.pk if programme else None)

    user.institution = institution
    user.programme = programme
    user.academic_level = academic_level
    from django.core.exceptions import ValidationError
    try:
        user.full_clean(exclude=['password'])
    except ValidationError as exc:
        if request.headers.get('HX-Request'):
            ctx = {
                'setup_form': form,
                'setup_saved': False,
                'setup_errors': exc.message_dict,
            }
            ctx.update(_setup_extra_context())
            return render(request, 'users/onboarding/partials/step_institution.html', ctx, status=400)
        messages.error(request, "Selected academic combination is invalid. Please review and try again.")
        return redirect('users:onboarding')
    fields_to_update = ['institution', 'programme', 'academic_level']
    if not user.bio:
        user.bio = user.generate_smart_bio()
        fields_to_update.append('bio')
    
    if programme:
        try:
            from courses.models import Course, Year
            matching_course = Course.objects.filter(name__iexact=programme.name).first()
            if matching_course:
                user.course = matching_course
                fields_to_update.append('course')
                if academic_level:
                    matching_year = Year.objects.filter(course=matching_course, level=academic_level.level).first()
                    if matching_year:
                        user.year = matching_year
                        fields_to_update.append('year')
        except Exception:
            pass
    elif institution_changed and not programme:
        user.course = None
        user.year = None
        fields_to_update.extend(['course', 'year'])

    user.save(update_fields=list(set(fields_to_update)))

    if programme_changed or (institution_changed and not programme):
        try:
            from documents.academic.models import StudentUnitEnrollment
            from documents.academic.services import StudentAcademicEnrollmentService
            StudentUnitEnrollment.objects.filter(
                user=user, source='auto_programme', is_active=True).update(is_active=False)
            if programme:
                StudentAcademicEnrollmentService.sync_student_units(user, force_resync=True)
        except Exception:
            pass

    if request.headers.get('HX-Request'):
        from users.forms import InstitutionSetupForm as _SetupForm
        ctx = {
            'setup_form': _SetupForm(initial={
                'institution': institution,
                'programme': programme,
                'academic_level': academic_level,
            }),
            'setup_saved': True,
        }
        ctx.update(_setup_extra_context())
        return render(request, 'users/onboarding/partials/step_institution.html', ctx)
    messages.success(request, "Academic setup saved. You can continue with onboarding.")
    return redirect('users:onboarding')


def verify_email_view(request, uidb64, token):
    """
    Verify email address using token.
    This is a non-blocking verification - users can still login without verification.
    """
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and verify_email_token(user, token):
        messages.success(request, 'Your email has been verified successfully!')
        return redirect('login')
    else:
        messages.error(request, 'The verification link is invalid or has expired. Please request a new verification email.')
        return redirect('login')


# ──────────────────────────────────────────────────────────────────────────────
# Google sign-in (website, Google Identity Services ID-token flow)
# ──────────────────────────────────────────────────────────────────────────────

_GOOGLE_AUTH_BACKEND = 'users.backends.CaseInsensitiveAuthBackend'


def _google_safe_next(request, data=None):
    """Return a validated `next` URL or '' — open redirects are rejected."""
    candidate = ''
    if data and data.get('next'):
        candidate = data.get('next')
    if not candidate:
        candidate = request.POST.get('next', '') or request.GET.get('next', '')
    return _safe_login_next(request, candidate)


_GOOGLE_CONTINUATION_KEY = '_google_login_next'
_GOOGLE_CONTINUATION_SALT = 'users.google_login_continuation'
_GOOGLE_CONTINUATION_TTL = 900


def _make_google_login_state(next_url):
    """Store next server-side and return an opaque, signed GIS button state."""
    if not next_url:
        return ''
    from importlib import import_module
    from django.conf import settings as _settings
    from django.core import signing

    session_engine = import_module(_settings.SESSION_ENGINE)
    continuation = session_engine.SessionStore()
    continuation[_GOOGLE_CONTINUATION_KEY] = next_url
    continuation.set_expiry(_GOOGLE_CONTINUATION_TTL)
    continuation.save()
    return signing.dumps(
        continuation.session_key, salt=_GOOGLE_CONTINUATION_SALT, compress=True
    )


def _google_redirect_safe_next(request):
    """Consume GIS state as an opaque continuation handle, separate from OAuth state."""
    from importlib import import_module
    from django.conf import settings as _settings
    from django.core import signing

    token = request.POST.get('state', '')
    if token:
        try:
            handle = signing.loads(
                token, salt=_GOOGLE_CONTINUATION_SALT, max_age=_GOOGLE_CONTINUATION_TTL
            )
        except signing.BadSignature:
            return ''
        if not isinstance(handle, str):
            return ''
        session_engine = import_module(_settings.SESSION_ENGINE)
        continuation = session_engine.SessionStore(session_key=handle)
        candidate = continuation.get(_GOOGLE_CONTINUATION_KEY, '')
        continuation.delete()
        return _safe_login_next(request, candidate)
    # Retain compatibility with an explicitly supplied local continuation.
    return _google_safe_next(request)


def _google_request_data(request):
    """Return POST data merged with a JSON body (GIS widget posts JSON)."""
    import json as _json

    data = {key: request.POST.get(key) for key in request.POST}
    if request.body:
        try:
            body = _json.loads(request.body)
            if isinstance(body, dict):
                for key, value in body.items():
                    if key not in data or not data[key]:
                        data[key] = value
        except Exception:
            pass
    return data


def _login_existing_google_link(request, link):
    """Complete login for an already-linked Google identity (verified `sub`)."""
    from django.utils import timezone as _timezone

    linked_user = link.user
    if not linked_user.is_active:
        return JsonResponse({'status': 'error', 'message': 'This account is disabled.'}, status=403)
    link.last_login_at = _timezone.now()
    link.save(update_fields=['last_login_at'])
    logger.info(f"[AUTH-GOOGLE] SUCCESS: User '{linked_user.username}' signed in with Google")
    return _google_complete_login(request, linked_user)


def _google_link_required_response(request, owner_username, claims):
    """Controlled conflict: email matches but ownership is unproven — never merge."""
    from users.services import google_auth_service

    google_auth_service.stash_pending_google(request, claims)
    logger.info(
        f"[AUTH-GOOGLE] LINK-REQUIRED: Google sub={claims.get('sub', '')[:8]}... "
        f"matches email of '{owner_username}'"
    )
    return JsonResponse({
        'status': 'link_required',
        'username': owner_username,
        'message': (
            f"An account ({owner_username}) already uses this email address. "
            "Sign in with your password to link Google sign-in."
        ),
    }, status=409)


_CREATE_MAX_ATTEMPTS = 3


def _create_google_user_account(claims, sub, google_email):
    """Create a User + GoogleAccount for a verified Google identity, race-safely.

    Returns (outcome, user) where outcome is one of:
      'created'              – new account, caller proceeds to login.
      'sub_exists'           – another request linked this `sub` first; `user`
                               is the linked account, safe to authenticate
                               because our own token verification matched it.
      'email_collision'      – an account with this email now exists; `user`
                               is None, caller must require explicit linking.
      'username_unavailable' – repeated username conflicts; `user` is None,
                               caller must return a controlled error.

    Only django.db.IntegrityError is caught, plus the narrow case of a
    username-uniqueness ValidationError raised by User.save()'s full_clean()
    (the database constraint and the model validation cover the same
    username uniqueness rule). Every other exception propagates so real
    programming or database failures stay visible.
    """
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError, transaction
    from django.utils import timezone as _timezone
    from users.models import GoogleAccount, GoogleAccountLinkMethod, User
    from users.services import google_auth_service

    for _ in range(_CREATE_MAX_ATTEMPTS):
        username = google_auth_service.derive_username_from_email(google_email) if google_email else None
        if not username:
            username = google_auth_service.derive_username_from_email(f"user_{sub[:12]}@google.local")
        try:
            with transaction.atomic():
                new_user = User(
                    username=username,
                    email=google_email,
                    first_name=(claims.get('given_name') or '')[:200],
                    last_name=(claims.get('family_name') or '')[:200],
                    email_verified=bool(claims.get('email_verified', False)),
                    has_completed_onboarding=False,
                )
                new_user.set_unusable_password()
                new_user.save()
                GoogleAccount.objects.create(
                    user=new_user,
                    sub=sub,
                    email=google_email,
                    email_verified=bool(claims.get('email_verified', False)),
                    hosted_domain=claims.get('hd', '') or '',
                    link_method=GoogleAccountLinkMethod.NEW_SIGNUP,
                    last_login_at=_timezone.now(),
                )
            return ('created', new_user)
        except IntegrityError:
            pass
        except ValidationError as exc:
            # User.save() validates uniqueness in Python before the database
            # constraint fires. Only a username-uniqueness failure is a
            # retryable race; anything else (e.g. an invalid field) is a
            # real problem and must propagate.
            if 'username' not in getattr(exc, 'message_dict', {}):
                raise

        # A uniqueness conflict occurred: re-query ground truth before deciding.
        link = GoogleAccount.objects.select_related('user').filter(sub=sub).first()
        if link is not None:
            return ('sub_exists', link.user)
        if google_email and User.objects.filter(email__iexact=google_email).exists():
            return ('email_collision', None)
        # Otherwise the conflict was the derived username: loop and derive
        # a fresh candidate against current database state.
        logger.info(f"[AUTH-GOOGLE] RETRY: username conflict for sub={sub[:8]}..., re-deriving")

    logger.warning(f"[AUTH-GOOGLE] FAILED: could not allocate username for sub={sub[:8]}...")
    return ('username_unavailable', None)


def _google_complete_login(request, target_user, link_record=None):
    """Log in via the standard Django session, honouring 2FA and onboarding.

    `link_record` is an unsaved GoogleAccount attached after auth_login so a
    2FA-enrolled user is only linked once the second factor verifies.
    """
    import time as _time

    from django.contrib.auth import login as auth_login
    from users.services import two_factor_service

    data = _google_request_data(request)
    device_id = data.get('device_id') or request.POST.get('device_id')
    if device_id:
        request.device_id = device_id

    if two_factor_service.is_two_factor_enabled(target_user):
        request.session['_2fa_pending_user_id'] = target_user.pk
        request.session['_2fa_pending_ts'] = int(_time.time())
        request.session['_2fa_backend'] = _GOOGLE_AUTH_BACKEND
        request.session['_2fa_next'] = _google_safe_next(request, data) or reverse('posts:home')
        if device_id:
            request.session['_2fa_device_id'] = device_id
        if link_record is not None:
            request.session['_google_pending_link_sub'] = link_record['sub']
            request.session['_google_pending_link'] = link_record
        logger.info(
            f"[SECURITY-2FA] CHALLENGE: Redirecting Google user '{target_user.username}' to 2FA challenge"
        )
        return JsonResponse({'status': 'ok', 'redirect': reverse('login_2fa_challenge')})

    auth_login(request, target_user, backend=_GOOGLE_AUTH_BACKEND)
    if link_record is not None:
        from users.models import GoogleAccount

        GoogleAccount.objects.get_or_create(
            sub=link_record['sub'],
            defaults={
                'user': target_user,
                'email': link_record['email'],
                'email_verified': link_record['email_verified'],
                'hosted_domain': link_record.get('hosted_domain', ''),
                'link_method': link_record['link_method'],
            },
        )
    redirect_to = _google_safe_next(request, data) or reverse('posts:home')
    return JsonResponse({'status': 'ok', 'redirect': redirect_to})


@require_http_methods(["POST"])
def google_complete_view(request):
    """Verify a Google ID token and log the user in with a normal Django session.

    Same-origin, CSRF-protected (no CSRF exemption). Expects `credential`
    (the GIS ID token) as a form field or JSON body.
    """
    from django.conf import settings as _settings
    from users.models import GoogleAccount, User
    from users.services import google_auth_service

    if request.user.is_authenticated:
        return JsonResponse({
            'status': 'ok',
            'redirect': _google_safe_next(request, _google_request_data(request))
                or reverse('posts:home'),
        })
    if not _settings.GOOGLE_LOGIN_ENABLED:
        return JsonResponse({'status': 'error', 'message': 'Google sign-in is currently unavailable.'}, status=403)

    if not google_auth_service.google_complete_allowed(request):
        logger.warning(
            "[SECURITY-RATELIMIT] BLOCKED: Google complete rate limit exceeded for "
            f"IP={google_auth_service.get_rate_limit_ip(request)}"
        )
        return JsonResponse({
            'status': 'error',
            'code': 'rate_limited',
            'message': 'Too many sign-in attempts. Please wait a few minutes and try again.',
        }, status=429)

    data = _google_request_data(request)
    credential = (data.get('credential') or '').strip()
    if not credential:
        return JsonResponse({'status': 'error', 'message': 'No Google credential was provided.'}, status=400)

    try:
        claims = google_auth_service.verify_google_id_token(credential)
    except google_auth_service.GoogleVerificationError as exc:
        logger.warning(f"[SECURITY-GOOGLE] Token verification failed ({exc.code}) from IP={get_client_ip(request)}")
        return JsonResponse({'status': 'error', 'message': str(exc), 'code': exc.code}, status=401)

    sub = claims.get('sub', '')
    google_email = (claims.get('email') or '').lower()

    existing_link = GoogleAccount.objects.select_related('user').filter(sub=sub).first()
    if existing_link is not None:
        return _login_existing_google_link(request, existing_link)

    if google_email:
        email_owner = User.objects.filter(email__iexact=google_email).first()
        if email_owner is not None:
            # Never silently merge on email: require proof of ownership.
            return _google_link_required_response(request, email_owner.username, claims)

    outcome, google_user = _create_google_user_account(claims, sub, google_email)
    if outcome == 'sub_exists':
        # A concurrent request linked this identity first; our verified
        # claims match it, so authenticate as the linked account.
        link = GoogleAccount.objects.select_related('user').filter(sub=sub).first()
        return _login_existing_google_link(request, link)
    if outcome == 'email_collision':
        owner = User.objects.filter(email__iexact=google_email).first()
        return _google_link_required_response(request, owner.username, claims)
    if outcome != 'created':
        return JsonResponse({
            'status': 'error',
            'code': 'username_unavailable',
            'message': 'Could not create your account right now. Please try again.',
        }, status=409)

    new_user = google_user

    ref_token = request.session.get('invite_token')
    if ref_token:
        try:
            from users.services.invite_service import process_invite_conversion

            process_invite_conversion(ref_token, new_user)
            for key in ('invite_token', 'inviter_username', 'inviter_id'):
                request.session.pop(key, None)
        except Exception as exc:
            logger.warning(f"[AUTH-GOOGLE] Invite conversion failed: {exc}")

    logger.info(f"[AUTH-GOOGLE] SUCCESS: Created user '{new_user.username}' (id={new_user.id}) via Google")
    return _google_complete_login(request, new_user)


@require_http_methods(["POST"])
def google_link_view(request):
    """Link a verified Google identity to an existing account after ownership proof.

    Requires a fresh pending Google verification (from `google_complete_view`)
    plus the existing account's username + password, and its 2FA code where
    2FA is enrolled. On success the Google identity is linked and (when no
    2FA challenge is pending) the user is logged in immediately.
    """
    from django.conf import settings as _settings
    from django.contrib.auth import authenticate
    from django.utils import timezone as _timezone
    from users.models import GoogleAccount, GoogleAccountLinkMethod, User
    from users.services import google_auth_service, recovery_code_service, two_factor_service

    if request.user.is_authenticated:
        google_auth_service.clear_pending_google(request)
        return JsonResponse({'status': 'ok', 'redirect': reverse('posts:home')})
    if not _settings.GOOGLE_LOGIN_ENABLED:
        return JsonResponse({'status': 'error', 'message': 'Google sign-in is currently unavailable.'}, status=403)

    pending = google_auth_service.pop_pending_google(request)
    if pending is None:
        return JsonResponse({
            'status': 'error',
            'message': 'Your Google verification expired. Please try Google sign-in again.',
        }, status=410)

    data = _google_request_data(request)
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    code = (data.get('code') or '').strip()
    if not username or not password:
        google_auth_service.stash_pending_google(request, {
            'sub': pending['sub'], 'email': pending['email'],
            'email_verified': pending['email_verified'], 'given_name': pending.get('given_name', ''),
            'family_name': pending.get('family_name', ''), 'hd': pending.get('hosted_domain', ''),
        })
        return JsonResponse({'status': 'error', 'message': 'Enter your username and password.'}, status=400)

    owner = authenticate(request, username=username, password=password)
    if owner is None:
        google_auth_service.stash_pending_google(request, {
            'sub': pending['sub'], 'email': pending['email'],
            'email_verified': pending['email_verified'], 'given_name': pending.get('given_name', ''),
            'family_name': pending.get('family_name', ''), 'hd': pending.get('hosted_domain', ''),
        })
        logger.warning(f"[SECURITY-GOOGLE] LINK-FAILED: bad password for '{username}'")
        return JsonResponse({'status': 'error', 'message': 'Invalid username or password.'}, status=401)

    if GoogleAccount.objects.filter(sub=pending['sub']).exists():
        google_auth_service.clear_pending_google(request)
        return JsonResponse({
            'status': 'error',
            'message': 'This Google account is already linked. Please sign in with Google.',
        }, status=409)
    if hasattr(owner, 'google_account'):
        google_auth_service.clear_pending_google(request)
        return JsonResponse({
            'status': 'error',
            'message': 'This account already has Google sign-in linked.',
        }, status=409)

    if two_factor_service.is_two_factor_enabled(owner):
        verified = two_factor_service.verify_totp(owner, code) or recovery_code_service.consume_recovery_code(owner, code)
        if not code or not verified:
            google_auth_service.stash_pending_google(request, {
                'sub': pending['sub'], 'email': pending['email'],
                'email_verified': pending['email_verified'], 'given_name': pending.get('given_name', ''),
                'family_name': pending.get('family_name', ''), 'hd': pending.get('hosted_domain', ''),
            })
            return JsonResponse({
                'status': 'two_factor_required',
                'message': 'Enter the 2FA code from your authenticator app to finish linking.',
            }, status=401)

    link_record = {
        'sub': pending['sub'],
        'email': pending['email'],
        'email_verified': pending['email_verified'],
        'hosted_domain': pending.get('hosted_domain', ''),
        'link_method': GoogleAccountLinkMethod.EXPLICIT_LINK,
    }
    google_auth_service.clear_pending_google(request)
    logger.info(f"[AUTH-GOOGLE] LINKED: Google sub={pending['sub'][:8]}... to user '{owner.username}'")
    response = _google_complete_login(request, owner, link_record=link_record)
    if isinstance(response, JsonResponse) and owner.email_verified is False and pending['email_verified']:
        User.objects.filter(pk=owner.pk).update(email_verified=True)
    return response


def _stash_google_2fa_challenge(request, target_user, next_url, device_id=None, link_record=None):
    """Shared 2FA-challenge stash for Google logins (JSON and redirect flows)."""
    import time as _time

    request.session['_2fa_pending_user_id'] = target_user.pk
    request.session['_2fa_pending_ts'] = int(_time.time())
    request.session['_2fa_backend'] = _GOOGLE_AUTH_BACKEND
    request.session['_2fa_next'] = next_url or reverse('posts:home')
    if device_id:
        request.session['_2fa_device_id'] = device_id
    if link_record is not None:
        request.session['_google_pending_link_sub'] = link_record['sub']
        request.session['_google_pending_link'] = link_record
    logger.info(
        f"[SECURITY-2FA] CHALLENGE: Redirecting Google user '{target_user.username}' to 2FA challenge"
    )


def _google_complete_login_redirect(request, target_user, link_record=None):
    """Redirect-flow twin of _google_complete_login: same session/2FA/onboarding
    semantics, but full-page redirects instead of JSON (Google posts back
    cross-site, so there is no AJAX context to answer)."""
    from django.contrib.auth import login as auth_login
    from users.services import two_factor_service

    next_url = _google_safe_next(request) or reverse('posts:home')
    if two_factor_service.is_two_factor_enabled(target_user):
        _stash_google_2fa_challenge(request, target_user, next_url,
                                    link_record=link_record)
        return redirect('login_2fa_challenge')

    auth_login(request, target_user, backend=_GOOGLE_AUTH_BACKEND)
    if link_record is not None:
        from users.models import GoogleAccount

        GoogleAccount.objects.get_or_create(
            sub=link_record['sub'],
            defaults={
                'user': target_user,
                'email': link_record['email'],
                'email_verified': link_record['email_verified'],
                'hosted_domain': link_record.get('hosted_domain', ''),
                'link_method': link_record['link_method'],
            },
        )
    return redirect(next_url)


@require_http_methods(["POST"])
@csrf_exempt
def google_redirect_view(request):
    """GIS redirect-mode login endpoint (`login_uri`).

    Google POSTs form-encoded `credential` + `g_csrf_token` here cross-site,
    so Django CSRF cannot apply; instead the Google double-submit cookie
    (`g_csrf_token` cookie vs form value) is verified per Google's documented
    pattern. All identity handling reuses the verified-token service and the
    race-safe creation helper; outcomes are full-page redirects.
    """
    import hmac as _hmac

    from django.conf import settings as _settings
    from users.models import GoogleAccount, User
    from users.services import google_auth_service

    next_url = _google_redirect_safe_next(request)
    def login_bounce(**params):
        return redirect(_login_url_with_next(request, next_url, **params))

    if request.user.is_authenticated:
        return redirect(next_url or 'posts:home')
    if not _settings.GOOGLE_LOGIN_ENABLED:
        messages.error(request, 'Google sign-in is currently unavailable.')
        return login_bounce()
    if not google_auth_service.google_complete_allowed(request):
        logger.warning(
            "[SECURITY-RATELIMIT] BLOCKED: Google redirect rate limit exceeded for "
            f"IP={google_auth_service.get_rate_limit_ip(request)}"
        )
        messages.error(request, 'Too many sign-in attempts. Please wait a few minutes and try again.')
        return login_bounce()

    cookie_token = request.COOKIES.get('g_csrf_token', '')
    body_token = request.POST.get('g_csrf_token', '')
    if not cookie_token or not body_token or not _hmac.compare_digest(cookie_token, body_token):
        logger.warning("[SECURITY-GOOGLE] Redirect login rejected: g_csrf_token mismatch")
        messages.error(request, 'Google sign-in could not be verified. Please try again.')
        return login_bounce()

    credential = (request.POST.get('credential') or '').strip()
    if not credential:
        messages.error(request, 'No Google credential was received. Please try again.')
        return login_bounce()

    try:
        claims = google_auth_service.verify_google_id_token(credential)
    except google_auth_service.GoogleVerificationError as exc:
        logger.warning(f"[SECURITY-GOOGLE] Redirect token verification failed ({exc.code})")
        messages.error(request, 'This Google sign-in is invalid or has expired. Please try again.')
        return login_bounce()

    sub = claims.get('sub', '')
    google_email = (claims.get('email') or '').lower()

    existing_link = GoogleAccount.objects.select_related('user').filter(sub=sub).first()
    if existing_link is not None:
        linked_user = existing_link.user
        if not linked_user.is_active:
            messages.error(request, 'This account is disabled.')
            return login_bounce()
        from django.utils import timezone as _timezone

        existing_link.last_login_at = _timezone.now()
        existing_link.save(update_fields=['last_login_at'])
        logger.info(f"[AUTH-GOOGLE] SUCCESS: User '{linked_user.username}' signed in with Google (redirect)")
        return _google_complete_login_redirect(request, linked_user)

    if google_email:
        email_owner = User.objects.filter(email__iexact=google_email).first()
        if email_owner is not None:
            # Never silently merge: stash proof-required claims, bounce to
            # the login page with the link form revealed.
            google_auth_service.stash_pending_google(request, claims)
            logger.info(
                f"[AUTH-GOOGLE] LINK-REQUIRED: Google sub={sub[:8]}... "
                f"matches email of '{email_owner.username}' (redirect)"
            )
            messages.warning(
                request,
                f"An account ({email_owner.username}) already uses this email address. "
                "Sign in with your password below to link Google sign-in.",
            )
            return login_bounce(google_link='1')

    outcome, google_user = _create_google_user_account(claims, sub, google_email)
    if outcome == 'sub_exists':
        link = GoogleAccount.objects.select_related('user').filter(sub=sub).first()
        return _google_complete_login_redirect(request, link.user)
    if outcome == 'email_collision':
        owner = User.objects.filter(email__iexact=google_email).first()
        google_auth_service.stash_pending_google(request, claims)
        messages.warning(
            request,
            f"An account ({owner.username}) already uses this email address. "
            "Sign in with your password below to link Google sign-in.",
        )
        return login_bounce(google_link='1')
    if outcome != 'created':
        messages.error(request, 'Could not create your account right now. Please try again.')
        return login_bounce()

    new_user = google_user
    ref_token = request.session.get('invite_token')
    if ref_token:
        try:
            from users.services.invite_service import process_invite_conversion

            process_invite_conversion(ref_token, new_user)
            for key in ('invite_token', 'inviter_username', 'inviter_id'):
                request.session.pop(key, None)
        except Exception as exc:
            logger.warning(f"[AUTH-GOOGLE] Invite conversion failed: {exc}")

    logger.info(f"[AUTH-GOOGLE] SUCCESS: Created user '{new_user.username}' (id={new_user.id}) via Google (redirect)")
    if new_user.email_verified is False and bool(claims.get('email_verified', False)):
        User.objects.filter(pk=new_user.pk).update(email_verified=True)
    return _google_complete_login_redirect(request, new_user)


@login_required
def pwanimate_profile_card(request, username):
    """Render the profile page's visual profile card for Pwanimate's context rail."""
    from pwanimate.tools.domain.users import UserProfileTool

    try:
        access = UserProfileTool().execute(user=request.user, username=username)
    except Exception:
        return HttpResponse(status=404)
    if not access.success:
        return HttpResponse(status=404)

    profile_user = get_object_or_404(
        User.objects.select_related('programme__department', 'course__school', 'year', 'academic_level'),
        username=username,
        is_active=True,
    )
    from users.services.privacy import can_view_profile
    if not can_view_profile(request.user, profile_user):
        return HttpResponse('', status=404)
    context = {
        'profile_user': profile_user,
        'is_following': Follow.objects.filter(follower=request.user, followed=profile_user).exists(),
        'followers_count': profile_user.follower_relationships.count(),
        'following_count': profile_user.following_relationships.count(),
        'total_likes': Like.objects.filter(post__author=profile_user).count(),
    }
    return render(request, 'users/partials/pwanimate_profile_card.html', context)


@login_required
def profile_view(request, username):
    profile_user = get_object_or_404(
        User.objects.select_related(
            'programme__department__school',
            'academic_level',
            'academic_year',
            'semester',
            'course__school',
            'year'
        ),
        username=username
    )

    from users.services.privacy import can_view_profile
    is_limited_profile = (
        request.user != profile_user
        and profile_user.profile_privacy == PrivacyLevel.PRIVATE
        and not Block.objects.filter(
            Q(blocker_id=request.user.pk, blocked_id=profile_user.pk)
            | Q(blocker_id=profile_user.pk, blocked_id=request.user.pk)
        ).exists()
    )
    if not can_view_profile(request.user, profile_user) and not is_limited_profile:
        private_context = {
            'profile_user': profile_user,
            'is_own_profile': False,
            'is_following': Follow.objects.filter(follower=request.user, followed=profile_user).exists(),
        }
        if request.headers.get('HX-Request'):
            return render(request, 'users/partials/private_profile_navigation_partial.html', private_context)
        return render(request, 'users/private_profile.html', private_context)

    # Fast individual count queries instead of a massive Cartesian product JOIN
    followers_count = profile_user.follower_relationships.count()
    following_count = profile_user.following_relationships.count()
    total_likes = Like.objects.filter(post__author=profile_user).count()
    pinches_sent_count = profile_user.pinches_sent.count()
    pinches_received_count = profile_user.pinches_received.count()

    # Pagination for posts - load first 10 posts
    from django.core.paginator import Paginator
    page = int(request.GET.get('page', 1))
    posts_per_page = 10

    # Filter posts: show regular posts and group posts only if viewing user is a member of the group
    from groups.models import Membership, MembershipStatus
    viewer_group_ids = list(Membership.objects.filter(
        user=request.user,
        status=MembershipStatus.APPROVED
    ).values_list('group_id', flat=True))

    from users.services.privacy import visible_posts_for
    posts_queryset = visible_posts_for(request.user, Post.objects.filter(
        author=profile_user,
        repost_of__isnull=True,  # exclude legacy-style reposts — they go in the Reposts tab
    ).filter(
        Q(group__isnull=True) | Q(group_id__in=viewer_group_ids)
    )).select_related('author', 'group', 'course', 'unit', 'repost_of').prefetch_related('likes', 'images', 'comments').order_by('-created_at')
    if is_limited_profile:
        posts_queryset = posts_queryset.none()
    paginator = Paginator(posts_queryset, posts_per_page)
    posts_page = paginator.get_page(page)

    is_following = Follow.objects.filter(follower=request.user, followed=profile_user).exists()

    # Get shared posts for this profile user
    from posts.models import SharedPost
    from posts.services.share_service import get_user_received_shares
    shared_posts = get_user_received_shares(profile_user)

    # Count unseen shared posts for the badge
    unseen_shared_count = shared_posts.filter(is_viewed=False).count()

    # Get all reposts made by this profile user (newest first)
    reposted_posts = Repost.objects.filter(
        reposter=profile_user
    ).select_related(
        'original_post__author',
        'original_post__group',
        'original_post__course',
        'original_post__unit',
        'original_post__repost_of',
        'reposter',
    ).prefetch_related(
        'original_post__likes',
        'original_post__images',
        'original_post__comments',
    ).order_by('-created_at')
    if is_limited_profile:
        reposted_posts = reposted_posts.none()
    repost_count = reposted_posts.count()

    # Get liked post IDs for the current user.
    # Must cover EVERY card surface rendered by this context: the Posts tab,
    # the Reposts tab (their original posts) and the Shared tab (their
    # original posts). liked_post_ids is treated as authoritative by the
    # check_is_liked tag — an incomplete set silently renders cards un-liked.
    liked_post_ids = set()
    if request.user.is_authenticated:
        relevant_ids = {p.id for p in posts_page}
        for rp in reposted_posts:
            if rp.original_post_id:
                relevant_ids.add(rp.original_post_id)
        for sp in shared_posts:
            if sp.original_post_id:
                relevant_ids.add(sp.original_post_id)
        for pid, sid in Like.objects.filter(user=request.user, post_id__in=relevant_ids).values_list('post_id', 'post__share_id'):
            liked_post_ids.add(pid)
            liked_post_ids.add(sid)
            liked_post_ids.add(str(sid))

    # Determine if viewing own profile
    is_own_profile = request.user == profile_user

    # Determine requested active tab
    active_tab = request.GET.get('tab', 'posts')
    if is_own_profile and active_tab == 'shared' and unseen_shared_count > 0:
        shared_posts.filter(is_viewed=False).update(is_viewed=True)
        unseen_shared_count = 0

    # Get profile completion percentage for owner
    profile_completion = profile_user.profile_completion_percentage if is_own_profile else None

    # HTMX Navigation Request: Return full navigation partial for page navigation
    # Distinguished from infinite scroll (has page parameter)
    if request.headers.get('HX-Request') and not request.GET.get('page'):
        context = {
            'profile_user': profile_user,
            'posts': posts_page,
            'is_following': is_following,
            'existing_conversation_id': _existing_direct_conversation_id(request.user, profile_user),
            'followers_count': followers_count,
            'following_count': following_count,
            'total_likes': total_likes,
            'pinches_sent_count': pinches_sent_count,
            'pinches_received_count': pinches_received_count,
            'shared_posts': shared_posts,
            'unseen_shared_count': unseen_shared_count,
            'is_own_profile': is_own_profile,
            'profile_completion': profile_completion,
            'has_more_posts': posts_page.has_next(),
            'liked_post_ids': liked_post_ids,
            'reposted_posts': reposted_posts,
            'repost_count': repost_count,
            'active_tab': active_tab,
            'is_limited_profile': is_limited_profile,
        }
        return render(request, 'users/partials/profile_navigation_partial.html', context)

    # For HTMX infinite scroll: render only the posts partial
    if request.headers.get('HX-Request') and page:
        return render(request, 'posts/partials/post_cards_list.html', {
            'posts': posts_page,
            'has_more_posts': posts_page.has_next(),
            'profile_user': profile_user,
            'liked_post_ids': liked_post_ids,
        })

    return render(request, 'users/profile.html', {
        'profile_user': profile_user,
        'posts': posts_page,
        'is_following': is_following,
        'existing_conversation_id': _existing_direct_conversation_id(request.user, profile_user),
        'followers_count': followers_count,
        'following_count': following_count,
        'total_likes': total_likes,
        'pinches_sent_count': pinches_sent_count,
        'pinches_received_count': pinches_received_count,
        'shared_posts': shared_posts,
        'unseen_shared_count': unseen_shared_count,
        'is_own_profile': is_own_profile,
        'profile_completion': profile_completion,
        'has_more_posts': posts_page.has_next(),
        'liked_post_ids': liked_post_ids,
        'reposted_posts': reposted_posts,
        'repost_count': repost_count,
        'active_tab': active_tab,
        'is_limited_profile': is_limited_profile,
    })


@login_required
def profile_connections(request, username, list_type):
    """Return followers, following, pinches sent, or pinches received list for profile connections sheet."""
    from django.core.paginator import Paginator

    profile_user = get_object_or_404(User, username=username)
    from users.services.privacy import can_view_profile
    if not can_view_profile(request.user, profile_user):
        return HttpResponse('', status=404)
    page = int(request.GET.get('page', 1))
    page_size = 20

    if list_type == 'followers':
        follows = (
            Follow.objects.filter(followed=profile_user)
            .select_related('follower', 'follower__course', 'follower__year')
            .order_by('-created_at')
        )
        users = [f.follower for f in follows]
        title = 'Followers'
        empty_message = 'No followers yet.'
    elif list_type == 'following':
        follows = (
            Follow.objects.filter(follower=profile_user)
            .select_related('followed', 'followed__course', 'followed__year')
            .order_by('-created_at')
        )
        users = [f.followed for f in follows]
        title = 'Following'
        empty_message = 'Not following anyone yet.'
    elif list_type == 'pinches_sent':
        pinches = (
            Pinch.objects.filter(pinch_user=profile_user)
            .select_related('pinched_user', 'pinched_user__course', 'pinched_user__year')
            .order_by('-created_at')
        )
        users = [p.pinched_user for p in pinches]
        title = 'Pinches Sent'
        empty_message = 'No pinches sent yet.'
    elif list_type == 'pinches_received':
        pinches = (
            Pinch.objects.filter(pinched_user=profile_user)
            .select_related('pinch_user', 'pinch_user__course', 'pinch_user__year')
            .order_by('-created_at')
        )
        users = [p.pinch_user for p in pinches]
        title = 'Pinches Received'
        empty_message = 'No pinches received yet.'
    else:
        from django.http import HttpResponse
        return HttpResponse('Not found', status=404)

    # Paginate users
    paginator = Paginator(users, page_size)
    users_page = paginator.get_page(page)

    # Build next page URL if there are more pages
    next_url = None
    if users_page.has_next():
        next_url = f"{request.path}?page={users_page.next_page_number()}"

    return render(request, 'users/partials/connections_list.html', {
        'users': users_page,
        'title': title,
        'empty_message': empty_message,
        'profile_user': profile_user,
        'list_type': list_type,
        'has_more': users_page.has_next(),
        'next_url': next_url,
    })


@login_required
def people_search(request):
    """Search for users via HTMX for the people modal powered by UnifiedSearchService."""
    search_query = request.GET.get('q', '').strip()
    connection_type = request.GET.get('connection_type', '')
    profile_username = request.GET.get('profile_username', '')
    page = int(request.GET.get('page', 1))
    page_size = 20

    from search.services.unified_search_service import UnifiedSearchService
    search_service = UnifiedSearchService()

    users_page, list_type, empty_message, empty_icon, has_more, next_url = search_service.search_people_models(
        query=search_query,
        user=request.user,
        connection_type=connection_type,
        profile_username=profile_username,
        page=page,
        page_size=page_size,
    )

    return render(request, 'users/partials/people_list.html', {
        'users': users_page,
        'list_type': list_type,
        'empty_message': empty_message,
        'empty_icon': empty_icon,
        'has_more': has_more,
        'next_url': next_url,
    })


@login_required
def mark_shared_viewed(request, username):
    """Mark all shared posts for the user as viewed"""
    from posts.models import SharedPost
    from django.http import HttpResponse
    from notifications.services.notification_service import invalidate_unread_count_cache
    from posts.services.share_service import get_user_received_shares
    from groups.models import Membership, MembershipStatus

    profile_user = get_object_or_404(User, username=username)

    # Only allow marking as viewed if viewing your own profile
    if request.user != profile_user:
        return HttpResponse('Unauthorized', status=403)

    # Get all received shares (direct + group shares)
    received_shares = get_user_received_shares(profile_user)

    # Mark all unseen received shares as viewed
    received_shares.filter(is_viewed=False).update(is_viewed=True)

    # Invalidate the unread count cache
    invalidate_unread_count_cache(profile_user.id)

    # Recalculate the unseen count to confirm it's 0
    shared_posts = get_user_received_shares(profile_user)
    unseen_count = shared_posts.filter(is_viewed=False).count()

    # Return updated tab HTML with updated count
    badge_html = f'<span class="badge bg-success rounded-pill ms-1">{unseen_count}</span>' if unseen_count > 0 else ''
    return HttpResponse(f'''
        <button class="nav-link" id="shared-tab" data-bs-toggle="tab" data-bs-target="#shared-content" type="button" role="tab"
                hx-post="/user/{username}/mark-shared-viewed/"
                hx-target="#shared-tab"
                hx-swap="outerHTML">
            Shared with {username}{badge_html}
        </button>
    ''')


@login_required
def update_profile_view(request):
    is_htmx = bool(request.headers.get('HX-Request'))
    if request.method == 'POST':
        form = ProfileUpdateForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Profile updated successfully.')
                if is_htmx:
                    from django.urls import reverse
                    return htmx_location_response(reverse('users:profile', kwargs={'username': request.user.username}))
                return redirect('users:profile', username=request.user.username)
            except Exception as e:
                messages.error(request, f'Error saving profile: {str(e)}')
                # Add debugging info
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Profile save error: {str(e)}", exc_info=True)
        else:
            # Add form errors to messages for debugging
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = ProfileUpdateForm(instance=request.user)

    template = 'users/partials/update_profile_navigation_partial.html' if is_htmx else 'users/update_profile.html'
    return render(request, template, {'form': form})


@login_required
@require_http_methods(["POST"])
@transaction.atomic
def toggle_follow(request, username):
    target = get_object_or_404(User, username=username)
    is_hx = request.headers.get('HX-Request')
    is_ajax = is_hx or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if target == request.user:
        if is_ajax:
            return JsonResponse({'error': 'Cannot follow yourself'}, status=400)
        return redirect('users:profile', username=username)

    if Block.objects.filter(Q(blocker=request.user, blocked=target) | Q(blocker=target, blocked=request.user)).exists():
        if is_ajax:
            return JsonResponse({'error': 'This connection is unavailable.'}, status=403)
        return redirect('users:profile', username=username)

    # Reel follow chips are one-way actions, not toggles. This keeps a stale
    # client state or a repeated tap from unexpectedly unfollowing someone.
    follow_only = request.GET.get('action') == 'follow'
    follow_qs = Follow.objects.filter(follower=request.user, followed=target)
    if follow_only:
        _, created = Follow.objects.get_or_create(follower=request.user, followed=target)
        if created:
            invalidate_unread_count_cache(target.id)
        is_following = True
    elif follow_qs.exists():
        follow_qs.delete()
        is_following = False
    else:
        Follow.objects.get_or_create(follower=request.user, followed=target)
        invalidate_unread_count_cache(target.id)
        is_following = True

    from users.services.friend_suggestion_service import invalidate_friend_suggestions_cache
    invalidate_friend_suggestions_cache(request.user.id)

    follower_count = target.follower_relationships.count()

    if is_hx:
        template = 'users/partials/follow_button_profile.html'
        if request.GET.get('source') == 'recruit':
            template = 'users/partials/follow_button_recruit.html'
        elif request.GET.get('source') == 'search':
            template = 'users/partials/follow_button_search.html'
        elif request.GET.get('source') == 'onboarding':
            template = 'users/partials/follow_button_onboarding.html'

        return render(request, template, {
            'profile_user': target,
            'recruit': target,
            'is_following': is_following,
            'existing_conversation_id': _existing_direct_conversation_id(request.user, target),
            'follower_count': follower_count,
        })

    if is_ajax:
        return JsonResponse({'is_following': is_following, 'follower_count': follower_count})

    return redirect('users:profile', username=username)


@login_required
@require_http_methods(["POST"])
def api_block_user(request, user_id):
    """Block a user, preventing profile and post access and removing follow edges."""
    target = get_object_or_404(User, pk=user_id, is_active=True)
    if target.pk == request.user.pk:
        return JsonResponse({'detail': 'You cannot block your own account.'}, status=400)
    Block.objects.get_or_create(blocker=request.user, blocked=target)
    Follow.objects.filter(
        Q(follower=request.user, followed=target) |
        Q(follower=target, followed=request.user)
    ).delete()
    if request.headers.get('HX-Request'):
        return HttpResponse('', status=204)
    return redirect('users:settings_blocked_users')


@login_required
@require_http_methods(["POST"])
@transaction.atomic
def toggle_pinch(request, username):
    """Toggle pinch on a user's profile"""
    from users.models import Pinch
    from django.utils import timezone
    from django.db.models import Count

    target = get_object_or_404(User, username=username)
    is_hx = request.headers.get('HX-Request')
    is_ajax = is_hx or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if target == request.user:
        if is_ajax:
            return JsonResponse({'error': 'Cannot pinch yourself'}, status=400)
        return redirect('users:profile', username=username)

    # Check if can pinch
    can_pinch, error_msg = Pinch.can_pinch(request.user, target)
    if not can_pinch:
        if is_ajax:
            return JsonResponse({'error': error_msg}, status=400)
        messages.error(request, error_msg)
        return redirect('users:profile', username=username)

    # Create pinch
    pinch = Pinch.objects.create(pinch_user=request.user, pinched_user=target)

    # Invalidate cache
    invalidate_unread_count_cache(target.id)

    if is_hx:
        # Calculate updated pinch counts for the current user
        request_user = User.objects.filter(id=request.user.id).annotate(
            pinches_sent_count=Count('pinches_sent', distinct=True),
            pinches_received_count=Count('pinches_received', distinct=True)
        ).first()

        return render(request, 'users/partials/pinch_button.html', {
            'profile_user': target,
            'pinches_sent_count': request_user.pinches_sent_count,
            'pinches_received_count': request_user.pinches_received_count,
        })

    if is_ajax:
        return JsonResponse({'success': True, 'message': 'Pinched successfully'})

    messages.success(request, f'You pinched {target.username}!')
    return redirect('users:profile', username=username)


@login_required
def get_suggestions(request):
    from users.services.friend_suggestion_service import get_friend_suggestions_for_user
    suggestions = get_friend_suggestions_for_user(request.user, limit=5)
    return render(request, 'users/partials/suggestions.html', {'suggestions': suggestions})


def encrypted_invite_landing(request, token):
    """
    Unified landing endpoint for cryptographically encrypted (AES-256-GCM) invite & app links (/i/<token>/).
    Decodes the authenticated ciphertext, records click telemetry, and routes to:
    - App download page if invite_type == 'app_download'
    - Registration page if invite_type == 'platform_invite'
    """
    from users.services.invite_service import track_invite_click, decrypt_invite_token
    from users.models import InviteType

    # Sanitize token: strip whitespace and trailing quotes that can appear
    # when users copy-paste links from messages or documents
    token = token.strip().strip('"').strip("'").strip("\u201c\u201d\u2018\u2019").strip()

    invite = track_invite_click(token)
    resolved_type = None

    if invite:
        resolved_type = invite.invite_type
        request.session['invite_token'] = token
        inv_id = getattr(invite, 'inviter_id', getattr(invite, 'user_id', None))
        inviter = getattr(invite, 'inviter', None)
        if not inviter and inv_id:
            from users.models import User
            try:
                inviter = User.objects.get(id=inv_id)
            except Exception:
                inviter = None
        if inv_id:
            request.session['inviter_id'] = inv_id
        if inviter:
            request.session['inviter_username'] = inviter.username
    else:
        decrypted = decrypt_invite_token(token)
        if decrypted:
            u_id, resolved_type = decrypted
            request.session['invite_token'] = token
            if u_id:
                request.session['inviter_id'] = u_id
                from users.models import User
                try:
                    inviter = User.objects.get(id=u_id)
                    request.session['inviter_username'] = inviter.username
                except Exception:
                    pass

    if resolved_type == InviteType.APP_DOWNLOAD:
        download_url = reverse('downloads')
        return redirect(f"{download_url}?ref={token}")

    # Default to platform invite
    if request.user.is_authenticated:
        messages.info(request, "You are already a member of vasto!")
        return redirect('posts:home')

    register_url = reverse('users:register')
    return redirect(f"{register_url}?ref={token}")


def app_invite_landing(request, token):
    """
    Landing endpoint for encrypted/opaque mobile app download referral links.
    Logs telemetry click count and redirects user to the official downloads page.
    """
    from users.services.invite_service import track_invite_click
    invite = track_invite_click(token)
    if invite:
        request.session['invite_token'] = token
        inv_id = getattr(invite, 'inviter_id', getattr(invite, 'user_id', None))
        if inv_id:
            request.session['inviter_id'] = inv_id
        inviter = getattr(invite, 'inviter', None)
        if inviter:
            request.session['inviter_username'] = inviter.username

    download_url = reverse('downloads')
    return redirect(f"{download_url}?ref={token}")


def platform_invite_landing(request, token):
    """
    Landing endpoint for encrypted/opaque platform invite links.
    Logs telemetry click count, records inviter session context, and redirects to register.
    """
    from users.services.invite_service import track_invite_click
    invite = track_invite_click(token)
    if invite:
        request.session['invite_token'] = token
        inv_id = getattr(invite, 'inviter_id', getattr(invite, 'user_id', None))
        if inv_id:
            request.session['inviter_id'] = inv_id
        inviter = getattr(invite, 'inviter', None)
        if inviter:
            request.session['inviter_username'] = inviter.username

    if request.user.is_authenticated:
        messages.info(request, "You are already a member of vasto!")
        return redirect('posts:home')

    register_url = reverse('users:register')
    return redirect(f"{register_url}?ref={token}")


def get_invite_share_data(request):
    """
    API endpoint returning encrypted invite links, texts, and telemetry stats for the user.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Authentication required'}, status=401)
    from users.services.invite_service import get_invite_data
    data = get_invite_data(request.user, request)
    return JsonResponse({'status': 'success', 'data': data})



@login_required
def settings_view(request):
    """Main settings landing page - navigation hub for all settings categories"""
    from users.services.invite_service import get_invite_data
    from admin_dashboard.models import FeedbackTicket
    invite_data = {}
    try:
        invite_data = get_invite_data(request.user, request)
    except Exception as e:
        logger.warning(f"Failed to fetch invite data for settings: {e}")

    context = {
        'settings_content_partial': 'users/settings/partials/index_content.html',
        'invite_data': invite_data,
        'feedback_category_choices': FeedbackTicket.Category.choices,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', context)
    return render(request, 'users/settings/index.html', context)


@login_required
def settings_profile_view(request):
    """Profile settings page"""
    is_htmx = bool(request.headers.get('HX-Request'))
    if request.method == 'POST':
        form = ProfileUpdateForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Profile updated successfully.')
                if is_htmx:
                    from django.urls import reverse
                    return htmx_location_response(reverse('users:settings_profile'))
                return redirect('users:settings_profile')
            except Exception as e:
                messages.error(request, f'Error saving profile: {str(e)}')
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Profile save error: {str(e)}", exc_info=True)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = ProfileUpdateForm(instance=request.user)

    if is_htmx:
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/profile_content.html',
            'form': form,
        })
    return render(request, 'users/settings/profile.html', {'form': form})


@login_required
def settings_academic_view(request):
    """Academic institution & programme settings page (full-page & HTMX supported)."""
    from users.forms import AcademicProfileUpdateForm
    from documents.academic.models import Institution, Programme

    is_htmx = bool(request.headers.get('HX-Request'))

    if request.method == 'POST':
        form = AcademicProfileUpdateForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Academic details updated successfully.')
                if is_htmx:
                    from pwaninet.utils.htmx import htmx_location_response
                    from django.urls import reverse
                    return htmx_location_response(reverse('users:settings_academic'))
                return redirect('users:settings_academic')
            except Exception as e:
                messages.error(request, f'Error saving academic details: {str(e)}')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}' if field != '__all__' else str(error))
    else:
        form = AcademicProfileUpdateForm(user=request.user)

    institutions = Institution.objects.filter(is_active=True).order_by('name')
    setup_programmes = Programme.objects.none()
    if request.user.institution_id:
        from django.db.models import Q
        setup_programmes = Programme.objects.filter(
            is_active=True
        ).filter(
            Q(department__school__institution_id=request.user.institution_id) |
            Q(department__school__faculty__institution_id=request.user.institution_id)
        ).distinct().order_by('name')

    context = {
        'form': form,
        'institutions': institutions,
        'setup_programmes': setup_programmes,
    }

    if is_htmx:
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/academic_content.html',
            **context,
        })
    return render(request, 'users/settings/academic.html', context)


@login_required
@require_http_methods(["GET", "PATCH", "POST"])
def api_user_academic_view(request):
    """REST API endpoint to retrieve or update the authenticated user's academic profile."""
    from users.forms import AcademicProfileUpdateForm
    from documents.academic.models import Institution, Programme
    import json

    user = request.user

    if request.method == "GET":
        return JsonResponse({
            'status': 'success',
            'academic_profile': {
                'institution': {
                    'id': user.institution.id,
                    'name': user.institution.name,
                    'short_name': user.institution.short_name,
                    'slug': user.institution.slug,
                } if user.institution else None,
                'programme': {
                    'id': user.programme.id,
                    'name': user.programme.name,
                    'code': user.programme.code,
                } if user.programme else None,
                'academic_level': {
                    'id': user.academic_level.id,
                    'level': user.academic_level.level,
                    'name': user.academic_level.name,
                } if user.academic_level else None,
                'bio': user.bio,
                'display_bio': user.get_display_bio(),
            }
        })

    data = request.POST
    if not data and request.body:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = {}

    form = AcademicProfileUpdateForm(data, user=user)
    if form.is_valid():
        updated_user = form.save()
        return JsonResponse({
            'status': 'success',
            'message': 'Academic profile updated successfully.',
            'academic_profile': {
                'institution_id': updated_user.institution_id,
                'programme_id': updated_user.programme_id,
                'academic_level_id': updated_user.academic_level_id,
                'bio': updated_user.bio,
                'display_bio': updated_user.get_display_bio(),
            }
        })
    return JsonResponse({
        'status': 'error',
        'errors': form.errors.get_json_data()
    }, status=400)


@login_required
@require_http_methods(["POST"])
def settings_delete_account_view(request):
    """Delete the account and either erase or anonymize its authored content."""
    choice = request.POST.get('content_action')
    password = request.POST.get('password', '')
    confirmed = request.POST.get('confirm_delete') == 'yes'
    if choice not in {'erase', 'anonymize'} or not confirmed or not request.user.check_password(password):
        messages.error(request, 'Confirm account deletion and enter your password to continue.')
        return redirect('users:settings_profile')

    from users.services.account_deletion import erase_account_and_content, anonymize_account_keep_content
    user = request.user
    if choice == 'erase':
        erase_account_and_content(user)
    else:
        anonymize_account_keep_content(user)
    logout(request)
    messages.success(request, 'Your account deletion request has been completed.')
    return redirect('login')


@login_required
def settings_appearance_view(request):
    """Appearance settings page"""
    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/appearance_content.html'
        })
    return render(request, 'users/settings/appearance.html')



@login_required
def settings_privacy_view(request):
    """Privacy and security settings page"""
    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/privacy_content.html'
        })
    return render(request, 'users/settings/privacy.html')


def settings_storage_view(request):
    """Storage settings page - seamlessly renders unified downloaded media page"""
    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/offline_media_viewer_content.html')
    return redirect('posts:offline_media_viewer')


def settings_downloads_view(request):
    """Downloads manager page - seamlessly renders unified downloaded media page"""
    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/offline_media_viewer_content.html')
    return redirect('posts:offline_media_viewer')


@login_required
def settings_about_view(request):
    """About vasto page with version information"""
    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/about_content.html'
        })
    return render(request, 'users/settings/about.html')


@login_required
def settings_password_manager_view(request):
    """Password manager settings page"""
    if request.method == 'POST':
        current_password = request.POST.get('current_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not request.user.check_password(current_password):
            messages.error(request, 'Current password is incorrect.')
            if request.headers.get('HX-Request'):
                return render(request, 'users/settings/partials/settings_navigation_partial.html', {
                    'settings_content_partial': 'users/settings/partials/password_content.html'
                })
            return render(request, 'users/settings/password_manager.html')

        if new_password != confirm_password:
            messages.error(request, 'New passwords do not match.')
            if request.headers.get('HX-Request'):
                return render(request, 'users/settings/partials/settings_navigation_partial.html', {
                    'settings_content_partial': 'users/settings/partials/password_content.html'
                })
            return render(request, 'users/settings/password_manager.html')

        if len(new_password) < 8:
            messages.error(request, 'Password must be at least 8 characters long.')
            if request.headers.get('HX-Request'):
                return render(request, 'users/settings/partials/settings_navigation_partial.html', {
                    'settings_content_partial': 'users/settings/partials/password_content.html'
                })
            return render(request, 'users/settings/password_manager.html')

        request.user.set_password(new_password)
        request.user.save()

        # Update session to keep user logged in
        from django.contrib.auth import update_session_auth_hash
        update_session_auth_hash(request, request.user)

        # Invalidate all other active sessions across devices
        from users.services.session_service import revoke_other_user_sessions
        revoked_count = revoke_other_user_sessions(request.user, request.session.session_key)

        ip = get_client_ip(request)
        logger.info(
            f"[SECURITY-PASSWORD] CHANGED: Password updated for user '{request.user.username}' (id={request.user.id}) "
            f"from IP={ip}; revoked {revoked_count} other sessions"
        )

        messages.success(request, 'Password changed successfully.')
        if request.headers.get('HX-Request'):
            return render(request, 'users/settings/partials/settings_navigation_partial.html', {
                'settings_content_partial': 'users/settings/partials/password_content.html'
            })
        return redirect('users:settings_password_manager')

    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/password_content.html'
        })
    return render(request, 'users/settings/password_manager.html')


def _render_two_factor_view(request, context):
    """Helper to render 2FA settings either as HTMX partial or full page."""
    if request.headers.get('HX-Request'):
        context['settings_content_partial'] = 'users/settings/partials/two_factor_content.html'
        return render(request, 'users/settings/partials/settings_navigation_partial.html', context)
    return render(request, 'users/settings/two_factor.html', context)


@login_required
def settings_two_factor_view(request):
    """
    Two-factor authentication management dashboard.
    Renders active protection status if enrolled, or onboarding banner if unenrolled.
    """
    from users.services.two_factor_service import (
        is_two_factor_enabled, get_two_factor
    )
    from users.services.recovery_code_service import get_remaining_recovery_codes_count

    enabled = is_two_factor_enabled(request.user)
    two_factor = get_two_factor(request.user) if enabled else None
    active_codes_count = get_remaining_recovery_codes_count(request.user) if enabled else 0

    context = {
        'is_enabled': enabled,
        'two_factor': two_factor,
        'active_codes_count': active_codes_count,
    }
    return _render_two_factor_view(request, context)


@login_required
def settings_two_factor_setup_view(request):
    """
    Starts or resumes 2FA enrollment.
    Generates QR code SVG and manual setup key for authenticator app.
    """
    from users.services.two_factor_service import (
        is_two_factor_enabled, get_two_factor, initialize_two_factor,
        get_provisioning_uri, generate_qr_svg, decrypt_secret
    )

    if is_two_factor_enabled(request.user):
        messages.info(request, _("Two-factor authentication is already enabled."))
        return redirect('users:settings_two_factor')

    # If pending setup exists and user didn't ask to reset, reuse the pending secret
    pending_tf = get_two_factor(request.user)
    if pending_tf and not pending_tf.is_enabled and request.GET.get('reset') != '1':
        try:
            secret = decrypt_secret(pending_tf.encrypted_secret)
        except Exception:
            pending_tf, secret = initialize_two_factor(request.user)
    else:
        pending_tf, secret = initialize_two_factor(request.user)

    formatted_secret = ' '.join(secret[i:i+4] for i in range(0, len(secret), 4))
    provisioning_uri = get_provisioning_uri(request.user)
    qr_svg = generate_qr_svg(provisioning_uri)

    context = {
        'state': 'setup',
        'qr_svg': qr_svg,
        'manual_secret': secret,
        'formatted_secret': formatted_secret,
    }
    return _render_two_factor_view(request, context)


@login_required
def settings_two_factor_verify_view(request):
    """
    Verifies 6-digit confirmation code and activates 2FA.
    Generates and returns recovery codes exactly once upon success.
    """
    if request.method != 'POST':
        return redirect('users:settings_two_factor_setup')

    from users.services.two_factor_service import (
        is_two_factor_enabled, confirm_and_enable_two_factor,
        get_two_factor, decrypt_secret, get_provisioning_uri, generate_qr_svg
    )
    from users.services.recovery_code_service import generate_recovery_codes

    if is_two_factor_enabled(request.user):
        messages.info(request, _("Two-factor authentication is already enabled."))
        return redirect('users:settings_two_factor')

    code = request.POST.get('code', '').strip()
    success = confirm_and_enable_two_factor(request.user, code)

    if success:
        # Generate recovery codes for immediate one-time display
        recovery_codes = generate_recovery_codes(request.user)
        context = {
            'state': 'recovery_codes_display',
            'recovery_codes': recovery_codes,
            'is_new_enrollment': True,
        }
        return _render_two_factor_view(request, context)

    # Verification failed: re-render setup with error message
    two_factor = get_two_factor(request.user)
    if two_factor:
        secret = decrypt_secret(two_factor.encrypted_secret)
        formatted_secret = ' '.join(secret[i:i+4] for i in range(0, len(secret), 4))
        provisioning_uri = get_provisioning_uri(request.user)
        qr_svg = generate_qr_svg(provisioning_uri)
    else:
        return redirect('users:settings_two_factor_setup')

    context = {
        'state': 'setup',
        'qr_svg': qr_svg,
        'manual_secret': secret,
        'formatted_secret': formatted_secret,
        'error_message': _("Invalid verification code. Please check the time on your device and authenticator app and try again."),
    }
    return _render_two_factor_view(request, context)


@login_required
def settings_two_factor_disable_view(request):
    """
    Disables 2FA after verifying current 6-digit TOTP code.
    Requires POST and valid TOTP code.
    """
    if request.method != 'POST':
        messages.error(request, _("Invalid request method."))
        return redirect('users:settings_two_factor')

    from users.services.two_factor_service import (
        is_two_factor_enabled, verify_totp, disable_two_factor
    )
    from users.services.recovery_code_service import consume_recovery_code

    if not is_two_factor_enabled(request.user):
        messages.info(request, _("Two-factor authentication is not enabled."))
        return redirect('users:settings_two_factor')

    code = request.POST.get('code', '').strip()
    if not code:
        messages.error(request, _("Please provide your 6-digit authenticator code or a recovery code to disable 2FA."))
        return redirect('users:settings_two_factor')

    used_recovery_code = False
    if verify_totp(request.user, code):
        verified = True
    elif consume_recovery_code(request.user, code):
        verified = True
        used_recovery_code = True
    else:
        verified = False

    if verified:
        disable_two_factor(request.user)
        logger.info(
            f"[SECURITY-2FA] DISABLED BY USER: 2FA disabled by '{request.user.username}' via "
            f"{'recovery code' if used_recovery_code else 'TOTP'} from IP={get_client_ip(request)}"
        )
        messages.success(request, _("Two-factor authentication has been successfully disabled."))
        return redirect('users:settings_two_factor')
    else:
        messages.error(request, _("Invalid authentication code. Could not disable two-factor authentication."))
        return redirect('users:settings_two_factor')


@login_required
def settings_two_factor_regenerate_codes_view(request):
    """
    Regenerates single-use recovery codes after verifying current 6-digit TOTP code.
    Displays the new recovery codes exactly once.
    """
    if request.method != 'POST':
        messages.error(request, _("Invalid request method."))
        return redirect('users:settings_two_factor')

    from users.services.two_factor_service import (
        is_two_factor_enabled, verify_totp
    )
    from users.services.recovery_code_service import regenerate_recovery_codes, consume_recovery_code

    if not is_two_factor_enabled(request.user):
        messages.info(request, _("Two-factor authentication is not enabled."))
        return redirect('users:settings_two_factor')

    code = request.POST.get('code', '').strip()
    if not code:
        messages.error(request, _("Please provide your 6-digit authenticator code or a recovery code to regenerate recovery codes."))
        return redirect('users:settings_two_factor')

    if verify_totp(request.user, code) or consume_recovery_code(request.user, code):
        new_codes = regenerate_recovery_codes(request.user)
        context = {
            'state': 'recovery_codes_display',
            'recovery_codes': new_codes,
            'is_regenerated': True,
        }
        return _render_two_factor_view(request, context)
    else:
        messages.error(request, _("Invalid authentication code. Could not regenerate recovery codes."))
        return redirect('users:settings_two_factor')


@login_required
@require_http_methods(["POST"])
def settings_two_factor_download_codes_view(request):
    """
    Directly streams recovery codes as a downloadable .txt file attachment.
    Does not read from or persist to DB; streams codes submitted from the active one-time display.
    """
    raw_codes = request.POST.getlist('codes')
    if not raw_codes:
        single = request.POST.get('codes', '')
        raw_codes = [c.strip() for c in single.split('\n') if c.strip()]

    lines = [
        "==================================================",
        "  vasto Two-Factor Authentication Recovery Codes",
        f"  Account: {request.user.username}",
        f"  Generated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "==================================================",
        "",
        "Save these codes in a safe, offline place.",
        "Each recovery code can be used ONLY ONCE.",
        "",
        "--------------------------------------------------",
    ]
    for i, c in enumerate(raw_codes, start=1):
        lines.append(f"  {i}. {c.strip()}")
    lines.extend([
        "--------------------------------------------------",
        "",
        "If you lose your authenticator app, use one of",
        "these codes to recover access to your account.",
        "==================================================",
    ])
    content = "\r\n".join(lines)
    filename = f"vasto-recovery-codes-{request.user.username}.txt"
    response = HttpResponse(content, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def settings_active_devices_view(request):
    """Active devices management page"""
    from django.contrib.sessions.models import Session
    from user_agents import parse
    import re

    def parse_device_info(user_agent_string):
        """Parse detailed device information from user agent string"""
        user_agent = parse(user_agent_string)

        # Determine device type
        device_type = 'desktop'
        if user_agent.is_mobile:
            device_type = 'mobile'
        elif user_agent.is_tablet:
            device_type = 'tablet'

        # Try to extract device model from user agent string
        device_model = user_agent.device.family or 'Unknown Device'

        # Common Android device patterns
        android_patterns = [
            r'(SM-[A-Z0-9]+)',  # Samsung
            r'(Pixel [0-9]+)',  # Google Pixel
            r'(Redmi [A-Za-z0-9]+)',  # Xiaomi Redmi
            r'(POCO [A-Za-z0-9]+)',  # Xiaomi POCO
            r'(Tecno [A-Za-z0-9 ]+)',  # Tecno
            r'(Infinix [A-Za-z0-9 ]+)',  # Infinix
            r'(Itel [A-Za-z0-9 ]+)',  # Itel
            r'(Nokia [A-Za-z0-9]+)',  # Nokia
            r'(OnePlus [A-Za-z0-9]+)',  # OnePlus
            r'(OPPO [A-Za-z0-9]+)',  # OPPO
            r'(vivo [A-Za-z0-9]+)',  # Vivo
            r'(HUAWEI [A-Za-z0-9]+)',  # Huawei
            r'(Moto [A-Za-z0-9]+)',  # Motorola
            r'(LG-[A-Za-z0-9]+)',  # LG
            r'(Sony [A-Za-z0-9]+)',  # Sony
        ]

        for pattern in android_patterns:
            match = re.search(pattern, user_agent_string, re.IGNORECASE)
            if match:
                device_model = match.group(1)
                break

        # iPhone/iPad detection
        if 'iPhone' in user_agent_string:
            device_model = 'iPhone'
        elif 'iPad' in user_agent_string:
            device_model = 'iPad'

        # OS version formatting
        os_version = user_agent.os.version_string or ''
        if user_agent.os.family == 'Android' and os_version:
            # Clean up Android version (e.g., "12" instead of "12.0.0")
            os_version = os_version.split('.')[0]

        # Browser info
        browser_name = user_agent.browser.family or 'Unknown Browser'
        browser_version = user_agent.browser.version_string or ''
        if browser_version:
            browser_info = f"{browser_name} {browser_version.split('.')[0]}"
        else:
            browser_info = browser_name

        # OS info
        os_info = f"{user_agent.os.family}"
        if os_version:
            os_info += f" {os_version}"

        return {
            'device_type': device_type,
            'device_model': device_model,
            'browser': browser_info,
            'os': os_info,
            'is_mobile': user_agent.is_mobile,
            'is_tablet': user_agent.is_tablet,
            'is_pc': user_agent.is_pc,
        }

    # Get current session
    current_session_key = request.session.session_key
    current_session = None

    # Try to get or create UserSession for current session
    from users.services.session_service import create_or_update_session
    current_session = UserSession.objects.filter(
        user=request.user,
        session_key=current_session_key
    ).first()
    if not current_session:
        current_session = create_or_update_session(request.user, request)
    else:
        current_session.is_current = True

    # Get other sessions
    other_sessions = UserSession.objects.filter(
        user=request.user
    ).exclude(
        session_key=current_session_key
    ).order_by('-last_activity')

    # Parse device info for other sessions
    for session in other_sessions:
        if session.user_agent:
            device_info = parse_device_info(session.user_agent)
            session.device_type = device_info['device_type']
            session.device_model = device_info['device_model']
            session.browser = device_info['browser']
            session.os = device_info['os']

    # Parse device info for current session
    if current_session and current_session.user_agent:
        device_info = parse_device_info(current_session.user_agent)
        current_session.device_type = device_info['device_type']
        current_session.device_model = device_info['device_model']
        current_session.browser = device_info['browser']
        current_session.os = device_info['os']

    # Phase 1: trusted + blocked device inventory for the devices page
    trusted_devices = TrustedDevice.objects.filter(user=request.user).order_by('-is_primary', '-trusted_at')
    blocked_devices = BlockedDevice.objects.filter(user=request.user).order_by('-created_at')
    device_hash_by_session = {
        account.session_key: account.device_id
        for account in DeviceAccount.objects.exclude(session_key='').filter(user=request.user)
    }
    trusted_hashes = set(trusted_devices.values_list('device_hash', flat=True))

    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/active_devices_content.html',
            'current_session': current_session,
            'other_sessions': other_sessions,
            'trusted_devices': trusted_devices,
            'blocked_devices': blocked_devices,
            'device_hash_by_session': device_hash_by_session,
            'trusted_hashes': trusted_hashes,
        })

    return render(request, 'users/settings/active_devices.html', {
        'current_session': current_session,
        'other_sessions': other_sessions,
        'trusted_devices': trusted_devices,
        'blocked_devices': blocked_devices,
        'device_hash_by_session': device_hash_by_session,
        'trusted_hashes': trusted_hashes,
    })


@login_required
def settings_blocked_users_view(request):
    """Blocked users management page"""
    blocked_users = Block.objects.filter(
        blocker=request.user
    ).select_related('blocked').order_by('-created_at')

    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/blocked_content.html',
            'blocked_users': blocked_users
        })

    return render(request, 'users/settings/blocked_users.html', {
        'blocked_users': blocked_users
    })


@login_required
def settings_profile_privacy_view(request):
    """Profile privacy settings page"""
    privacy_levels = PRIVACY_CHOICES
    if request.method == 'POST':
        profile_privacy = request.POST.get('profile_privacy')

        if profile_privacy not in [value for value, _label in privacy_levels]:
            messages.error(request, 'Invalid privacy level.')
        else:
            request.user.profile_privacy = profile_privacy
            request.user.save()
            messages.success(request, 'Profile privacy updated successfully.')

        if request.headers.get('HX-Request'):
            return render(request, 'users/settings/partials/settings_navigation_partial.html', {
                'settings_content_partial': 'users/settings/partials/profile_privacy_content.html',
                'privacy_levels': privacy_levels
            })
        return redirect('users:settings_profile_privacy')

    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/profile_privacy_content.html',
            'privacy_levels': privacy_levels
        })
    return render(request, 'users/settings/profile_privacy.html', {
        'privacy_levels': privacy_levels
    })


@login_required
def settings_post_privacy_view(request):
    """Post privacy settings page"""
    privacy_levels = PRIVACY_CHOICES
    if request.method == 'POST':
        post_privacy = request.POST.get('post_privacy')

        if post_privacy not in [value for value, _label in privacy_levels]:
            messages.error(request, 'Invalid privacy level.')
        else:
            request.user.post_privacy = post_privacy
            request.user.save()
            messages.success(request, 'Post privacy updated successfully.')

        if request.headers.get('HX-Request'):
            return render(request, 'users/settings/partials/settings_navigation_partial.html', {
                'settings_content_partial': 'users/settings/partials/post_privacy_content.html',
                'privacy_levels': privacy_levels
            })
        return redirect('users:settings_post_privacy')

    privacy_levels = PrivacyLevel.choices
    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/post_privacy_content.html',
            'privacy_levels': privacy_levels
        })
    return render(request, 'users/settings/post_privacy.html', {
        'privacy_levels': privacy_levels
    })


@login_required
def settings_hidden_authors_view(request):
    """Hidden authors management page"""
    hidden_authors = HiddenAuthor.objects.filter(
        hider=request.user
    ).select_related('hidden_author').order_by('-created_at')

    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/hidden_content.html',
            'hidden_authors': hidden_authors
        })

    return render(request, 'users/settings/hidden_authors.html', {
        'hidden_authors': hidden_authors
    })


@login_required
def settings_notifications_view(request):
    """Notification preferences settings page"""
    from notifications.services.preference_service import NotificationPreferenceService
    from notifications.events import EventTypes
    from datetime import time
    from django.contrib import messages

    event_type_mapping = {
        'POSTS_POST_LIKED': EventTypes.POSTS_POST_LIKED.value,
        'POSTS_COMMENT_CREATED': EventTypes.POSTS_COMMENT_CREATED.value,
        'POSTS_COMMENT_REPLY_CREATED': EventTypes.POSTS_COMMENT_REPLY_CREATED.value,
        'POSTS_COMMENT_REPLIED': EventTypes.POSTS_COMMENT_REPLIED.value,
        'POSTS_POST_SHARED': EventTypes.POSTS_POST_SHARED.value,
        'POSTS_POST_REPOSTED': EventTypes.POSTS_POST_REPOSTED.value,
        'POSTS_POST_SHARED_TO_GROUP': EventTypes.POSTS_POST_SHARED_TO_GROUP.value,
        'USERS_USER_FOLLOWED': EventTypes.USERS_USER_FOLLOWED.value,
        'USERS_USER_PINCHED': EventTypes.USERS_USER_PINCHED.value,
        'GROUPS_MEMBER_INVITED': EventTypes.GROUPS_MEMBER_INVITED.value,
        'GROUPS_MEMBER_REQUESTED': EventTypes.GROUPS_MEMBER_REQUESTED.value,
        'GROUPS_MEMBER_APPROVED': EventTypes.GROUPS_MEMBER_APPROVED.value,
        'GROUPS_MEMBER_REJECTED': EventTypes.GROUPS_MEMBER_REJECTED.value,
        'DOCUMENTS_DOCUMENT_UPLOADED': EventTypes.DOCUMENTS_DOCUMENT_UPLOADED.value,
        'DOCUMENTS_DOCUMENT_DOWNLOADED': EventTypes.DOCUMENTS_DOCUMENT_DOWNLOADED.value,
        'DOCUMENTS_DOCUMENT_BOOKMARKED': EventTypes.DOCUMENTS_DOCUMENT_BOOKMARKED.value,
        'DOCUMENTS_DOCUMENT_RATED': EventTypes.DOCUMENTS_DOCUMENT_RATED.value,
        'MESSAGING_MESSAGE_SENT': EventTypes.MESSAGING_MESSAGE_SENT.value,
        'MESSAGING_CONVERSATION_CREATED': EventTypes.MESSAGING_CONVERSATION_CREATED.value,
        'MESSAGING_CONVERSATION_MEMBER_ADDED': EventTypes.MESSAGING_CONVERSATION_MEMBER_ADDED.value,
        'COURSES_ASSIGNMENT_PUBLISHED': EventTypes.COURSES_ASSIGNMENT_PUBLISHED.value,
    }

    preferences = NotificationPreferenceService.get_or_create_preferences(request.user)

    # Handle form submission
    if request.method == 'POST':
        # Update global preferences
        preferences.email_enabled = request.POST.get('email_enabled') in ('on', 'true', '1')
        preferences.email_digest = request.POST.get('email_digest') in ('on', 'true', '1')
        preferences.push_enabled = request.POST.get('push_enabled') in ('on', 'true', '1')
        preferences.push_sound = request.POST.get('push_sound') in ('on', 'true', '1')
        preferences.in_app_enabled = request.POST.get('in_app_enabled') in ('on', 'true', '1')
        preferences.in_app_toast_enabled = request.POST.get('in_app_toast_enabled') in ('on', 'true', '1')

        # Update quiet hours
        preferences.quiet_hours_enabled = request.POST.get('quiet_hours_enabled') in ('on', 'true', '1')
        quiet_hours_start = request.POST.get('quiet_hours_start')
        quiet_hours_end = request.POST.get('quiet_hours_end')

        if quiet_hours_start:
            try:
                preferences.quiet_hours_start = time.fromisoformat(quiet_hours_start)
            except (ValueError, TypeError):
                preferences.quiet_hours_start = None
        else:
            preferences.quiet_hours_start = None

        if quiet_hours_end:
            try:
                preferences.quiet_hours_end = time.fromisoformat(quiet_hours_end)
            except (ValueError, TypeError):
                preferences.quiet_hours_end = None
        else:
            preferences.quiet_hours_end = None

        # Update type preferences
        if not isinstance(preferences.type_preferences, dict):
            preferences.type_preferences = {}

        for field_name, event_type in event_type_mapping.items():
            email_val = request.POST.get(f'type_{field_name}_email') in ('on', 'true', '1')
            push_val = request.POST.get(f'type_{field_name}_push') in ('on', 'true', '1')
            in_app_val = request.POST.get(f'type_{field_name}_in_app') in ('on', 'true', '1')
            preferences.type_preferences[event_type] = {
                'email': email_val,
                'push': push_val,
                'in_app': in_app_val,
            }

        preferences.save()

        # Update cache on user instance if present
        if hasattr(request.user, '_state') and hasattr(request.user._state, 'fields_cache'):
            request.user._state.fields_cache['notification_preferences'] = preferences

        messages.success(request, 'Notification preferences updated successfully.')

        if not request.headers.get('HX-Request'):
            return redirect('users:settings_notifications')

    # Ensure type_preferences is a dictionary
    if not isinstance(preferences.type_preferences, dict):
        preferences.type_preferences = {}

    # Check if event types are populated; if not, merge default type preferences
    has_event_types = any(k in preferences.type_preferences for k in event_type_mapping.values())
    if not has_event_types:
        defaults = NotificationPreferenceService.get_default_type_preferences()
        for k, v in defaults.items():
            if k not in preferences.type_preferences:
                preferences.type_preferences[k] = v
        preferences.save()

    # Create a mapping for template access (dot keys -> underscore keys)
    template_preferences = preferences
    template_preferences.type_preferences_template = {}
    for underscore_key, dot_key in event_type_mapping.items():
        if dot_key in preferences.type_preferences and isinstance(preferences.type_preferences[dot_key], dict):
            template_preferences.type_preferences_template[underscore_key] = preferences.type_preferences[dot_key]
        else:
            template_preferences.type_preferences_template[underscore_key] = {
                'email': preferences.email_enabled,
                'push': preferences.push_enabled,
                'in_app': preferences.in_app_enabled,
            }

    if request.headers.get('HX-Request'):
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/notification_preferences_content.html',
            'preferences': template_preferences
        })

    return render(request, 'users/settings/notifications.html', {
        'preferences': template_preferences
    })


@login_required
@require_http_methods(["POST"])
def api_sign_out_session(request, session_id):
    """
    Sign out a specific session (API endpoint for HTMX).

    Optional form field ``block``: when truthy ("1"/"true"), the device
    fingerprint bound to that session is added to BlockedDevice so it cannot
    sign into this account again — the "kick out + block device" option.
    """
    from users.services.session_service import revoke_session
    from users.services.device_alert_service import (
        _terminate_session_hard, invalidate_blocked_hash_cache,
    )
    try:
        session = UserSession.objects.get(id=session_id, user=request.user)

        # Prevent signing out current session
        if session.session_key == request.session.session_key:
            return JsonResponse({'error': 'Cannot sign out current session'}, status=400)

        want_block = request.POST.get('block') in ('1', 'true', 'True', 'on')
        if want_block:
            device_hash = DeviceAccount.objects.filter(
                user=request.user, session_key=session.session_key
            ).values_list('device_id', flat=True).first()
            if device_hash:
                BlockedDevice.objects.get_or_create(
                    user=request.user,
                    device_hash=device_hash,
                    defaults={'reason': 'user_reported'},
                )
                invalidate_blocked_hash_cache(request.user.pk)

        revoke_session(session.id, user=request.user)
        _terminate_session_hard(request.user, session.session_key)
        return HttpResponse('')  # HTMX will remove the element
    except UserSession.DoesNotExist:
        return JsonResponse({'error': 'Session not found'}, status=404)


@login_required
@require_http_methods(["POST"])
def api_session_alive(request):
    """
    Session-guard heartbeat (Phase 1): tells an open tab whether its session
    is still valid server-side. Covers logouts/reblocks that happen without
    a websocket available (or while the socket is reconnecting).
    """
    from django.contrib.sessions.models import Session
    from pwaninet.middleware.device_block_middleware import _blocked_hashes_for_user

    claim = request.POST.get('session_key', '')
    own_key = request.session.session_key or ''
    if claim and claim != own_key:
        return JsonResponse({'alive': False, 'reason': 'wrong_session'}, status=200)

    reasons = []
    if own_key and not Session.objects.filter(session_key=own_key).exists():
        reasons.append('signed_out')

    blocked_hashes = _blocked_hashes_for_user(request.user.pk)
    device_hash = DeviceAccount.objects.filter(
        user=request.user, session_key=own_key
    ).values_list('device_id', flat=True).first() if own_key else None
    if device_hash and device_hash in blocked_hashes:
        reasons.append('device_blocked')

    if reasons:
        _terminate_session_hard(request.user, own_key)
        return JsonResponse({'alive': False, 'reason': reasons[0]}, status=200)
    return JsonResponse({'alive': True}, status=200)


@login_required
@require_http_methods(["POST"])
def api_sign_out_all_sessions(request):
    """Sign out all other sessions (API endpoint for HTMX)"""
    from users.services.session_service import revoke_other_user_sessions
    from users.services.device_alert_service import dispatch_force_logout, revoke_session_keys
    current_session_key = request.session.session_key

    revoked_keys = revoke_session_keys(request.user, current_session_key)

    revoke_other_user_sessions(request.user, current_session_key)

    dispatch_force_logout(request.user.pk, revoked_keys)

    messages.success(request, 'All other devices have been signed out.')
    if request.headers.get('HX-Request'):
        current_session = UserSession.objects.filter(user=request.user, session_key=current_session_key).first()
        return render(request, 'users/settings/partials/settings_navigation_partial.html', {
            'settings_content_partial': 'users/settings/partials/active_devices_content.html',
            'current_session': current_session,
            'other_sessions': []
        })
    return redirect('users:settings_active_devices')


@login_required
@require_http_methods(["POST"])
def api_unblock_user(request, user_id):
    """Unblock a user (API endpoint for HTMX)"""
    try:
        blocked_user = User.objects.get(id=user_id)
        block = Block.objects.get(blocker=request.user, blocked=blocked_user)
        block.delete()

        return HttpResponse('')  # HTMX will remove the element
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    except Block.DoesNotExist:
        return JsonResponse({'error': 'Block not found'}, status=404)


@login_required
@require_http_methods(["POST"])
def api_show_hidden_author(request, user_id):
    """Remove author from hidden list (API endpoint for HTMX)"""
    try:
        hidden_author = User.objects.get(id=user_id)
        hidden = HiddenAuthor.objects.get(hider=request.user, hidden_author=hidden_author)
        hidden.delete()
        from posts.models import AuthorPreference
        AuthorPreference.objects.filter(user=request.user, author=hidden_author).update(preference='normal')

        return HttpResponse('')  # HTMX will remove the element
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    except HiddenAuthor.DoesNotExist:
        return JsonResponse({'error': 'Hidden author not found'}, status=404)


@login_required
@require_http_methods(["POST"])
def mark_onboarding_complete(request):
    """
    Mark the current user's onboarding as completed.
    """
    request.user.has_completed_onboarding = True
    request.user.save(update_fields=['has_completed_onboarding'])
    return JsonResponse({'status': 'success'})


@login_required
def onboarding_wizard_view(request):
    """
    Dedicated pre-feed onboarding view.
    Presents classmates to follow and groups to join before reaching the home feed.
    """
    from recommendations.services.engine import UnifiedRecommendationEngine
    from groups.models import Membership, MembershipStatus

    # If already completed and not explicitly restarting, go straight to feed
    if request.user.has_completed_onboarding and not request.GET.get('restart'):
        return redirect('posts:home')

    data = UnifiedRecommendationEngine.get_onboarding_data(request.user)

    following_ids = set(
        Follow.objects.filter(follower=request.user).values_list('followed_id', flat=True)
    )
    joined_group_ids = set(
        Membership.objects.filter(
            user=request.user,
            status__in=[MembershipStatus.APPROVED, MembershipStatus.PENDING]
        ).values_list('group_id', flat=True)
    )

    context = {
        'classmates': data['classmates'],
        'recommended_groups': data['recommended_groups'],
        'official_groups': data['official_groups'],
        'following_ids': following_ids,
        'joined_group_ids': joined_group_ids,
    }
    try:
        from documents.academic.models import Institution, Programme
        from users.forms import InstitutionSetupForm
        context['setup_form'] = InstitutionSetupForm(initial={
            'institution': request.user.institution,
            'programme': request.user.programme,
            'academic_level': request.user.academic_level,
        })
        context['institutions'] = Institution.objects.filter(is_active=True).order_by('name')
        if request.user.programme_id:
            context['setup_programmes'] = Programme.objects.filter(
                is_active=True,
                department__school__faculty__institution=request.user.institution,
            ).order_by('name') if request.user.institution_id else Programme.objects.none()
        else:
            context['setup_programmes'] = Programme.objects.none()
    except Exception:
        pass
    return render(request, 'users/onboarding/wizard.html', context)


@login_required
@require_http_methods(["POST"])
def batch_follow_view(request):
    """
    1-click Follow All Suggested classmates during onboarding.
    """
    import json
    from recommendations.services.engine import UnifiedRecommendationEngine

    user_ids = request.POST.getlist('user_ids')
    if not user_ids and request.body:
        try:
            body = json.loads(request.body)
            user_ids = body.get('user_ids', [])
        except Exception:
            pass

    if not user_ids:
        # Default: follow all top suggestions
        suggestions = UnifiedRecommendationEngine.get_recommended_users(
            request.user, limit=12, use_cache=False, context='onboarding'
        )
        user_ids = [u.id for u in suggestions]

    user_ids = [int(uid) for uid in user_ids if str(uid).isdigit() and int(uid) != request.user.id]

    new_follows = []
    for uid in user_ids:
        follow, created = Follow.objects.get_or_create(follower=request.user, followed_id=uid)
        if created:
            new_follows.append(follow)
            invalidate_unread_count_cache(uid)

    UnifiedRecommendationEngine.invalidate_all_user_caches(request.user.id)

    if request.headers.get('HX-Request'):
        data = UnifiedRecommendationEngine.get_onboarding_data(request.user)
        following_ids = set(
            Follow.objects.filter(follower=request.user).values_list('followed_id', flat=True)
        )
        return render(request, 'users/onboarding/partials/step_classmates.html', {
            'classmates': data['classmates'],
            'following_ids': following_ids,
            'all_followed': True,
        })

    return JsonResponse({'status': 'success', 'followed_count': len(new_follows)})


@login_required
@require_http_methods(["GET", "POST"])
def complete_onboarding_view(request):
    """
    Mark onboarding as complete, set session flag for feed welcome banner,
    and redirect user to their populated home feed.
    """
    from recommendations.services.engine import UnifiedRecommendationEngine
    from django.core.cache import cache
    request.user.has_completed_onboarding = True
    request.user.save(update_fields=['has_completed_onboarding'])

    UnifiedRecommendationEngine.invalidate_all_user_caches(request.user.id)
    cache.delete(f'feed:user_suggestions:{request.user.id}')
    cache.delete(f'feed:following_ids:{request.user.id}')
    cache.delete(f'feed:suggested_groups:{request.user.id}')

    request.session['just_onboarded'] = True
    messages.success(request, "Welcome to vasto! You're connected with your campus network.")
    return redirect('posts:home')


@login_required
@require_http_methods(["POST"])
def dismiss_welcome_banner_view(request):
    """Dismiss the feed welcome banner for the current session."""
    if 'just_onboarded' in request.session:
        del request.session['just_onboarded']
    return JsonResponse({'status': 'dismissed'})


@login_required
def switch_account_view(request, user_id):
    """
    Switch to a different account that is associated with the current device.
    """
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme

    # Try to get device ID from attribute, headers, POST, or query params
    device_id = getattr(request, 'device_id', None)
    if not device_id:
        device_id = request.headers.get('X-Device-ID')
    if not device_id and hasattr(request, 'POST'):
        device_id = request.POST.get('device_id')
    if not device_id and hasattr(request, 'GET'):
        device_id = request.GET.get('device_id')

    if not device_id:
        messages.error(request, 'Unable to identify device. Please refresh the page.')
        target_url = reverse('posts:home')
        if request.headers.get('HX-Request'):
            response = HttpResponse(status=200)
            response['HX-Redirect'] = target_url
            return response
        return redirect('posts:home')

    # Propagate device_id onto request object so signals use it accurately
    request.device_id = device_id
    hashed_device_id = hash_device_id(device_id)

    # Verify the target account is associated with this device
    try:
        device_account = DeviceAccount.objects.get(
            user_id=user_id,
            device_id=hashed_device_id
        )
        target_user = device_account.user
    except DeviceAccount.DoesNotExist:
        messages.error(request, 'Account not found on this device.')
        target_url = reverse('posts:home')
        if request.headers.get('HX-Request'):
            response = HttpResponse(status=200)
            response['HX-Redirect'] = target_url
            return response
        return redirect('posts:home')

    import time
    from django.utils import timezone
    from django.utils.http import http_date
    from django.conf import settings
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY

    # 1. Save current user's session in DeviceAccount so it stays persistent on this device
    if request.user.is_authenticated and hasattr(request, 'session') and request.session.session_key:
        current_da = DeviceAccount.objects.filter(
            user=request.user,
            device_id=hashed_device_id
        ).first()
        if current_da:
            current_da.session_key = request.session.session_key
            current_da.last_used = timezone.now()
            current_da.save(update_fields=['session_key', 'last_used'])

    # 2. Check if target user has an existing valid session in django_session
    target_session = None
    if device_account.session_key:
        store = SessionStore(session_key=device_account.session_key)
        if store.exists(device_account.session_key):
            session_data = store.load()
            if session_data.get(SESSION_KEY) == str(target_user.pk):
                target_session = store

    # 3. If no existing valid session, create a new session directly for target_user
    if not target_session:
        target_session = SessionStore()
        target_session[SESSION_KEY] = target_user._meta.pk.value_to_string(target_user)
        target_session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
        target_session[HASH_SESSION_KEY] = target_user.get_session_auth_hash()
        target_session.save()

    # 4. Update the target device account with the persistent session key
    target_session.modified = True
    target_session.save()
    device_account.session_key = target_session.session_key
    device_account.last_used = timezone.now()
    device_account.save(update_fields=['session_key', 'last_used'])

    # 5. Attach target user and target session to request
    request.user = target_user
    request.session = target_session

    messages.success(request, f'Switched to {target_user.get_full_name() or target_user.username}')

    # Determine target URL
    next_url = request.GET.get('next') or request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        target_url = next_url
    else:
        target_url = reverse('posts:home')

    # For HTMX requests, issue HX-Redirect to force a clean full-page reload
    if request.headers.get('HX-Request'):
        response = HttpResponse(status=200)
        response['HX-Redirect'] = target_url
    else:
        response = redirect(target_url)

    # 6. Explicitly set session cookie on response to switch the browser session directly
    max_age = target_session.get_expiry_age()
    expires_time = time.time() + max_age
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        target_session.session_key,
        max_age=max_age,
        expires=http_date(expires_time),
        domain=settings.SESSION_COOKIE_DOMAIN,
        path=settings.SESSION_COOKIE_PATH,
        secure=settings.SESSION_COOKIE_SECURE or None,
        httponly=settings.SESSION_COOKIE_HTTPONLY or None,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )

    return response



@login_required
def get_device_accounts_view(request):
    """
    Get list of accounts associated with the current device.
    Returns JSON for the account switcher modal.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Try to get device ID from headers first, then query parameters
    device_id = request.headers.get('X-Device-ID')
    if not device_id:
        device_id = request.GET.get('device_id')
    logger.info(f'[Device Accounts] Request from user {request.user.username}, device_id: {device_id[:8] if device_id else "None"}...')

    if not device_id:
        logger.warning('[Device Accounts] No device ID in request headers or query parameters')
        return JsonResponse({'accounts': [], 'error': 'Device not identified'})

    hashed_device_id = hash_device_id(device_id)
    logger.info(f'[Device Accounts] Hashed device ID: {hashed_device_id[:16]}...')

    # Ensure the current logged-in user is associated with this device
    try:
        current_da, created = DeviceAccount.objects.get_or_create(
            user=request.user,
            device_id=hashed_device_id,
            defaults={'session_key': request.session.session_key}
        )
        if not created and not current_da.session_key and request.session.session_key:
            current_da.session_key = request.session.session_key
            current_da.save(update_fields=['session_key', 'last_used'])
    except Exception as e:
        logger.warning(f'[Device Accounts] Could not link current user: {e}')

    device_accounts = DeviceAccount.objects.filter(
        device_id=hashed_device_id
    ).select_related('user').order_by('-last_used')
    
    logger.info(f'[Device Accounts] Found {device_accounts.count()} device accounts')

    accounts_data = []
    for da in device_accounts:
        try:
            pic_url = da.user.profile_pic.url if (da.user.profile_pic and hasattr(da.user.profile_pic, 'url')) else '/static/images/default_pic1.jpg'
            accounts_data.append({
                'id': da.user.id,
                'username': da.user.username,
                'full_name': da.user.get_full_name() or str(da.user),
                'profile_pic': pic_url,
                'last_used': da.last_used.isoformat() if da.last_used else None,
                'is_current': da.user.id == request.user.id
            })
        except Exception as e:
            logger.error(f'[Device Accounts] Error processing account {da.user.username}: {e}')

    logger.info(f'[Device Accounts] Returning {len(accounts_data)} accounts')
    return JsonResponse({'accounts': accounts_data})



@login_required
def remove_account_from_device_view(request, user_id):
    """
    Remove an account from the current device's account list.
    Does not delete the user account, just removes the device association.
    """
    # Try to get device ID from headers first (HTMX/fetch requests)
    device_id = request.headers.get('X-Device-ID')

    # Fallback to POST or GET parameters
    if not device_id:
        device_id = request.POST.get('device_id') or request.GET.get('device_id')

    is_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
        request.headers.get('HX-Request') == 'true' or
        'application/json' in request.headers.get('Accept', '')
    )

    if not device_id:
        if is_json:
            return JsonResponse({'status': 'error', 'message': 'Unable to identify device.'}, status=400)
        messages.error(request, 'Unable to identify device.')
        return redirect('posts:home')

    hashed_device_id = hash_device_id(device_id)

    # Prevent removing the current account
    if user_id == request.user.id:
        if is_json:
            return JsonResponse({'status': 'error', 'message': 'Cannot remove the currently active account.'}, status=400)
        messages.error(request, 'Cannot remove the currently active account.')
        return redirect('posts:home')

    try:
        device_account = DeviceAccount.objects.get(
            user_id=user_id,
            device_id=hashed_device_id
        )
        device_account.delete()
        if is_json:
            return JsonResponse({'status': 'success', 'message': 'Account removed from device.'})
        messages.success(request, 'Account removed from device.')
    except DeviceAccount.DoesNotExist:
        if is_json:
            return JsonResponse({'status': 'error', 'message': 'Account not found on this device.'}, status=404)
        messages.error(request, 'Account not found on this device.')

    return redirect('posts:home')



@login_required
def view_profile_photo_fullscreen(request, username, photo_type):
    """
    View profile or cover photo in full screen mode.
    Only accessible if the viewer is following the profile user or if it's their own profile.
    """
    profile_user = get_object_or_404(User, username=username)

    from users.services.privacy import can_view_profile
    if not can_view_profile(request.user, profile_user):
        return redirect('users:profile', username=username)

    # Check if user is allowed to view the photo
    is_own_profile = request.user == profile_user
    is_following = Follow.objects.filter(follower=request.user, followed=profile_user).exists()

    if not is_own_profile and not is_following:
        messages.error(request, 'You need to follow this user to view their photos in full screen.')
        return redirect('users:profile', username=username)

    # Determine which photo to show
    if photo_type == 'profile':
        photo_url = profile_user.profile_pic.url
        photo_title = f"{profile_user.get_full_name}'s Profile Photo"
    elif photo_type == 'cover':
        if not profile_user.cover_photo:
            messages.error(request, 'This user does not have a cover photo.')
            return redirect('users:profile', username=username)
        photo_url = profile_user.cover_photo.url
        photo_title = f"{profile_user.get_full_name}'s Cover Photo"
    else:
        messages.error(request, 'Invalid photo type.')
        return redirect('users:profile', username=username)

    # Get like count and check if current user liked the photo
    from .models import UserProfilePhotoLike
    like_count = UserProfilePhotoLike.objects.filter(
        profile_user=profile_user,
        photo_type=photo_type
    ).count()

    is_liked = UserProfilePhotoLike.objects.filter(
        user=request.user,
        profile_user=profile_user,
        photo_type=photo_type
    ).exists()

    context = {
        'profile_user': profile_user,
        'photo_url': photo_url,
        'photo_type': photo_type,
        'photo_title': photo_title,
        'is_own_profile': is_own_profile,
        'like_count': like_count,
        'is_liked': is_liked,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'users/partials/profile_photo_fullscreen_navigation_partial.html', context)
    return render(request, 'users/profile_photo_fullscreen.html', context)


@login_required
def toggle_profile_photo_like(request, username, photo_type):
    """
    Toggle like status for a user's profile or cover photo.
    API endpoint for fullscreen photo view.
    """
    profile_user = get_object_or_404(User, username=username)

    from users.services.privacy import can_view_profile
    if not can_view_profile(request.user, profile_user):
        return JsonResponse({'detail': 'This profile is not available to you.'}, status=404)

    # Validate photo type
    if photo_type not in ['profile', 'cover']:
        return JsonResponse(
            {'detail': 'Invalid photo type. Must be profile or cover.'},
            status=400
        )

    # Check if photo exists
    if photo_type == 'cover' and not profile_user.cover_photo:
        return JsonResponse(
            {'detail': 'This user does not have a cover photo.'},
            status=404
        )

    from .models import UserProfilePhotoLike
    like, created = UserProfilePhotoLike.objects.get_or_create(
        user=request.user,
        profile_user=profile_user,
        photo_type=photo_type
    )

    likes_count = UserProfilePhotoLike.objects.filter(
        profile_user=profile_user,
        photo_type=photo_type
    ).count()

    if created:
        return JsonResponse(
            {'detail': 'Photo liked.', 'likes_count': likes_count},
            status=201
        )
    else:
        like.delete()
        return JsonResponse(
            {'detail': 'Photo unliked.', 'likes_count': likes_count},
            status=200
        )


# API ViewSets
class UserViewSet(viewsets.ModelViewSet):
    """
    API ViewSet for User model.
    Provides list, create, retrieve, update, partial_update, delete actions.
    """
    queryset = User.objects.all().select_related('course__school', 'year')
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = UserFilter
    search_fields = ['username', 'first_name', 'last_name', 'second_name']
    ordering_fields = ['username', 'date_joined', 'last_login']
    ordering = ['-date_joined']

    def get_queryset(self):
        from users.services.privacy import apply_user_discovery_exclusions
        visible = apply_user_discovery_exclusions(User.objects.all(), viewer=self.request.user)
        if self.request.user.is_authenticated:
            visible = visible | User.objects.filter(pk=self.request.user.pk)
        return visible.select_related('course__school', 'year', 'programme', 'academic_level', 'academic_year', 'semester').distinct()

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return UserPublicSerializer
        elif self.action in ['update', 'partial_update']:
            return UserUpdateSerializer
        return UserSerializer

    def get_permissions(self):
        if self.action == 'create':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    @extend_schema(
        summary="Get current user profile",
        description="Returns the authenticated user's profile information",
        responses={200: UserPublicSerializer}
    )
    @action(detail=False, methods=['get'])
    def me(self, request):
        """Get current user profile"""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)

    @extend_schema(
        summary="Follow or unfollow a user",
        description="Toggle follow status for a user",
        responses={200: {"status": "unfollowed"}, 201: {"status": "followed"}, 400: {"error": "message"}}
    )
    @action(detail=True, methods=['post'])
    def follow(self, request, pk=None):
        """Follow or unfollow a user"""
        target_user = self.get_object()
        if target_user == request.user:
            return Response(
                {'error': 'You cannot follow yourself'},
                status=status.HTTP_400_BAD_REQUEST
            )

        follow, created = Follow.objects.get_or_create(
            follower=request.user,
            followed=target_user
        )

        if not created:
            # Unfollow
            follow.delete()
            return Response({'status': 'unfollowed'}, status=status.HTTP_200_OK)

        return Response({'status': 'followed'}, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Like or unlike user photo",
        description="Toggle like status for a user's profile or cover photo",
        responses={200: {"detail": "Photo unliked.", "likes_count": 0}, 201: {"detail": "Photo liked.", "likes_count": 1}}
    )
    @action(detail=True, methods=['post'], url_path='photos/(?P<photo_type>[^/.]+)/like')
    def photo_like(self, request, pk=None, photo_type=None):
        """Like or unlike a user's profile or cover photo"""
        profile_user = self.get_object()

        # Validate photo type
        if photo_type not in ['profile', 'cover']:
            return Response(
                {'detail': 'Invalid photo type. Must be profile or cover.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if photo exists
        if photo_type == 'cover' and not profile_user.cover_photo:
            return Response(
                {'detail': 'This user does not have a cover photo.'},
                status=status.HTTP_404_NOT_FOUND
            )

        from .models import UserProfilePhotoLike
        like, created = UserProfilePhotoLike.objects.get_or_create(
            user=request.user,
            profile_user=profile_user,
            photo_type=photo_type
        )

        likes_count = UserProfilePhotoLike.objects.filter(
            profile_user=profile_user,
            photo_type=photo_type
        ).count()

        if created:
            return Response(
                {'detail': 'Photo liked.', 'likes_count': likes_count},
                status=status.HTTP_201_CREATED
            )
        else:
            like.delete()
            return Response(
                {'detail': 'Photo unliked.', 'likes_count': likes_count},
                status=status.HTTP_200_OK
            )

    @extend_schema(
        summary="Get user's followers",
        description="Returns a paginated list of users following the specified user",
        responses={200: UserPublicSerializer(many=True)}
    )
    @action(detail=True, methods=['get'])
    def followers(self, request, pk=None):
        """Get list of followers for a user"""
        user = self.get_object()
        from users.services.privacy import apply_user_discovery_exclusions
        followers = apply_user_discovery_exclusions(
            User.objects.filter(follower_relationships__followed=user), viewer=request.user
        )
        page = self.paginate_queryset(followers)
        if page is not None:
            serializer = UserPublicSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = UserPublicSerializer(followers, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get users followed by user",
        description="Returns a paginated list of users that the specified user is following",
        responses={200: UserPublicSerializer(many=True)}
    )
    @action(detail=True, methods=['get'])
    def following(self, request, pk=None):
        """Get list of users followed by a user"""
        user = self.get_object()
        from users.services.privacy import apply_user_discovery_exclusions
        following = apply_user_discovery_exclusions(
            User.objects.filter(follower_relationships__follower=user), viewer=request.user
        )
        page = self.paginate_queryset(following)
        if page is not None:
            serializer = UserPublicSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = UserPublicSerializer(following, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Update notification preferences",
        description="Update the authenticated user's notification preferences",
        request=NotificationPreferencesSerializer,
        responses={200: NotificationPreferencesSerializer, 400: {"error": "message"}}
    )
    @action(detail=False, methods=['put', 'patch'])
    def update_preferences(self, request):
        """Update notification preferences"""
        serializer = NotificationPreferencesSerializer(request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Update theme preference",
        description="Update the authenticated user's theme preference (light, dark, or system)",
        responses={200: {"theme_preference": "string"}, 400: {"error": "message"}}
    )
    @action(detail=False, methods=['patch'])
    def update_theme(self, request):
        """Update theme preference"""
        theme = request.data.get('theme_preference')
        if theme not in ['light', 'dark', 'system']:
            return Response(
                {'error': 'Invalid theme preference. Must be light, dark, or system.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        request.user.theme_preference = theme
        request.user.save()
        return Response({'theme_preference': theme})

    @extend_schema(
        summary="Update audio preference",
        description="Update the authenticated user's audio preference for video playback (muted or unmuted)",
        responses={200: {"audio_preference": "string"}, 400: {"error": "message"}}
    )
    @action(detail=False, methods=['patch'])
    def update_audio_preference(self, request):
        """Update audio preference"""
        audio_pref = request.data.get('audio_preference')
        if audio_pref not in ['muted', 'unmuted']:
            return Response(
                {'error': 'Invalid audio preference. Must be muted or unmuted.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        request.user.audio_preference = audio_pref
        request.user.save()
        return Response({'audio_preference': audio_pref})

    @extend_schema(
        summary="Update appearance preferences",
        description="Update the authenticated user's appearance preferences (theme, font_size, language, font_family, font_style)",
        responses={200: {"theme_preference": "string", "font_size_preference": "string", "language_preference": "string", "font_family_preference": "string", "font_style_preference": "string"}, 400: {"error": "message"}}
    )
    @action(detail=False, methods=['patch'])
    def update_preferences(self, request):
        """Update appearance preferences"""
        data = request.data
        updated_fields = {}

        # Update theme preference if provided
        if 'theme_preference' in data:
            theme = data['theme_preference']
            if theme not in ['light', 'dark', 'system']:
                return Response(
                    {'error': 'Invalid theme preference. Must be light, dark, or system.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            request.user.theme_preference = theme
            updated_fields['theme_preference'] = theme

        # Update chat theme preference if provided
        if 'chat_theme_preference' in data:
            chat_theme = data['chat_theme_preference']
            valid_chat_themes = [
                'default',
                'ocean-wave',
                'ocean-breeze',
                'classic',
                'sand-gradient',
                'swahili-wave',
                'midnight-coast',
                'forest-glow',
                'pwani-neon',
            ]
            if chat_theme not in valid_chat_themes:
                return Response(
                    {'error': f'Invalid chat theme preference. Must be one of: {", ".join(valid_chat_themes)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            request.user.chat_theme_preference = chat_theme
            updated_fields['chat_theme_preference'] = chat_theme

        # Update font size preference if provided
        if 'font_size_preference' in data:
            font_size = data['font_size_preference']
            if font_size not in ['tiny', 'small', 'medium', 'large']:
                return Response(
                    {'error': 'Invalid font size preference. Must be tiny, small, medium, or large.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            request.user.font_size_preference = font_size
            updated_fields['font_size_preference'] = font_size

        # Update language preference if provided
        if 'language_preference' in data:
            language = data['language_preference']
            if language not in ['en', 'sw']:
                return Response(
                    {'error': 'Invalid language preference. Must be en or sw.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            request.user.language_preference = language
            updated_fields['language_preference'] = language

        # Update font family preference if provided
        if 'font_family_preference' in data:
            font_family = data['font_family_preference']
            valid_font_families = ['default', 'inter', 'roboto', 'ibm_plex_serif', 'manrope', 'playfair_display', 'romanesco', 'story_script', 'poppins', 'lora', 'merriweather', 'dancing_script', 'great_vibes', 'parisienne', 'satisfy', 'cookie', 'italianno', 'tangerine']
            if font_family not in valid_font_families:
                return Response(
                    {'error': 'Invalid font family preference. Must be one of: ' + ', '.join(valid_font_families)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            request.user.font_family_preference = font_family
            updated_fields['font_family_preference'] = font_family

        # Update font style preference if provided
        if 'font_style_preference' in data:
            font_style = data['font_style_preference']
            if font_style not in ['normal', 'italic']:
                return Response(
                    {'error': 'Invalid font style preference. Must be normal or italic.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            request.user.font_style_preference = font_style
            updated_fields['font_style_preference'] = font_style

        request.user.save()
        return Response(updated_fields)


class FollowViewSet(viewsets.ModelViewSet):
    """
    API ViewSet for Follow model.
    """
    queryset = Follow.objects.all()
    serializer_class = FollowSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = FollowFilter
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_permissions(self):
        if self.action == 'create':
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return Follow.objects.filter(follower=self.request.user).select_related('follower', 'followed')

    def perform_create(self, serializer):
        serializer.save(follower=self.request.user)


class PinchViewSet(viewsets.ModelViewSet):
    """
    API ViewSet for Pinch model.
    """
    queryset = Pinch.objects.all().select_related('pinch_user', 'pinched_user')
    serializer_class = PinchSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return Pinch.objects.filter(pinch_user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(pinch_user=self.request.user)


class DeviceAccountViewSet(viewsets.ModelViewSet):
    """
    API ViewSet for DeviceAccount model.
    """
    queryset = DeviceAccount.objects.all()
    serializer_class = DeviceAccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['last_used']
    ordering = ['-last_used']

    def get_queryset(self):
        return DeviceAccount.objects.filter(user=self.request.user)


@require_http_methods(["GET"])
def user_online_status_api(request, user_id):
    """
    API endpoint to check if a user is online.
    Returns JSON with is_online status using heartbeat-based presence tracking.
    FROZEN FOR MVP - Messaging presence disabled
    """
    try:
        # Messaging presence - FROZEN FOR MVP
        # from messaging.presence import PresenceService

        # Check if user is online based on heartbeat freshness - FROZEN FOR MVP
        # is_online = PresenceService.is_user_online(user_id)
        # last_seen = PresenceService.get_last_seen(user_id)

        # Fallback to basic User model is_online field
        from users.models import User
        user = User.objects.get(id=user_id)

        return JsonResponse({
            'is_online': user.is_online,
            'user_id': int(user_id),
            'last_seen': None  # Presence tracking disabled
        })
    except Exception as e:
        return JsonResponse({
            'is_online': False,
            'user_id': int(user_id),
            'error': str(e)
        }, status=500)


# ============================================================================
# Phase 1: New-device login alerts — verification sheet + respond endpoints
# ============================================================================

def _acting_device_hash(request):
    from users.services.device_alert_service import resolve_device_hash
    return resolve_device_hash(request)


def _verification_context(request, verification):
    """Shared context for the challenge page/sheet states."""
    from users.services.device_alert_service import event_is_expired
    acting_hash = _acting_device_hash(request) or ''
    is_pending = verification.status == DeviceVerificationStatus.PENDING
    can_act = (
        is_pending
        and not event_is_expired(verification)
        and acting_hash != verification.device_hash
        and acting_hash != ''
    )
    own_device = acting_hash == verification.device_hash
    if acting_hash == '' and is_pending:
        # No device identity → safest default: show info, disable actions
        can_act = False
    return {
        'verification': verification,
        'device_label': verification.device_name or verification.device_type.title(),
        'location_label': verification.location_label or 'Unknown location',
        'is_pending': is_pending,
        'is_expired': verification.status == DeviceVerificationStatus.EXPIRED,
        'can_act': can_act,
        'own_device': own_device,
        'respond_url_approve': reverse('users:api_device_verification_approve', args=[verification.pk]),
        'respond_url_deny': reverse('users:api_device_verification_deny', args=[verification.pk]),
        'devices_url': reverse('users:settings_active_devices'),
        'status': verification.status,
    }


@login_required
def device_verification_view(request, verification_id):
    """
    New-device sign-in alert: opened by tapping the in-app notification card,
    the FCM/WebPush push popup, or directly from Settings → Devices.
    HTMX requests get the bottom-sheet partial; direct navigation gets a full
    page that renders the same sheet centered.
    """
    verification = get_object_or_404(
        DeviceVerificationEvent.objects.select_related('user'),
        pk=verification_id,
        user=request.user,
    )
    context = _verification_context(request, verification)
    template = (
        'users/partials/device_verification_sheet.html'
        if request.headers.get('HX-Request')
        else 'users/device_verification.html'
    )
    response = render(request, template, context)
    if not request.headers.get('HX-Request'):
        response['X-Frame-Options'] = 'DENY'
    return response


@login_required
@require_http_methods(["POST"])
def api_device_verification_approve(request, verification_id):
    """'Yes, it was me' — trust this device. Rejected if sent from the
    device under verification itself (the attacker must not approve)."""
    verification = get_object_or_404(
        DeviceVerificationEvent, pk=verification_id, user=request.user
    )
    from users.services import device_alert_service as service
    if not service._ratelimit_allow(request.user):
        return JsonResponse({'ok': False, 'error': 'rate_limited'}, status=429)
    ok, error_code = service.respond(
        verification,
        'approve',
        acting_device_hash=_acting_device_hash(request) or '',
    )
    if not ok:
        return JsonResponse({'ok': False, 'error': error_code}, status=409 if error_code == 'already_handled' else 403)
    return JsonResponse({'ok': True, 'decision': 'approved', 'status': verification.status})


@login_required
@require_http_methods(["POST"])
def api_device_verification_deny(request, verification_id):
    """'It wasn't me' — block device; optional nuke escalation."""
    verification = get_object_or_404(
        DeviceVerificationEvent, pk=verification_id, user=request.user
    )
    from users.services import device_alert_service as service
    if not service._ratelimit_allow(request.user):
        return JsonResponse({'ok': False, 'error': 'rate_limited'}, status=429)
    escalation = 'nuke' if (
        request.POST.get('escalation') == 'nuke' or request.headers.get('X-Escalation') == 'nuke'
    ) else 'block'
    ok, error_code = service.respond(
        verification,
        'deny',
        escalation=escalation,
        acting_device_hash=_acting_device_hash(request) or '',
    )
    if not ok:
        return JsonResponse({'ok': False, 'error': error_code}, status=409 if error_code == 'already_handled' else 403)
    return JsonResponse({'ok': True, 'decision': 'blocked', 'escalation': escalation})


@login_required
@require_http_methods(["POST"])
def api_trusted_device_untrust(request, device_id):
    """Remove trust. Password confirmation required."""
    trusted = get_object_or_404(TrustedDevice, pk=device_id, user=request.user)
    password = request.POST.get('password', '')
    if not request.user.check_password(password):
        return JsonResponse({'ok': False, 'error': 'bad_password'}, status=403)
    was_primary = trusted.is_primary
    trusted_hash = trusted.device_hash
    trusted.delete()
    if was_primary:
        remaining = TrustedDevice.objects.filter(user=request.user).order_by('trusted_at').first()
        if remaining:
            remaining.is_primary = True
            remaining.save(update_fields=['is_primary'])

    # Removed trust, so the events machinery sees it as a fresh device next login
    from users.services.device_alert_service import invalidate_and_broadcast
    _ = invalidate_and_broadcast  # no-op helper reference
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(["POST"])
def api_trusted_device_make_primary(request, device_id):
    trusted = get_object_or_404(TrustedDevice, pk=device_id, user=request.user)
    TrustedDevice.objects.filter(user=request.user, is_primary=True).exclude(pk=trusted.pk).update(is_primary=False)
    trusted.is_primary = True
    trusted.save(update_fields=['is_primary'])
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(["POST"])
def api_blocked_device_unblock(request, blocked_id):
    """Unblock a device fingerprint. Password confirmation required."""
    blocked = get_object_or_404(BlockedDevice, pk=blocked_id, user=request.user)
    password = request.POST.get('password', '')
    if not request.user.check_password(password):
        return JsonResponse({'ok': False, 'error': 'bad_password'}, status=403)
    blocked.delete()
    from pwaninet.middleware.device_block_middleware import invalidate_blocked_device_cache
    invalidate_blocked_device_cache(request.user.pk)
    return JsonResponse({'ok': True})
