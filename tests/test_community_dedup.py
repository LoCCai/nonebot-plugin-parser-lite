import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from nonebot.matcher import current_event
from nonebot_plugin_uninfo import Scene, SceneType, Session, SupportScope, User
import pytest
from render_test_support import ROOT  # noqa: F401

from nonebot_plugin_parser_lite import matchers
from nonebot_plugin_parser_lite.config import Config
from nonebot_plugin_parser_lite.matchers import community_dedup as cooldown
from nonebot_plugin_parser_lite.matchers.rule import SearchResult
from nonebot_plugin_parser_lite.parsers.ds import DsParser
from nonebot_plugin_parser_lite.parsers.taptap import TapTapParser


def session(target="20001", user="10001", *, private=False):
    return Session(
        self_id="30001", adapter="OneBot V11", scope=SupportScope.qq_client,
        scene=Scene(target, SceneType.PRIVATE if private else SceneType.GROUP),
        user=User(user),
    )


def search(url):
    parser = TapTapParser if "taptap.cn" in url else DsParser
    keyword, searched = parser.search_url(url)
    return SearchResult(url, keyword, searched)


@pytest.fixture
def pipeline(monkeypatch):
    parsed = AsyncMock(return_value=object())
    rendered = AsyncMock(return_value="summary")
    sent = AsyncMock()
    reactions = AsyncMock()

    async def content(*_args, **_kwargs):
        yield "body"

    monkeypatch.setattr(matchers, "community_parse_dedup", cooldown.CommunityParseDeduplicator())
    monkeypatch.setattr(matchers, "_get_or_parse_result", parsed)
    monkeypatch.setattr(matchers.RENDERER, "render_messages", rendered)
    monkeypatch.setattr(matchers.RENDERER, "send_content", content)
    monkeypatch.setattr(matchers, "send_with_media_fallback", sent)
    monkeypatch.setattr(matchers, "apply_media_speedup", Mock())
    monkeypatch.setattr(matchers.UniHelper, "message_reaction", reactions)
    monkeypatch.setattr(matchers.pconfig, "plite_lazy_download", False)
    monkeypatch.setattr(matchers.pconfig, "plite_summary_in_forward", False)
    monkeypatch.setattr(matchers.pconfig, "plite_community_parse_dedup_minutes", 30)
    token = current_event.set(object())
    yield SimpleNamespace(parsed=parsed, rendered=rendered, sent=sent, reactions=reactions)
    current_event.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", [
    ("https://www.taptap.cn/moment/123?share=first", "https://www.taptap.cn/explore/123?share=second"),
    ("https://ds.163.com/feed/abc123?share=first", "https://ds.163.com/article/abc123?share=second"),
])
async def test_same_group_members_and_url_variants_share_one_window(pipeline, first, second):
    await matchers.parser_handler(session(), search(first))
    await matchers.parser_handler(session(user="10002"), search(second))
    pipeline.parsed.assert_awaited_once()
    pipeline.rendered.assert_awaited_once()
    assert pipeline.sent.await_count == 2
    assert pipeline.reactions.await_count == 2
    matchers.apply_media_speedup.assert_called_once()
    await matchers.parser_handler(session("20002"), search(second))
    await matchers.parser_handler(session("10001", private=True), search(second))
    await matchers.parser_handler(session("10002", user="10002", private=True), search(second))
    assert pipeline.parsed.await_count == 4


@pytest.mark.asyncio
async def test_inflight_duplicates_are_silent_and_cancellation_allows_retry(pipeline):
    started, finish = asyncio.Event(), asyncio.Event()

    async def parse(_sr):
        started.set()
        await finish.wait()
        return object()

    pipeline.parsed.side_effect = parse
    sr = search("https://www.taptap.cn/moment/123")
    first = asyncio.create_task(matchers.parser_handler(session(), sr))
    await started.wait()
    await asyncio.gather(*(matchers.parser_handler(session(user=str(i)), sr) for i in range(20)))
    assert pipeline.parsed.await_count == 1
    assert pipeline.reactions.await_count == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    finish.set()
    await matchers.parser_handler(session(), sr)
    await matchers.parser_handler(session(), sr)
    assert pipeline.parsed.await_count == 2


@pytest.mark.asyncio
async def test_send_failure_does_not_start_success_window(pipeline):
    sr = search("https://www.taptap.cn/moment/123")
    pipeline.sent.side_effect = RuntimeError("send failed")
    with pytest.raises(RuntimeError):
        await matchers.parser_handler(session(), sr)
    pipeline.sent.side_effect = None
    await matchers.parser_handler(session(), sr)
    await matchers.parser_handler(session(), sr)
    assert pipeline.parsed.await_count == 2


def test_expiry_is_fixed_from_success_and_cache_is_bounded(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cooldown, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    guard = cooldown.CommunityParseDeduplicator(max_entries=2)
    with guard.acquire("post") as attempt:
        clock[0] = 200
        attempt.succeeded = True
    clock[0] = 1999
    with guard.acquire("post") as duplicate:
        assert duplicate is None
    clock[0] = 2000
    with guard.acquire("post") as retry:
        assert retry is not None
    for key in range(3):
        with guard.acquire(key) as attempt:
            attempt.succeeded = True
    assert len(guard._recent) == 2
    clock[0] += 1800
    with guard.acquire("new"):
        assert not guard._recent


def test_other_platforms_are_outside_community_cooldown():
    sr = SimpleNamespace(keyword="www.bilibili.com")
    assert cooldown.community_parse_key("QQClient_GROUP_1", sr) is None


@pytest.mark.asyncio
async def test_configured_minutes_control_actual_matcher_cooldown(pipeline, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cooldown, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(matchers.pconfig, "plite_community_parse_dedup_minutes", 2)
    sr = search("https://www.taptap.cn/moment/123")
    await matchers.parser_handler(session(), sr)
    clock[0] = 219
    await matchers.parser_handler(session(), sr)
    pipeline.parsed.assert_awaited_once()
    clock[0] = 220
    await matchers.parser_handler(session(), sr)
    assert pipeline.parsed.await_count == 2


@pytest.mark.asyncio
async def test_zero_minutes_allows_completed_repeats(pipeline, monkeypatch):
    monkeypatch.setattr(matchers.pconfig, "plite_community_parse_dedup_minutes", 0)
    sr = search("https://ds.163.com/feed/abc123")
    await matchers.parser_handler(session(), sr)
    await matchers.parser_handler(session(), sr)
    assert pipeline.parsed.await_count == 2


def test_config_default_and_validated_range():
    from pydantic import ValidationError

    assert Config().community_parse_dedup_minutes == 30
    assert Config(plite_community_parse_dedup_minutes="15").community_parse_dedup_minutes == 15
    for value in (-1, 10081, 1.5, "invalid"):
        with pytest.raises(ValidationError):
            Config(plite_community_parse_dedup_minutes=value)
