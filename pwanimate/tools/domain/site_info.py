"""
Site Info Domain Tool for Pwanimate.

Allows Pwanimate to answer questions about vasto and itself
by loading curated site content from markdown files.
"""

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import ToolValidationError
from pwanimate.services.site_content import SiteContentService


class SiteInfoTool(BaseDomainTool):
    name = "site_info"
    description = "Read official first-party information about Vasto (formerly Pwaninet), Spark (formerly Pwanimate), platform creator and capabilities. Prefer this source for platform identity questions."
    parameters_schema = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The topic to get information about Use 'about' for Vasto history/creator; use 'pwanimate' for Spark identity/capabilities. 'spark' is an alias.",
            },
        },
        "required": ["topic"],
    }

    def execute(self, user, topic, **kwargs):
        topic = str(topic).strip().lower()
        if not topic:
            raise ToolValidationError("Topic is required.", tool_name=self.name)

        content = SiteContentService.get_content(topic)
        if content:
            return ToolResult.ok({
                "topic": topic,
                "content": content,
                "source": "site_content",
            })

        available = SiteContentService.get_available_topics()
        return ToolResult.ok({
            "topic": topic,
            "content": None,
            "available_topics": available,
            "source": "site_content",
        })
