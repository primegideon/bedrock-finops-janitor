"""
Tether Lifecycle Hook Handler for Amazon Bedrock AgentCore Managed Harness.

Enforces deterministic security boundaries across AgentCore lifecycle events:
  - before_invocation: Evaluates input against Amazon Bedrock Guardrails.
  - before_tool_call: Validates tool allowlist, action mode, and blast-radius limits.
  - after_invocation: Evaluates assistant output against Amazon Bedrock Guardrails.
"""

import json
import os
from typing import Any, Dict, List

import boto3

MAX_BLAST_RADIUS = 3
ALLOWED_TOOLS = {"audit_waste", "stop_dev_instances"}


def _get_bedrock_runtime():
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("bedrock-runtime", region_name=region)


def _extract_latest_text(messages: List[Dict[str, Any]], target_role: str) -> str:
    texts = []
    for msg in reversed(messages or []):
        if msg.get("role") == target_role:
            for block in msg.get("content", []):
                if "text" in block and block["text"]:
                    texts.append(block["text"])
            if texts:
                break
    return "\n".join(texts).strip()


def _normalize_tool_name(raw_name: str) -> str:
    if "___" in raw_name:
        return raw_name.split("___", 1)[1]
    return raw_name


def _handle_before_invocation(context_data: Dict[str, Any]) -> Dict[str, Any]:
    guardrail_id = os.environ.get("GUARDRAIL_ID", "")
    guardrail_version = os.environ.get("GUARDRAIL_VERSION", "1")
    if not guardrail_id:
        return {"decision": "allow"}

    user_text = _extract_latest_text(context_data.get("messages", []), "user")
    if not user_text:
        return {"decision": "allow"}

    client = _get_bedrock_runtime()
    resp = client.apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=guardrail_version,
        source="INPUT",
        content=[{"text": {"text": user_text}}],
    )
    if resp.get("action") == "GUARDRAIL_INTERVENED":
        outputs = resp.get("outputs", [])
        msg = outputs[0].get("text") if outputs else "Blocked by Bedrock Guardrail policy."
        return {"decision": "deny", "reason": msg}

    return {"decision": "allow"}


def _handle_before_tool_call(context_data: Dict[str, Any]) -> Dict[str, Any]:
    raw_tool = context_data.get("toolName") or context_data.get("name") or ""
    tool_name = _normalize_tool_name(raw_tool)
    tool_input = context_data.get("toolInput") or context_data.get("input") or {}

    if tool_name not in ALLOWED_TOOLS:
        return {
            "decision": "deny",
            "reason": f"Tool '{tool_name}' is not on the Tether allowlist.",
        }

    if tool_name == "stop_dev_instances":
        mode = str(tool_input.get("mode", "stop")).lower()
        if mode != "stop":
            return {
                "decision": "deny",
                "reason": f"Prohibited action mode '{mode}'. Only 'stop' is permitted.",
            }

        raw_ids = tool_input.get("instance_ids", [])
        if isinstance(raw_ids, str):
            try:
                raw_ids = json.loads(raw_ids)
            except json.JSONDecodeError:
                raw_ids = [i.strip() for i in raw_ids.split(",") if i.strip()]

        if not isinstance(raw_ids, list) or not raw_ids:
            return {
                "decision": "deny",
                "reason": "stop_dev_instances requires a non-empty list of instance_ids.",
            }

        unique_ids = list(dict.fromkeys(str(i).strip() for i in raw_ids if str(i).strip()))
        if len(unique_ids) > MAX_BLAST_RADIUS:
            return {
                "decision": "deny",
                "reason": (
                    f"Blast-radius boundary exceeded: requested {len(unique_ids)} instances, "
                    f"maximum allowed per execution is {MAX_BLAST_RADIUS}."
                ),
            }

    return {"decision": "allow"}


def _handle_after_invocation(context_data: Dict[str, Any]) -> Dict[str, Any]:
    guardrail_id = os.environ.get("GUARDRAIL_ID", "")
    guardrail_version = os.environ.get("GUARDRAIL_VERSION", "1")
    if not guardrail_id:
        return {"decision": "allow"}

    assistant_text = _extract_latest_text(context_data.get("messages", []), "assistant")
    if not assistant_text:
        return {"decision": "allow"}

    client = _get_bedrock_runtime()
    resp = client.apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=guardrail_version,
        source="OUTPUT",
        content=[{"text": {"text": assistant_text}}],
    )
    if resp.get("action") == "GUARDRAIL_INTERVENED":
        return {
            "decision": "deny",
            "reason": "Assistant output suppressed by Bedrock Guardrail output policy.",
        }

    return {"decision": "allow"}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    hook_type = event.get("event") or event.get("hookType") or event.get("hook_type") or ""
    context_data = event.get("context") or event

    if hook_type in ("before_invocation", "beforeInvocation"):
        return _handle_before_invocation(context_data)
    if hook_type in ("before_tool_call", "beforeToolCall"):
        return _handle_before_tool_call(context_data)
    if hook_type in ("after_invocation", "afterInvocation"):
        return _handle_after_invocation(context_data)

    return {"decision": "allow"}
