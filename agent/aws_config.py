"""
Shared AWS session and resource resolution helpers for Tether.

Supports standard boto3 credential resolution as well as explicit
AWS_PROFILE / AWS_REGION / TETHER_GUARDRAIL_ID environment overrides.
"""

import os
from typing import Optional

import boto3

DEFAULT_REGION = "us-east-1"
DEFAULT_GUARDRAIL_NAME = "Tether-Safety-Guardrail"
DEFAULT_GUARDRAIL_VERSION = "1"
DEFAULT_LAMBDA_NAME = "Tether-Action-Handler"
DEFAULT_LAMBDA_ROLE_NAME = "Tether-Lambda-Execution-Role"
DEFAULT_HARNESS_NAME = "Tether_Managed_Harness"
DEFAULT_HARNESS_ENDPOINT = "prod"


def get_aws_region() -> str:
    return os.environ.get("AWS_REGION", DEFAULT_REGION)


def get_aws_session(profile_name: Optional[str] = None) -> boto3.Session:
    """Create a boto3 Session using AWS_PROFILE if set, otherwise default credential chain."""
    resolved_profile = profile_name or os.environ.get("AWS_PROFILE")
    region = get_aws_region()
    if resolved_profile:
        return boto3.Session(profile_name=resolved_profile, region_name=region)
    return boto3.Session(region_name=region)


def get_account_id(session: boto3.Session) -> str:
    """Resolve the active AWS Account ID via STS."""
    return session.client("sts").get_caller_identity()["Account"]


def resolve_guardrail_id(
    session: boto3.Session,
    guardrail_name: str = DEFAULT_GUARDRAIL_NAME,
) -> str:
    """Resolve Guardrail ID from TETHER_GUARDRAIL_ID or by name lookup in Bedrock."""
    env_id = os.environ.get("TETHER_GUARDRAIL_ID")
    if env_id:
        return env_id

    bedrock = session.client("bedrock")
    paginator = bedrock.get_paginator("list_guardrails")
    for page in paginator.paginate():
        for item in page.get("guardrails", []):
            if item.get("name") == guardrail_name:
                return item["id"]

    raise RuntimeError(
        f"Guardrail '{guardrail_name}' not found in region {get_aws_region()}. "
        "Run scripts/deploy_bedrock_agent.py first or set TETHER_GUARDRAIL_ID."
    )


def resolve_guardrail_version(
    session: boto3.Session,
    guardrail_id: str,
) -> str:
    """Resolve latest published Guardrail version via TETHER_GUARDRAIL_VERSION or Bedrock."""
    env_ver = os.environ.get("TETHER_GUARDRAIL_VERSION")
    if env_ver:
        return env_ver

    bedrock = session.client("bedrock")
    versions = []
    paginator = bedrock.get_paginator("list_guardrails")
    for page in paginator.paginate(guardrailIdentifier=guardrail_id):
        for item in page.get("guardrails", []):
            ver = str(item.get("version", ""))
            if ver.isdigit():
                versions.append(int(ver))

    if versions:
        return str(max(versions))
    return DEFAULT_GUARDRAIL_VERSION


def resolve_harness_arn(
    session: boto3.Session,
    harness_name: str = DEFAULT_HARNESS_NAME,
) -> str:
    """Resolve AgentCore Managed Harness ARN from TETHER_HARNESS_ARN or by name."""
    env_arn = os.environ.get("TETHER_HARNESS_ARN")
    if env_arn:
        return env_arn

    ctrl = session.client("bedrock-agentcore-control")
    for item in ctrl.list_harnesses().get("harnesses", []):
        if item.get("harnessName") == harness_name:
            return item["arn"]

    raise RuntimeError(
        f"AgentCore Harness '{harness_name}' not found in region {get_aws_region()}. "
        "Run scripts/deploy_agentcore.py first or set TETHER_HARNESS_ARN."
    )
