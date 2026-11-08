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
    star.Star = object
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
    return module.TouchHeadPlugin


TouchHeadPlugin = load_plugin()


class MentionCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.gif_path = Path(self.temp_dir.name) / "petpet.gif"
        self.gif_path.write_bytes(b"GIF89a")
        self.avatar = Image.new("RGBA", (100, 100), "blue")
        self.addCleanup(self.avatar.close)
        self.plugin = TouchHeadPlugin.__new__(TouchHeadPlugin)
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

    async def test_target_avatar_failure_does_not_retry_sender(self):
        self.plugin._download_image.return_value = None
        result = await self.plugin.handle_command(FakeEvent([At(qq="234567", name="目标")]))
        self.assertEqual(result, ("text", "抱歉，无法获取头像，无法生成摸头杀图片。"))
        self.plugin._download_image.assert_awaited_once_with(
            "https://q1.qlogo.cn/g?b=qq&nk=234567&s=640"
        )
        self.plugin._build_petpet_gif.assert_not_called()


class GifGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.plugin = TouchHeadPlugin.__new__(TouchHeadPlugin)
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

    def test_speed_config_controls_frame_duration(self):
        # issue #15：speed 配置必须实际生效——值越大摸得越快（帧时长越短）。
        for speed, expected_duration in ((2.0, 50), (0.5, 200), (1.0, 100)):
            with self.subTest(speed=speed):
                self.plugin.config = {"patpat": {"speed": speed}}
                gif_path = self.plugin._build_petpet_gif(self.avatar, "target")
                self.assertIsNotNone(gif_path)
                with Image.open(gif_path) as gif:
                    for index in range(gif.n_frames):
                        gif.seek(index)
                        self.assertEqual(gif.info["duration"], expected_duration)
                        gif.load()

    def test_speed_config_invalid_values_fall_back_to_default(self):
        # 未配置 / 非法值回退默认（duration=100）；越界值钳制进 0.5~3.0。
        cases = [
            (None, 100),
            ({}, 100),
            ({"patpat": {}}, 100),
            ({"patpat": {"speed": "abc"}}, 100),
            ({"patpat": {"speed": 0}}, 100),
            ({"patpat": {"speed": -1}}, 100),
            ({"patpat": {"speed": 99}}, 33),   # 钳制到 3.0 → 100/3 ≈ 33ms
            ({"patpat": {"speed": 0.1}}, 200),  # 钳制到 0.5 → 200ms
        ]
        for config, expected in cases:
            with self.subTest(config=config, expected=expected):
                self.plugin.config = config
                self.assertEqual(self.plugin._frame_duration_ms(), expected)


if __name__ == "__main__":
    unittest.main()
