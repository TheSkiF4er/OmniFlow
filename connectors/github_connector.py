"""
OmniFlow — Universal Automation & Workflow Engine
GitHub Connector

This module provides:
  - Issue creation & editing
  - PR creation & merging
  - Repository content read/write
  - GitHub Actions workflow triggers
  - Webhook event receiver (for OmniFlow events)
  - OmniFlow workflow-step integration

Requirements:
  - aiohttp
"""

import json
import logging
from typing import Optional, Dict, Any

import aiohttp
from aiohttp import web

logger = logging.getLogger("omniflow.github")


class GitHubConnector:
    """
    GitHub Connector for OmniFlow.

    Example workflow block:

    {
        "type": "github.create_issue",
        "config": {
            "token": "GITHUB_TOKEN",
            "repo": "username/repo",
            "title": "Bug: workflow failed",
            "body": "Full logs attached..."
        }
    }
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str):
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None

    # -------------------------------------------------------------
    # HTTP Client
    # -------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def api_request(
        self, method: str, endpoint: str, data: Optional[dict] = None
    ) -> dict:

        url = f"{self.BASE_URL}{endpoint}"
        session = await self._get_session()

        try:
            resp = await session.request(method, url, json=data)
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                payload = await resp.json()
            else:
                payload = await resp.text()

            if resp.status >= 300:
                raise RuntimeError(f"GitHub API error {resp.status}: {payload}")

            return payload

        except Exception as e:
            logger.error(f"GitHub API request failed: {e}")
            raise

    # -------------------------------------------------------------
    # Issues
    # -------------------------------------------------------------

    async def create_issue(self, repo: str, title: str, body: str = "", labels=None):
        labels = labels or []
        data = {"title": title, "body": body, "labels": labels}

        return await self.api_request("POST", f"/repos/{repo}/issues", data=data)

    async def comment_issue(self, repo: str, issue_number: int, comment: str):
        data = {"body": comment}

        return await self.api_request(
            "POST", f"/repos/{repo}/issues/{issue_number}/comments", data=data
        )

    # -------------------------------------------------------------
    # Pull Requests
    # -------------------------------------------------------------

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ):
        data = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }

        return await self.api_request("POST", f"/repos/{repo}/pulls", data=data)

    async def merge_pull_request(
        self, repo: str, pr_number: int, message: str = "Merged via OmniFlow"
    ):
        data = {"commit_message": message}

        return await self.api_request(
            "PUT", f"/repos/{repo}/pulls/{pr_number}/merge", data=data
        )

    # -------------------------------------------------------------
    # Repository Content
    # -------------------------------------------------------------

    async def get_file(self, repo: str, path: str, ref: str = "main"):
        return await self.api_request(
            "GET", f"/repos/{repo}/contents/{path}?ref={ref}"
        )

    async def update_file(
        self,
        repo: str,
        path: str,
        content_base64: str,
        sha: str,
        message: str = "Update file via OmniFlow",
        branch: str = "main",
    ):
        data = {
            "message": message,
            "content": content_base64,
            "sha": sha,
            "branch": branch,
        }

        return await self.api_request(
            "PUT", f"/repos/{repo}/contents/{path}", data=data
        )

    # -------------------------------------------------------------
    # GitHub Actions workflow
    # -------------------------------------------------------------

    async def trigger_workflow(
        self, repo: str, workflow_file: str, ref: str = "main", inputs: dict = None
    ):
        data = {"ref": ref}
        if inputs:
            data["inputs"] = inputs

        return await self.api_request(
            "POST", f"/repos/{repo}/actions/workflows/{workflow_file}/dispatches", data=data
        )

    # -------------------------------------------------------------
    # Webhook Receiver (for OmniFlow)
    # -------------------------------------------------------------

    @staticmethod
    async def webhook_handler(request: web.Request):
        """
        Handles GitHub webhooks and converts them into OmniFlow events.

        Add in OmniFlow server:

            app.router.add_post("/webhooks/github", GitHubConnector.webhook_handler)

        """
        try:
            payload = await request.json()
            event_type = request.headers.get("X-GitHub-Event")
            delivery_id = request.headers.get("X-GitHub-Delivery")

            logger.info(f"GitHub Webhook received ({event_type}, {delivery_id})")

            # Convert to OmniFlow event format
            return web.json_response(
                {"status": "ok", "event": event_type, "payload": payload}
            )

        except Exception as e:
            logger.error(f"GitHub webhook parsing error: {e}")
            return web.json_response({"error": str(e)}, status=400)

    # -------------------------------------------------------------
    # OmniFlow Workflow Step Executor
    # -------------------------------------------------------------

    async def execute_step(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes workflow steps described in OmniFlow JSON.

        Example config:

        {
            "action": "create_issue",
            "repo": "user/repo",
            "title": "...",
            "body": "..."
        }
        """

        action = config.get("action")

        if action == "create_issue":
            return await self.create_issue(
                config["repo"], config["title"], config.get("body", "")
            )

        if action == "comment_issue":
            return await self.comment_issue(
                config["repo"],
                config["issue_number"],
                config["comment"]
            )

        if action == "create_pull_request":
            return await self.create_pull_request(
                config["repo"],
                config["title"],
                config["head"],
                config["base"],
                config.get("body", ""),
                config.get("draft", False),
            )

        if action == "merge_pull_request":
            return await self.merge_pull_request(
                config["repo"],
                config["pr_number"],
                config.get("message", "Merged via OmniFlow"),
            )

        if action == "trigger_workflow":
            return await self.trigger_workflow(
                config["repo"],
                config["workflow_file"],
                config.get("ref", "main"),
                config.get("inputs"),
            )

        raise ValueError(f"Unknown GitHub action: {action}")

    # -------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------

    async def close(self):
        if self._session:
            await self._session.close()


# -------------------------------------------------------------
# Test usage (local)
# -------------------------------------------------------------

async def _test():
    import os

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO", "octocat/Hello-World")

    gh = GitHubConnector(token)

    issue = await gh.create_issue(repo, "Test from OmniFlow", "Automated issue.")
    print("Created issue:", issue)

    await gh.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(_test())
