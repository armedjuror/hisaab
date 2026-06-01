"""
plugins/telegram.py — Telegram channel adapter (multi-user).

Registers a FastAPI router at /plugins/telegram/webhook.
Auth (signup + OTP login) is handled by AuthFlowMixin before any expense parsing.
All business logic is delegated to services.py via BasePlugin helpers.

Setup:
    export TELEGRAM_BOT_TOKEN=<your-bot-token>
    # Register webhook once:
    curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://yourdomain.com/plugins/telegram/webhook"
    # Note: router prefix is /telegram, mounted under /plugins → full path: /plugins/telegram/webhook

Commands:
    /channels  — list linked bots/channels
    /help      — show all commands
"""

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from models import SessionLocal
from plugins.auth_flow import AuthFlowMixin
from plugins.base import BasePlugin, InboundMessage
from plugins.registry import register as register_plugin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TelegramPlugin(AuthFlowMixin, BasePlugin):
    """Telegram adapter — only knows about HTTP calls to Telegram's Bot API."""

    def __init__(self):
        self._token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        register_plugin(self)

    @property
    def name(self) -> str:
        return "telegram"

    def welcome_text(self, user) -> str:
        return f"👋 Welcome, *{user.name}*!\nLogged in as `{user.email}`\n\n" + self._help_text()

    @staticmethod
    def _help_text() -> str:
        return (
            "*Expenses & income* — text or 🎙️ voice note:\n"
            "  `spent 450 on lunch`\n"
            "  `paid 1200 electricity from HDFC`\n"
            "  `got salary 85000`\n\n"
            "*Lending:*\n"
            "  `lent 2000 to Rahul`\n"
            "  `borrowed 5000 from Priya`\n"
            "  `who owes me` — unsettled lent amounts\n"
            "  `what do I owe` — unsettled borrowed amounts\n\n"
            "*Quick queries:*\n"
            "  `my accounts` — balances\n"
            "  `my categories` — list categories\n"
            "  `my budgets` — this month's budget progress\n"
            "  `what did I spend today`\n\n"
            "*Reports:*\n"
            "  /report — this month's summary\n"
            "  /report last — last month\n"
            "  /report jan — January (add year: `jan 2024`)\n"
            "  /report trend — 6-month spending trend\n\n"
            "*Accounts:*\n"
            "  `my accounts` — list balances\n"
            "  `add HDFC Savings as bank account` — create account\n"
            "  `delete all accounts` — remove all your accounts\n\n"
            "*Other:*\n"
            "  /channels — show your linked bots/channels\n"
            "  /apikey — get your API access token\n"
            "  /help — show this message\n"
        )

    async def _get_file_path(self, file_id: str) -> str | None:
        """Resolve a Telegram file_id to a direct download URL."""
        if not self._token:
            return None
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.telegram.org/bot{self._token}/getFile",
                params={"file_id": file_id},
                timeout=10,
            )
            data = r.json()
            if data.get("ok") and data["result"].get("file_path"):
                return f"https://api.telegram.org/file/bot{self._token}/{data['result']['file_path']}"
        return None

    async def transcribe_voice(self, file_id: str) -> str | None:
        """Download a Telegram voice/audio file and transcribe it with Whisper (runs in thread pool)."""
        import asyncio
        import tempfile, os
        url = await self._get_file_path(file_id)
        if not url:
            return None
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=60)
            r.raise_for_status()
        suffix = ".oga" if ".oga" in url else ".mp3" if ".mp3" in url else ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(r.content)
            tmp_path = f.name
        try:
            from services import transcribe_audio
            return await asyncio.get_event_loop().run_in_executor(None, transcribe_audio, tmp_path)
        finally:
            os.unlink(tmp_path)

    async def ocr_photo(self, file_id: str) -> str | None:
        """Download a Telegram photo and extract text via RapidOCR (runs in thread pool)."""
        import asyncio
        import tempfile, os
        url = await self._get_file_path(file_id)
        if not url:
            return None
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=60)
            r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(r.content)
            tmp_path = f.name
        try:
            from services import ocr_image
            return await asyncio.get_event_loop().run_in_executor(None, ocr_image, tmp_path)
        finally:
            os.unlink(tmp_path)

    async def send_message(self, chat_id: str, text: str) -> int | None:
        """Send a message and return its message_id."""
        if not self._token:
            log.warning("TELEGRAM_BOT_TOKEN not set — skipping send")
            return None
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
        data = r.json()
        if not data.get("ok"):
            log.error(
                "Telegram sendMessage failed for chat_id=%s: %s",
                chat_id,
                data.get("description", data),
            )
            raise RuntimeError(data.get("description", "Telegram delivery failed"))
        return data["result"]["message_id"]

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        if not self._token or not message_id:
            return
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{self._token}/deleteMessage",
                json={"chat_id": chat_id, "message_id": message_id},
                timeout=10,
            )

    async def send_transient_message(self, chat_id: str, text: str, delay: float = 3.0) -> None:
        """Send a status message and delete it after `delay` seconds."""
        message_id = await self.send_message(chat_id, text)
        async def _delete_later():
            await asyncio.sleep(delay)
            await self.delete_message(chat_id, message_id)
        asyncio.create_task(_delete_later())

    async def handle(self, msg: InboundMessage, db: Session) -> None:
        # Auth gate — handles signup/OTP flow; returns User or None
        user = await self.get_authenticated_user(msg, db)
        if user is None:
            return  # auth flow is in progress

        # Attach authenticated user to message
        msg.user_id = user.id

        text = (msg.text or "").strip()

        # ── /help ─────────────────────────────────────────────────────────
        if text.lower() == "/help":
            await self.send_message(msg.chat_id, self._help_text())
            return

        # ── /apikey — return the user's Personal Access Token ─────────────
        if text.lower() == "/apikey":
            import oauth_service as _oas
            session = _oas.get_or_create_session(user.id, db)
            await self.send_message(
                msg.chat_id,
                f"Your Personal Access Token:\n\n`{session.token}`\n\n"
                f"Use it as:\n`Authorization: Bearer {session.token}`\n\n"
                f"Valid for 30 days. Keep it secret.",
            )
            return

        # ── Voice / audio note? Transcribe with Whisper ───────────────────
        if not msg.text and msg.raw:
            message = msg.raw.get("message", {})
            voice = message.get("voice") or message.get("audio")
            if voice:
                await self.send_transient_message(msg.chat_id, "🎙️ Transcribing your voice note…")
                try:
                    transcript = await self.transcribe_voice(voice["file_id"])
                    if transcript:
                        await self.send_transient_message(msg.chat_id, "🎙️ Transcription: \n" + transcript)
                        msg.text = transcript
                        log.info("Voice transcribed: %s", transcript)
                    else:
                        await self.send_message(msg.chat_id, "Couldn't transcribe the audio. Please try again.")
                        return
                except Exception:
                    log.exception("Voice transcription failed")
                    await self.send_message(msg.chat_id, "Voice transcription failed. Send a text message instead.")
                    return

        # ── Photo/invoice image? OCR ──────────────────────────────────────
        # Always OCR photos — even when a caption is present (caption + OCR text both go to AI)
        photo = msg.raw and msg.raw.get("message", {}).get("photo", [])
        if photo:
            await self.send_transient_message(msg.chat_id, "🔍 Reading your invoice…")
            try:
                ocr_text = await self.ocr_photo(photo[-1]["file_id"])
                if ocr_text:
                    caption = msg.raw.get("message", {}).get("caption", "")
                    receipt_block = "RECEIPT:\n" + ocr_text
                    msg.text = (receipt_block + '\n' + caption).strip() if caption else receipt_block
                    log.info("OCR extracted: %s", msg.text[:100])
                else:
                    await self.send_message(msg.chat_id, "Couldn't read the image. Try typing the amount instead.")
                    return
            except Exception as exc:
                log.exception("OCR failed")
                await self.send_message(
                    msg.chat_id,
                    f"Image reading failed: {exc}\n\nTry typing the expense as text instead."
                )
                return

        if not msg.text:
            await self.send_message(msg.chat_id, "Send me a text message, a voice note, or a photo of your receipt.")
            return

        if not msg.text.startswith("RECEIPT"):
            # ── Account management (delete all / create)? ────────────────────
            manage_reply = await self.maybe_manage_accounts(msg, db)
            if manage_reply is not None:
                await self.send_message(msg.chat_id, manage_reply)
                return

            # ── Set / update account balance? ────────────────────────────────
            balance_reply = self.maybe_update_account_balance(msg, db)
            if balance_reply is not None:
                await self.send_message(msg.chat_id, balance_reply)
                return

            # ── Account list? ─────────────────────────────────────────────────
            accounts_reply = self.maybe_list_accounts(msg, db)
            if accounts_reply is not None:
                await self.send_message(msg.chat_id, accounts_reply)
                return

            # ── Category list? ────────────────────────────────────────────────
            categories_reply = self.maybe_list_categories(msg, db)
            if categories_reply is not None:
                await self.send_message(msg.chat_id, categories_reply)
                return

            # ── Budget summary? ───────────────────────────────────────────────
            budget_reply = self.maybe_list_budgets(msg, db)
            if budget_reply is not None:
                await self.send_message(msg.chat_id, budget_reply)
                return

            # ── Today's spends? ───────────────────────────────────────────────
            today_reply = self.maybe_get_today_spends(msg, db)
            if today_reply is not None:
                await self.send_message(msg.chat_id, today_reply)
                return

            # ── Lending? ──────────────────────────────────────────────────────
            lending_reply = await self.maybe_handle_lending(msg, db)
            if lending_reply is not None:
                await self.send_message(msg.chat_id, lending_reply)
                return

            # ── Report request? ───────────────────────────────────────────────
            report = await self.maybe_get_report(msg, db)
            if report is not None:
                await self.send_message(msg.chat_id, report)
                return

        # ── Expense / income entry ────────────────────────────────────────
        try:
            parsed = self.parse(msg, db)
        except Exception as exc:
            log.exception("AI parse failed")
            await self.send_message(msg.chat_id, f"Couldn't parse that: {exc}")
            return

        # Treat as chat if AI flagged it, or if there's no amount and nothing is missing
        # (AI occasionally misclassifies greetings as transactions with empty missing list)
        if parsed.chat or (parsed.amount is None and not parsed.missing):
            await self.send_message(msg.chat_id, parsed.reply or "Hey! Send me an expense or income to log.")
            return

        if parsed.missing:
            await self.send_message(msg.chat_id, self.missing_prompt(parsed))
            return

        try:
            self.save(parsed, db, msg)
        except Exception as exc:
            log.exception("Save failed")
            await self.send_message(msg.chat_id, f"Couldn't save: {exc}")
            return

        warning = self.budget_warning(parsed, db, user_id=msg.user_id)
        await self.send_message(msg.chat_id, (parsed.reply or "Logged!") + warning)


# Singleton plugin instance
_plugin = TelegramPlugin()


@router.post("/webhook", include_in_schema=False)
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    message = payload.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    msg_id  = str(message.get("message_id", ""))
    text    = message.get("text", "") or message.get("caption", "")

    if not chat_id:
        return {"ok": False, "reason": "no chat_id"}

    msg = InboundMessage(
        text       = text or None,
        source_ref = msg_id,
        chat_id    = chat_id,
        raw        = payload,
    )
    async def _handle_in_background():
        db = SessionLocal()
        try:
            await _plugin.handle(msg, db)
        finally:
            db.close()

    asyncio.create_task(_handle_in_background())
    return {"ok": True}
