"""
AI Gateway Data Contracts and DTOs for Pwanimate.

Defines provider-neutral message, request, and response contracts.
Directly accepts Phase 4 ContextPackage without ORM dependencies.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pwanimate.context.types import ContextPackage

VALID_ROLES = {"system", "user", "assistant"}


@dataclass
class AttachmentData:
    """
    Provider-neutral representation of a message attachment.
    """
    id: str = ""
    name: str = ""
    mime_type: str = "application/octet-stream"
    attachment_type: str = "document"  # 'image' or 'document'
    data_bytes: Optional[bytes] = None
    file_path: Optional[str] = None
    url: str = ""

    def __init__(
        self,
        id: str = "",
        name: str = "",
        mime_type: str = "application/octet-stream",
        attachment_type: str = "document",
        data_bytes: Optional[bytes] = None,
        file_path: Optional[str] = None,
        url: str = "",
        attachment_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ):
        self.id = id or attachment_id or ""
        self.name = name or file_name or ""
        self.mime_type = mime_type
        self.attachment_type = attachment_type
        self.data_bytes = data_bytes
        self.file_path = file_path
        self.url = url

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "mime_type": self.mime_type,
            "attachment_type": self.attachment_type,
            "url": self.url,
            "file_path": self.file_path,
        }


@dataclass
class ChatMessage:
    """
    Individual conversational message.

    Attributes:
        role: Sender role, must be one of 'system', 'user', 'assistant'.
        content: Message text.
        attachments: Optional list of AttachmentData associated with this message turn.
    """
    role: str
    content: str
    attachments: List[AttachmentData] = field(default_factory=list)

    def __post_init__(self):
        if self.role not in VALID_ROLES:
            raise ValueError(f"Invalid message role '{self.role}'. Supported roles: {VALID_ROLES}")
        if not isinstance(self.content, str):
            raise TypeError("Message content must be a string.")
        if not isinstance(self.attachments, list):
            self.attachments = list(self.attachments) if self.attachments else []

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.attachments:
            data["attachments"] = [
                a.to_dict() if hasattr(a, "to_dict") else a for a in self.attachments
            ]
        return data


@dataclass
class LLMRequest:
    """
    Provider-neutral request sent to the AI Gateway.

    Attributes:
        task: Operational task identifier (e.g., 'rag', 'summary', 'general').
        messages: List of ChatMessage turns.
        context: Optional Phase 4 ContextPackage containing bounded grounding items.
        system_instruction: Optional high-level behavioral boundary.
        provider: Optional explicit provider override ('gemini', 'groq', 'openrouter', 'mock').
        model: Optional explicit model override.
        temperature: Sampling temperature (default 0.2 for grounded generation).
        max_tokens: Maximum tokens in response.
        metadata: Provider-neutral metadata/options.
        attachments: Optional active query attachments passed to the gateway.
    """
    task: str = "general"
    messages: List[ChatMessage] = field(default_factory=list)
    context: Optional[ContextPackage] = None
    system_instruction: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1024
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments: List[AttachmentData] = field(default_factory=list)

    def __post_init__(self):
        if self.system_instruction is not None and not isinstance(self.system_instruction, str):
            raise TypeError("system_instruction must be a string or None.")
        if self.temperature < 0.0 or self.temperature > 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {self.temperature}")
        if self.max_tokens is None:
            self.max_tokens = 1024
        elif self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")
        # Ensure messages is a list
        if not isinstance(self.messages, list):
            raise TypeError("messages must be a list of ChatMessage instances")


@dataclass
class LLMResponse:
    """
    Normalized, provider-independent response returned by the AI Gateway.

    Attributes:
        content: Generated text response.
        provider: Identifier of provider that fulfilled request ('gemini', 'groq', etc.).
        model: Exact model name used for generation.
        finish_reason: Normalized completion reason ('stop', 'length', 'content_filter', etc.).
        prompt_tokens: Number of prompt tokens if reported by provider.
        completion_tokens: Number of output tokens if reported by provider.
        total_tokens: Total tokens if reported by provider.
        citations: Grounded citation labels (preserved from ContextPackage and/or provider).
        metadata: Provider-specific normalized metadata (latency, raw headers, etc.).
    """
    content: str
    provider: str
    model: str
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    citations: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        from pwanimate.ai.completion import normalize_finish_reason
        self.finish_reason = normalize_finish_reason(self.finish_reason)

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to clean JSON-serializable dictionary."""
        return {
            "content": self.content,
            "provider": self.provider,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "citations": list(self.citations),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GenerationPolicy:
    """
    Task-aware generation policy specifying output token budget and sampling parameters.
    """
    max_output_tokens: int
    temperature: float = 0.2


# Initial task policies:
# - conversational: greetings, short orientation (512 tokens)
# - tool: presentation of deterministic domain tool records (1024 tokens)
# - rag: academic tutoring, conceptual explanation, grounding citations (4096 tokens)
DEFAULT_TASK_POLICIES: Dict[str, GenerationPolicy] = {
    "conversational": GenerationPolicy(max_output_tokens=512, temperature=0.2),
    "general": GenerationPolicy(max_output_tokens=512, temperature=0.2),
    "tool": GenerationPolicy(max_output_tokens=1024, temperature=0.2),
    "rag": GenerationPolicy(max_output_tokens=4096, temperature=0.2),
}

DEFAULT_GENERATION_POLICY = GenerationPolicy(max_output_tokens=1024, temperature=0.2)


def get_task_policy(task: str) -> GenerationPolicy:
    """Resolve generation policy for a given task, falling back to safe 1024 default."""
    if not task:
        return DEFAULT_GENERATION_POLICY
    return DEFAULT_TASK_POLICIES.get(task.strip().lower(), DEFAULT_GENERATION_POLICY)
