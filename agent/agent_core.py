"""
Tether AgentCore Harness for Amazon Bedrock.
Implements the AgentCore architecture combining Bedrock Runtime,
Bedrock Guardrail (ID: 7eokel3iit9r), and deterministic tool execution.
"""

import json
import os
import boto3
from typing import Dict, Any, List

PROFILE_NAME = "bedrock-janitor"
REGION_NAME = "us-east-1"
GUARDRAIL_ID = "7eokel3iit9r"
GUARDRAIL_VERSION = "1"
MODEL_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"

TOOL_DEFINITIONS = [
    {
        "toolSpec": {
            "name": "audit_waste",
            "description": "Lists EC2 instances and reports idle metrics to identify development waste.",
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
            "description": "Safely stops up to 3 development EC2 instances. Fails if count > 3 or if targeting non-development instances.",
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

def get_bedrock_client():
    if os.environ.get("MOCK_MODE", "false").lower() == "true":
        return None
    session = boto3.Session(profile_name=PROFILE_NAME, region_name=REGION_NAME)
    return session.client("bedrock-runtime")

class TetherAgentCore:
    def __init__(self, guardrail_id: str = GUARDRAIL_ID, guardrail_version: str = GUARDRAIL_VERSION):
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version
        self.system_prompt = load_system_prompt()
        self.model_id = MODEL_ID
        self.client = get_bedrock_client()

    def invoke(self, user_input: str, conversation_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executes a turn through the AgentCore harness.
        Passes input through Bedrock Guardrail, evaluates model reasoning, and executes tools.
        """
        messages = list(conversation_history or [])
        messages.append({
            "role": "user",
            "content": [{"text": user_input}]
        })

        if not self.client:
            # Simulation / local evaluation mode
            return self._mock_invocation(user_input)

        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=messages,
                system=[{"text": self.system_prompt}],
                toolConfig={"tools": TOOL_DEFINITIONS},
                guardrailConfig={
                    "guardrailIdentifier": self.guardrail_id,
                    "guardrailVersion": self.guardrail_version
                }
            )
            return {
                "status": "success",
                "stopReason": response.get("stopReason"),
                "output": response.get("output", {}).get("message", {}),
                "usage": response.get("usage", {})
            }
        except Exception as e:
            error_str = str(e)
            if "violates" in error_str or "AccessDeniedException" in error_str:
                return {
                    "status": "blocked",
                    "reason": "Bedrock Guardrail or Account Policy Triggered",
                    "details": error_str
                }
            raise

    def _mock_invocation(self, user_input: str) -> Dict[str, Any]:
        """Simulation logic for local testing and offline verification."""
        lower_input = user_input.lower()

        # Simulated Layer 1 Guardrail check
        if any(term in lower_input for term in ["sudo", "ignore previous instructions", "superadmin", "drop table"]):
            return {
                "status": "blocked",
                "reason": "Bedrock Guardrail: Prompt attack filter triggered",
                "message": "Access Denied: The requested prompt violates Tether safety boundaries."
            }

        if "terminate" in lower_input or "delete" in lower_input:
            return {
                "status": "blocked",
                "reason": "Bedrock Guardrail: Destructive operations policy triggered",
                "message": "Access Denied: Terminate and delete operations are strictly prohibited."
            }

        # Simulated Layer 2 Agent reasoning
        if "audit" in lower_input or "list" in lower_input:
            return {
                "status": "tool_call",
                "tool": "audit_waste",
                "message": "Auditing development resources in us-east-1..."
            }

        return {
            "status": "success",
            "message": "Tether is standing by to audit development resources."
        }
