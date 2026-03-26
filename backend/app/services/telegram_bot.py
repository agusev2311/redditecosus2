from __future__ import annotations

from telegram import InlineQueryResultArticle, InlineQueryResultGif, InlineQueryResultPhoto, InlineQueryResultVideo, InputTextMessageContent, Update
from telegram.ext import Application, CommandHandler, ContextTypes, InlineQueryHandler

from app.config import settings
from app.db.session import SessionLocal
from app.models import MediaItem, User
from app.services.disk_usage import summarize_disk_usage
from app.services.processing import enqueue_media


def _resolve_user(session, telegram_username: str | None):
    if not telegram_username:
        return None
    normalized = telegram_username.lstrip("@").lower()
    return session.query(User).filter(User.telegram_username.ilike(normalized)).first()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Bot connected. Use /search <query>, /stats, /reindex <media_id> or inline mode via @bot query."
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usage = summarize_disk_usage()
    await update.message.reply_text(
        "\n".join(
            [
                f"Drive used: {usage['drive_used'] / 1024 / 1024 / 1024:.2f} GB",
                f"Project used: {usage['project']['total'] / 1024 / 1024 / 1024:.2f} GB",
                f"Other on drive: {usage['other_on_drive'] / 1024 / 1024 / 1024:.2f} GB",
            ]
        )
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_text = " ".join(context.args).strip()
    if not query_text:
        await update.message.reply_text("Usage: /search <query>")
        return
    session = SessionLocal()
    try:
        user = _resolve_user(session, update.effective_user.username)
        if user is None:
            await update.message.reply_text("Your Telegram username is not linked to a site user yet.")
            return
        rows = (
            session.query(MediaItem)
            .filter(MediaItem.owner_id == user.id, MediaItem.description.ilike(f"%{query_text}%"))
            .limit(10)
            .all()
        )
        if not rows:
            await update.message.reply_text("Nothing found.")
            return
        await update.message.reply_text("\n".join(f"{item.id}: {item.original_filename} [{item.kind.value}]" for item in rows))
    finally:
        session.close()


async def reindex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /reindex <media_id>")
        return
    try:
        job_id = enqueue_media(context.args[0])
        await update.message.reply_text(f"Queued job {job_id}")
    except Exception as exc:
        await update.message.reply_text(f"Failed: {exc}")


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_text = (update.inline_query.query or "").strip()
    session = SessionLocal()
    try:
        user = _resolve_user(session, update.inline_query.from_user.username)
        if user is None:
            await update.inline_query.answer(
                results=[
                    InlineQueryResultArticle(
                        id="not-linked",
                        title="Link Telegram username in the site profile",
                        input_message_content=InputTextMessageContent("Telegram account is not linked to the library yet."),
                        description="Set your Telegram username in the site profile first.",
                    )
                ],
                cache_time=1,
            )
            return

        query = session.query(MediaItem).filter(MediaItem.owner_id == user.id)
        if query_text:
            query = query.filter(MediaItem.description.ilike(f"%{query_text}%"))
        rows = query.limit(20).all()
        results = []
        base_url = settings.telegram_inline_base_url.rstrip("/")
        for item in rows:
            if base_url:
                file_url = f"{base_url}/api/media/{item.id}/file/public"
                thumb_url = f"{base_url}/api/media/{item.id}/thumbnail/public"
                if item.kind.value == "image":
                    results.append(InlineQueryResultPhoto(id=item.id, photo_url=file_url, thumbnail_url=thumb_url, title=item.original_filename, description=item.description or ""))
                elif item.kind.value == "gif":
                    results.append(InlineQueryResultGif(id=item.id, gif_url=file_url, thumbnail_url=thumb_url, title=item.original_filename, caption=item.description or ""))
                else:
                    results.append(InlineQueryResultVideo(id=item.id, video_url=file_url, mime_type=item.mime_type or "video/mp4", thumbnail_url=thumb_url, title=item.original_filename, caption=item.description or ""))
            else:
                results.append(
                    InlineQueryResultArticle(
                        id=item.id,
                        title=item.original_filename,
                        description=item.description or "",
                        input_message_content=InputTextMessageContent(f"{item.original_filename}\n{item.description or ''}"),
                    )
                )
        await update.inline_query.answer(results=results, cache_time=5, is_personal=True)
    finally:
        session.close()


def run_telegram_bot() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("search", search))
    application.add_handler(CommandHandler("reindex", reindex))
    application.add_handler(InlineQueryHandler(inline_query))
    application.run_polling()

