import json

import pytest
from render_test_support import ROOT  # noqa: F401

from nonebot_plugin_parser_lite.parsers.rednote.page_state import (
    NotePageUnavailable,
    note_page_state,
)


@pytest.mark.parametrize(("url", "html"), [
    ("https://example.invalid/website-login/captcha", "captcha"),
    ("https://example.invalid/login", "login"),
    ("https://example.invalid/404", "missing"),
    ("https://example.invalid/explore/id", "<html>no data</html>"),
    ("https://example.invalid/explore/id", '<script>window.__INITIAL_STATE__={"noteData":{"data":{}}}</script>'),
])
def test_missing_note_is_reported_before_typed_decoding(url, html):
    with pytest.raises(NotePageUnavailable):
        note_page_state(html, url)


def test_undefined_text_is_preserved_but_missing_values_are_normalized():
    html = '<script>window.__INITIAL_STATE__ = {"noteData":{"data":{"noteData":{"title":"undefined"},"other":undefined}}};</script>'
    value = json.loads(note_page_state(html, "https://example.invalid/explore/id"))
    assert value["noteData"]["data"]["noteData"]["title"] == "undefined"
    assert value["noteData"]["data"]["other"] is None
