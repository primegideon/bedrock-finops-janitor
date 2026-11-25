"""
Tether AgentCore Harness for Amazon Bedrock.
Combines Bedrock Runtime Converse API, Guardrail (ID: 7eokel3iit9r),
and AWS Lambda tool execution (Tether-Action-Handler).
"""

import importlib.util
import json
import os
from typing import Any, Dict, List, Optional

import boto3

PROFILE_NAME = "bedrock-janitor"
REGION_NAME = "us-east-1"
GUARDRAIL_ID = "7eokel3iit9r"
GUARDRAIL_VERSION = "1"
DEFAULT_MODEL_ID = "us.meta.llama3-3-70b-instruct-v1:0"
LAMBDA_FUNCTION_NAME = "Tether-Action-Handler"

TOOL_DEFINITIONS = [
    {
        "toolSpec": {
            "name": "audit_waste",
            "description": "Lists EC2 instances and reports tags and eligibility for development cleanup.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {}
                }
            }
        }
    },
    {
        "toolSpec": {
            "name": "stop_dev_instances",
            "description": "Safely stops up to 3 development EC2 instances. Fails if count exceeds 3 or if targeting non-development instances.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "instance_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of up to 3 development EC2 instance IDs"
                        },
                        "confirmation_token": {
                            "type": "string",
                            "description": "Administrative approval token if requesting changes"
                        }
                    },
                    "required": ["instance_ids"]
                }
            }
        }
    }
]


def load_system_prompt() -> str:
    prompt_file = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read().strip()


def _load_local_action_handler():
    handler_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "lambda", "action_handler.py")
    )
    spec = importlib.util.spec_from_file_location("tether_action_handler", handler_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_aws_session():
    if os.environ.get("MOCK_MODE", "false").lower() == "true":
        return None
    return boto3.Session(profile_name=PROFILE_NAME, region_name=REGION_NAME)


class TetherAgentCore:
    def __init__(
        self,
        guardrail_id: str = GUARDRAIL_ID,
        guardrail_version: str = GUARDRAIL_VERSION,
        model_id: Optional[str] = None,
    ):
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version
        self.system_prompt = load_system_prompt()
        self.model_id = model_id or os.environ.get("TETHER_MODEL_ID", DEFAULT_MODEL_ID)
        self.session = _get_aws_session()
        self.client = self.session.client("bedrock-runtime") if self.session else None
        self.lambda_client = self.session.client("lambda") if self.session else None

    def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch tool call to AWS Lambda or local action_handler."""
        use_local = (
            os.environ.get("MOCK_MODE", "false").lower() == "true"
            or os.environ.get("LOCAL_TOOLS", "false").lower() == "true"
            or self.lambda_client is None
        )
        payload = {"tool": tool_name, "input": tool_input or {}}

        if use_local:
            handler = _load_local_action_handler()
            return handler.lambda_handler(payload, None)

        resp = self.lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        raw_body = resp["Payload"].read().decode("utf-8")
        return json.loads(raw_body)

    def invoke(
        self,
        user_input: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 5,
    ) -> Dict[str, Any]:
        """
        Execute a prompt through the AgentCore loop.
        Evaluates Bedrock Guardrail, processes tool_use blocks via Lambda, and returns the final response.
        """
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": [{"text": user_input}]})

        if not self.client:
            return self._mock_invocation(user_input)

        tool_trace = []
        for _ in range(max_turns):
            try:
                response = self.client.converse(
                    modelId=self.model_id,
                    messages=messages,
                    system=[{"text": self.system_prompt}],
                    toolConfig={"tools": TOOL_DEFINITIONS},
                    guardrailConfig={
                        "guardrailIdentifier": self.guardrail_id,
                        "guardrailVersion": self.guardrail_version,
                    },
                )
            except Exception as exc:
                error_str = str(exc)
                if "violates" in error_str or "AccessDeniedException" in error_str:
                    return {
                        "status": "blocked",
                        "reason": "Bedrock Guardrail or Account Policy Triggered",
                        "details": error_str,
                    }
                raise

            stop_reason = response.get("stopReason")
            assistant_msg = response.get("output", {}).get("message", {})
            messages.append(assistant_msg)

            if stop_reason == "guardrail_intervened":
                text = "".join(
                    block.get("text", "") for block in assistant_msg.get("content", [])
                )
                return {
                    "status": "blocked",
                    "stopReason": stop_reason,
                    "reason": "Bedrock Guardrail intervened",
                    "message": text,
                    "tool_trace": tool_trace,
                }

            if stop_reason != "tool_use":
                text = "".join(
                    block.get("text", "") for block in assistant_msg.get("content", [])
                )
                return {
                    "status": "success",
                    "stopReason": stop_reason,
                    "message": text,
                    "output": assistant_msg,
                    "tool_trace": tool_trace,
                    "usage": response.get("usage", {}),
                }

            tool_results = []
            for block in assistant_msg.get("content", []):
                if "toolUse" in block:
                    tool_use = block["toolUse"]
                    t_name = tool_use["name"]
                    t_input = tool_use.get("input", {})
                    t_id = tool_use["toolUseId"]

                    result = self._execute_tool(t_name, t_input)
                    tool_trace.append({"tool": t_name, "input": t_input, "result": result})

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": t_id,
                            "content": [{"json": result}],
                            "status": "success" if result.get("status") == "success" else "error",
                        }
                    })

            messages.append({"role": "user", "content": tool_results})

        return {
            "status": "error",
            "reason": "Max tool-execution turns exceeded",
            "tool_trace": tool_trace,
        }

    def _mock_invocation(self, user_input: str) -> Dict[str, Any]:
        """Simulation logic for offline unit testing."""
        lower_input = user_input.lower()

        if any(term in lower_input for term in ["sudo", "ignore previous instructions", "superadmin", "drop table"]):
            return {
                "status": "blocked",
                "reason": "Bedrock Guardrail: Prompt attack filter triggered",
                "message": "Access Denied: The requested prompt violates Tether safety boundaries.",
            }

        if "terminate" in lower_input or "delete" in lower_input:
            return {
                "status": "blocked",
                "reason": "Bedrock Guardrail: Destructive operations policy triggered",
                "message": "Access Denied: Terminate and delete operations are strictly prohibited.",
            }

        if "audit" in lower_input or "list" in lower_input:
            res = self._execute_tool("audit_waste", {})
            return {
                "status": "tool_call",
                "tool": "audit_waste",
                "result": res,
                "message": f"Audited {len(res.get('resources', []))} resources.",
            }

        return {
            "status": "success",
            "message": "Tether is standing by to audit development resources.",
        }
