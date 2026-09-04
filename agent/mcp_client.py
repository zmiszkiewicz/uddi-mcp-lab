#!/usr/bin/env python3
"""
Minimal client for the hosted Infoblox Model Context Protocol (MCP) Server.

WHY THIS EXISTS
---------------
The Claude API has an `mcp_servers` connector that would make this file
unnecessary — Claude connects to the remote MCP server itself and you never
write a tool loop. That connector is not available on Amazon Bedrock, which is
where this lab's Claude runs. So the agent connects to the MCP server itself,
lists its tools, and executes tool calls on Claude's behalf.

That is not a workaround so much as a demonstration: it makes visible exactly
what the MCP server exposes and exactly which call each answer came from, which
is the point the lab is trying to teach.

AUTHENTICATION
--------------
`Authorization: Token <service api key>` — the same header the MCP Server
expects everywhere. The key is read from disk on every connection rather than
captured once, so swapping the file swaps the agent's permissions with no
restart. That is what makes the read-only -> read/write handover in Challenge 3
real rather than narrative.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lab_config as cfg  # noqa: E402


class McpUnavailable(RuntimeError):
    """The MCP server could not be reached or refused the connection."""


def read_key():
    """
    The Service API key currently in force.

    Read fresh every time. 03/setup-shell writes the read/write key here when
    the learner reaches the approval gate; 05/setup-shell writes the read-only
    key back for the RBAC exercise.
    """
    try:
        with open(cfg.AGENT_KEY_FILE) as handle:
            key = handle.read().strip()
    except FileNotFoundError:
        raise McpUnavailable(
            f"No Infoblox Service API key at {cfg.AGENT_KEY_FILE}. The track "
            f"setup did not finish — restart the track."
        ) from None
    if not key:
        raise McpUnavailable(f"{cfg.AGENT_KEY_FILE} is empty.")
    return key


def auth_headers():
    return {"Authorization": f"Token {read_key()}"}


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #

class McpSession:
    """
    One MCP session, held open for the duration of a single agent turn.

    Opened per turn rather than per process so that a key swap between turns
    takes effect, and so a dropped connection cannot wedge the whole app.

    TODO-27 — transport. This uses Streamable HTTP, the current MCP standard
    transport and what a hosted server at an /mcp path almost certainly speaks.
    If the Infoblox MCP Server turns out to expose HTTP+SSE instead, swap
    `streamablehttp_client` for `sse_client` from `mcp.client.sse`; the rest of
    this class is transport-agnostic. Confirm against the MCP Setup Guide.
    """

    def __init__(self, url=None):
        self.url = url or cfg.MCP_SERVER_URL
        self._exit_stack = None
        self._session = None

    async def __aenter__(self):
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._exit_stack = AsyncExitStack()
        try:
            read, write, _ = await self._exit_stack.enter_async_context(
                streamablehttp_client(self.url, headers=auth_headers())
            )
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
        except Exception as exc:                       # noqa: BLE001
            await self._exit_stack.aclose()
            raise McpUnavailable(
                f"Could not connect to the Infoblox MCP Server at {self.url}. "
                f"Access is gated by RBAC — if the service user behind your key "
                f"has no MCP Server role the connection is refused outright. "
                f"({exc})"
            ) from exc
        return self

    async def __aexit__(self, *exc_info):
        if self._exit_stack:
            await self._exit_stack.aclose()

    async def list_tools(self):
        """
        Every tool the server exposes, as Anthropic tool definitions.

        The shapes line up almost exactly — an MCP tool has name, description
        and inputSchema; a Claude tool wants name, description and input_schema.
        """
        result = await self._session.list_tools()
        tools = []
        for tool in result.tools:
            schema = tool.inputSchema or {"type": "object", "properties": {}}
            tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": schema,
            })
        return tools

    async def call_tool(self, name, arguments):
        """
        Execute one tool call and return its result as text.

        Errors are returned as text rather than raised: Claude handles "that
        call failed because X" far better than the agent loop dying, and a
        denied write is a legitimate, expected outcome in Challenge 5.
        """
        try:
            result = await self._session.call_tool(name, arguments or {})
        except Exception as exc:                       # noqa: BLE001
            return f"Tool call failed: {exc}", True

        chunks = []
        for block in result.content or []:
            text = getattr(block, "text", None)
            chunks.append(text if text is not None else str(block))

        return ("\n".join(chunks) or "(no content returned)",
                bool(getattr(result, "isError", False)))
