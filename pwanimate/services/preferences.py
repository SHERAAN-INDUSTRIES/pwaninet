"""
Pwanimate Preference Service.

Centralized service layer for managing student AI assistant preferences,
controlled choices, validation, and safe defaults.
"""

from typing import Any, Dict, Optional
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from pwanimate.models import (
    PwanimatePreferences,
    PwanimateResponseStyle,
    PwanimateTone,
    PwanimateVoiceGender,
    PwanimateVoiceStyle,
)

logger = logging.getLogger(__name__)


class PwanimatePreferenceService:
    """
    Service managing persistent student preferences for Pwanimate.

    Guarantees:
    - Strict user-level authorization & ownership boundaries.
    - Centralized validation of controlled choices and text limits.
    - Safe defaults for existing and new users without eager schema changes.
    - Zero leakage of private instruction text into application logs.
    """

    @classmethod
    def _validate_user(cls, user: Any) -> None:
        """Ensure the supplied user object is authenticated and valid."""
        if user is None or not getattr(user, "is_authenticated", False):
            raise ValueError("An authenticated user instance is required.")
        if getattr(user, "pk", None) is None and getattr(user, "id", None) is None:
            raise ValueError("User instance must have a valid identifier.")

    @classmethod
    def get_preferences(cls, user: Any) -> Optional[PwanimatePreferences]:
        """
        Retrieve persisted preferences for the authenticated user, or None if uninitialized.
        Does not perform any database write.
        """
        cls._validate_user(user)
        # Check if already joined via select_related
        if hasattr(user, "_state") and hasattr(user._state, "fields_cache"):
            if "pwanimate_preferences" in user._state.fields_cache:
                return user._state.fields_cache["pwanimate_preferences"]
        try:
            return PwanimatePreferences.objects.filter(user=user).first()
        except Exception as exc:
            logger.error("Failed to fetch Pwanimate preferences for user %s: %s", getattr(user, "pk", None), exc)
            return None

    @classmethod
    def get_or_create_preferences(cls, user: Any) -> PwanimatePreferences:
        """
        Retrieve existing preferences or initialize a persistent record with safe defaults.
        """
        cls._validate_user(user)
        prefs, created = PwanimatePreferences.objects.get_or_create(
            user=user,
            defaults={
                "nickname": "",
                "tone": PwanimateTone.NEUTRAL,
                "response_style": PwanimateResponseStyle.BALANCED,
                "personal_instructions": "",
                "voice_style": PwanimateVoiceStyle.WARM,
                "voice_gender": PwanimateVoiceGender.FEMALE,
                "auto_tts_enabled": False,
            },
        )
        if created:
            logger.info("Initialized default Pwanimate preferences for user_id=%s", getattr(user, "pk", None))
        if hasattr(user, "_state") and hasattr(user._state, "fields_cache"):
            user._state.fields_cache["pwanimate_preferences"] = prefs
        return prefs

    @classmethod
    def get_preferences_or_defaults(cls, user: Any) -> PwanimatePreferences:
        """
        Safe read-only accessor returning persisted preferences if they exist,
        or an unpersisted instance with default neutral settings if absent.

        Guarantees zero database writes during read paths (e.g. context building).
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return PwanimatePreferences(
                nickname="",
                tone=PwanimateTone.NEUTRAL,
                response_style=PwanimateResponseStyle.BALANCED,
                personal_instructions="",
                voice_style=PwanimateVoiceStyle.WARM,
                voice_gender=PwanimateVoiceGender.FEMALE,
                auto_tts_enabled=False,
            )

        persisted = cls.get_preferences(user)
        if persisted is not None:
            return persisted

        return PwanimatePreferences(
            user=user,
            nickname="",
            tone=PwanimateTone.NEUTRAL,
            response_style=PwanimateResponseStyle.BALANCED,
            personal_instructions="",
            voice_style=PwanimateVoiceStyle.WARM,
            voice_gender=PwanimateVoiceGender.FEMALE,
            auto_tts_enabled=False,
        )

    @classmethod
    def validate_preferences(
        cls,
        *,
        nickname: Optional[str] = None,
        tone: Optional[str] = None,
        response_style: Optional[str] = None,
        personal_instructions: Optional[str] = None,
        voice_style: Optional[str] = None,
        voice_gender: Optional[str] = None,
        auto_tts_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Validate and normalize individual preference inputs.
        Returns a dictionary of cleaned values or raises ValidationError.
        """
        cleaned: Dict[str, Any] = {}
        errors: Dict[str, str] = {}

        if nickname is not None:
            norm_nick = str(nickname).strip()
            if len(norm_nick) > 50:
                errors["nickname"] = "Nickname cannot exceed 50 characters."
            else:
                cleaned["nickname"] = norm_nick

        if tone is not None:
            norm_tone = str(tone).strip().lower()
            if norm_tone not in dict(PwanimateTone.choices):
                errors["tone"] = f"Invalid tone '{tone}'. Allowed choices: {PwanimateTone.values}."
            else:
                cleaned["tone"] = norm_tone

        if response_style is not None:
            norm_style = str(response_style).strip().lower()
            if norm_style not in dict(PwanimateResponseStyle.choices):
                errors["response_style"] = (
                    f"Invalid response style '{response_style}'. Allowed choices: {PwanimateResponseStyle.values}."
                )
            else:
                cleaned["response_style"] = norm_style

        if personal_instructions is not None:
            norm_inst = str(personal_instructions).strip()
            if len(norm_inst) > 500:
                errors["personal_instructions"] = "Personal instructions cannot exceed 500 characters."
            else:
                cleaned["personal_instructions"] = norm_inst

        if voice_style is not None:
            norm_style = str(voice_style).strip().lower()
            if norm_style not in dict(PwanimateVoiceStyle.choices):
                errors["voice_style"] = "Invalid voice style."
            else:
                cleaned["voice_style"] = norm_style

        if voice_gender is not None:
            norm_gender = str(voice_gender).strip().lower()
            if norm_gender not in dict(PwanimateVoiceGender.choices):
                errors["voice_gender"] = "Invalid voice gender."
            else:
                cleaned["voice_gender"] = norm_gender

        if auto_tts_enabled is not None:
            cleaned["auto_tts_enabled"] = bool(auto_tts_enabled)

        if errors:
            raise ValidationError(errors)

        return cleaned

    @classmethod
    def update_preferences(
        cls,
        user: Any,
        *,
        nickname: Optional[str] = None,
        tone: Optional[str] = None,
        response_style: Optional[str] = None,
        personal_instructions: Optional[str] = None,
        voice_style: Optional[str] = None,
        voice_gender: Optional[str] = None,
        auto_tts_enabled: Optional[bool] = None,
    ) -> PwanimatePreferences:
        """
        Partially or fully update preferences for the authenticated user.
        Preserves omitted fields. Enforces server-side validation.
        """
        cls._validate_user(user)

        cleaned_data = cls.validate_preferences(
            nickname=nickname,
            tone=tone,
            response_style=response_style,
            personal_instructions=personal_instructions,
            voice_style=voice_style,
            voice_gender=voice_gender,
            auto_tts_enabled=auto_tts_enabled,
        )

        with transaction.atomic():
            prefs = cls.get_or_create_preferences(user)

            updated_fields = []
            for field_name, value in cleaned_data.items():
                setattr(prefs, field_name, value)
                updated_fields.append(field_name)

            if updated_fields:
                prefs.full_clean()
                prefs.save(update_fields=updated_fields + ["updated_at"])
                if hasattr(user, "_state") and hasattr(user._state, "fields_cache"):
                    user._state.fields_cache["pwanimate_preferences"] = prefs
                # Safe logging: field names and user ID only, NEVER instruction content
                logger.info(
                    "Updated Pwanimate preferences for user_id=%s (fields: %s)",
                    getattr(user, "pk", None),
                    updated_fields,
                )

        return prefs
