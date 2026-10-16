import asyncio
import json
import re

from nonebot import get_driver, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot_plugin_uninfo import ADMIN, SupportScope, Uninfo

from ..config import pconfig

_DISABLED_GROUPS_PATH = pconfig.data_dir / "disabled_groups.json"
_DISABLED_GROUPS_SET: set[str] = set()
_DISABLED_GROUPS_LOCK = asyncio.Lock()


async def _write_disabled_groups(groups: set[str]) -> None:
    temp_path = _DISABLED_GROUPS_PATH.with_suffix(".json.tmp")
    try:
        await temp_path.write_text(
            json.dumps(sorted(groups), ensure_ascii=False),
            encoding="utf-8",
        )
        await temp_path.replace(_DISABLED_GROUPS_PATH)
    finally:
        await temp_path.unlink(missing_ok=True)


async def load_or_initialize_set() -> set[str]:
    """加载或初始化关闭解析的名单"""
    if not await _DISABLED_GROUPS_PATH.exists():
        await _write_disabled_groups(_DISABLED_GROUPS_SET)
    return set(json.loads(await _DISABLED_GROUPS_PATH.read_text(encoding="utf-8")))


async def save_disabled_groups():
    """保存关闭解析的名单"""
    async with _DISABLED_GROUPS_LOCK:
        await _write_disabled_groups(_DISABLED_GROUPS_SET)


async def set_group_enabled(group_key: str, enabled: bool) -> bool:
    """原子更新群聊开关，返回状态是否发生变化。"""
    async with _DISABLED_GROUPS_LOCK:
        was_disabled = group_key in _DISABLED_GROUPS_SET
        updated = _DISABLED_GROUPS_SET.copy()
        if enabled:
            updated.discard(group_key)
        else:
            updated.add(group_key)
        changed = was_disabled == enabled
        if changed:
            await _write_disabled_groups(updated)
            _DISABLED_GROUPS_SET.clear()
            _DISABLED_GROUPS_SET.update(updated)
        return changed


@get_driver().on_startup
async def init_disable_groups():
    """初始化关闭解析的名单"""
    global _DISABLED_GROUPS_SET
    _DISABLED_GROUPS_SET = await load_or_initialize_set()


# Rule
def is_enabled(session: Uninfo) -> bool:
    if f"{session.scope}_{session.user.id}" in pconfig.blacklist_users:
        # 黑名单用户，直接禁用
        return False
    if session.scene.is_private:
        return True
    # 群聊：看这个群 key 是否在关闭列表里
    return f"{session.scope}_{session.scene_path}" not in _DISABLED_GROUPS_SET


async def _toggle_group(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    session: Uninfo,
    argument: Message,
    *,
    enabled: bool,
) -> None:
    target = argument.extract_plain_text().strip()
    if target:
        if not await SUPERUSER(bot, event):
            await matcher.finish(
                "仅超级管理员可以指定群号；群主和管理员请在目标群内使用不带群号的指令"
            )
        if session.scope != SupportScope.qq_client:
            await matcher.finish(
                "指定群号仅支持 QQ 群聊；其他平台请在目标会话使用不带群号的指令"
            )
        if re.fullmatch(r"[1-9][0-9]{0,19}", target) is None:
            await matcher.finish("群号格式错误，请在指令后填写一个正整数群号")
        group_key = f"{session.scope}_{target}"
        prefix = f"群 {target} 的"
    else:
        if session.scene.is_private:
            await matcher.finish("私聊时请在指令后指定群号，仅超级管理员可用")
        group_key = f"{session.scope}_{session.scene_path}"
        prefix = ""
    changed = await set_group_enabled(group_key, enabled=enabled)
    action = "开启" if enabled else "关闭"
    message = f"{prefix}解析已{action}"
    if not changed:
        message += f"，无需重复{action}"
    await matcher.finish(message)


@on_command(
    "开启解析", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True
).handle()
async def handle_enable(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    session: Uninfo,
    argument: Message = CommandArg(),
):
    """开启当前群解析；超级管理员可指定其他 QQ 群。"""
    await _toggle_group(matcher, bot, event, session, argument, enabled=True)


@on_command(
    "关闭解析", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True
).handle()
async def handle_disable(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    session: Uninfo,
    argument: Message = CommandArg(),
):
    """关闭当前群解析；超级管理员可指定其他 QQ 群。"""
    await _toggle_group(matcher, bot, event, session, argument, enabled=False)
