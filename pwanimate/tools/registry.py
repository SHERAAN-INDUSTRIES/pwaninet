"""
Tool Registry for Pwanimate Domain Tools.

Manages tool lifecycle, discovery, schema export, and execution dispatch.
"""

from typing import Any, Dict, List, Optional
import logging

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import (
    ToolError,
    ToolNotFoundError,
    ToolValidationError,
    ToolPermissionError,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    In-memory registry of available domain tools.
    """

    def __init__(self):
        self._tools: Dict[str, BaseDomainTool] = {}

    def register(self, tool: BaseDomainTool) -> None:
        """Register a domain tool instance."""
        if not isinstance(tool, BaseDomainTool):
            raise TypeError(f"Expected BaseDomainTool, got {type(tool)}")
        if not tool.name:
            raise ValueError("Tool name must not be empty")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseDomainTool:
        """
        Retrieve a registered tool by name.
        Raises ToolNotFoundError if not registered.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.", tool_name=name)
        return self._tools[name]

    def list_tools(self) -> List[BaseDomainTool]:
        """Return a list of all registered tool instances."""
        return list(self._tools.values())

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Return JSON schema metadata for all registered tools."""
        return [tool.to_dict() for tool in self._tools.values()]

    def execute(self, name: str, user: Any, user_context: Optional[Any] = None, **kwargs) -> ToolResult:
        """
        Dispatch execution to a named tool with parameter validation and error handling.
        """
        tool = self.get(name)
        try:
            validated = tool.validate_parameters(**kwargs)
            if user_context is not None:
                validated["user_context"] = user_context
            return tool.execute(user=user, **validated)
        except ToolPermissionError as exc:
            logger.warning("Tool permission denied: %s", exc)
            return ToolResult.fail(str(exc), tool_name=name, error_type="permission_denied")
        except ToolValidationError as exc:
            logger.warning("Tool parameter validation failed: %s", exc)
            return ToolResult.fail(str(exc), tool_name=name, error_type="validation_error")
        except ToolError as exc:
            logger.error("Tool execution error: %s", exc, exc_info=True)
            return ToolResult.fail(str(exc), tool_name=name, error_type="tool_error")
        except Exception as exc:
            logger.exception("Unexpected error executing tool '%s': %s", name, exc)
            return ToolResult.fail(
                f"Unexpected error executing tool '{name}': {str(exc)}",
                tool_name=name,
                error_type="unhandled_exception",
            )


_default_registry: Optional[ToolRegistry] = None


def get_default_tool_registry() -> ToolRegistry:
    """
    Factory providing the standard Pwanimate domain tool registry.
    """
    global _default_registry
    if _default_registry is None:
        registry = ToolRegistry()
        from pwanimate.tools.domain import (
            AcademicLookupTool,
            StudentTimetableTool,
            DocumentDetailTool,
            GroupAnnouncementsTool,
            UserProfileTool,
            NotificationSummaryTool,
            PeopleDiscoveryTool,
            LocalTimeTool, ContentSearchTool, SendNotificationTool,
            WebSearchTool,
            SiteInfoTool,
            SendMessageTool, FollowUserTool,
        )
        registry.register(AcademicLookupTool())
        registry.register(StudentTimetableTool())
        registry.register(DocumentDetailTool())
        registry.register(GroupAnnouncementsTool())
        registry.register(UserProfileTool())
        registry.register(NotificationSummaryTool())
        registry.register(PeopleDiscoveryTool())
        registry.register(LocalTimeTool())
        registry.register(ContentSearchTool())
        registry.register(SendNotificationTool())
        registry.register(WebSearchTool())
        registry.register(SiteInfoTool())
        # Write actions. These only propose; the student confirms.
        registry.register(SendMessageTool())
        registry.register(FollowUserTool())
        _default_registry = registry
    return _default_registry
