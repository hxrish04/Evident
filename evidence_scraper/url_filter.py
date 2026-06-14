"""Generic, profile-driven URL filtering.

The filter is deliberately generous: false positives are cheap (the LLM
classification step throws them out), false negatives lose items entirely.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from .profile import Profile

# Always-excluded junk, independent of project.
DEFAULT_EXCLUDE_PATTERNS = [
    r"\.(pdf|jpg|jpeg|png|gif|svg|webp|avif|ico|css|js|mp4|mov|webm|zip|gz|xls|xlsx|doc|docx|ppt|pptx|xml|json|txt)(\?|$)",
    r"/news\b", r"/blog\b", r"/press", r"/article", r"/event",
    r"/career", r"/jobs?\b",
    r"/login", r"/signin", r"/sign-in", r"/register", r"/account",
    r"/cart", r"/checkout", r"/wishlist",
    r"/legal", r"/privacy", r"/terms", r"/cookie", r"/accessibility",
    r"/contact", r"/about-us", r"/sitemap",
    r"/search\?", r"[?&]page=\d{3,}",
]


class UrlFilter:
    """Built once per profile; used by discovery to score/filter URLs."""

    def __init__(self, profile: Profile):
        d = profile.discovery
        self.include_keywords = [k.lower() for k in d.include_keywords]
        self.url_hints = [h.lower() for h in d.url_hints]
        patterns = DEFAULT_EXCLUDE_PATTERNS + list(d.exclude_patterns)
        self.exclude_re = re.compile("|".join(patterns), re.IGNORECASE)

    def is_excluded(self, url: str) -> bool:
        return bool(self.exclude_re.search(url))

    def looks_relevant(self, text: str) -> bool:
        """True if URL or anchor text mentions an include keyword."""
        if not text:
            return False
        t = text.lower().replace("_", "-").replace(" ", "-")
        plain = text.lower()
        return any(k in t or k in plain for k in self.include_keywords)

    def has_url_hint(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(h in path for h in self.url_hints)

    def is_candidate(self, url: str, anchor_text: str = "") -> bool:
        """Generous candidate check for sitemap/crawl discovery."""
        if self.is_excluded(url):
            return False
        if not self.include_keywords and not self.url_hints:
            return True  # profile gave no hints: let the LLM sort it out
        return (
            self.looks_relevant(url)
            or self.looks_relevant(anchor_text)
            or self.has_url_hint(url)
        )


def slug_from_url(url: str) -> Optional[str]:
    """Guess a short item id from the last meaningful path segment."""
    path = urlparse(url).path
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    last = segments[-1]
    last = re.sub(r"\.(html?|php|aspx?)$", "", last, flags=re.IGNORECASE)
    last = re.sub(r"[^A-Za-z0-9\-]+", "-", last).strip("-").lower()
    return last or None
