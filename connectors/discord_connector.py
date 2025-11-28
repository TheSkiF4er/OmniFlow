"""
OmniFlow — Universal Automation & Workflow Engine
Discord Connector

This connector provides:
  - Sending messages to channels
  - Listening for messages/events via Discord Gateway
  - Rich embeds
  - File uploads
  - Workflow step integration
  - Async execution compatible with OmniFlow task executor

Requires:
  - aiohttp
  - websockets

"""

import json
import asyncio
import logging
from typing import Dict, Any, Optional, List

import aiohttp
import websockets

logger = logging.getLogger("omniflow.discord")


class DiscordConnector:
    """
    Discord Connector for OmniFlow.

    You can use this connector inside any workflow block:

    Example (inside OmniFlow workflow JSON):

    {
        "type": "discord.send_message",
        "config": {
            "token": "BOT_TOKEN",
            "channel_id": "123456789",
            "message": "Workflow started!"
        }
    }

    """

    BASE_URL = "https://discord.com/api/v10"
    GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.session: Optional[aiohttp.ClientSession] = None
        self.gateway_socket = None
        self.heartbeat_interval = None
        self.sequence = None

    # ---------------------------------------------------------
    # HTTP Client
    # ---------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={"Authorization": f"Bot {self.bot_token}"}
            )
        return self.session

    async def api_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
        files: Optional[dict] = None,
    ) -> dict:
        """
        Sends a REST API request to Discord.
        """

        url = f"{self.BASE_URL}{endpoint}"
        session = await self._get_session()

        try:
            if files:
                form = aiohttp.FormData()
                for name, f in files.items():
                    form.add_field(name, f["data"], filename=f.get("filename"))
                if data:
                    form.add_field("payload_json", json.dumps(data))
                resp = await session.request(method, url, data=form)
            else:
                resp = await session.request(method, url, json=data)

            content = await resp.json()
            if resp.status >= 300:
                raise RuntimeError(f"Discord API error {resp.status}: {content}")

            return content

        except Exception as e:
            logger.error(f"Discord API error: {e}")
            raise

    # ---------------------------------------------------------
    # Messaging
    # ---------------------------------------------------------

    async def send_message(
        self,
        channel_id: str,
        message: str,
        embed: Optional[Dict[str, Any]] = None,
        file: Optional[str] = None,
    ) -> dict:
        """
        Sends text message, embed, or file to a specific channel.
        """

        payload = {"content": message}
        if embed:
            payload["embeds"] = [embed]

        files = None
        if file:
            with open(file, "rb") as f:
                files = {"file": {"data": f.read(), "filename": file}}

        return await self.api_request(
            "POST", f"/channels/{channel_id}/messages", data=payload, files=files
        )

    # ---------------------------------------------------------
    # Listening to Discord events (Gateway API)
    # ---------------------------------------------------------

    async def connect_gateway(self):
        """
        Connects to Discord Gateway WebSocket and identifies the bot.
        """

        logger.info("Connecting to Discord Gateway...")

        self.gateway_socket = await websockets.connect(self.GATEWAY_URL)

        # Receive hello
        hello = json.loads(await self.gateway_socket.recv())
        self.heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000

        # Identify payload
        identify = {
            "op": 2,
            "d": {
                "token": self.bot_token,
                "intents": 513,  # Guild messages + direct messages
                "properties": {"os": "linux", "browser": "omniflow", "device": "omniflow"},
            },
        }
        await self.gateway_socket.send(json.dumps(identify))

        # Start heartbeat
        asyncio.create_task(self._heartbeat())

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            hb = {"op": 1, "d": self.sequence}
            await self.gateway_socket.send(json.dumps(hb))

    async def listen_events(self, on_event):
        """
        Listens for events and forwards them to workflow blocks.
        """

        if not self.gateway_socket:
            await self.connect_gateway()

        logger.info("Listening for Discord events...")

        try:
            while True:
                msg = await self.gateway_socket.recv()
                event = json.loads(msg)

                op = event.get("op")
                event_type = event.get("t")
                data = event.get("d")

                # Update sequence number
                if "s" in event:
                    self.sequence = event["s"]

                if event_type:
                    logger.debug(f"Received event: {event_type}")
                    await on_event(event_type, data)

        except Exception as e:
            logger.error(f"Discord Gateway error: {e}")

    # ---------------------------------------------------------
    # OmniFlow Workflow Step API
    # ---------------------------------------------------------

    async def execute_step(self, step_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a workflow step described in OmniFlow JSON.
        """

        action = step_config.get("action")

        if action == "send_message":
            return await self.send_message(
                step_config["channel_id"],
                step_config.get("message", ""),
                embed=step_config.get("embed"),
                file=step_config.get("file"),
            )

        raise ValueError(f"Unknown Discord action: {action}")

    # ---------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------

    async def close(self):
        if self.session:
            await self.session.close()
        if self.gateway_socket:
            await self.gateway_socket.close()


# -------------------------------------------------------------
# Standalone usage (for testing)
# -------------------------------------------------------------

async def _test():
    import os

    token = os.getenv("DISCORD_BOT_TOKEN")
    channel = os.getenv("DISCORD_TEST_CHANNEL")

    dc = DiscordConnector(bot_token=token)
    resp = await dc.send_message(channel, "Hello from OmniFlow Discord connector!")
    print(resp)

    await dc.close()


if __name__ == "__main__":
    asyncio.run(_test())
          
