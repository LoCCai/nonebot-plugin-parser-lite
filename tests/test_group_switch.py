from types import SimpleNamespace
from unittest.mock import AsyncMock

from anyio import Path
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.exception import FinishedException
from nonebot.internal.params import DependencyCache
from nonebot.matcher import matchers
from nonebot_plugin_uninfo import (
    Member,
    Role,
    Scene,
    SceneType,
    Session,
    SupportScope,
    User,
    get_session,
)
import pytest
from render_test_support import pconfig

from nonebot_plugin_parser_lite.matchers import filter as switches

# Synthetic identifiers for isolated tests, unrelated to production accounts.
ADMIN_QQ = "10001"
CURRENT_GROUP = "20001"
TARGET_GROUP = "20002"
BOT_QQ = "30001"


@pytest.fixture
def switch_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        switches, "_DISABLED_GROUPS_PATH", Path(tmp_path / "disabled.json")
    )
    monkeypatch.setattr(switches, "_DISABLED_GROUPS_SET", set())
    monkeypatch.setattr(pconfig, "plite_blacklist_users", [])


def context(*, superuser=True, private=False, scope=SupportScope.qq_client):
    # Use the real NoneBot permission checker and adapter event types. No API
    # call is needed to change a local parsing rule.
    bot = Bot(
        SimpleNamespace(
            get_name=lambda: "OneBot V11",
            config=SimpleNamespace(superusers={ADMIN_QQ} if superuser else set()),
        ),
        BOT_QQ,
    )
    event = GroupMessageEvent.model_validate(
        {
            "time": 1,
            "self_id": int(BOT_QQ),
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "group_id": int(CURRENT_GROUP),
            "user_id": int(ADMIN_QQ),
            "message_id": 1,
            "font": 0,
            "message": [],
            "original_message": [],
            "raw_message": "",
            "to_me": True,
            "sender": {"user_id": int(ADMIN_QQ), "role": "admin"},
        }
    )
    session = Session(
        self_id=bot.self_id,
        adapter="OneBot V11",
        scope=scope,
        scene=Scene(
            ADMIN_QQ if private else CURRENT_GROUP,
            SceneType.PRIVATE if private else SceneType.GROUP,
        ),
        user=User(ADMIN_QQ),
    )
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    return matcher, bot, event, session


async def run_switch(argument="", *, enabled=False, **kwargs):
    matcher, bot, event, session = context(**kwargs)
    handler = switches.handle_enable if enabled else switches.handle_disable
    with pytest.raises(FinishedException):
        await handler(matcher, bot, event, session, Message(argument))
    return matcher.finish.call_args.args[0], session


@pytest.mark.asyncio
async def test_superuser_cross_group_persists_and_leaves_source_group_enabled(
    switch_state,
):
    message, source = await run_switch(f"  {TARGET_GROUP}  ")
    assert TARGET_GROUP in message
    assert "已关闭" in message
    target = context()[3]
    target.scene.id = TARGET_GROUP
    assert switches.is_enabled(source)
    assert not switches.is_enabled(target)
    switches._DISABLED_GROUPS_SET.clear()
    await switches.init_disable_groups()
    assert not switches.is_enabled(target)
    assert switches.is_enabled(source)
    message, _ = await run_switch(TARGET_GROUP)
    assert "无需重复关闭" in message


@pytest.mark.asyncio
async def test_group_admin_cannot_supply_target_even_for_current_group(switch_state):
    # Handler validation; registered command permissions are checked separately.
    for target in (TARGET_GROUP, CURRENT_GROUP):
        message, _ = await run_switch(target, superuser=False)
        assert "仅超级管理员" in message
    assert switches._DISABLED_GROUPS_SET == set()
    assert not await switches._DISABLED_GROUPS_PATH.exists()
    message, source = await run_switch(superuser=False)
    assert message == "解析已关闭"
    assert not switches.is_enabled(source)
    message, _ = await run_switch(enabled=True, superuser=False)
    assert message == "解析已开启"
    assert switches.is_enabled(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [switches.handle_enable, switches.handle_disable])
@pytest.mark.parametrize(
    ("superuser", "role", "allowed"),
    [
        (False, "ADMINISTRATOR", True),
        (False, "OWNER", True),
        (False, "MEMBER", False),
        (True, "MEMBER", True),
    ],
)
async def test_registered_command_permission(handler, superuser, role, allowed):
    registered = next(
        matcher
        for group in matchers.values()
        for matcher in group
        if any(dependent.call is handler for dependent in matcher.handlers)
    )
    _, bot, event, session = context(superuser=superuser)
    session.member = Member(session.user, roles=[Role(role)])
    cached_session = DependencyCache()
    cached_session.set_result(session)
    assert (
        await registered.permission(
            bot, event, dependency_cache={get_session: cached_session}
        )
        is allowed
    )


@pytest.mark.asyncio
async def test_enable_target_only_and_prefixed_superuser_identity(switch_state):
    matcher, bot, event, session = context()
    bot.config.superusers = {f"onebot:{ADMIN_QQ}"}
    switches._DISABLED_GROUPS_SET.update(
        {f"QQClient_{CURRENT_GROUP}", f"QQClient_{TARGET_GROUP}"}
    )
    with pytest.raises(FinishedException):
        await switches.handle_enable(
            matcher, bot, event, session, Message(TARGET_GROUP)
        )
    assert switches._DISABLED_GROUPS_SET == {f"QQClient_{CURRENT_GROUP}"}
    assert TARGET_GROUP in matcher.finish.call_args.args[0]


@pytest.mark.asyncio
async def test_private_superuser_must_supply_group_and_can_control_it(switch_state):
    message, _ = await run_switch(private=True)
    assert "请在指令后指定群号" in message
    assert not switches._DISABLED_GROUPS_SET
    message, _ = await run_switch(TARGET_GROUP, private=True)
    assert TARGET_GROUP in message
    assert switches._DISABLED_GROUPS_SET == {f"QQClient_{TARGET_GROUP}"}
    await run_switch(TARGET_GROUP, private=True, enabled=True)
    assert not switches._DISABLED_GROUPS_SET


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argument", ["0", "-1", "01", "1 2", "1abc", "１２３", "1" * 21]
)
async def test_invalid_target_never_changes_current_group(switch_state, argument):
    message, _ = await run_switch(argument)
    assert "群号格式错误" in message
    assert not switches._DISABLED_GROUPS_SET
    assert not await switches._DISABLED_GROUPS_PATH.exists()


@pytest.mark.asyncio
async def test_scope_isolation_and_other_platform_current_group(switch_state):
    message, _ = await run_switch(TARGET_GROUP, scope=SupportScope.discord)
    assert "指定群号仅支持 QQ" in message
    assert not switches._DISABLED_GROUPS_SET
    await run_switch(scope=SupportScope.discord)
    assert switches._DISABLED_GROUPS_SET == {f"Discord_{CURRENT_GROUP}"}
    assert not switches.is_enabled(context(scope=SupportScope.discord)[3])
    assert switches.is_enabled(context()[3])


@pytest.mark.asyncio
async def test_bot_mention_in_command_argument_preserves_target(switch_state):
    # Handler-level argument extraction only; adapter to_me preprocessing is
    # separate, so documented commands put the mention before the command.
    matcher, bot, event, session = context()
    argument = Message(
        [
            MessageSegment.at(bot.self_id),
            MessageSegment.text(f" {TARGET_GROUP}"),
        ]
    )
    with pytest.raises(FinishedException):
        await switches.handle_disable(matcher, bot, event, session, argument)
    assert switches._DISABLED_GROUPS_SET == {f"QQClient_{TARGET_GROUP}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("operation", ["write_text", "replace"])
async def test_failed_save_keeps_memory_and_disk_unchanged_and_can_retry(
    switch_state, monkeypatch, enabled, operation
):
    key = f"QQClient_{TARGET_GROUP}"
    await switches.save_disabled_groups()
    await switches.set_group_enabled(key, enabled=not enabled)
    original = switches._DISABLED_GROUPS_SET.copy()
    saved = await switches._DISABLED_GROUPS_PATH.read_text(encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(Path, operation, AsyncMock(side_effect=OSError("disk failure")))
        with pytest.raises(OSError, match="disk failure"):
            await switches.set_group_enabled(key, enabled=enabled)
    assert switches._DISABLED_GROUPS_SET == original
    assert await switches._DISABLED_GROUPS_PATH.read_text(encoding="utf-8") == saved
    assert not await switches._DISABLED_GROUPS_PATH.with_suffix(".json.tmp").exists()
    assert await switches.set_group_enabled(key, enabled=enabled)
    switches._DISABLED_GROUPS_SET.clear()
    await switches.init_disable_groups()
    assert (key not in switches._DISABLED_GROUPS_SET) == enabled
