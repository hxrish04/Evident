"""
Shared access-policy, throttling, and safe outbound request utilities.
The rule here is simple: behave like a respectful public-data client, not a stealth crawler trying to squeeze past boundaries.
"""

from __future__ import annotations

import ipaddress
import os
import random
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx


DEFAULT_USER_AGENT = "AI-Outreach-Agent/1.0"
DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_REDIRECTS = 3
MAX_REQUESTS_PER_RUN = int(os.getenv("MAX_REQUESTS_PER_RUN", "40"))
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "20"))
PER_DOMAIN_COOLDOWN_SECONDS = float(os.getenv("PER_DOMAIN_COOLDOWN_SECONDS", "0.15"))
ALLOW_PRIVATE_TARGETS = os.getenv("ALLOW_PRIVATE_TARGETS", "").lower() in {"1", "true", "yes"}
BLOCK_PATTERNS = [
    (re.compile(r"captcha|verify you are human|are you human", re.I), "challenge_detected"),
    (re.compile(r"attention required|access denied|forbidden", re.I), "forbidden"),
    (re.compile(r"rate limit|too many requests|slow down", re.I), "rate_limited"),
]
_DOMAIN_LOCK = threading.Lock()
_DOMAIN_STATE: dict[str, dict[str, float]] = {}


def domain_from_url(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def same_domain(candidate: str, allowed_domain: str | None) -> bool:
    if not allowed_domain:
        return True
    host = domain_from_url(candidate)
    allowed = allowed_domain.lower()
    return host == allowed or host.endswith(f".{allowed}")


def resolve_ip_addresses(hostname: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return []
    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        address_text = info[4][0]
        try:
            addresses.append(ipaddress.ip_address(address_text))
        except ValueError:
            continue
    return addresses


def is_private_or_local_host(hostname: str) -> bool:
    lowered = hostname.lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return True
    addresses = resolve_ip_addresses(lowered)
    if not addresses:
        return False
    return any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        for address in addresses
    )


def normalize_public_url(url: str, *, allowed_domain: str | None = None, allow_private: bool = False) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are allowed.")
    if not parsed.netloc:
        raise ValueError("URL must include a hostname.")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("URL hostname is missing.")
    if not allow_private and not ALLOW_PRIVATE_TARGETS and is_private_or_local_host(hostname):
        raise ValueError("Private, localhost, and internal network targets are blocked.")
    normalized = parsed._replace(fragment="").geturl()
    if allowed_domain and not same_domain(normalized, allowed_domain):
        raise ValueError(f"Outbound fetch blocked outside allowed domain: {hostname}")
    return normalized


def robots_url_for(target_url: str) -> str:
    parsed = urlparse(target_url)
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"


def check_robots_policy(target_url: str, user_agent: str = DEFAULT_USER_AGENT) -> dict[str, Any]:
    robots_url = robots_url_for(target_url)
    notes: list[str] = []
    try:
        response = httpx.get(
            robots_url,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
    except Exception as exc:
        return {
            "robots_present": False,
            "path_allowed": "unknown",
            "notes": f"robots.txt could not be checked: {exc}",
            "robots_url": robots_url,
        }

    if response.status_code == 404:
        return {
            "robots_present": False,
            "path_allowed": "unknown",
            "notes": "robots.txt not found",
            "robots_url": robots_url,
        }

    if response.status_code >= 400:
        return {
            "robots_present": False,
            "path_allowed": "unknown",
            "notes": f"robots.txt check returned HTTP {response.status_code}",
            "robots_url": robots_url,
        }

    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(response.text.splitlines())
    allowed = parser.can_fetch(user_agent, target_url)
    notes.append("robots.txt found")
    notes.append("requested path not explicitly disallowed" if allowed else "requested path appears disallowed")
    return {
        "robots_present": True,
        "path_allowed": allowed,
        "notes": "; ".join(notes),
        "robots_url": robots_url,
    }


def detect_block_reason(status_code: int | None, text: str = "", error_text: str = "") -> str | None:
    if status_code == 403:
        return "forbidden"
    if status_code == 429:
        return "rate_limited"
    haystack = f"{text} {error_text}".lower()
    for pattern, reason in BLOCK_PATTERNS:
        if pattern.search(haystack):
            return reason
    if status_code and status_code >= 500:
        return "render_failed"
    if text is not None and len(text.strip()) < 80:
        return "low_content"
    return None


@dataclass
class RunAccessTracker:
    target_domain: str
    requests_attempted: int = 0
    blocked_responses_count: int = 0
    throttled_delays_count: int = 0
    domains_skipped_due_policy_or_block: int = 0
    requests_blocked_by_policy: int = 0
    stop_reason: str = ""
    _blocked_domains: set[str] = field(default_factory=set)
    _seen_domains: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note_attempt(self, domain: str) -> bool:
        with self._lock:
            if self.requests_attempted >= MAX_REQUESTS_PER_RUN:
                return False
            self.requests_attempted += 1
            self._seen_domains.add(domain)
            return True

    def note_throttle(self) -> None:
        with self._lock:
            self.throttled_delays_count += 1

    def note_blocked(self, domain: str, reason: str) -> None:
        with self._lock:
            self.blocked_responses_count += 1
            self._blocked_domains.add(domain)
            if not self.stop_reason:
                self.stop_reason = reason

    def note_policy_skip(self, domain: str, reason: str) -> None:
        with self._lock:
            self.requests_blocked_by_policy += 1
            self.domains_skipped_due_policy_or_block += 1
            self._blocked_domains.add(domain)
            if not self.stop_reason:
                self.stop_reason = reason

    def should_stop(self) -> bool:
        with self._lock:
            return (
                self.requests_attempted >= MAX_REQUESTS_PER_RUN
                or self.blocked_responses_count >= 2
                or self.requests_blocked_by_policy >= 1
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests_attempted": self.requests_attempted,
                "blocked_responses_count": self.blocked_responses_count,
                "throttled_delays_count": self.throttled_delays_count,
                "domains_skipped_due_policy_or_block": self.domains_skipped_due_policy_or_block,
                "requests_blocked_by_policy": self.requests_blocked_by_policy,
                "stopped_due_to_access": bool(self.stop_reason),
                "access_stop_reason": self.stop_reason,
            }


def throttle_domain_requests(url: str, tracker: RunAccessTracker | None = None) -> None:
    domain = domain_from_url(url)
    if not domain:
        return
    with _DOMAIN_LOCK:
        state = _DOMAIN_STATE.setdefault(domain, {"last_request_at": 0.0, "recent_count": 0.0, "window_start": time.time()})
        now = time.time()
        if now - state["window_start"] >= 60:
            state["window_start"] = now
            state["recent_count"] = 0
        if state["recent_count"] >= MAX_REQUESTS_PER_MINUTE:
            sleep_for = min(5.0, 60 - (now - state["window_start"]))
            if sleep_for > 0:
                if tracker:
                    tracker.note_throttle()
                time.sleep(sleep_for)
                now = time.time()
                state["window_start"] = now
                state["recent_count"] = 0
        elapsed = now - state["last_request_at"]
        minimum_gap = PER_DOMAIN_COOLDOWN_SECONDS + random.uniform(0.02, 0.08)
        if elapsed < minimum_gap:
            if tracker:
                tracker.note_throttle()
            time.sleep(minimum_gap - elapsed)
        state["last_request_at"] = time.time()
        state["recent_count"] += 1
