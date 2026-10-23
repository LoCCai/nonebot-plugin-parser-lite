"""Bounded per-target cooldown for repeated community post parsing."""

from collections import OrderedDict
from collections.abc import Hashable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import time


@dataclass(slots=True)
class ParseAttempt:
    succeeded: bool = False


class CommunityParseDeduplicator:
    def __init__(self, *, window_seconds: float = 1800, max_entries: int = 8192):
        self.window_seconds = window_seconds
        self.max_entries = max_entries
        self._recent: OrderedDict[Hashable, float] = OrderedDict()
        self._active: set[Hashable] = set()

    @contextmanager
    def acquire(self, key: Hashable) -> Iterator[ParseAttempt | None]:
        now = time.monotonic()
        while self._recent and next(iter(self._recent.values())) + self.window_seconds <= now:
            self._recent.popitem(last=False)
        if key in self._active or key in self._recent:
            yield None
            return

        self._active.add(key)
        attempt = ParseAttempt()
        try:
            yield attempt
            if attempt.succeeded and self.window_seconds > 0:
                self._recent[key] = time.monotonic()
                while len(self._recent) > self.max_entries:
                    self._recent.popitem(last=False)
        finally:
            self._active.discard(key)


community_parse_dedup = CommunityParseDeduplicator()


def community_parse_key(target_key: str, search_result) -> tuple[str, ...] | None:
    fields = {
        "www.taptap.cn": ("taptap", "moment_id"),
        "ds.163.com": ("ds", "feed_id"),
    }
    source_field = fields.get(search_result.keyword.lower())
    if source_field is None:
        return None
    source, field = source_field
    content_id = search_result.searched.get(field)
    if not content_id:
        return None
    # Canonical IDs fold moment/explore and feed/article URL variants.
    return source, target_key, str(content_id)
