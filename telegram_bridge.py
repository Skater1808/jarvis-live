"""
Telegram bridge for remote Jarvis control.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from google import genai
try:
    from telegram import Update
    from telegram.error import NetworkError, RetryAfter, TimedOut
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except Exception as telegram_import_error:
    Update = Any  # type: ignore[assignment]
    Application = Any  # type: ignore[assignment]
    ContextTypes = Any  # type: ignore[assignment]
    CommandHandler = None
    MessageHandler = None
    filters = None
    NetworkError = Exception
    RetryAfter = Exception
    TimedOut = Exception
    TELEGRAM_AVAILABLE = False
    TELEGRAM_IMPORT_ERROR = telegram_import_error
else:
    TELEGRAM_IMPORT_ERROR = None

from memory import get_memory_stats
from quick_notes import add_quick_note


PromptHandler = Callable[[str, Optional[dict[str, Any]]], Awaitable[str]]


class TelegramBridge:
    """Encapsulates Telegram bot polling and message handling."""

    def __init__(self, config: dict[str, Any], handle_prompt_fn: PromptHandler):
        self._config = config
        self._handle_prompt_fn = handle_prompt_fn
        self._telegram_cfg = config.get("telegram", {}) or {}
        self._token = (self._telegram_cfg.get("bot_token") or "").strip()
        self._poll_interval = float(self._telegram_cfg.get("poll_interval_seconds", 1.0))
        self._voice_reply_enabled = bool(self._telegram_cfg.get("voice_reply", False))
        self._allowed_user_ids = set(self._telegram_cfg.get("allowed_user_ids", []) or [])
        self._allowed_chat_ids = set(self._telegram_cfg.get("allowed_chat_ids", []) or [])
        self._gemini_api_key = (config.get("gemini_api_key") or "").strip()
        self._gemini_client: Optional[genai.Client] = None
        if self._gemini_api_key:
            self._gemini_client = genai.Client(api_key=self._gemini_api_key)

        self._application: Optional[Application] = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            print("[telegram] Bridge laeuft bereits.", flush=True)
            return
        if not TELEGRAM_AVAILABLE:
            raise RuntimeError(f"python-telegram-bot fehlt oder defekt: {TELEGRAM_IMPORT_ERROR}")
        if not self._token:
            print("[telegram] Kein Bot-Token konfiguriert. Bridge bleibt deaktiviert.", flush=True)
            return

        self._application = Application.builder().token(self._token).build()
        self._register_handlers(self._application)

        try:
            await self._application.initialize()
            await self._application.start()
            if self._application.updater is None:
                raise RuntimeError("Telegram Updater nicht verfuegbar.")
            await self._application.updater.start_polling(poll_interval=self._poll_interval)
            self._running = True
            print("[telegram] Bridge gestartet (Polling aktiv).", flush=True)
        except Exception as exc:
            print(f"[telegram] Start fehlgeschlagen: {exc}", flush=True)
            await self.stop()
            raise

    async def stop(self) -> None:
        if not self._application:
            self._running = False
            return
        try:
            if self._application.updater is not None:
                await self._application.updater.stop()
            await self._application.stop()
            await self._application.shutdown()
            print("[telegram] Bridge gestoppt.", flush=True)
        except Exception as exc:
            print(f"[telegram] Fehler beim Stoppen: {exc}", flush=True)
        finally:
            self._application = None
            self._running = False

    def _register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("note", self._cmd_note))
        app.add_handler(CommandHandler("memory", self._cmd_memory))
        app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

    def _is_authorized_ids(self, user_id: Optional[int], chat_id: Optional[int]) -> bool:
        if self._allowed_user_ids and user_id not in self._allowed_user_ids:
            return False
        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            return False
        return True

    async def _enforce_authorization(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        user_id = user.id if user else None
        chat_id = chat.id if chat else None
        if self._is_authorized_ids(user_id, chat_id):
            return True
        print(f"[telegram] Zugriff verweigert user_id={user_id}, chat_id={chat_id}", flush=True)
        if update.message:
            await self._safe_reply(update, "Nicht autorisiert.")
        return False

    async def _safe_reply(self, update: Update, text: str) -> None:
        if not update.message:
            return
        await self._safe_send_message(update.message.chat_id, text)

    async def _safe_send_message(self, chat_id: int, text: str, retries: int = 3) -> None:
        if not self._application:
            return
        for attempt in range(1, retries + 1):
            try:
                await self._application.bot.send_message(chat_id=chat_id, text=text)
                return
            except RetryAfter as exc:
                delay = max(float(exc.retry_after), 1.0)
                print(f"[telegram] RetryAfter beim Senden, warte {delay:.1f}s", flush=True)
                await asyncio.sleep(delay)
            except (TimedOut, NetworkError) as exc:
                if attempt == retries:
                    print(f"[telegram] Senden fehlgeschlagen nach {retries} Versuchen: {exc}", flush=True)
                    return
                delay = float(attempt)
                print(f"[telegram] Netzwerkfehler beim Senden (Versuch {attempt}): {exc}", flush=True)
                await asyncio.sleep(delay)
            except Exception as exc:
                print(f"[telegram] Unerwarteter Sendefehler: {exc}", flush=True)
                return

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not await self._enforce_authorization(update):
            return
        await self._safe_reply(
            update,
            "Jarvis Telegram-Bridge aktiv. Nutzen Sie /help fuer Befehle.",
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not await self._enforce_authorization(update):
            return
        help_text = (
            "Verfuegbare Befehle:\n"
            "/start - Bridge initialisieren\n"
            "/help - Diese Hilfe\n"
            "/status - Brueckenstatus\n"
            "/note <text> - Schnelle Notiz speichern\n"
            "/memory - Speicherstatistik anzeigen\n"
            "Oder senden Sie normalen Text bzw. Voice-Nachrichten."
        )
        await self._safe_reply(update, help_text)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not await self._enforce_authorization(update):
            return
        status_text = (
            "Telegram-Bridge laeuft.\n"
            f"Voice-Reply: {'aktiviert' if self._voice_reply_enabled else 'deaktiviert'}\n"
            "STT: Gemini-Audio-Transkription (best effort)"
        )
        await self._safe_reply(update, status_text)

    async def _cmd_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._enforce_authorization(update):
            return
        note_text = " ".join(context.args or []).strip()
        if not note_text:
            await self._safe_reply(update, "Bitte nutzen: /note <text>")
            return
        try:
            result = await add_quick_note(note_text, self._config)
            await self._safe_reply(update, result)
        except Exception as exc:
            print(f"[telegram] Fehler bei /note: {exc}", flush=True)
            await self._safe_reply(update, "Notiz konnte nicht gespeichert werden.")

    async def _cmd_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not await self._enforce_authorization(update):
            return
        try:
            stats = await get_memory_stats()
            reply = (
                "Memory-Statistik:\n"
                f"- Gespraeche: {stats.get('conversations', 0)}\n"
                f"- Fakten: {stats.get('facts', 0)}\n"
                f"- Vorlieben: {stats.get('preferences', 0)}"
            )
            await self._safe_reply(update, reply)
        except Exception as exc:
            print(f"[telegram] Fehler bei /memory: {exc}", flush=True)
            await self._safe_reply(update, "Memory-Statistik aktuell nicht verfuegbar.")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not await self._enforce_authorization(update):
            return
        if not update.message or not update.message.text:
            return
        text = update.message.text.strip()
        if not text:
            return
        source_meta = self._build_source_meta(update, "text")
        print(f"[telegram] Text empfangen: {text[:80]}", flush=True)
        try:
            answer = await asyncio.wait_for(
                self._handle_prompt_fn(text, source_meta),
                timeout=90,
            )
            await self._safe_reply(update, answer or "Keine Antwort erhalten.")
        except asyncio.TimeoutError:
            await self._safe_reply(update, "Zeitueberschreitung bei Jarvis-Anfrage.")
        except Exception as exc:
            print(f"[telegram] Textverarbeitung fehlgeschlagen: {exc}", flush=True)
            await self._safe_reply(update, "Fehler bei der Verarbeitung Ihrer Nachricht.")

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not await self._enforce_authorization(update):
            return
        if not update.message or not update.message.voice:
            return

        await self._safe_reply(update, "Voice empfangen, transkribiere...")
        try:
            transcript = await self._transcribe_voice_message(update)
        except Exception as exc:
            print(f"[telegram] Voice-Handling Fehler: {exc}", flush=True)
            await self._safe_reply(update, "Voice konnte nicht verarbeitet werden.")
            return

        if not transcript:
            await self._safe_reply(
                update,
                "Keine Transkription verfuegbar. Bitte pruefen Sie FFmpeg/Gemini-Konfiguration.",
            )
            return

        source_meta = self._build_source_meta(update, "voice")
        source_meta["transcript"] = transcript
        print(f"[telegram] Voice transkribiert: {transcript[:80]}", flush=True)
        try:
            answer = await asyncio.wait_for(
                self._handle_prompt_fn(transcript, source_meta),
                timeout=90,
            )
            await self._safe_reply(update, answer or "Keine Antwort erhalten.")
        except asyncio.TimeoutError:
            await self._safe_reply(update, "Zeitueberschreitung bei Jarvis-Anfrage.")
        except Exception as exc:
            print(f"[telegram] Fehler nach Voice-Transkription: {exc}", flush=True)
            await self._safe_reply(update, "Fehler bei der Verarbeitung Ihrer Voice-Nachricht.")

    def _build_source_meta(self, update: Update, input_type: str) -> dict[str, Any]:
        user = update.effective_user
        chat = update.effective_chat
        return {
            "source": "telegram",
            "input_type": input_type,
            "user_id": user.id if user else None,
            "chat_id": chat.id if chat else None,
            "username": user.username if user else None,
        }

    async def _transcribe_voice_message(self, update: Update) -> str:
        if not self._application:
            return ""
        if not update.message or not update.message.voice:
            return ""

        voice = update.message.voice
        tg_file = await self._application.bot.get_file(voice.file_id)

        with tempfile.TemporaryDirectory(prefix="jarvis_tg_") as temp_dir:
            temp_path = Path(temp_dir)
            ogg_path = temp_path / "voice.ogg"
            wav_path = temp_path / "voice.wav"

            await tg_file.download_to_drive(custom_path=str(ogg_path))
            await asyncio.to_thread(self._convert_ogg_to_wav, ogg_path, wav_path)
            transcript = await self._transcribe_wav_with_gemini(wav_path)
            return transcript.strip()

    @staticmethod
    def _convert_ogg_to_wav(ogg_path: Path, wav_path: Path) -> None:
        # Try multiple possible FFmpeg paths
        ffmpeg_paths = [
            "ffmpeg",  # Try system PATH first
            r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\FFmpeg\bin\ffmpeg.exe",
            r"C:\Users\Emil\AppData\Local\Microsoft\WinGet\packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe",
        ]
        
        ffmpeg_cmd = None
        for path in ffmpeg_paths:
            try:
                # Test if ffmpeg exists at this path
                test_result = subprocess.run([path, "-version"], 
                                           capture_output=True, text=True, timeout=5)
                if test_result.returncode == 0:
                    ffmpeg_cmd = path
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
                continue
        
        if not ffmpeg_cmd:
            raise RuntimeError(
                "FFmpeg nicht gefunden. Bitte installieren Sie FFmpeg und stellen Sie sicher, "
                "dass es im PATH verfuegbar ist oder unter C:\\Program Files\\FFmpeg\\bin\\ffmpeg.exe"
            )
        
        cmd = [
            ffmpeg_cmd,
            "-y",
            "-i",
            str(ogg_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg-Konvertierung fehlgeschlagen mit {ffmpeg_cmd}. "
                f"Details: {result.stderr.strip()[:300]}"
            )

    async def _transcribe_wav_with_gemini(self, wav_path: Path) -> str:
        if not self._gemini_client:
            print("[telegram] Kein Gemini-Client fuer STT verfuegbar.", flush=True)
            return ""

        wav_data = await asyncio.to_thread(wav_path.read_bytes)
        prompt = (
            "Transkribiere diese deutsche Sprachaufnahme praezise als Klartext. "
            "Liefere nur den transkribierten Text ohne Zusatzkommentare."
        )

        def _call_gemini() -> Any:
            from google.genai import types

            return self._gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    prompt,
                    types.Part.from_bytes(data=wav_data, mime_type="audio/wav"),
                ],
            )

        try:
            response = await asyncio.wait_for(asyncio.to_thread(_call_gemini), timeout=45)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text
            print("[telegram] Gemini-STT lieferte keinen Text. TODO: STT-Fallback anbinden.", flush=True)
            return ""
        except asyncio.TimeoutError:
            print("[telegram] Gemini-STT Timeout.", flush=True)
            return ""
        except Exception as exc:
            print(f"[telegram] Gemini-STT Fehler: {exc}. TODO: robusten STT-Fallback integrieren.", flush=True)
            return ""
