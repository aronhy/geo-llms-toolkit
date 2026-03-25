"""Phase 3 adapter contract for geo-llms-toolkit."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AdapterSiteIdentity:
    name: str
    url: str
    locale: str = ""


@dataclass
class AdapterPage:
    url: str
    group: str
    title: str = ""
    meta: Dict[str, object] = field(default_factory=dict)


@dataclass
class AdapterFetchOptions:
    timeout: int = 12
    user_agent: str = ""
    max_bytes: int = 1_000_000


@dataclass
class AdapterHttpResponse:
    url: str
    final_url: str
    status: int
    headers: Dict[str, str]
    body: str
    error: str = ""


@dataclass
class AdapterActionResult:
    ok: bool
    detail: str = ""
    meta: Dict[str, object] = field(default_factory=dict)


class GeoAdapterContract(ABC):
    """Minimal adapter contract for multi-platform migration (Phase 3)."""

    @abstractmethod
    def get_site_identity(self) -> AdapterSiteIdentity:
        """Return basic site identity (name/url/locale)."""

    @abstractmethod
    def fetch(self, url: str, options: Optional[AdapterFetchOptions] = None) -> AdapterHttpResponse:
        """Fetch a URL and return normalized HTTP response."""

    @abstractmethod
    def list_high_value_pages(self, limit: int) -> List[AdapterPage]:
        """List high-value pages that should be included in llms/index pools."""

    @abstractmethod
    def list_low_value_pages(self, limit: int) -> List[AdapterPage]:
        """List low-value pages for noindex/diagnostic checks."""

    @abstractmethod
    def write_index_files(self, llms_text: str, llms_full_text: str) -> AdapterActionResult:
        """Persist llms artifacts in adapter-specific storage."""

    @abstractmethod
    def send_notification(self, payload: Dict[str, object]) -> AdapterActionResult:
        """Send notification via adapter-specific transport (email/webhook/etc.)."""

    @abstractmethod
    def purge_cache(self, context: Dict[str, object]) -> AdapterActionResult:
        """Purge adapter-side cache/CDN based on provided context."""

