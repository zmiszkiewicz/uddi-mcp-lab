#!/usr/bin/env python3
"""
The chat tab the learner works in.

Streamlit, deliberately: the track needs one browser tab with a chat box and a
visible record of which Infoblox calls produced which answer, and that is about
forty lines of Streamlit versus a frontend build nobody will maintain.

Every tool call is rendered as it happens, collapsed but expandable. That is a
teaching decision — the whole argument of the lab is "the agent reasons,
Infoblox supplies the trusted data", and a learner who can see `get_host_config`
fire and return the smoking gun believes that in a way they will not from prose.

Run:  streamlit run app.py --server.port 8501 --server.address 0.0.0.0
"""

import asyncio
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lab_config as cfg  # noqa: E402
from bedrock_agent import preflight, run_turn  # noqa: E402
from mcp_client import McpUnavailable  # noqa: E402


st.set_page_config(page_title="TechCorp Operations Assistant",
                   page_icon="🛠️", layout="wide")


# --------------------------------------------------------------------------- #
# Sidebar — what the learner is actually connected to
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.markdown("### Connection")
    st.caption("Infoblox MCP Server")
    st.code(cfg.MCP_SERVER_URL, language=None)
    st.caption("Model (Amazon Bedrock)")
    st.code(cfg.BEDROCK_MODEL_ID, language=None)
    st.caption(f"Region: `{cfg.AWS_REGION}`")

    # Which key is in force right now. This changes when Challenge 3 hands over
    # the read/write key, and changes back at Challenge 5 — worth showing,
    # because "which credential am I acting as" is the lesson.
    st.markdown("### Credential")
    try:
        from mcp_client import read_key
        key = read_key()
        st.success(f"Service API key active (…{key[-6:]})")
    except McpUnavailable as exc:
        st.error(str(exc))

    if st.button("Clear conversation"):
        st.session_state.history = []
        st.session_state.transcript = []
        st.rerun()


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

if "history" not in st.session_state:
    st.session_state.history = []       # the message list Claude sees
if "transcript" not in st.session_state:
    st.session_state.transcript = []    # what we render, incl. tool calls

st.title("TechCorp Operations Assistant")
st.caption("Connected to Infoblox Universal DDI through the Infoblox Model "
           "Context Protocol (MCP) Server.")

problems = preflight()
if problems:
    st.error("The assistant is not ready:\n\n"
             + "\n".join(f"- {p}" for p in problems))
    st.stop()


def render(entry):
    """Render one transcript entry."""
    if entry["kind"] == "user":
        with st.chat_message("user"):
            st.markdown(entry["text"])
    elif entry["kind"] == "text":
        with st.chat_message("assistant"):
            st.markdown(entry["text"])
    elif entry["kind"] == "tool":
        icon = "🔧" if not entry.get("is_error") else "⚠️"
        with st.expander(f"{icon} Infoblox · `{entry['name']}`", expanded=False):
            st.caption("Request")
            st.json(entry.get("input") or {})
            st.caption("Response")
            st.code(str(entry.get("content", ""))[:8000], language=None)


for entry in st.session_state.transcript:
    render(entry)


# --------------------------------------------------------------------------- #
# Turn
# --------------------------------------------------------------------------- #

prompt = st.chat_input("Ask about your DNS, DHCP, or IP address management…")

if prompt:
    st.session_state.transcript.append({"kind": "user", "text": prompt})
    render(st.session_state.transcript[-1])

    live = st.container()
    pending = {}

    def on_event(kind, payload):
        """
        Called from inside the agent loop as things happen.

        Tool calls are rendered the moment they fire rather than after the turn
        completes, so a long investigation shows progress instead of a spinner.
        """
        if kind == "tool_call":
            pending["name"] = payload["name"]
            pending["input"] = payload["input"]
        elif kind == "tool_result":
            entry = {
                "kind": "tool",
                "name": payload["name"],
                "input": pending.get("input"),
                "content": payload["content"],
                "is_error": payload["is_error"],
            }
            st.session_state.transcript.append(entry)
            with live:
                render(entry)
        elif kind == "text" and payload.strip():
            entry = {"kind": "text", "text": payload}
            st.session_state.transcript.append(entry)
            with live:
                render(entry)

    with st.spinner("Querying Infoblox…"):
        try:
            asyncio.run(run_turn(prompt, st.session_state.history, on_event))
        except McpUnavailable as exc:
            st.error(str(exc))
        except Exception as exc:                       # noqa: BLE001
            st.error(f"The assistant hit an error: {exc}")
