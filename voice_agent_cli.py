"""
Terminal REPL to test the voice agent without Twilio phone calls.

It boots the same Flask app (`create_app()`), uses the same OpenAI client,
tenant config, and the exact same tool-calling logic used by
`blueprints/voice_agent.py`.
"""

from __future__ import annotations

import argparse
import sys

from app import create_app
from config import Config


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Voice agent terminal tester (no Twilio).")
    p.add_argument(
        "--tenant-id",
        default=None,
        help="Tenant id to load tenant config + integration credentials (optional).",
    )
    p.add_argument(
        "--call-sid",
        default="CLI_CALL",
        help="Call SID to store in session state (optional).",
    )
    p.add_argument(
        "--system-only",
        action="store_true",
        help="Ignore tenant config/integrations even if tenant-id is provided.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    app = create_app()

    # Lazy imports so Flask app is fully initialized first.
    from blueprints.voice_agent import (
        _get_tenant_agent_config,
        _init_voice_session_state,
        _voice_may_use_tools,
        get_openai_response,
    )
    from utils.tenant_google_calendar_credentials import tenant_may_use_calendar_tools
    from utils.tenant_hubspot_credentials import tenant_may_use_hubspot_tools

    with app.app_context():
        tenant_id = args.tenant_id if not args.system_only else None
        tenant_cfg = _get_tenant_agent_config(tenant_id) if tenant_id else {}

        state = _init_voice_session_state(
            tenant_id=tenant_id,
            tenant_config=tenant_cfg,
            call_sid=args.call_sid,
        )
        messages = state["messages"]

        print("Voice agent CLI ready.")
        if tenant_id:
            print(f"- tenant_id: {tenant_id}")
        print("- Type your message and press Enter.")
        print("- Commands: /quit, /reset, /tools, /history")
        print()

        while True:
            try:
                user_text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if not user_text:
                continue

            if user_text.lower() in {"/quit", "/exit"}:
                return 0

            if user_text.lower() == "/reset":
                tenant_cfg = _get_tenant_agent_config(tenant_id) if tenant_id else {}
                state = _init_voice_session_state(
                    tenant_id=tenant_id,
                    tenant_config=tenant_cfg,
                    call_sid=args.call_sid,
                )
                messages = state["messages"]
                print("assistant> Reset conversation.")
                continue

            if user_text.lower() == "/history":
                for m in messages:
                    role = m.get("role")
                    content = (m.get("content") or "").strip()
                    if content:
                        print(f"[{role}] {content}")
                continue

            if user_text.lower() == "/tools":
                allowed = (tenant_cfg or {}).get("allowed_actions")
                cal_ok = bool(state.get("credentials_json") and tenant_may_use_calendar_tools(allowed))
                hub_ok = bool(state.get("hubspot_access_token") and tenant_may_use_hubspot_tools(allowed))
                print(
                    "assistant> Tools enabled: "
                    + ", ".join([x for x, ok in [("google_calendar", cal_ok), ("hubspot", hub_ok)] if ok])
                    or "none"
                )
                continue

            # Mirror the websocket handler behavior.
            messages.append({"role": "user", "content": user_text})

            allowed_actions = (tenant_cfg or {}).get("allowed_actions")
            cred_json = state.get("credentials_json")
            hub_token = state.get("hubspot_access_token")

            tools_enabled = bool(
                (cred_json and tenant_may_use_calendar_tools(allowed_actions))
                or (hub_token and tenant_may_use_hubspot_tools(allowed_actions))
            )
            wants_tools = bool(tools_enabled and _voice_may_use_tools(messages, user_text))

            if wants_tools:
                wait_msg = (Config.VOICE_WAIT_MESSAGE or "One moment while I check that.").strip()
                print(f"assistant> {wait_msg}")

            hangup_state = {"requested": False}
            reply = get_openai_response(
                messages,
                credentials_json=cred_json if wants_tools else None,
                hubspot_access_token=hub_token if wants_tools else None,
                hangup_state=hangup_state,
            )
            messages.append({"role": "assistant", "content": reply})
            print(f"assistant> {reply}")

            if hangup_state.get("requested"):
                print("assistant> (end_call requested)")


if __name__ == "__main__":
    raise SystemExit(main())

