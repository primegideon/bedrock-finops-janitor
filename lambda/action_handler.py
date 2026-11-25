"""
Tether Lambda Action Handler.

Implements Layer 3 runtime boundary enforcement for the Tether FinOps agent.
Supports both Bedrock AgentCore tool invocation payloads and OpenAPI Action Group events.
"""

import json
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

MAX_BLAST_RADIUS = 3
ALLOWED_ENV_TAG = "Development"
ALLOWED_OWNER_TAG = "DevSandbox"
FORBIDDEN_ENV_TAGS = {"Production", "Staging", "Prod"}

MOCK_INVENTORY = [
    {
        "id": "i-0a1b2c3d4e5f00001",
        "name": "ci-test-runner",
        "tags": {"Environment": "Development", "Name": "ci-test-runner"},
        "state": "running",
        "idle_hours": 96,
    },
    {
        "id": "i-0a1b2c3d4e5f00002",
        "name": "feature-sandbox-01",
        "tags": {"Environment": "Development", "Name": "feature-sandbox-01"},
        "state": "running",
        "idle_hours": 48,
    },
    {
        "id": "i-0a1b2c3d4e5f00003",
        "name": "pr-preview-env",
        "tags": {"Owner": "DevSandbox", "Name": "pr-preview-env"},
        "state": "running",
        "idle_hours": 72,
    },
    {
        "id": "i-0a1b2c3d4e5f00004",
        "name": "nightly-build-worker",
        "tags": {"Environment": "Development", "Name": "nightly-build-worker"},
        "state": "running",
        "idle_hours": 120,
    },
    {
        "id": "i-0prod999999999999",
        "name": "core-billing-api",
        "tags": {"Environment": "Production", "Name": "core-billing-api"},
        "state": "running",
        "idle_hours": 0,
    },
    {
        "id": "i-0untagged88888888",
        "name": "legacy-untagged-host",
        "tags": {"Name": "legacy-untagged-host"},
        "state": "running",
        "idle_hours": 200,
    },
]


def _is_mock_mode() -> bool:
    return os.environ.get("MOCK_MODE", "false").lower() == "true"


def _get_ec2_client():
    if _is_mock_mode():
        return None
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("ec2", region_name=region)


def _is_eligible_dev_tags(tags: Dict[str, str]) -> bool:
    """Return True only when tags explicitly mark a non-protected dev resource."""
    if tags.get("Protected", "").lower() == "true":
        return False
    env = tags.get("Environment", "")
    if env in FORBIDDEN_ENV_TAGS:
        return False
    if env == ALLOWED_ENV_TAG or tags.get("Owner") == ALLOWED_OWNER_TAG:
        return True
    return False


def audit_waste() -> Dict[str, Any]:
    """List running EC2 instances and classify cleanup eligibility."""
    ec2 = _get_ec2_client()
    if ec2 is None:
        results = []
        for item in MOCK_INVENTORY:
            eligible = _is_eligible_dev_tags(item["tags"])
            results.append({
                "id": item["id"],
                "name": item["name"],
                "environment": item["tags"].get("Environment", "Untagged"),
                "owner": item["tags"].get("Owner", "Unknown"),
                "state": item["state"],
                "idle_hours": item["idle_hours"],
                "eligible_for_stop": eligible,
            })
        return {
            "status": "success",
            "mode": "mock",
            "resources": results,
            "eligible_count": sum(1 for r in results if r["eligible_for_stop"]),
        }

    paginator = ec2.get_paginator("describe_instances")
    resources = []
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
    ):
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                resources.append({
                    "id": inst["InstanceId"],
                    "name": tags.get("Name", "Unnamed"),
                    "environment": tags.get("Environment", "Untagged"),
                    "owner": tags.get("Owner", "Unknown"),
                    "state": inst["State"]["Name"],
                    "eligible_for_stop": _is_eligible_dev_tags(tags),
                })

    return {
        "status": "success",
        "mode": "live",
        "resources": resources,
        "eligible_count": sum(1 for r in resources if r["eligible_for_stop"]),
    }


def stop_dev_instances(
    instance_ids: List[str],
    confirmation_token: Optional[str] = None,
    action_mode: str = "stop",
) -> Dict[str, Any]:
    """
    Safely stop up to MAX_BLAST_RADIUS development EC2 instances.

    Enforces:
    1. Only 'stop' operations are permitted (terminate/delete rejected).
    2. Target list length must not exceed MAX_BLAST_RADIUS (3).
    3. Every target must pass strict development tag checks.
    """
    if str(action_mode).lower() != "stop":
        return {
            "status": "error",
            "code": "FORBIDDEN_ACTION",
            "message": f"Action '{action_mode}' is prohibited. Tether only permits 'stop'.",
        }

    if not instance_ids or not isinstance(instance_ids, list):
        return {
            "status": "error",
            "code": "INVALID_INPUT",
            "message": "Provide a non-empty list of instance IDs.",
        }

    unique_ids = list(dict.fromkeys(str(i).strip() for i in instance_ids if str(i).strip()))
    if not unique_ids:
        return {
            "status": "error",
            "code": "INVALID_INPUT",
            "message": "No valid instance IDs provided.",
        }

    if len(unique_ids) > MAX_BLAST_RADIUS:
        return {
            "status": "error",
            "code": "BLAST_RADIUS_EXCEEDED",
            "message": (
                f"Boundary violation: maximum {MAX_BLAST_RADIUS} instances can be stopped per execution "
                f"(requested {len(unique_ids)}). Present the full inventory and request human approval."
            ),
            "requested_count": len(unique_ids),
            "max_allowed": MAX_BLAST_RADIUS,
        }

    ec2 = _get_ec2_client()
    if ec2 is None:
        inventory_map = {item["id"]: item for item in MOCK_INVENTORY}
        for inst_id in unique_ids:
            record = inventory_map.get(inst_id)
            if not record:
                return {
                    "status": "error",
                    "code": "RESOURCE_NOT_FOUND",
                    "message": f"Instance '{inst_id}' was not found.",
                }
            if not _is_eligible_dev_tags(record["tags"]):
                env_val = record["tags"].get("Environment", "Untagged")
                return {
                    "status": "error",
                    "code": "ENVIRONMENT_BOUNDARY_VIOLATION",
                    "message": (
                        f"Boundary violation: '{inst_id}' has Environment='{env_val}'. "
                        "Only non-protected Development or DevSandbox resources may be stopped."
                    ),
                }

        return {
            "status": "success",
            "mode": "mock",
            "action": "STOPPED",
            "instances": unique_ids,
            "confirmation_token_used": bool(confirmation_token),
            "message": f"Stopped {len(unique_ids)} development instance(s).",
        }

    try:
        desc = ec2.describe_instances(InstanceIds=unique_ids)
    except ClientError as exc:
        return {
            "status": "error",
            "code": "AWS_DESCRIBE_ERROR",
            "message": str(exc),
        }

    found_ids = set()
    for reservation in desc.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            inst_id = inst["InstanceId"]
            found_ids.add(inst_id)
            tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
            if not _is_eligible_dev_tags(tags):
                env_val = tags.get("Environment", "Untagged")
                return {
                    "status": "error",
                    "code": "ENVIRONMENT_BOUNDARY_VIOLATION",
                    "message": (
                        f"Boundary violation: '{inst_id}' has Environment='{env_val}'. "
                        "Only non-protected Development or DevSandbox resources may be stopped."
                    ),
                }

    missing = [i for i in unique_ids if i not in found_ids]
    if missing:
        return {
            "status": "error",
            "code": "RESOURCE_NOT_FOUND",
            "message": f"Instances not found: {missing}",
        }

    try:
        stop_resp = ec2.stop_instances(InstanceIds=unique_ids)
        stopping = [
            {
                "id": item["InstanceId"],
                "previous_state": item["PreviousState"]["Name"],
                "current_state": item["CurrentState"]["Name"],
            }
            for item in stop_resp.get("StoppingInstances", [])
        ]
        return {
            "status": "success",
            "mode": "live",
            "action": "STOPPED",
            "instances": stopping,
        }
    except ClientError as exc:
        return {
            "status": "error",
            "code": "IAM_OR_EC2_DENIED",
            "message": str(exc),
        }


def _parse_instance_ids(raw_ids: Any) -> List[str]:
    if isinstance(raw_ids, list):
        return [str(x).strip() for x in raw_ids if str(x).strip()]
    if isinstance(raw_ids, str):
        try:
            parsed = json.loads(raw_ids)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
        return [item.strip() for item in raw_ids.split(",") if item.strip()]
    return []


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Entrypoint for AWS Lambda.

    Handles:
    1. Direct AgentCore invocations: {"tool": "<name>", "input": {...}}
    2. Bedrock OpenAPI Action Group invocations: {"apiPath": "...", "parameters": [...]}
    """
    # Path 1: Direct AgentCore tool invocation
    if "tool" in event:
        tool_name = event["tool"]
        tool_input = event.get("input", {}) or {}
        if tool_name == "audit_waste":
            return audit_waste()
        if tool_name == "stop_dev_instances":
            ids = _parse_instance_ids(tool_input.get("instance_ids", []))
            token = tool_input.get("confirmation_token")
            mode = tool_input.get("mode", "stop")
            return stop_dev_instances(ids, confirmation_token=token, action_mode=mode)
        return {"status": "error", "code": "UNKNOWN_TOOL", "message": f"Unknown tool: {tool_name}"}

    # Path 2: OpenAPI Action Group invocation
    action_group = event.get("actionGroup", "")
    api_path = event.get("apiPath", "")
    http_method = event.get("httpMethod", "")
    parameters = event.get("parameters", []) or []
    param_dict = {p.get("name"): p.get("value") for p in parameters}

    if api_path == "/audit-waste":
        result = audit_waste()
    elif api_path == "/stop-dev-instances":
        ids = _parse_instance_ids(param_dict.get("instance_ids", []))
        token = param_dict.get("confirmation_token")
        mode = param_dict.get("mode", "stop")
        result = stop_dev_instances(ids, confirmation_token=token, action_mode=mode)
    else:
        result = {"status": "error", "code": "UNKNOWN_ENDPOINT", "message": f"Unknown path: {api_path}"}

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "apiPath": api_path,
            "httpMethod": http_method,
            "httpStatusCode": 200 if result.get("status") == "success" else 400,
            "responseBody": {
                "application/json": {
                    "body": json.dumps(result)
                }
            },
        },
    }
