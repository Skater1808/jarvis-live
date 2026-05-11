import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_bridge import TelegramBridge


class DummyUser:
    def __init__(self, user_id: int, username: str = "tester"):
        self.id = user_id
        self.username = username


class DummyChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class DummyMessage:
    def __init__(self, chat_id: int, text: str = "", voice=None):
        self.chat_id = chat_id
        self.text = text
        self.voice = voice


class DummyUpdate:
    def __init__(self, user_id: int, chat_id: int, text: str = "", voice=None):
        self.effective_user = DummyUser(user_id)
        self.effective_chat = DummyChat(chat_id)
        self.message = DummyMessage(chat_id, text=text, voice=voice)


class TelegramBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_defaults_without_telegram_block(self):
        async def fake_handler(text: str, source_meta=None):
            del source_meta
            return text

        bridge = TelegramBridge({"gemini_api_key": ""}, fake_handler)
        self.assertEqual(bridge._poll_interval, 1.0)
        self.assertFalse(bridge._voice_reply_enabled)
        self.assertTrue(bridge._is_authorized_ids(1, 1))

    async def test_authorization_allow_and_deny(self):
        async def fake_handler(text: str, source_meta=None):
            del source_meta
            return text

        cfg = {
            "gemini_api_key": "",
            "telegram": {
                "allowed_user_ids": [100],
                "allowed_chat_ids": [200],
            },
        }
        bridge = TelegramBridge(cfg, fake_handler)
        self.assertTrue(bridge._is_authorized_ids(100, 200))
        self.assertFalse(bridge._is_authorized_ids(999, 200))
        self.assertFalse(bridge._is_authorized_ids(100, 999))

    async def test_text_message_maps_to_prompt_callback(self):
        callback = AsyncMock(return_value="Antwort von Jarvis")
        bridge = TelegramBridge({"gemini_api_key": ""}, callback)
        bridge._safe_reply = AsyncMock()
        bridge._enforce_authorization = AsyncMock(return_value=True)

        update = DummyUpdate(user_id=1, chat_id=2, text="Hallo Jarvis")
        context = SimpleNamespace()
        await bridge._handle_text(update, context)

        callback.assert_awaited_once()
        called_text, called_meta = callback.await_args.args
        self.assertEqual(called_text, "Hallo Jarvis")
        self.assertEqual(called_meta["source"], "telegram")
        bridge._safe_reply.assert_awaited_once_with(update, "Antwort von Jarvis")

    async def test_note_command_calls_add_quick_note(self):
        callback = AsyncMock(return_value="ok")
        bridge = TelegramBridge({"gemini_api_key": ""}, callback)
        bridge._safe_reply = AsyncMock()
        bridge._enforce_authorization = AsyncMock(return_value=True)

        update = DummyUpdate(user_id=1, chat_id=2, text="/note Test")
        context = SimpleNamespace(args=["Test", "Notiz"])

        with patch("telegram_bridge.add_quick_note", new=AsyncMock(return_value="Gespeichert, Sir.")) as mocked_note:
            await bridge._cmd_note(update, context)
            mocked_note.assert_awaited_once_with("Test Notiz", bridge._config)
        bridge._safe_reply.assert_awaited_once_with(update, "Gespeichert, Sir.")


if __name__ == "__main__":
    unittest.main()
