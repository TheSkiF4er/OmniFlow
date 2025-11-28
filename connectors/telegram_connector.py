"""
Telegram Connector for OmniFlow — Universal Automation & Workflow Engine
-----------------------------------------------------------------------

Provides a standardized interface to interact with Telegram Bot API
for sending messages, handling updates, and triggering OmniFlow events.

Features:
- Send messages, photos, documents
- Inline keyboards & custom replies
- Polling or webhook mode
- Async workflow-step integration with OmniFlow
- Logging and error handling
- Supports multiple bots

Dependencies:
    pip install aiohttp python-dotenv
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List

import aiohttp
from aiohttp import web

logger = logging.getLogger("omniflow.telegram")


class TelegramConnector:
    """
    Telegram Bot Connector for OmniFlow.
    """

    BASE_URL = "https://api.telegram.org"

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self._session: Optional[aiohttp.ClientSession] = None

    # -------------------------------------------------------------
    # HTTP Client
    # -------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def api_request(
        self, method: str, endpoint: str, data: Optional[dict] = None
    ) -> dict:
        url = f"{self.BASE_URL}/bot{self.bot_token}/{endpoint}"
        session = await self._get_session()
        try:
            async with session.post(url, json=data) as resp:
                resp_json = await resp.json()
                if not resp_json.get("ok", False):
                    raise RuntimeError(f"Telegram API error: {resp_json}")
                return resp_json
        except Exception as e:
            logger.error(f"Telegram API request failed: {e}")
            raise

    # -------------------------------------------------------------
    # Messaging
    # -------------------------------------------------------------

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup: dict = None
    ):
        data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return await self.api_request("POST", "sendMessage", data=data)

    async def send_photo(self, chat_id: int, photo_url: str, caption: str = ""):
        data = {"chat_id": chat_id, "photo": photo_url, "caption": caption}
        return await self.api_request("POST", "sendPhoto", data=data)

    async def send_document(self, chat_id: int, document_url: str, caption: str = ""):
        data = {"chat_id": chat_id, "document": document_url, "caption": caption}
        return await self.api_request("POST", "sendDocument", data=data)

    # -------------------------------------------------------------
    # Updates (Polling)
    # -------------------------------------------------------------

    async def get_updates(self, offset: int = 0, timeout: int = 30):
        data = {"offset": offset, "timeout": timeout}
        resp = await self.api_request("POST", "getUpdates", data=data)
        return resp.get("result", [])

    # -------------------------------------------------------------
    # Webhook handler for OmniFlow
    # -------------------------------------------------------------

    @staticmethod
    async def webhook_handler(request: web.Request):
        """
        Handles incoming Telegram updates via webhook and converts
        them into OmniFlow events.
        """
        try:
            payload = await request.json()
            update_id = payload.get("update_id")
            message = payload.get("message", {})

            logger.info(f"Received Telegram update: {update_id}")

            # Convert into OmniFlow event format
            return web.json_response({"status": "ok", "update_id": update_id, "message": message})

        except Exception as e:
            logger.error(f"Telegram webhook parsing error: {e}")
            return web.json_response({"error": str(e)}, status=400)

    # -------------------------------------------------------------
    # Workflow Step Integration
    # -------------------------------------------------------------

    async def execute_step(self, config: Dict[str, Any]):
        """
        Execute Telegram workflow step in OmniFlow.

        Example config:
        {
            "action": "send_message",
            "chat_id": 123456,
            "text": "Hello from OmniFlow"
        }
        """
        action = config.get("action")
        if action == "send_message":
            return await self.send_message(config["chat_id"], config["text"])
        elif action == "send_photo":
            return await self.send_photo(config["chat_id"], config["photo_url"], config.get("caption", ""))
        elif action == "send_document":
            return await self.send_document(config["chat_id"], config["document_url"], config.get("caption", ""))
        else:
            raise ValueError(f"Unknown Telegram action: {action}")

    # -------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------

    async def close(self):
        if self._session:
            await self._session.close()


# -------------------------------------------------------------
# Test / Local Usage
# -------------------------------------------------------------
if __name__ == "__main__":
    import os

    async def _test():
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = int(os.getenv("TELEGRAM_TEST_CHAT_ID", "0"))

        connector = TelegramConnector(token)
        await connector.send_message(chat_id, "Hello from OmniFlow Telegram Connector!")
        await connector.close()

    asyncio.run(_test())
  
