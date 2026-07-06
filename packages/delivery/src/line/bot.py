"""
LINE delivery channel for Mantis Scout.

LINE Messaging API is webhook-based (no polling), so this module runs
a small aiohttp server that LINE platform POSTs events to, and exposes
push_message for the signal dispatcher.

Requires LINE_CHANNEL_ACCESS_TOKEN + LINE_CHANNEL_SECRET.
Run as part of the delivery worker (src/worker.py) alongside Telegram
and Discord.
"""
from __future__ import annotations

import logging

from aiohttp import web
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import FollowEvent, MessageEvent, TextMessageContent, UnfollowEvent

from src.line.commands import handle_follow, handle_text, handle_unfollow
from src.common.subscription_manager import SubscriptionManager

log = logging.getLogger(__name__)

line_sub_manager = SubscriptionManager(channel="line")


def build_app(
    channel_secret: str,
    messaging_api: AsyncMessagingApi,
    sub_manager: SubscriptionManager,
    stats: dict,
) -> web.Application:
    parser = WebhookParser(channel_secret)

    async def callback(request: web.Request) -> web.Response:
        signature = request.headers.get("X-Line-Signature", "")
        body      = await request.text()

        try:
            events = parser.parse(body, signature)
        except InvalidSignatureError:
            return web.Response(status=400, text="invalid signature")

        for event in events:
            try:
                if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                    reply = handle_text(event.message.text, event.source.user_id, sub_manager, stats)
                    await messaging_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
                    )
                elif isinstance(event, FollowEvent):
                    reply = handle_follow(event.source.user_id, sub_manager)
                    await messaging_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
                    )
                elif isinstance(event, UnfollowEvent):
                    handle_unfollow(event.source.user_id, sub_manager)
            except Exception as exc:
                log.warning("Error handling LINE event: %s", exc)

        return web.Response(status=200, text="OK")

    app = web.Application()
    app.router.add_post("/callback", callback)
    return app


def build_messaging_api(channel_access_token: str) -> tuple[AsyncApiClient, AsyncMessagingApi]:
    config     = Configuration(access_token=channel_access_token)
    api_client = AsyncApiClient(config)
    return api_client, AsyncMessagingApi(api_client)


async def run_line_bot(
    channel_secret: str,
    messaging_api: AsyncMessagingApi,
    sub_manager: SubscriptionManager,
    stats: dict,
    port: int = 8000,
) -> None:
    app    = build_app(channel_secret, messaging_api, sub_manager, stats)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("LINE webhook server listening on :%d/callback", port)

    try:
        # Keep the server alive until cancelled by the worker
        import asyncio
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def send_signal_to_subscribers(messaging_api: AsyncMessagingApi, message: str, user_ids: list[str]) -> list[str]:
    """Push a formatted signal message to a list of LINE user ids. Returns ids that succeeded."""
    delivered = []
    for user_id in user_ids:
        try:
            await messaging_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=message)])
            )
            delivered.append(user_id)
        except Exception as exc:
            log.warning("Failed to push to LINE user_id=%s: %s", user_id, exc)
    return delivered
