"""
Deploy Tether Bedrock Managed Agent and Safety Guardrail.
Executes against AWS us-east-1 using the bedrock-janitor profile.
"""

import json
import os
import sys
import time
import boto3
from botocore.exceptions import ClientError

PROFILE_NAME = "bedrock-janitor"
REGION_NAME = "us-east-1"
AGENT_NAME = "Tether-FinOps-Agent"
GUARDRAIL_NAME = "Tether-Safety-Guardrail"
ROLE_NAME = "Tether-Bedrock-Agent-Role"
FOUNDATION_MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0"

def get_session():
    return boto3.Session(profile_name=PROFILE_NAME, region_name=REGION_NAME)

def deploy_guardrail(bedrock_client, config_path):
    print(f"Deploying Bedrock Guardrail: {GUARDRAIL_NAME}...")
    with open(config_path, "r", encoding="utf-8") as f:
        guardrail_payload = json.load(f)

    # Check for existing guardrail
    existing_id = None
    paginator = bedrock_client.get_paginator("list_guardrails")
    for page in paginator.paginate():
        for item in page.get("guardrails", []):
            if item.get("name") == GUARDRAIL_NAME:
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

    # Create published version for the agent
    print("Publishing Guardrail version...")
    version_resp = bedrock_client.create_guardrail_version(
        guardrailIdentifier=guardrail_id,
        description="Version 1 for Tether FinOps Agent"
    )
    version = version_resp["version"]
    print(f"Guardrail ready. ID: {guardrail_id}, Version: {version}")
    return guardrail_id, version, guardrail_arn

def get_or_create_agent_role(iam_client, account_id):
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
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{REGION_NAME}:{account_id}:agent/*"}
                }
            }
        ]
    }

    try:
        role_resp = iam_client.get_role(RoleName=ROLE_NAME)
        role_arn = role_resp["Role"]["Arn"]
        print(f"Role exists: {role_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"Creating IAM Role: {ROLE_NAME}...")
            role_resp = iam_client.create_role(
                RoleName=ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for Tether Bedrock Managed Agent"
            )
            role_arn = role_resp["Role"]["Arn"]
        else:
            raise

    # Attach model invocation and guardrail permissions
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": f"arn:aws:bedrock:{REGION_NAME}::foundation-model/*"
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock:ApplyGuardrail"],
                "Resource": f"arn:aws:bedrock:{REGION_NAME}:{account_id}:guardrail/*"
            }
        ]
    }

    iam_client.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="TetherBedrockAgentExecutionPolicy",
        PolicyDocument=json.dumps(policy_doc)
    )
    print("IAM Role policy attached. Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn

def deploy_agent(bedrock_agent_client, role_arn, guardrail_id, guardrail_version, prompt_path):
    print(f"Deploying Bedrock Agent: {AGENT_NAME}...")
    with open(prompt_path, "r", encoding="utf-8") as f:
        instruction_text = f.read()

    existing_agent_id = None
    paginator = bedrock_agent_client.get_paginator("list_agents")
    for page in paginator.paginate():
        for agent in page.get("agentSummaries", []):
            if agent.get("agentName") == AGENT_NAME:
                existing_agent_id = agent.get("agentId")
                break
        if existing_agent_id:
            break

    guardrail_config = {
        "guardrailIdentifier": guardrail_id,
        "guardrailVersion": str(guardrail_version)
    }

    if existing_agent_id:
        print(f"Updating existing Agent ({existing_agent_id})...")
        resp = bedrock_agent_client.update_agent(
            agentId=existing_agent_id,
            agentName=AGENT_NAME,
            agentResourceRoleArn=role_arn,
            foundationModel=FOUNDATION_MODEL,
            instruction=instruction_text,
            guardrailConfiguration=guardrail_config
        )
        agent_id = resp["agent"]["agentId"]
    else:
        print("Creating new Bedrock Agent...")
        resp = bedrock_agent_client.create_agent(
            agentName=AGENT_NAME,
            agentResourceRoleArn=role_arn,
            foundationModel=FOUNDATION_MODEL,
            instruction=instruction_text,
            guardrailConfiguration=guardrail_config,
            idleSessionTTLInSeconds=1800
        )
        agent_id = resp["agent"]["agentId"]

    print("Preparing Agent working draft...")
    time.sleep(3)
    bedrock_agent_client.prepare_agent(agentId=agent_id)
    print(f"Agent prepared successfully. Agent ID: {agent_id}")
    return agent_id

def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(root_dir, "agent", "guardrail_config.json")
    prompt_path = os.path.join(root_dir, "agent", "system_prompt.txt")

    session = get_session()
    sts_client = session.client("sts")
    account_id = sts_client.get_caller_identity()["Account"]
    print(f"Connected to AWS Account: {account_id} in {REGION_NAME}")

    bedrock_client = session.client("bedrock")
    bedrock_agent_client = session.client("bedrock-agent")
    iam_client = session.client("iam")

    # Step 1: Deploy Guardrail
    guardrail_id, guardrail_version, _ = deploy_guardrail(bedrock_client, config_path)

    # Step 2: Ensure Agent Execution IAM Role
    role_arn = get_or_create_agent_role(iam_client, account_id)

    # Step 3: Deploy Agent with Guardrail Association
    agent_id = deploy_agent(bedrock_agent_client, role_arn, guardrail_id, guardrail_version, prompt_path)

    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE")
    print(f"Agent Name:        {AGENT_NAME}")
    print(f"Agent ID:          {agent_id}")
    print(f"Foundation Model:  {FOUNDATION_MODEL}")
    print(f"Guardrail ID:      {guardrail_id} (Version {guardrail_version})")
    print(f"IAM Role ARN:      {role_arn}")
    print("=" * 60)

if __name__ == "__main__":
    main()
