"""
Deploy Tether Bedrock Guardrail and IAM Execution Role for AgentCore.

Note: AWS transitioned Bedrock Agents Classic (create_agent) into maintenance mode
for accounts without prior usage. Tether runs on the Amazon Bedrock AgentCore
architecture (Bedrock Runtime Converse API + Guardrails + Lambda Action Handler).

Respects AWS_PROFILE and AWS_REGION environment variables.
"""

import json
import os
import sys
import time
from botocore.exceptions import ClientError

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

from agent.aws_config import (  # noqa: E402
    DEFAULT_GUARDRAIL_NAME,
    get_account_id,
    get_aws_region,
    get_aws_session,
)

ROLE_NAME = "Tether-Bedrock-Agent-Role"


def deploy_guardrail(bedrock_client, config_path: str):
    print(f"Deploying Bedrock Guardrail: {DEFAULT_GUARDRAIL_NAME}...")
    with open(config_path, "r", encoding="utf-8") as f:
        guardrail_payload = json.load(f)

    existing_id = None
    paginator = bedrock_client.get_paginator("list_guardrails")
    for page in paginator.paginate():
        for item in page.get("guardrails", []):
            if item.get("name") == DEFAULT_GUARDRAIL_NAME:
                existing_id = item.get("id")
                break
        if existing_id:
            break

    if existing_id:
        print(f"Existing Guardrail found ({existing_id}). Updating configuration...")
        update_params = dict(guardrail_payload)
        update_params["guardrailIdentifier"] = existing_id
        response = bedrock_client.update_guardrail(**update_params)
        guardrail_id = response["guardrailId"]
        guardrail_arn = response["guardrailArn"]
    else:
        print("Creating new Guardrail...")
        response = bedrock_client.create_guardrail(**guardrail_payload)
        guardrail_id = response["guardrailId"]
        guardrail_arn = response["guardrailArn"]

    print("Publishing Guardrail version...")
    version_resp = bedrock_client.create_guardrail_version(
        guardrailIdentifier=guardrail_id,
        description="Published version for Tether AgentCore",
    )
    version = version_resp["version"]
    print(f"Guardrail ready. ID: {guardrail_id}, Version: {version}")
    return guardrail_id, version, guardrail_arn


def ensure_agent_role(iam_client, account_id: str, region: str) -> str:
    print(f"Checking IAM Role: {ROLE_NAME}...")
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock:{region}:{account_id}:agent/*"
                    },
                },
            }
        ],
    }

    try:
        role_resp = iam_client.get_role(RoleName=ROLE_NAME)
        role_arn = role_resp["Role"]["Arn"]
        print(f"Role exists: {role_arn}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            print(f"Creating IAM Role: {ROLE_NAME}...")
            role_resp = iam_client.create_role(
                RoleName=ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for Tether Bedrock AgentCore",
            )
            role_arn = role_resp["Role"]["Arn"]
        else:
            raise

    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [
                    f"arn:aws:bedrock:{region}::foundation-model/*",
                    f"arn:aws:bedrock:{region}:{account_id}:inference-profile/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock:ApplyGuardrail"],
                "Resource": f"arn:aws:bedrock:{region}:{account_id}:guardrail/*",
            },
        ],
    }

    iam_client.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="TetherBedrockAgentExecutionPolicy",
        PolicyDocument=json.dumps(policy_doc),
    )
    print("IAM Role policy updated.")
    return role_arn


def main():
    config_path = os.path.join(ROOT_DIR, "agent", "guardrail_config.json")
    region = get_aws_region()
    session = get_aws_session()
    account_id = get_account_id(session)
    print(f"Connected to AWS Account: {account_id} in {region}")

    bedrock_client = session.client("bedrock")
    iam_client = session.client("iam")

    guardrail_id, guardrail_version, guardrail_arn = deploy_guardrail(
        bedrock_client, config_path
    )
    role_arn = ensure_agent_role(iam_client, account_id, region)

    print("\n" + "=" * 60)
    print("LAYER 1 & LAYER 2 PROVISIONING COMPLETE")
    print(f"Guardrail Name:    {DEFAULT_GUARDRAIL_NAME}")
    print(f"Guardrail ID:      {guardrail_id} (Version {guardrail_version})")
    print(f"Guardrail ARN:     {guardrail_arn}")
    print(f"IAM Role ARN:      {role_arn}")
    print("=" * 60)


if __name__ == "__main__":
    main()
