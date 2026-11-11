"""
Site Content Service for Pwanimate.

Loads and caches vasto/Pwanimate site content from markdown files
so Pwanimate can answer questions about the platform itself.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_CONTENT_DIR = Path(__file__).parent.parent / "site_content"
_cache: Dict[str, str] = {}


class SiteContentService:
    """Loads and caches vasto site content markdown files."""

    @classmethod
    def _ensure_loaded(cls) -> None:
        if _cache:
            return
        if not _CONTENT_DIR.exists():
            logger.warning("Site content directory not found: %s", _CONTENT_DIR)
            return
        for md_file in _CONTENT_DIR.glob("*.md"):
            try:
                _cache[md_file.stem] = md_file.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not load site content file %s: %s", md_file.name, exc)

    @classmethod
    def get_content(cls, topic: str) -> Optional[str]:
        """Get content by topic. Tries exact match first, then partial."""
        cls._ensure_loaded()
        topic = topic.lower().strip()
        topic = {'spark':'pwanimate','vasto':'about','fasto':'about','pwaninet':'about',
                 'founder':'about','creator':'about','features':'pwanimate'}.get(topic,topic)
        if not topic:
            return None
        if topic in _cache:
            return _cache[topic]
        for key, content in _cache.items():
            if topic in key or key in topic:
                return content
        return None

    @classmethod
    def get_available_topics(cls) -> List[str]:
        """Return list of available topic keys."""
        cls._ensure_loaded()
        return list(_cache.keys())

    @classmethod
    def get_all_content(cls) -> Dict[str, str]:
        """Return all cached content."""
        cls._ensure_loaded()
        return dict(_cache)
