from dataclasses import dataclass
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


class ComponentType(str, Enum):
    At = "At"
    Plain = "Plain"


@dataclass
class At:
    qq: int | str
    name: str | None = ""
    type = ComponentType.At


@dataclass
class AtAll(At):
    qq: str = "all"


@dataclass
class Plain:
    text: str
    type = ComponentType.Plain


class FakeEvent:
    def __init__(self, messages, sender_id="123456", self_id="100001"):
        self.message_obj = SimpleNamespace(
            message=messages,
            sender=SimpleNamespace(user_id=sender_id, nickname="发送者"),
            self_id=self_id,
        )

    def get_messages(self):
        return self.message_obj.message

    def get_sender_id(self):
        return self.message_obj.sender.user_id

    def get_sender_name(self):
        return self.message_obj.sender.nickname

    def get_self_id(self):
        return self.message_obj.self_id

    def plain_result(self, text):
        return "text", text

    def image_result(self, path):
        return "image", path


def load_plugin():
    # Unit tests use AstrBot's public component shape without starting the bot runtime.
    modules = {
        name: ModuleType(name)
        for name in (
            "astrbot",
            "astrbot.api",
            "astrbot.api.event",
            "astrbot.api.message_components",
            "astrbot.api.star",
        )
    }
    star = modules["astrbot.api.star"]
    class Star:
        def __init__(self, context):
            self.context = context

    star.Star = Star
    star.Context = object
    star.StarTools = SimpleNamespace()
    modules["astrbot.api"].star = star
    modules["astrbot.api"].logger = Mock()
    event_api = modules["astrbot.api.event"]
    event_api.AstrMessageEvent = FakeEvent
    event_api.filter = SimpleNamespace(command=lambda name: lambda handler: handler)
    components = modules["astrbot.api.message_components"]
    components.At = At
    components.AtAll = AtAll
    components.Plain = Plain
    spec = importlib.util.spec_from_file_location("touch_head_under_test", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


plugin_module = load_plugin()
TouchHeadPlugin = plugin_module.TouchHeadPlugin


class PluginInitializationTests(unittest.TestCase):
    def test_missing_injected_config_logs_warning_and_uses_default_speed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                plugin_module.StarTools, "get_data_dir", create=True, return_value=Path(temp_dir)
            ), patch.object(plugin_module.logger, "warning") as warning:
                plugin = TouchHeadPlugin(context=object())
            self.assertEqual(plugin.config, {})
            warning.assert_called_once()
            self.assertIn("未注入", warning.call_args.args[0])
            self.assertEqual(plugin._get_frame_duration(), 100)

    def test_astrbot_injected_config_is_used_for_gif_generation(self):
        config = {"patpat": {"speed": 2.0}}
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                plugin_module.StarTools, "get_data_dir", create=True, return_value=Path(temp_dir)
            ):
                plugin = TouchHeadPlugin(context=object(), config=config)
            self.assertIs(plugin.config, config)
            self.assertTrue(plugin.output_dir.is_dir())
            with Image.new("RGBA", (100, 100), "blue") as avatar:
                gif_path = plugin._build_petpet_gif(avatar, "target")
            self.assertIsNotNone(gif_path)
            with Image.open(gif_path) as gif:
                self.assertEqual(gif.n_frames, 5)
                for index in range(gif.n_frames):
                    gif.seek(index)
                    self.assertEqual(gif.info["duration"], 50)


class MentionCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.gif_path = Path(self.temp_dir.name) / "petpet.gif"
        self.gif_path.write_bytes(b"GIF89a")
        self.avatar = Image.new("RGBA", (100, 100), "blue")
        self.addCleanup(self.avatar.close)
        self.plugin = TouchHeadPlugin.__new__(TouchHeadPlugin)
        self.plugin.config = {}
        self.plugin._download_image = AsyncMock(return_value=self.avatar)
        self.plugin._build_petpet_gif = Mock(return_value=self.gif_path)

    async def assert_target(self, messages, user_id, nickname):
        self.plugin._download_image.reset_mock()
        self.plugin._build_petpet_gif.reset_mock()
        result = await self.plugin.handle_command(FakeEvent(messages))
        self.assertEqual(result, ("image", str(self.gif_path)))
        self.plugin._download_image.assert_awaited_once_with(
            f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
        )
        self.plugin._build_petpet_gif.assert_called_once_with(self.avatar, nickname)

    async def test_uses_typed_at_qq_and_name(self):
        for qq in (234567, "234567", " 00234567 "):
            with self.subTest(qq=qq):
                await self.assert_target(
                    [Plain("/摸摸 "), At(qq=qq, name="缘滢.")], "234567", "缘滢."
                )

    async def test_missing_target_name_uses_qq(self):
        for name in (None, ""):
            with self.subTest(name=name):
                await self.assert_target(
                    [Plain("/摸摸"), At(qq="234567", name=name)], "234567", "234567"
                )

    async def test_bot_wakeup_mention_does_not_override_target(self):
        await self.assert_target(
            [At(qq=100001, name="机器人"), Plain(" /摸摸 "), At(qq="234567", name="目标")],
            "234567",
            "目标",
        )

    async def test_explicit_bot_target_after_command_is_selected(self):
        for messages in (
            [Plain("/摸摸 "), At(qq="100001", name="机器人")],
            [At(qq="100001"), Plain(" /摸摸 "), At(qq="100001", name="机器人")],
        ):
            with self.subTest(messages=messages):
                await self.assert_target(messages, "100001", "机器人")

    async def test_no_user_mention_keeps_sender_target(self):
        for messages in (
            [Plain("/摸摸")],
            [At(qq="100001", name="机器人"), Plain(" /摸摸")],
        ):
            with self.subTest(messages=messages):
                await self.assert_target(messages, "123456", "发送者")

    async def test_self_id_getter_failure_uses_message_self_id(self):
        for error in (AttributeError("not available"), RuntimeError("adapter failure")):
            with self.subTest(error=error):
                self.plugin._download_image.reset_mock()
                event = FakeEvent(
                    [At(qq="100001"), Plain(" /摸摸 "), At(qq="234567", name="目标")]
                )
                event.get_self_id = Mock(side_effect=error)
                result = await self.plugin.handle_command(event)
                self.assertEqual(result, ("image", str(self.gif_path)))
                self.plugin._download_image.assert_awaited_once_with(
                    "https://q1.qlogo.cn/g?b=qq&nk=234567&s=640"
                )

    async def test_empty_or_invalid_self_id_uses_message_self_id(self):
        for self_id in (None, "", " ", "not-a-qq", "000"):
            with self.subTest(self_id=self_id):
                self.plugin._download_image.reset_mock()
                event = FakeEvent(
                    [At(qq="100001"), Plain(" /摸摸 "), At(qq="234567", name="目标")]
                )
                event.get_self_id = Mock(return_value=self_id)
                result = await self.plugin.handle_command(event)
                self.assertEqual(result, ("image", str(self.gif_path)))
                self.plugin._download_image.assert_awaited_once_with(
                    "https://q1.qlogo.cn/g?b=qq&nk=234567&s=640"
                )

    async def test_unknown_self_id_rejects_ambiguous_leading_mention(self):
        for self_id in (None, "", "not-a-qq", "0"):
            with self.subTest(self_id=self_id):
                event = FakeEvent([At(qq="100001"), Plain(" /摸摸")], self_id=self_id)
                event.get_self_id = Mock(side_effect=AttributeError("not available"))
                result = await self.plugin.handle_command(event)
                self.assertEqual(result[0], "text")
                self.assertIn("机器人", result[1])
                self.plugin._download_image.assert_not_awaited()
                self.plugin._build_petpet_gif.assert_not_called()

    async def test_unknown_self_id_allows_no_mention_or_explicit_target(self):
        for messages, target_id in (
            ([Plain("/摸摸")], "123456"),
            ([Plain("/摸摸 "), At(qq="234567")], "234567"),
        ):
            with self.subTest(messages=messages):
                self.plugin._download_image.reset_mock()
                event = FakeEvent(messages, self_id=None)
                event.get_self_id = Mock(side_effect=AttributeError("not available"))
                result = await self.plugin.handle_command(event)
                self.assertEqual(result, ("image", str(self.gif_path)))
                self.plugin._download_image.assert_awaited_once_with(
                    f"https://q1.qlogo.cn/g?b=qq&nk={target_id}&s=640"
                )

    async def test_first_user_mention_is_selected(self):
        await self.assert_target(
            [Plain("/摸摸 "), At(qq="234567", name="第一位"), At(qq="345678", name="第二位")],
            "234567",
            "第一位",
        )

    async def test_invalid_mentions_do_not_fall_back_to_sender(self):
        invalid_mentions = [
            At(qq=value)
            for value in ("", None, "all", "0", "000", "not-a-qq", "１２３", "123&x=1")
        ]
        invalid_mentions.append(AtAll())
        for mention in invalid_mentions:
            with self.subTest(mention=mention):
                result = await self.plugin.handle_command(FakeEvent([Plain("/摸摸 "), mention]))
                self.assertEqual(result[0], "text")
                self.assertIn("@", result[1])
                self.plugin._download_image.assert_not_awaited()
                self.plugin._build_petpet_gif.assert_not_called()

    async def test_configured_speed_controls_gif_returned_by_command(self):
        self.plugin.config = {"patpat": {"speed": 2.0}}
        self.plugin.output_dir = Path(self.temp_dir.name)
        self.plugin.assets_dir = ROOT / "data" / "petpet"
        self.plugin._is_terminating = False
        self.plugin._build_petpet_gif = TouchHeadPlugin._build_petpet_gif.__get__(
            self.plugin, TouchHeadPlugin
        )
        result = await self.plugin.handle_command(FakeEvent([Plain("/摸摸")]))
        self.assertEqual(result[0], "image")
        with Image.open(result[1]) as gif:
            self.assertEqual(gif.n_frames, 5)
            for index in range(gif.n_frames):
                gif.seek(index)
                self.assertEqual(gif.info["duration"], 50)

    async def test_target_avatar_failure_does_not_retry_sender(self):
        self.plugin._download_image.return_value = None
        result = await self.plugin.handle_command(FakeEvent([At(qq="234567", name="目标")]))
        self.assertEqual(
            result,
            ("text", "抱歉，无法获取头像，无法生成摸头杀图片（QQ 头像接口异常）。"),
        )
        self.plugin._download_image.assert_awaited_once_with(
            "https://q1.qlogo.cn/g?b=qq&nk=234567&s=640"
        )
        self.plugin._build_petpet_gif.assert_not_called()


class PlatformEvent(FakeEvent):
    def __init__(self, messages, platform, **kwargs):
        super().__init__(messages, **kwargs)
        self._platform = platform

    def get_platform_name(self):
        return self._platform


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {}

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    closed = False

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)


class PlatformSupportTests(unittest.IsolatedAsyncioTestCase):
    """issue #16：多平台适配（平台识别 / @ 解析 / 头像解析与提示）。"""

    def setUp(self):
        self.plugin = TouchHeadPlugin.__new__(TouchHeadPlugin)
        self.plugin.config = {}

    def test_platform_kind_mapping(self):
        for platform, kind in (
            ("", "qq"),  # 未提供平台名的适配器沿用 QQ 语义
            ("aiocqhttp", "qq"),
            ("qq_official", "qq"),
            ("telegram", "telegram"),
            ("tg", "telegram"),
            ("discord", "discord"),
            ("slack", "slack"),
            ("lark", "feishu"),
            ("feishu", "feishu"),
            ("wechat", "weixin"),
            ("weixin_official", "weixin"),
            ("some_new_platform", "unknown"),
        ):
            with self.subTest(platform=platform):
                event = PlatformEvent([Plain("/摸摸")], platform)
                self.assertEqual(self.plugin._platform_kind(event), kind)

    def test_non_qq_platform_accepts_non_numeric_at_id(self):
        for platform, target in (
            ("telegram", "@target_user"),
            ("feishu", "ou_8c9d1e2f"),
            ("slack", "U0G9QF9C6"),
            ("discord", "1029384756102938475"),
        ):
            with self.subTest(platform=platform, target=target):
                event = PlatformEvent(
                    [Plain("/摸摸 "), At(qq=target, name="目标")], platform
                )
                resolved = self.plugin._resolve_mentioned_user(event)
                self.assertEqual(resolved, (target, "目标"))

    async def test_avatar_url_dispatches_to_platform_resolver(self):
        event = PlatformEvent([Plain("/摸摸")], "telegram")
        self.plugin._telegram_avatar_url = AsyncMock(return_value="https://t.example/a.jpg")
        url = await self.plugin._resolve_avatar_url(event, "telegram", "@user")
        self.assertEqual(url, "https://t.example/a.jpg")
        self.plugin._telegram_avatar_url.assert_awaited_once_with(event, "@user")

    async def test_avatar_url_resolvers_require_credentials(self):
        for kind, user_id in (
            ("telegram", "@user"),
            ("discord", "1029384756102938475"),
            ("slack", "U0G9QF9C6"),
            ("feishu", "ou_8c9d1e2f"),
        ):
            with self.subTest(kind=kind):
                self.plugin.config = {"platform_tokens": {}}
                resolver = getattr(self.plugin, f"_{kind}_avatar_url")
                if kind == "telegram":
                    url = await resolver(PlatformEvent([Plain("/摸摸")], "telegram"), user_id)
                else:
                    url = await resolver(user_id)
                self.assertIsNone(url)

    async def test_unsupported_platform_has_no_avatar_url(self):
        event = PlatformEvent([Plain("/摸摸")], "some_new_platform")
        url = await self.plugin._resolve_avatar_url(event, "unknown", "123")
        self.assertIsNone(url)

    def test_avatar_unavailable_reason_gives_config_hint(self):
        for kind, keyword in (
            ("telegram", "telegram_bot_token"),
            ("discord", "discord_bot_token"),
            ("slack", "slack_bot_token"),
            ("feishu", "feishu_app"),
            ("weixin", "微信官方平台不提供用户头像"),
            ("unknown", "未知平台"),
        ):
            with self.subTest(kind=kind):
                self.assertIn(keyword, self.plugin._avatar_unavailable_reason(kind))

    async def test_avatar_failure_message_includes_platform_hint(self):
        self.plugin._download_image = AsyncMock(return_value=None)
        event = PlatformEvent([Plain("/摸摸")], "weixin")
        result = await self.plugin.handle_command(event)
        self.assertEqual(result[0], "text")
        self.assertIn("微信官方平台不提供用户头像", result[1])
        self.plugin._download_image.assert_not_awaited()

    def test_redact_secrets_masks_credentials(self):
        url = "https://api.telegram.org/file/bot123456:AAH-secret_token/photo/file_1.jpg"
        redacted = self.plugin._redact_secrets(url)
        self.assertNotIn("AAH-secret_token", redacted)
        self.assertIn("bot***", redacted)
        self.assertIn("file_1.jpg", redacted)
        self.assertNotIn("1234-abcd", self.plugin._redact_secrets("xoxb-1234-abcd"))
        self.assertNotIn(
            "abc", self.plugin._redact_secrets("https://x/y?access_token=abc&z=1")
        )
        self.assertEqual(self.plugin._redact_secrets(None), "")

    def test_user_key_normalization(self):
        self.assertTrue(self.plugin._is_same_user("@MyBot", "mybot"))
        self.assertTrue(self.plugin._is_same_user(" 100001 ", "100001"))
        self.assertFalse(self.plugin._is_same_user("", ""))
        self.assertFalse(self.plugin._is_same_user("@alice", "@bob"))

    def test_non_qq_leading_mention_is_kept_as_candidate(self):
        # 命令前的 @ 在非 QQ 平台无法确认是否机器人自身：不直接采用，
        # 但也不丢——后面没有更明确的 @ 时用它。
        for platform in ("telegram", "feishu", "slack", "discord"):
            with self.subTest(platform=platform):
                event = PlatformEvent(
                    [At(qq="@some_user", name="某人"), Plain(" /摸摸")], platform
                )
                self.assertEqual(
                    self.plugin._resolve_mentioned_user(event), ("@some_user", "某人")
                )

    def test_non_qq_later_mention_overrides_leading_candidate(self):
        event = PlatformEvent(
            [At(qq="@some_user", name="某人"), Plain(" /摸摸 "), At(qq="@target", name="目标")],
            "telegram",
        )
        self.assertEqual(
            self.plugin._resolve_mentioned_user(event), ("@target", "目标")
        )

    def test_require_session_refuses_after_terminate(self):
        self.plugin._is_terminating = True
        with self.assertRaises(RuntimeError):
            self.plugin._require_session()

    async def test_telegram_avatar_main_path(self):
        self.plugin.config = {"platform_tokens": {"telegram_bot_token": "123:abc"}}
        self.plugin._session = FakeSession(
            [
                FakeResponse(200, {"ok": True, "result": {"photos": [[{"file_id": "f1"}]]}}),
                FakeResponse(200, {"ok": True, "result": {"file_path": "photos/x.jpg"}}),
            ]
        )
        event = PlatformEvent([Plain("/摸摸")], "telegram")
        url = await self.plugin._telegram_avatar_url(event, "987654")
        self.assertEqual(
            url, "https://api.telegram.org/file/bot123:abc/photos/x.jpg"
        )

    async def test_telegram_resolves_username_via_get_chat_member(self):
        self.plugin.config = {"platform_tokens": {"telegram_bot_token": "123:abc"}}
        self.plugin._session = FakeSession(
            [
                FakeResponse(200, {"ok": True, "result": {"user": {"id": 987654}}}),
                FakeResponse(200, {"ok": True, "result": {"photos": [[{"file_id": "f1"}]]}}),
                FakeResponse(200, {"ok": True, "result": {"file_path": "photos/x.jpg"}}),
            ]
        )
        event = PlatformEvent([Plain("/摸摸")], "telegram")
        event.get_group_id = lambda: "-100123"
        url = await self.plugin._telegram_avatar_url(event, "@target_user")
        self.assertTrue(url.endswith("photos/x.jpg"))
        self.assertIn("getChatMember", self.plugin._session.calls[0][1])

    async def test_telegram_error_response_returns_none(self):
        self.plugin.config = {"platform_tokens": {"telegram_bot_token": "bad"}}
        self.plugin._session = FakeSession(
            [FakeResponse(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
        )
        event = PlatformEvent([Plain("/摸摸")], "telegram")
        self.assertIsNone(await self.plugin._telegram_avatar_url(event, "987654"))

    async def test_slack_error_response_returns_none(self):
        self.plugin.config = {"platform_tokens": {"slack_bot_token": "xoxb-1"}}
        self.plugin._session = FakeSession(
            [FakeResponse(200, {"ok": False, "error": "missing_scope"})]
        )
        self.assertIsNone(await self.plugin._slack_avatar_url("U0G9QF9C6"))

    async def test_slack_avatar_main_path(self):
        self.plugin.config = {"platform_tokens": {"slack_bot_token": "xoxb-1"}}
        self.plugin._session = FakeSession(
            [
                FakeResponse(
                    200,
                    {"ok": True, "user": {"profile": {"image_192": "https://slack/a.png"}}},
                )
            ]
        )
        self.assertEqual(
            await self.plugin._slack_avatar_url("U0G9QF9C6"), "https://slack/a.png"
        )

    async def test_feishu_accepts_plain_string_avatar(self):
        self.plugin.config = {
            "platform_tokens": {"feishu_app_id": "id", "feishu_app_secret": "sec"}
        }
        self.plugin._session = FakeSession(
            [
                FakeResponse(200, {"tenant_access_token": "t-1"}),
                FakeResponse(
                    200, {"code": 0, "data": {"user": {"avatar": "https://feishu/plain.png"}}}
                ),
            ]
        )
        self.assertEqual(
            await self.plugin._feishu_avatar_url("ou_abc123"), "https://feishu/plain.png"
        )

    async def test_feishu_accepts_avatar_url_field(self):
        self.plugin.config = {
            "platform_tokens": {"feishu_app_id": "id", "feishu_app_secret": "sec"}
        }
        self.plugin._session = FakeSession(
            [
                FakeResponse(200, {"tenant_access_token": "t-1"}),
                FakeResponse(
                    200, {"code": 0, "data": {"user": {"avatar_url": "https://feishu/url.png"}}}
                ),
            ]
        )
        self.assertEqual(
            await self.plugin._feishu_avatar_url("ou_abc123"), "https://feishu/url.png"
        )

    async def test_discord_rejects_non_snowflake_id(self):
        self.plugin.config = {"platform_tokens": {"discord_bot_token": "t"}}
        self.plugin._session = FakeSession([])
        for uid in ("", "abc", "../../users", "@name", "12 34"):
            with self.subTest(uid=uid):
                self.assertIsNone(await self.plugin._discord_avatar_url(uid))
        self.assertEqual(self.plugin._session.calls, [])

    async def test_discord_error_response_is_not_treated_as_user(self):
        self.plugin.config = {"platform_tokens": {"discord_bot_token": "t"}}
        self.plugin._session = FakeSession(
            [FakeResponse(404, {"code": 10013, "message": "Unknown User"})]
        )
        self.assertIsNone(await self.plugin._discord_avatar_url("123456789012345678"))

    async def test_discord_falls_back_to_default_avatar(self):
        self.plugin.config = {"platform_tokens": {"discord_bot_token": "t"}}
        self.plugin._session = FakeSession(
            [FakeResponse(200, {"id": "123456789012345678", "avatar": None})]
        )
        url = await self.plugin._discord_avatar_url("123456789012345678")
        self.assertTrue(url.startswith("https://cdn.discordapp.com/embed/avatars/"))

    async def test_discord_custom_avatar_uses_cdn_path(self):
        self.plugin.config = {"platform_tokens": {"discord_bot_token": "t"}}
        self.plugin._session = FakeSession(
            [FakeResponse(200, {"id": "123456789012345678", "avatar": "a_abcdef"})]
        )
        url = await self.plugin._discord_avatar_url("123456789012345678")
        self.assertEqual(
            url,
            "https://cdn.discordapp.com/avatars/123456789012345678/a_abcdef.gif?size=256",
        )

    async def test_feishu_accepts_variant_avatar_fields(self):
        self.plugin.config = {
            "platform_tokens": {"feishu_app_id": "id", "feishu_app_secret": "sec"}
        }
        self.plugin._session = FakeSession(
            [
                FakeResponse(200, {"tenant_access_token": "t-1"}),
                FakeResponse(
                    200,
                    {
                        "code": 0,
                        "data": {"user": {"avatar": {"avatar_big": "https://feishu/big.png"}}},
                    },
                ),
            ]
        )
        self.assertEqual(
            await self.plugin._feishu_avatar_url("ou_abc123"), "https://feishu/big.png"
        )

    async def test_feishu_error_code_returns_none(self):
        self.plugin.config = {
            "platform_tokens": {"feishu_app_id": "id", "feishu_app_secret": "sec"}
        }
        self.plugin._session = FakeSession(
            [
                FakeResponse(200, {"tenant_access_token": "t-1"}),
                FakeResponse(200, {"code": 41050, "msg": "no permission"}),
            ]
        )
        self.assertIsNone(await self.plugin._feishu_avatar_url("ou_abc123"))


class GifGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.plugin = TouchHeadPlugin.__new__(TouchHeadPlugin)
        self.plugin.config = {}
        self.plugin.output_dir = Path(self.temp_dir.name)
        self.plugin.assets_dir = ROOT / "data" / "petpet"
        self.plugin._is_terminating = False
        self.avatar = Image.new("RGBA", (100, 100), "blue")
        self.addCleanup(self.avatar.close)

    def test_hand_moves_up_five_pixels_without_moving_avatar(self):
        self.plugin.assets_dir = self.plugin.output_dir / "templates"
        self.plugin.assets_dir.mkdir()
        markers = [(30, (255, 0, 0)), (34, (0, 255, 0))]
        originals = {}
        for index, (top, color) in enumerate(markers):
            template = self.plugin.assets_dir / f"frame{index}.png"
            with Image.new("RGBA", (112, 112), (0, 0, 0, 0)) as hand:
                ImageDraw.Draw(hand).rectangle((40, top, 60, top + 9), fill=(*color, 255))
                hand.save(template)
            originals[template] = template.read_bytes()

        gif_path = self.plugin._build_petpet_gif(self.avatar, "target")
        self.assertIsNotNone(gif_path)
        with Image.open(gif_path) as gif:
            self.assertEqual(gif.size, (112, 112))
            self.assertEqual(gif.n_frames, len(markers))
            for index, (top, color) in enumerate(markers):
                gif.seek(index)
                with gif.convert("RGB") as frame:
                    self.assertEqual(frame.getpixel((50, top - 5)), color)
                    self.assertEqual(frame.getpixel((50, top + 9)), (0, 0, 255))
                    self.assertEqual(frame.getpixel((50, 16)), (0, 0, 255))
                    self.assertEqual(frame.getpixel((50, 15)), (255, 255, 255))
        for template, original in originals.items():
            self.assertEqual(template.read_bytes(), original)

    def assert_duration(self, config, expected):
        self.plugin.config = config
        gif_path = self.plugin._build_petpet_gif(self.avatar, "target")
        self.assertIsNotNone(gif_path)
        with Image.open(gif_path) as gif:
            self.assertEqual(gif.n_frames, 5)
            for index in range(gif.n_frames):
                gif.seek(index)
                self.assertEqual(gif.info["duration"], expected)

    def test_configured_speeds_change_each_frame_duration(self):
        for speed, duration in ((0.5, 200), (1.0, 100), (1.5, 70), (2.0, 50), (3.0, 30)):
            with self.subTest(speed=speed):
                self.assert_duration({"patpat": {"speed": speed}}, duration)

    def test_bad_speed_falls_back_to_default(self):
        for speed in (None, True, False, 0, -1, float("nan"), float("inf"), "x", [], {}):
            with self.subTest(speed=speed):
                self.assert_duration({"patpat": {"speed": speed}}, 100)

    def test_speed_outside_ui_range_falls_back_to_default(self):
        for speed in (0.1, 0.49, 3.01, 4.0, 10**400):
            with self.subTest(speed=speed):
                self.assert_duration({"patpat": {"speed": speed}}, 100)

    def test_missing_or_malformed_config_falls_back_to_default(self):
        for config in (None, {}, {"patpat": None}, {"patpat": []}, {"patpat": {}}):
            with self.subTest(config=config):
                self.assert_duration(config, 100)

    def test_changed_config_is_used_by_subsequent_gifs(self):
        config = {"patpat": {"speed": 0.5}}
        self.assert_duration(config, 200)
        config["patpat"]["speed"] = 2.0
        self.assert_duration(config, 50)

    def test_bundled_templates_still_produce_a_complete_gif(self):
        gif_path = self.plugin._build_petpet_gif(self.avatar, "target")
        self.assertIsNotNone(gif_path)
        with Image.open(gif_path) as gif:
            self.assertEqual(gif.size, (112, 112))
            self.assertEqual(gif.n_frames, 5)
            self.assertEqual(gif.info["loop"], 0)
            for index in range(gif.n_frames):
                gif.seek(index)
                self.assertEqual(gif.info["duration"], 100)
                gif.load()


if __name__ == "__main__":
    unittest.main()
