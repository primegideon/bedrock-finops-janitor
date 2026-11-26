"""
Tether AgentCore Client.

Invokes the deployed Amazon Bedrock AgentCore Managed Harness
(Tether_Managed_Harness) and parses hook events, tool executions,
and model responses from the InvokeHarness stream.
"""

import importlib.util
import os
import uuid
from typing import Any, Dict, List, Optional

from botocore.config import Config

from agent.aws_config import (
    DEFAULT_HARNESS_ENDPOINT,
    get_aws_session,
    resolve_harness_arn,
)


def _load_local_action_handler():
    handler_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "lambda", "action_handler.py")
    )
    spec = importlib.util.spec_from_file_location("tether_action_handler", handler_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TetherAgentCore:
    def __init__(
        self,
        harness_arn: Optional[str] = None,
        endpoint_name: Optional[str] = None,
        profile_name: Optional[str] = None,
    ):
        self.endpoint_name = endpoint_name or os.environ.get(
            "TETHER_HARNESS_ENDPOINT", DEFAULT_HARNESS_ENDPOINT
        )
        is_mock = os.environ.get("MOCK_MODE", "false").lower() == "true"
        self.session = None if is_mock else get_aws_session(profile_name=profile_name)
        self.agentcore_client = (
            self.session.client(
                "bedrock-agentcore",
                config=Config(read_timeout=180, connect_timeout=15),
            )
            if self.session
            else None
        )

        if harness_arn:
            self.harness_arn = harness_arn
        elif self.session is not None:
            self.harness_arn = resolve_harness_arn(self.session)
        else:
            self.harness_arn = "mock-harness"

    def invoke(
        self,
        user_input: str,
        session_id: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a prompt to the live Amazon Bedrock AgentCore Managed Harness.
        Returns structured status, hook decisions, tool calls, and text output.
        """
        if not self.agentcore_client:
            return self._mock_invocation(user_input)

        kwargs: Dict[str, Any] = {
            "harnessArn": self.harness_arn,
            "qualifier": self.endpoint_name,
            "runtimeSessionId": session_id or str(uuid.uuid4()),
            "messages": [{"role": "user", "content": [{"text": user_input}]}],
        }
        model_id = model_override or os.environ.get("TETHER_MODEL_ID")
        if model_id:
            kwargs["model"] = {
                "bedrockModelConfig": {
                    "modelId": model_id,
                    "apiFormat": "converse_stream",
                }
            }

        resp = self.agentcore_client.invoke_harness(**kwargs)

        text_parts: List[str] = []
        hook_events: List[Dict[str, Any]] = []
        tool_calls: List[Dict[str, Any]] = []
        stop_reason = None

        for event in resp["stream"]:
            if "hookEvent" in event:
                hook_events.append(event["hookEvent"])
            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                if "toolUse" in start:
                    tool_calls.append(start["toolUse"])
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    text_parts.append(delta["text"])
            if "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason")

        denied_hooks = [h for h in hook_events if h.get("decision") == "deny"]
        status = "blocked" if stop_reason == "hook_stopped" else "success"
        reason = "; ".join(h.get("reason", "") for h in denied_hooks if h.get("reason"))

        return {
            "status": status,
            "stopReason": stop_reason,
            "hook_events": hook_events,
            "tool_calls": tool_calls,
            "reason": reason,
            "message": "".join(text_parts).strip() or reason,
        }

    def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute local action_handler for offline unit testing."""
        handler = _load_local_action_handler()
        return handler.lambda_handler({"tool": tool_name, "input": tool_input or {}}, None)

    def _mock_invocation(self, user_input: str) -> Dict[str, Any]:
        """Simulation logic for offline unit testing."""
        lower_input = user_input.lower()

        if any(term in lower_input for term in ["sudo", "ignore previous instructions", "superadmin", "drop table"]):
            return {
                "status": "blocked",
                "stopReason": "hook_stopped",
                "reason": "Blocked by Tether-Safety-Guardrail: Prompt attack filter triggered",
                "message": "Access Denied: The requested prompt violates Tether safety boundaries.",
            }

        if "terminate" in lower_input or "delete" in lower_input:
            return {
                "status": "blocked",
                "stopReason": "hook_stopped",
                "reason": "Blocked by Tether-Safety-Guardrail: Destructive operations policy triggered",
                "message": "Access Denied: Terminate and delete operations are strictly prohibited.",
            }

        if "audit" in lower_input or "list" in lower_input:
            res = self._execute_tool("audit_waste", {})
            return {
                "status": "success",
                "tool_calls": [{"name": "audit_waste"}],
                "result": res,
                "message": f"Audited {len(res.get('resources', []))} resources.",
            }

        return {
            "status": "success",
            "message": "Tether is standing by to audit development resources.",
        }
