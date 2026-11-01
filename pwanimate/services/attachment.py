"""
Pwanimate Attachment Service.

Provides secure validation, magic-byte inspection, storage management,
and authorization enforcement for user chat attachments.
"""

import os
import re
import uuid
import logging
from typing import Optional, Tuple, Dict, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.text import get_valid_filename

from pwanimate.models import PwanimateAttachment, PwanimateConversation

logger = logging.getLogger(__name__)

# Configurable limits
DEFAULT_MAX_IMAGE_SIZE = 10 * 1024 * 1024      # 10MB
DEFAULT_MAX_DOC_SIZE = 25 * 1024 * 1024        # 25MB

# Dangerous file extensions strictly rejected
DANGEROUS_EXTENSIONS = {
    '.exe', '.dll', '.so', '.dylib', '.bin', '.sh', '.bash', '.bat', '.cmd',
    '.ps1', '.vbs', '.js.bak', '.com', '.msi', '.app', '.dmg', '.jar',
    '.pyc', '.pyd', '.php', '.php3', '.php4', '.php5', '.phtml', '.asp',
    '.aspx', '.jsp', '.jspx', '.cgi', '.pl', '.svg', '.svgz',
}

ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
ALLOWED_IMAGE_MIMES = {
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
}

ALLOWED_DOC_EXTENSIONS = {
    '.pdf', '.docx', '.doc', '.pptx', '.ppt',
    '.txt', '.md', '.csv', '.json', '.html', '.xml',
    '.sql', '.py', '.js',
}
ALLOWED_DOC_MIMES = {
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.ms-powerpoint',
    'text/plain',
    'text/markdown',
    'text/csv',
    'application/json',
    'text/html',
    'text/xml',
    'application/xml',
    'application/sql',
    'text/x-python',
    'text/x-script.python',
    'application/javascript',
    'text/javascript',
    'application/octet-stream',  # fallback when client sends generic octet-stream for valid extensions
}

# Magic signatures
MAGIC_JPEG = b'\xff\xd8\xff'
MAGIC_PNG = b'\x89PNG\r\n\x1a\n'
MAGIC_GIF87 = b'GIF87a'
MAGIC_GIF89 = b'GIF89a'
MAGIC_PDF = b'%PDF-'
MAGIC_ZIP = b'PK\x03\x04'  # docx, pptx


def sanitize_filename(filename: str) -> str:
    """
    Sanitize display filename to prevent path traversal and strip dangerous characters.
    """
    if not filename:
        return "attachment"
    
    # Strip directory components
    clean_name = os.path.basename(filename).strip()
    clean_name = clean_name.replace('\x00', '')
    clean_name = re.sub(r'[\r\n\t]', '', clean_name)
    
    # Use Django's get_valid_filename
    base, ext = os.path.splitext(clean_name)
    safe_base = get_valid_filename(base) or "attachment"
    safe_ext = ext.lower()[:10]
    
    final_name = f"{safe_base}{safe_ext}"
    if len(final_name) > 200:
        final_name = final_name[:190] + safe_ext
    return final_name


class AttachmentValidator:
    """Validator for uploaded chat files."""

    @classmethod
    def validate_file(cls, uploaded_file) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Validate an uploaded file.
        Returns: (is_valid, error_code, error_message, attachment_type)
        """
        if not uploaded_file:
            return False, "NO_FILE", "No file was provided.", None

        # 1. Filename & extension check
        raw_name = getattr(uploaded_file, "name", "")
        if not raw_name:
            return False, "INVALID_FILENAME", "Uploaded file has no filename.", None

        clean_name = sanitize_filename(raw_name)
        ext = os.path.splitext(clean_name)[1].lower()

        if not ext:
            return False, "NO_EXTENSION", "File must have a valid extension.", None

        if ext in DANGEROUS_EXTENSIONS:
            return False, "DANGEROUS_FILE_TYPE", f"Files with extension '{ext}' are not permitted.", None

        # 2. Determine attachment type
        is_image = ext in ALLOWED_IMAGE_EXTENSIONS
        is_doc = ext in ALLOWED_DOC_EXTENSIONS

        if not is_image and not is_doc:
            return (
                False,
                "UNSUPPORTED_FORMAT",
                f"File format '{ext}' is not supported. Supported: images, PDF, Word, PowerPoint, text, and code files.",
                None,
            )

        attachment_type = "image" if is_image else "document"

        # 3. Size validation
        max_size = getattr(settings, "PWANIMATE_MAX_IMAGE_SIZE", DEFAULT_MAX_IMAGE_SIZE) if is_image else getattr(settings, "PWANIMATE_MAX_DOC_SIZE", DEFAULT_MAX_DOC_SIZE)
        if uploaded_file.size > max_size:
            max_mb = max_size / (1024 * 1024)
            return (
                False,
                "FILE_TOO_LARGE",
                f"File size exceeds the {max_mb:.0f}MB limit for {attachment_type}s.",
                attachment_type,
            )

        if uploaded_file.size == 0:
            return False, "EMPTY_FILE", "Uploaded file is empty.", attachment_type

        # 4. MIME validation
        declared_mime = (getattr(uploaded_file, "content_type", "") or "").lower().strip()
        if is_image and declared_mime and declared_mime not in ALLOWED_IMAGE_MIMES:
            return (
                False,
                "INVALID_MIME_TYPE",
                f"Declared MIME type '{declared_mime}' does not match allowed image formats.",
                attachment_type,
            )

        # 5. Magic bytes inspection
        header = uploaded_file.read(1024)
        uploaded_file.seek(0)

        if is_image:
            if ext in {'.jpg', '.jpeg'} and not header.startswith(MAGIC_JPEG):
                return False, "CORRUPT_OR_SPOOFED_IMAGE", "File signature does not match a valid JPEG image.", attachment_type
            elif ext == '.png' and not header.startswith(MAGIC_PNG):
                return False, "CORRUPT_OR_SPOOFED_IMAGE", "File signature does not match a valid PNG image.", attachment_type
            elif ext == '.gif' and not (header.startswith(MAGIC_GIF87) or header.startswith(MAGIC_GIF89)):
                return False, "CORRUPT_OR_SPOOFED_IMAGE", "File signature does not match a valid GIF image.", attachment_type
            elif ext == '.webp' and not (header.startswith(b'RIFF') and b'WEBP' in header[:16]):
                return False, "CORRUPT_OR_SPOOFED_IMAGE", "File signature does not match a valid WebP image.", attachment_type

        elif is_doc:
            if ext == '.pdf' and not header.startswith(MAGIC_PDF):
                return False, "CORRUPT_OR_SPOOFED_PDF", "File signature does not match a valid PDF document.", attachment_type
            elif ext in {'.docx', '.pptx'} and not header.startswith(MAGIC_ZIP):
                return False, "CORRUPT_OR_SPOOFED_DOC", f"File signature does not match a valid Microsoft Office document ({ext}).", attachment_type
            elif ext in {'.txt', '.md', '.csv', '.json', '.sql', '.py', '.js'}:
                # Verify text is decodable without null bytes
                try:
                    text_sample = header.decode('utf-8', errors='strict')
                    if '\x00' in text_sample:
                        return False, "BINARY_TEXT_FILE", "Text file contains binary null bytes.", attachment_type
                except UnicodeDecodeError:
                    return False, "INVALID_TEXT_ENCODING", "Text document must be valid UTF-8 encoded.", attachment_type

        return True, None, None, attachment_type


class AttachmentService:
    """Service handling attachment persistence and authorization."""

    @classmethod
    def create_attachment(
        cls,
        user,
        uploaded_file,
        conversation_id: Optional[str] = None,
    ) -> PwanimateAttachment:
        """
        Validate and persist an attachment for an authenticated user.
        """
        is_valid, err_code, err_msg, att_type = AttachmentValidator.validate_file(uploaded_file)
        if not is_valid:
            raise ValidationError({"file": err_msg, "code": err_code})

        conversation = None
        if conversation_id:
            try:
                conv_uuid = uuid.UUID(str(conversation_id).strip())
                conversation = PwanimateConversation.objects.filter(id=conv_uuid, user=user).first()
            except (ValueError, TypeError):
                pass

        clean_name = sanitize_filename(uploaded_file.name)
        mime_type = (getattr(uploaded_file, "content_type", "") or "application/octet-stream").lower().strip()
        
        # Normalize mime type for known extensions if sent generic
        ext = os.path.splitext(clean_name)[1].lower()
        if ext in {'.jpg', '.jpeg'}:
            mime_type = 'image/jpeg'
        elif ext == '.png':
            mime_type = 'image/png'
        elif ext == '.webp':
            mime_type = 'image/webp'
        elif ext == '.gif':
            mime_type = 'image/gif'
        elif ext == '.pdf':
            mime_type = 'application/pdf'
        elif ext == '.docx':
            mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        elif ext == '.pptx':
            mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        elif ext in {'.txt', '.md', '.py', '.js', '.sql'}:
            mime_type = 'text/plain'

        attachment = PwanimateAttachment.objects.create(
            user=user,
            conversation=conversation,
            file=uploaded_file,
            file_name=clean_name,
            file_size=uploaded_file.size,
            mime_type=mime_type,
            attachment_type=att_type or "document",
            # Images are queued for one vision pass at upload, so they are
            # "pending" until that pass finishes -- exactly like a document
            # waiting to be read. While it is pending the chat path refuses
            # the turn rather than sending the image to a second model.
            processing_status="pending",
        )
        return attachment

    @classmethod
    def get_authorized_attachment(cls, user, attachment_id) -> Optional[PwanimateAttachment]:
        """
        Retrieve attachment enforcing strict ownership/admin access.
        """
        if not user or not getattr(user, "is_authenticated", False):
            return None

        try:
            att_uuid = uuid.UUID(str(attachment_id).strip()) if not isinstance(attachment_id, uuid.UUID) else attachment_id
        except (ValueError, TypeError):
            return None

        qs = PwanimateAttachment.objects.filter(id=att_uuid)
        if not (user.is_staff or user.is_superuser):
            qs = qs.filter(user=user)

        return qs.first()

    @classmethod
    def link_attachments_to_message(cls, attachments, message, conversation=None) -> None:
        """
        Link one or more PwanimateAttachment instances to a message and conversation.
        """
        if not attachments or not message:
            return
        target_conv = conversation or getattr(message, "conversation", None)
        for att in attachments:
            att.message = message
            if target_conv and not att.conversation_id:
                att.conversation = target_conv
            att.save(update_fields=["message", "conversation"])

