import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from render_test_support import ROOT  # noqa: F401

from nonebot_plugin_parser_lite.exception import ParseException
from nonebot_plugin_parser_lite.parsers.kugou import KuGouParser


def share_page(data):
    return "<script>var dataFromSmarty = " + json.dumps(data) + ", other = 1;</script>"


@pytest_asyncio.fixture
async def parser():
    instance = KuGouParser()
    try:
        yield instance
    finally:
        await instance.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    [], [None], ["song"], [1], [[]], [{}], [{"hash": None}], [{"hash": 42}],
    [{"hash": []}], [{"hash": ""}], [{"hash": "  "}],
    [{"hash": None}, {"hash": "another-song"}],
])
async def test_missing_or_malformed_song_never_uses_another_song(parser, data):
    assert parser._extract_hash(share_page(data)) == ""


@pytest.mark.asyncio
async def test_valid_hash_is_trimmed_and_normalized(parser):
    assert parser._extract_hash(share_page([{"hash": " abc123 "}])) == "ABC123"


@pytest.mark.asyncio
@pytest.mark.parametrize("html", ["<html>login required</html>", "var dataFromSmarty = [invalid],"])
async def test_unavailable_or_invalid_page_is_not_a_song(parser, html):
    assert parser._extract_hash(html) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("song_hash", [None, " abc123 "])
async def test_share_page_handles_missing_and_valid_hashes(song_hash):
    parser = KuGouParser()
    original_client = parser.httpx
    try:
        parser.parse_by_hash = AsyncMock(return_value=object())
        response = SimpleNamespace(
            text=share_page([{"hash": song_hash}]), raise_for_status=Mock()
        )
        parser.httpx = SimpleNamespace(get=AsyncMock(return_value=response))
        searched = SimpleNamespace(url="https://www.kugou.com/share/test.html")
        if song_hash is None:
            with pytest.raises(ParseException, match="未返回可用的歌曲信息"):
                await parser._parse_kugou_share(searched)
            parser.parse_by_hash.assert_not_awaited()
        else:
            result = await parser._parse_kugou_share(searched)
            assert result is parser.parse_by_hash.return_value
            parser.parse_by_hash.assert_awaited_once_with("ABC123")
        response.raise_for_status.assert_called_once()
    finally:
        parser.httpx = original_client
        await parser.aclose()
