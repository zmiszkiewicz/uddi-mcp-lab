#!/usr/bin/env python3
"""
The agent loop: Claude on Amazon Bedrock, driving the Infoblox MCP Server.

    learner prompt
        -> Claude (Bedrock)
        -> tool_use blocks
        -> executed against the Infoblox MCP Server
        -> tool_result blocks back to Claude
        -> repeat until Claude stops calling tools
        -> answer

A manual loop rather than the SDK's tool runner, for two specific reasons: the
runner is beta and this runs on Bedrock, and we want each tool call surfaced in
the UI as it happens so the learner can see which Infoblox call produced which
part of the answer. That visibility is a teaching goal, not a debug aid.

Credentials come from the Instruqt AWS sandbox account (Bedrock) and from the
participant's Infoblox Service API key (MCP). Neither is hardcoded.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lab_config as cfg  # noqa: E402
from mcp_client import McpSession, McpUnavailable  # noqa: E402


SYSTEM_PROMPT = """\
You are an operations assistant for the network team at TechCorp, connected to \
Infoblox Universal DDI through the Infoblox Model Context Protocol (MCP) Server.

You are talking to the on-call network operator. Ground every factual claim \
about their environment in a tool call — you have live access to their DNS, \
DHCP, IPAM and asset data, so never answer from general knowledge about how DNS \
usually works when you could look up how THEIR DNS is actually configured. If a \
tool call returns nothing useful, say so plainly rather than filling the gap \
with a plausible guess.

Two habits matter more than anything else:

Before making any change, state exactly what you intend to change — which \
objects, which fields, which values — and wait for the operator to approve it. \
There is no dry-run mode; write operations take effect immediately against a \
real tenant. Never batch an unrequested change in alongside an approved one.

When you are diagnosing, distinguish what is *configured* from what is actually \
*happening*. Most real faults live in the gap between the two.

Be concise and concrete. This operator is technical and is working an incident.\
"""

MAX_ITERATIONS = 12


def bedrock_client():
    """
    Claude on Bedrock, in the Instruqt-provided AWS sandbox account.

    Credentials resolve through the standard boto3 chain — track_scripts/
    setup-shell writes them to /root/.aws/credentials, so nothing is passed in
    here and no key is ever held in this process.
    """
    from anthropic import AnthropicBedrockMantle
    return AnthropicBedrockMantle(aws_region=cfg.AWS_REGION)


async def run_turn(user_message, history, on_event=None):
    """
    Run one full turn: everything from the learner's message to a final answer,
    however many tool calls that takes.

    `history` is the running message list and is mutated in place, so the caller
    keeps the conversation across turns.

    `on_event(kind, payload)` is called as things happen — kind is one of
    "thinking", "tool_call", "tool_result", "text" — so the UI can show tool
    calls live instead of after the fact.

    Returns the final assistant text.
    """
    def emit(kind, payload):
        if on_event:
            on_event(kind, payload)

    client = bedrock_client()
    history.append({"role": "user", "content": user_message})

    async with McpSession() as mcp:
        tools = await mcp.list_tools()
        emit("thinking", f"{len(tools)} Infoblox tools available")

        final_text = ""
        for _ in range(MAX_ITERATIONS):
            response = client.messages.create(
                model=cfg.BEDROCK_MODEL_ID,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=history,
            )

            # Surface any prose in this turn before running the tool calls, so
            # the learner sees the reasoning ahead of the actions.
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    emit("text", block.text)
                    final_text = block.text

            if response.stop_reason != "tool_use":
                history.append({"role": "assistant", "content": response.content})
                return final_text

            history.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                emit("tool_call", {"name": block.name, "input": block.input})
                content, is_error = await mcp.call_tool(block.name, block.input)
                emit("tool_result", {"name": block.name, "content": content,
                                     "is_error": is_error})

                # Every tool_use block needs a matching tool_result in ONE user
                # message — splitting them across messages teaches Claude to
                # stop making parallel calls.
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })

            history.append({"role": "user", "content": tool_results})

        emit("text", "Stopped after too many tool calls without reaching an "
                     "answer. Try narrowing the question.")
        return final_text


def preflight():
    """
    Check both halves of the agent before the UI accepts input, so a
    misconfigured lab says so instead of failing on the learner's first message.

    Returns a list of human-readable problems; empty means good to go.
    """
    problems = []

    try:
        from mcp_client import read_key
        read_key()
    except McpUnavailable as exc:
        problems.append(str(exc))

    try:
        bedrock_client()
    except Exception as exc:                           # noqa: BLE001
        problems.append(
            f"Could not create the Bedrock client in {cfg.AWS_REGION}: {exc}"
        )

    return problems
