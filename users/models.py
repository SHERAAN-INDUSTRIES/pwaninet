from django.db import models
from users.storage import ProfilePictureStorage
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from PIL import Image
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.contrib.postgres.fields import JSONField
from django.contrib.postgres.search import SearchVectorField
from django.contrib.postgres.indexes import GinIndex, OpClass


class GlobalRole(models.TextChoices):
    PRESIDENT = 'PRESIDENT', 'President'
    DELEGATE = 'DELEGATE', 'Delegate'
    VERIFIED = 'VERIFIED', 'Verified'
    NORMAL = 'NORMAL', 'Normal'


class ThemePreference(models.TextChoices):
    LIGHT = 'light', 'Light'
    DARK = 'dark', 'Dark'
    SYSTEM = 'system', 'System Default'


class ChatThemePreference(models.TextChoices):
    DEFAULT = 'default', 'Ocean Wave (Default)'
    OCEAN_WAVE = 'ocean-wave', 'Ocean Wave'
    OCEAN_BREEZE = 'ocean-breeze', 'Ocean Breeze'
    CLASSIC = 'classic', 'Classic'
    SAND_GRADIENT = 'sand-gradient', 'Sand Gradient'
    SWAHILI_WAVE = 'swahili-wave', 'Swahili Wave'
    MIDNIGHT_COAST = 'midnight-coast', 'Midnight Coast'
    FOREST_GLOW = 'forest-glow', 'Forest Glow'
    PWANI_NEON = 'pwani-neon', 'Pwani Neon'


class FontSizePreference(models.TextChoices):
    TINY = 'tiny', 'Tiny'
    SMALL = 'small', 'Small'
    MEDIUM = 'medium', 'Medium'
    LARGE = 'large', 'Large'


class LanguagePreference(models.TextChoices):
    ENGLISH = 'en', 'English'
    KISWAHILI = 'sw', 'Kiswahili'


class FontFamilyPreference(models.TextChoices):
    DEFAULT = 'default', 'Default (System)'
    INTER = 'inter', 'Inter'
    ROBOTO = 'roboto', 'Roboto'
    IBM_PLEX_SERIF = 'ibm_plex_serif', 'IBM Plex Serif'
    MANROPE = 'manrope', 'Manrope'
    PLAYFAIR_DISPLAY = 'playfair_display', 'Playfair Display'
    ROMANESCO = 'romanesco', 'Romanesco'
    STORY_SCRIPT = 'story_script', 'Story Script'
    POPPINS = 'poppins', 'Poppins'
    LORA = 'lora', 'Lora'
    MERRIWEATHER = 'merriweather', 'Merriweather'
    DANCING_SCRIPT = 'dancing_script', 'Dancing Script'
    GREAT_VIBES = 'great_vibes', 'Great Vibes'
    PARISIENNE = 'parisienne', 'Parisienne'
    SATISFY = 'satisfy', 'Satisfy'
    COOKIE = 'cookie', 'Cookie'
    ITALIANNO = 'italianno', 'Italianno'
    TANGERINE = 'tangerine', 'Tangerine'


class FontStylePreference(models.TextChoices):
    NORMAL = 'normal', 'Normal'
    ITALIC = 'italic', 'Italic'


class AudioPreference(models.TextChoices):
    MUTED = 'muted', 'Muted'
    UNMUTED = 'unmuted', 'Unmuted'


class PrivacyLevel(models.TextChoices):
    PUBLIC = 'PUBLIC', 'Everyone'
    AUTHENTICATED = 'AUTHENTICATED', 'vasto Users'
    FOLLOWERS = 'FOLLOWERS', 'Followers Only'
    PRIVATE = 'PRIVATE', 'Only Me'


PRIVACY_CHOICES = [
    (PrivacyLevel.AUTHENTICATED, 'vasto Users'),
    (PrivacyLevel.FOLLOWERS, 'Followers Only'),
    (PrivacyLevel.PRIVATE, 'Only Me'),
]


class HeroShowcaseSet(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    center_reel = models.ImageField(upload_to='hero_showcase/reels/')
    pwani_gate = models.ImageField(upload_to='hero_showcase/campus/')
    pwanimate = models.ImageField(upload_to='hero_showcase/ai/')
    doc_repo = models.ImageField(upload_to='hero_showcase/docs/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Hero Showcase Set'
        verbose_name_plural = 'Hero Showcase Sets'

    def __str__(self):
        return f"{self.name} ({'Active' if self.is_active else 'Disabled'})"


class CollaborationStatus(models.TextChoices):
    OPEN_TO_PROJECTS = 'open_to_projects', 'Open to Projects'
    OPEN_TO_STUDY_GROUPS = 'open_to_study_groups', 'Open to Study Groups'
    OPEN_TO_NETWORKING = 'open_to_networking', 'Open to Networking'
    NOT_LOOKING = 'not_looking', 'Not Looking'


class CustomUserManager(UserManager):
    pass


class User(AbstractUser):
    objects = CustomUserManager()
    first_name = models.CharField(max_length=200, null=True, blank=True, db_index=True)
    second_name = models.CharField(max_length=200, null=True, blank=True, db_index=True)
    last_name = models.CharField(max_length=200, null=True, blank=True, db_index=True)
    
    # Legacy academic fields (for backward compatibility)
    year = models.ForeignKey('courses.Year', on_delete=models.SET_NULL, null=True, blank=True)
    course = models.ForeignKey('courses.Course', on_delete=models.CASCADE, null=True, blank=True)
    
    # New academic profile fields
    institution = models.ForeignKey(
        'documents.Institution',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        help_text="Current institution (optional until setup is completed)"
    )
    programme = models.ForeignKey(
        'documents.Programme',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        help_text="Current academic programme"
    )
    academic_level = models.ForeignKey(
        'documents.AcademicLevel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        help_text="Current academic level (Year 1, Year 2, etc.)"
    )
    academic_year = models.ForeignKey(
        'documents.AcademicYear',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        help_text="Current academic year"
    )
    semester = models.ForeignKey(
        'documents.Semester',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        help_text="Current semester"
    )
    
    global_role = models.CharField(max_length=20, choices=GlobalRole.choices, default=GlobalRole.NORMAL, db_index=True)
    profile_pic = models.ImageField(
        default='profile_pic/default_pic1.jpg',
        upload_to='profile_pic',
        null=True,
        blank=True,
        storage=ProfilePictureStorage(),
    )
    cover_photo = models.ImageField(upload_to='covers/', blank=True, null=True)
    bio = models.TextField(max_length=500, blank=True)
    
    # Extended profile fields
    headline = models.CharField(max_length=100, blank=True, help_text="Professional tagline or headline")
    interests = models.TextField(blank=True, help_text="Comma-separated interests")
    collaboration_status = models.CharField(
        max_length=30,
        choices=CollaborationStatus.choices,
        blank=True,
        default='',
        help_text="Current collaboration availability"
    )
    skills = models.JSONField(default=list, blank=True, help_text="List of skills")
    projects = models.JSONField(default=list, blank=True, help_text="List of projects with title, description, and link")
    github_url = models.URLField(blank=True, help_text="GitHub profile URL")
    linkedin_url = models.URLField(blank=True, help_text="LinkedIn profile URL")
    portfolio_url = models.URLField(blank=True, help_text="Portfolio website URL")
    twitter_url = models.URLField(blank=True, help_text="Twitter/X profile URL")

    # Notification preferences
    notify_on_like = models.BooleanField(default=True)
    notify_on_follow = models.BooleanField(default=True)
    notify_on_invite = models.BooleanField(default=True)
    notify_on_group_request = models.BooleanField(default=True)
    notify_on_group_approved = models.BooleanField(default=True)
    notify_on_pinch = models.BooleanField(default=True)
    notify_on_comment_reply = models.BooleanField(default=True)
    email_notifications = models.BooleanField(default=False)

    # Theme preference
    theme_preference = models.CharField(
        max_length=10,
        choices=ThemePreference.choices,
        default=ThemePreference.SYSTEM
    )

    # Chat theme preference
    chat_theme_preference = models.CharField(
        max_length=30,
        choices=ChatThemePreference.choices,
        default=ChatThemePreference.DEFAULT
    )

    # Font size preference
    font_size_preference = models.CharField(
        max_length=10,
        choices=FontSizePreference.choices,
        default=FontSizePreference.MEDIUM
    )

    # Language preference
    language_preference = models.CharField(
        max_length=5,
        choices=LanguagePreference.choices,
        default=LanguagePreference.ENGLISH
    )

    # Font family preference
    font_family_preference = models.CharField(
        max_length=30,
        choices=FontFamilyPreference.choices,
        default=FontFamilyPreference.DEFAULT
    )

    # Font style preference
    font_style_preference = models.CharField(
        max_length=10,
        choices=FontStylePreference.choices,
        default=FontStylePreference.NORMAL
    )

    # Audio preference for video playback
    audio_preference = models.CharField(
        max_length=10,
        choices=AudioPreference.choices,
        default=AudioPreference.MUTED,
        help_text="Default audio state for video playback"
    )

    # Online status tracking
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(auto_now=True)

    # Onboarding tracking
    has_completed_onboarding = models.BooleanField(default=False, help_text="Whether user has completed the onboarding tour")

    # Email verification
    email_verified = models.BooleanField(default=False, help_text="Whether the user's email address has been verified")

    # Privacy settings
    profile_privacy = models.CharField(
        max_length=20,
        choices=PRIVACY_CHOICES,
        default=PrivacyLevel.AUTHENTICATED,
        help_text="Who can view profile information"
    )
    post_privacy = models.CharField(
        max_length=20,
        choices=PRIVACY_CHOICES,
        default=PrivacyLevel.AUTHENTICATED,
        help_text="Default visibility for new posts"
    )
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        verbose_name = 'user'
        verbose_name_plural = 'users'
        indexes = [
            GinIndex(fields=['search_vector'], name='user_search_vector_idx'),
            GinIndex(
                OpClass('username', name='gin_trgm_ops'),
                name='user_username_trgm_idx',
            ),
            GinIndex(
                OpClass('first_name', name='gin_trgm_ops'),
                OpClass('last_name', name='gin_trgm_ops'),
                name='user_names_trgm_idx',
            ),
        ]

    def update_search_vector(self):
        """Recompute search vector with field weights."""
        from django.contrib.postgres.search import SearchVector
        User.objects.filter(pk=self.pk).update(
            search_vector=(
                SearchVector('username', weight='A') +
                SearchVector('first_name', weight='A') +
                SearchVector('last_name', weight='A') +
                SearchVector('headline', weight='B') +
                SearchVector('bio', weight='C')
            )
        )

    def get_programme_institution(self):
        """Derive the institution owning the selected programme (None if unset/unmapped)."""
        programme = getattr(self, 'programme', None)
        getter = getattr(programme, 'get_institution', None)
        try:
            return getter() if callable(getter) else None
        except Exception:
            return None

    def clean(self):
        super().clean()
        # Backstop: institution/programme mismatch is rejected wherever full_clean runs
        # (forms, admin, setup service). Nulls and legacy-unmapped chains never raise.
        institution_id = getattr(self, 'institution_id', None)
        programme_id = getattr(self, 'programme_id', None)
        if institution_id and programme_id:
            try:
                derived = self.get_programme_institution()
            except Exception:
                derived = None
            if derived is not None and getattr(derived, 'pk', None) != institution_id:
                raise ValidationError({
                    'programme': 'Selected programme does not belong to the selected institution.'
                })
        # Ensure only one president exists (only check when role is being set to PRESIDENT)
        if self.global_role == GlobalRole.PRESIDENT:
            # Check if this is a new instance or role is being changed to PRESIDENT
            if self.pk is None or User.objects.filter(pk=self.pk, global_role=GlobalRole.PRESIDENT).exists():
                return  # Already president or new instance, skip check

            existing_president = User.objects.filter(global_role=GlobalRole.PRESIDENT).exclude(pk=self.pk).first()
            if existing_president:
                raise ValidationError({
                    'global_role': f'There can only be one president. {existing_president.username} is already the president.'
                })

    def save(self, *args, **kwargs):
        old_user = None
        if self.pk is not None:
            old_user = User.objects.filter(pk=self.pk).first()
            if old_user and old_user.global_role != self.global_role:
                self.full_clean()
        else:
            self.full_clean()

        academic_changed = (
            old_user is None
            or old_user.programme_id != self.programme_id
            or old_user.academic_level_id != self.academic_level_id
            or old_user.semester_id != self.semester_id
            or old_user.academic_year_id != self.academic_year_id
            or old_user.course_id != self.course_id
            or old_user.year_id != self.year_id
        )

        super().save(*args, **kwargs)

        if academic_changed and (self.programme_id or self.course_id):
            try:
                from documents.academic.services import StudentAcademicEnrollmentService
                StudentAcademicEnrollmentService.sync_student_units(self)
            except Exception:
                pass

    def get_enrolled_units(self):
        """Return active StudentUnitEnrollment records for this user (auto-syncing if needed)."""
        from documents.academic.services import StudentAcademicEnrollmentService
        return StudentAcademicEnrollmentService.get_active_enrollments(self)

    def generate_smart_bio(self):
        """Intelligently construct a bio based on the user's institution, programme, and study level."""
        prog_name = None
        if self.programme:
            prog_name = self.programme.name
        elif self.course:
            prog_name = self.course.name

        level_name = None
        if self.academic_level:
            level_name = self.academic_level.name
        elif self.year:
            level_name = f"Year {self.year.level}"

        inst_name = None
        if self.institution:
            inst_name = self.institution.name
        elif self.programme and self.programme.get_institution():
            inst_name = self.programme.get_institution().name

        if level_name and prog_name and inst_name:
            return f"{level_name} {prog_name} student at {inst_name}"
        if prog_name and inst_name:
            return f"{prog_name} student at {inst_name}"
        if level_name and prog_name:
            return f"{level_name} {prog_name} student"
        if prog_name:
            return f"{prog_name} student"
        if inst_name:
            return f"Student at {inst_name}"
        return "Scholar on Vasto"

    def get_display_bio(self):
        """Return user's custom bio if present, otherwise dynamically synthesize an intelligent bio."""
        if self.bio and self.bio.strip():
            return self.bio.strip()
        return self.generate_smart_bio()

    def __str__(self):
        return f"{self.first_name} {self.second_name}".strip() or self.username
    
    @property
    def is_profile_complete(self):
        # Check new academic profile first
        if self.programme and self.academic_level:
            return True
        # Fallback to legacy fields for backward compatibility
        return bool(self.course and self.year)
    
    @property
    def profile_completion_percentage(self):
        """
        Calculate profile completion percentage based on:
        - Profile Picture: 15%
        - Bio: 10%
        - Headline: 10%
        - Skills: 20%
        - Projects: 20%
        - Collaboration Status: 10%
        - Links: 5%
        - Interests: 10%
        """
        score = 0
        
        # Profile Picture (15%)
        if self.profile_pic and self.profile_pic.name != 'profile_pic/default_pic1.jpg':
            score += 15
        
        # Bio (10%)
        if self.bio:
            score += 10
        
        # Headline (10%)
        if self.headline:
            score += 10
        
        # Skills (20%)
        if self.skills and len(self.skills) > 0:
            score += 20
        
        # Projects (20%)
        if self.projects and len(self.projects) > 0:
            score += 20
        
        # Collaboration Status (10%)
        if self.collaboration_status:
            score += 10
        
        # Links (5%)
        links_count = sum([
            bool(self.github_url),
            bool(self.linkedin_url),
            bool(self.portfolio_url),
            bool(self.twitter_url)
        ])
        if links_count > 0:
            score += 5
        
        # Interests (10%)
        if self.interests:
            score += 10
        
        return score

    @property
    def is_two_factor_enabled(self) -> bool:
        """Return True if the user has an active, confirmed 2FA configuration."""
        try:
            return bool(self.two_factor and self.two_factor.is_enabled)
        except Exception:
            return False


class Follow(models.Model):
    follower = models.ForeignKey(User, on_delete=models.CASCADE, related_name='following_relationships', db_index=True)
    followed = models.ForeignKey(User, on_delete=models.CASCADE, related_name='follower_relationships', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('follower', 'followed')
        indexes = [
            models.Index(fields=['follower', 'followed']),
            models.Index(fields=['followed', 'follower']),
        ]

    def __str__(self):
        return f"{self.follower.username} follows {self.followed.username}"


class Pinch(models.Model):
    """Profile pinch interaction - one pinch per user pair per 24 hours"""
    pinch_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pinches_sent', db_index=True)
    pinched_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pinches_received', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['pinch_user', 'pinched_user']),
            models.Index(fields=['created_at']),
            models.Index(fields=['pinched_user', 'created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.pinch_user.username} pinched {self.pinched_user.username}"

    @classmethod
    def can_pinch(cls, pinch_user, pinched_user):
        """Check if pinch_user can pinch pinched_user (not self, not already today)"""
        if pinch_user == pinched_user:
            return False, "Cannot pinch yourself"
        
        from django.utils import timezone
        today = timezone.now().date()
        
        if cls.objects.filter(
            pinch_user=pinch_user,
            pinched_user=pinched_user,
            created_at__date=today
        ).exists():
            return False, "Already pinched today"
        
        return True, None


class DeviceAccount(models.Model):
    """Tracks accounts that have been used on a specific device"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='device_accounts', db_index=True)
    device_id = models.CharField(max_length=255, db_index=True)
    last_used = models.DateTimeField(auto_now=True, db_index=True)
    session_key = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        unique_together = ('user', 'device_id')
        ordering = ['-last_used']
        indexes = [
            models.Index(fields=['user', 'device_id']),
            models.Index(fields=['device_id', 'last_used']),
        ]

    def __str__(self):
        return f"{self.user.username} on device {self.device_id}"


class UserSession(models.Model):
    """Tracks active user sessions for device management"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user_sessions', db_index=True)
    session_key = models.CharField(max_length=255, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_name = models.CharField(max_length=255, blank=True)
    device_type = models.CharField(max_length=20, choices=[('mobile', 'Mobile'), ('tablet', 'Tablet'), ('desktop', 'Desktop')], default='desktop', blank=True)
    browser = models.CharField(max_length=100, blank=True)
    operating_system = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=255, blank=True)
    login_time = models.DateTimeField(auto_now_add=True, db_index=True)
    last_activity = models.DateTimeField(auto_now=True, db_index=True)
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ['-last_activity']
        indexes = [
            models.Index(fields=['user', '-last_activity']),
            models.Index(fields=['session_key']),
            models.Index(fields=['-last_activity']),
        ]

    def __str__(self):
        return f"{self.user.username} session on {self.device_name or 'Unknown Device'}"


class Block(models.Model):
    """Tracks blocked users"""
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_users', db_index=True)
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_by', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('blocker', 'blocked')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['blocker', '-created_at']),
            models.Index(fields=['blocked']),
        ]

    def __str__(self):
        return f"{self.blocker.username} blocked {self.blocked.username}"


class UserProfilePhotoLike(models.Model):
    """Likes for user profile and cover photos"""
    PHOTO_TYPE_CHOICES = [
        ('profile', 'Profile Photo'),
        ('cover', 'Cover Photo'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='photo_likes', db_index=True)
    profile_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_photo_likes', db_index=True)
    photo_type = models.CharField(max_length=10, choices=PHOTO_TYPE_CHOICES, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('user', 'profile_user', 'photo_type')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['profile_user', 'photo_type', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} likes {self.profile_user.username}'s {self.photo_type} photo"


class HiddenAuthor(models.Model):
    """Tracks authors whose posts are hidden from user's feed"""
    hider = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hidden_authors', db_index=True)
    hidden_author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hidden_by', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('hider', 'hidden_author')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['hider', '-created_at']),
            models.Index(fields=['hidden_author']),
        ]

    def __str__(self):
        return f"{self.hider.username} hid {self.hidden_author.username}"


class UserTwoFactor(models.Model):
    """
    Two-factor authentication (TOTP) configuration for a user.
    Secret is stored encrypted at rest via authenticated encryption.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='two_factor',
        db_index=True
    )
    is_enabled = models.BooleanField(default=False, db_index=True)
    encrypted_secret = models.TextField(help_text="Authenticated encrypted TOTP secret")
    enrolled_at = models.DateTimeField(null=True, blank=True)
    last_used_timestep = models.BigIntegerField(null=True, blank=True, help_text="Last successfully consumed TOTP timestep for replay protection")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Two-Factor Configuration"
        verbose_name_plural = "User Two-Factor Configurations"

    def __str__(self):
        status = "enabled" if self.is_enabled else "pending"
        return f"UserTwoFactor({self.user.username}, status={status})"


class RecoveryCode(models.Model):
    """
    Single-use recovery code for account recovery / 2FA backup.
    Plaintext code is never stored; only cryptographic password hash is retained.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='recovery_codes',
        db_index=True
    )
    code_hash = models.CharField(max_length=255, help_text="Cryptographic hash of the recovery code")
    is_consumed = models.BooleanField(default=False, db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Recovery Code"
        verbose_name_plural = "Recovery Codes"
        indexes = [
            models.Index(fields=['user', 'is_consumed']),
        ]
        ordering = ['created_at']

    def __str__(self):
        status = "consumed" if self.is_consumed else "active"
        return f"RecoveryCode(user={self.user.username}, id={self.id}, status={status})"


class InviteType(models.TextChoices):
    APP_DOWNLOAD = 'app_download', 'Mobile App Download'
    PLATFORM_INVITE = 'platform_invite', 'Invite to vasto'


class GoogleAccountLinkMethod(models.TextChoices):
    NEW_SIGNUP = 'new_signup', 'New account via Google'
    EXPLICIT_LINK = 'explicit_link', 'Linked to existing account'


class GoogleAccount(models.Model):
    """
    Links a stable Google identity (`sub`) to a local user.

    Lookup is always by `sub`, never by email alone, so a Google account
    can never silently merge into an existing local account that happens
    to share the same email address.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='google_account',
        db_index=True,
    )
    sub = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Stable Google subject identifier from the verified ID token",
    )
    email = models.EmailField(help_text="Email snapshot from the Google ID token at link time")
    email_verified = models.BooleanField(
        default=False,
        help_text="Whether Google reported the email as verified at link time",
    )
    hosted_domain = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Google Workspace hosted domain (hd claim), if present",
    )
    link_method = models.CharField(
        max_length=20,
        choices=GoogleAccountLinkMethod.choices,
        default=GoogleAccountLinkMethod.NEW_SIGNUP,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Google Account"
        verbose_name_plural = "Google Accounts"

    def __str__(self):
        return f"GoogleAccount(sub={self.sub}, user={self.user.username})"


class PlatformInvite(models.Model):
    """
    Peer-to-peer invite and mobile app referral links with opaque tokens and telemetry.
    """
    token = models.CharField(max_length=128, unique=True, db_index=True)
    inviter = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='platform_invites',
        db_index=True
    )
    invite_type = models.CharField(
        max_length=32,
        choices=InviteType.choices,
        default=InviteType.PLATFORM_INVITE,
        db_index=True
    )
    clicks_count = models.PositiveIntegerField(default=0)
    conversions_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_clicked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Platform Invite"
        verbose_name_plural = "Platform Invites"
        unique_together = ('inviter', 'invite_type')
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['inviter', 'invite_type']),
        ]

    def __str__(self):
        return f"PlatformInvite({self.inviter.username}, type={self.invite_type}, token={self.token})"


class DeviceAlertChannel(models.TextChoices):
    """How a device verification challenge was delivered."""
    PUSH = 'push', 'Push notification'
    IN_APP = 'in_app', 'In-app notification'
    EMAIL = 'email', 'Email fallback'


class DeviceVerificationStatus(models.TextChoices):
    """Lifecycle of a new-device sign-in challenge."""
    PENDING = 'pending', 'Pending response'
    APPROVED = 'approved', 'Approved by user'
    REJECTED = 'rejected', 'Rejected by user'
    EXPIRED = 'expired', 'Expired without response'


class DeviceBlockReason(models.TextChoices):
    """Why a device fingerprint was blocked from the account."""
    USER_REPORTED = 'user_reported', 'User reported sign-in as not theirs'
    SUSPECTED_ABUSE = 'suspected_abuse', 'Automatically suspected abuse'


class TrustedDevice(models.Model):
    """
    A device fingerprint the user has confirmed as theirs.

    Trusted devices receive new sign-in alerts (via their push subscription)
    and are exempt from further verification challenges. Lookup always uses
    the peppered hash from ``device_service.hash_device_id`` so raw device
    identifiers are never stored.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='trusted_devices',
        db_index=True,
    )
    device_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Peppered SHA-256 of the device ID (see device_service.hash_device_id)",
    )
    nickname = models.CharField(max_length=100, blank=True, default='')
    device_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Device label snapshot at trust time, e.g. 'iPhone 15 Pro (Safari)'",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Primary trusted device; at most one per user (enforced in services)",
    )
    source_event = models.ForeignKey(
        'DeviceVerificationEvent',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='trusted_from_event',
        help_text="Verification event whose approval created this trust, if any",
    )
    trusted_at = models.DateTimeField(auto_now_add=True)
    last_confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Trusted Device"
        verbose_name_plural = "Trusted Devices"
        unique_together = ('user', 'device_hash')
        ordering = ['-is_primary', '-trusted_at']
        indexes = [
            models.Index(fields=['user', 'device_hash']),
            models.Index(fields=['device_hash']),
        ]

    def __str__(self):
        label = self.nickname or self.device_name or self.device_hash[:10]
        return f"TrustedDevice({self.user.username}, {label})"


class DeviceVerificationEvent(models.Model):
    """
    One challenge emitted when a sign-in comes from a device the user has
    never trusted. Non-blocking: the session is live either way; this row is
    the alert + audit trail the user acts on from a trusted device.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='device_verifications',
        db_index=True,
    )
    device_hash = models.CharField(max_length=64, db_index=True)
    # Snapshot of device metadata at challenge time (no FK to UserSession so
    # the audit trail survives session revocation).
    device_name = models.CharField(max_length=255, blank=True, default='')
    device_type = models.CharField(
        max_length=20,
        choices=[('mobile', 'Mobile'), ('tablet', 'Tablet'), ('desktop', 'Desktop')],
        default='desktop',
        blank=True,
    )
    browser = models.CharField(max_length=100, blank=True, default='')
    operating_system = models.CharField(max_length=100, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    location_city = models.CharField(max_length=100, blank=True, default='')
    location_country = models.CharField(max_length=100, blank=True, default='')
    location_source = models.CharField(
        max_length=20,
        default='ip',
        help_text="Source of the location estimate (IP-only in phase 1)",
    )
    session_key = models.CharField(max_length=255, blank=True, default='')
    notification = models.ForeignKey(
        'notifications.NotificationObject',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='device_verifications',
    )
    channel = models.CharField(
        max_length=20,
        choices=DeviceAlertChannel.choices,
        default=DeviceAlertChannel.IN_APP,
    )
    status = models.CharField(
        max_length=20,
        choices=DeviceVerificationStatus.choices,
        default=DeviceVerificationStatus.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_from_hash = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text="Device hash of the trusted device that acted on this event",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Device Verification Event"
        verbose_name_plural = "Device Verification Events"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'expires_at']),
            models.Index(fields=['device_hash']),
        ]

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.status == self.status.PENDING and self.expires_at <= timezone.now()

    @property
    def location_label(self):
        parts = [p for p in (self.location_city, self.location_country) if p]
        return ", ".join(parts)

    def __str__(self):
        return (
            f"DeviceVerificationEvent({self.user.username}, "
            f"{self.device_name or 'Unknown Device'}, status={self.status})"
        )


class BlockedDevice(models.Model):
    """
    A device fingerprint the user said was not theirs, refused both for fresh
    logins and any session bound to it. Stores the peppered hash only.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='blocked_devices',
        db_index=True,
    )
    device_hash = models.CharField(max_length=64, db_index=True)
    reason = models.CharField(
        max_length=20,
        choices=DeviceBlockReason.choices,
        default=DeviceBlockReason.USER_REPORTED,
    )
    source_event = models.ForeignKey(
        'DeviceVerificationEvent',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='blocks_from_event',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Blocked Device"
        verbose_name_plural = "Blocked Devices"
        unique_together = ('user', 'device_hash')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'device_hash']),
        ]

    def __str__(self):
        return f"BlockedDevice({self.user.username}, {self.device_hash[:10]}, {self.reason})"


class ServiceAccount(models.Model):
    """Service account for internal API authentication."""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='service_account',
        help_text="Django user associated with this service account"
    )
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Service account name"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this service account is active"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this service account was created"
    )
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this service account was last used"
    )

    class Meta:
        db_table = 'service_account'
        verbose_name = "Service Account"
        verbose_name_plural = "Service Accounts"
        ordering = ['-created_at']

    def __str__(self):
        return self.name
