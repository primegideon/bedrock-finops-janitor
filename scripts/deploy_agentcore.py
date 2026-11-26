"""
Deploy Tether onto the Amazon Bedrock AgentCore Managed Harness.

Provisions:
  1. Tether-Hook-Execution-Role and Tether-AgentCore-Hooks Lambda
  2. Tether-AgentCore-Execution-Role (for AgentCore Gateway + Harness)
  3. Tether-FinOps-Gateway (MCP Gateway) + Tether-Action-Target (Lambda target)
  4. Tether-Managed-Harness (AgentCore Managed Harness with lifecycle hooks)
  5. Tether-Harness-Endpoint (Active endpoint for invoke_harness)

Respects AWS_PROFILE and AWS_REGION environment variables.
"""

import io
import json
import os
import sys
import time
import zipfile
from botocore.exceptions import ClientError

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

from agent.aws_config import (  # noqa: E402
    DEFAULT_GUARDRAIL_VERSION,
    DEFAULT_LAMBDA_NAME,
    get_account_id,
    get_aws_region,
    get_aws_session,
    resolve_guardrail_id,
)

HOOK_ROLE_NAME = "Tether-Hook-Execution-Role"
HOOK_LAMBDA_NAME = "Tether-AgentCore-Hooks"
AGENTCORE_ROLE_NAME = "Tether-AgentCore-Execution-Role"
GATEWAY_NAME = "Tether-FinOps-Gateway"
TARGET_NAME = "Tether-Action-Target"
HARNESS_NAME = "Tether_Managed_Harness"
ENDPOINT_NAME = "prod"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def _build_zip(file_path: str, arcname: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, arcname=arcname)
    buf.seek(0)
    return buf.read()


def ensure_hook_lambda(session, account_id: str, region: str, guardrail_id: str, guardrail_version: str) -> str:
    iam = session.client("iam")
    lam = session.client("lambda")

    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role_arn = iam.get_role(RoleName=HOOK_ROLE_NAME)["Role"]["Arn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            role_arn = iam.create_role(
                RoleName=HOOK_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description="Execution role for Tether AgentCore Lifecycle Hooks",
            )["Role"]["Arn"]
        else:
            raise

    iam.attach_role_policy(
        RoleName=HOOK_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    iam.put_role_policy(
        RoleName=HOOK_ROLE_NAME,
        PolicyName="TetherHookGuardrailPolicy",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["bedrock:ApplyGuardrail"],
                    "Resource": f"arn:aws:bedrock:{region}:{account_id}:guardrail/*",
                }
            ],
        }),
    )
    time.sleep(8)

    hook_path = os.path.join(ROOT_DIR, "lambda", "hook_handler.py")
    zip_bytes = _build_zip(hook_path, "hook_handler.py")
    env_vars = {
        "Variables": {
            "GUARDRAIL_ID": guardrail_id,
            "GUARDRAIL_VERSION": guardrail_version,
        }
    }

    try:
        existing = lam.get_function(FunctionName=HOOK_LAMBDA_NAME)
        lam.update_function_code(FunctionName=HOOK_LAMBDA_NAME, ZipFile=zip_bytes)
        lam.get_waiter("function_updated_v2").wait(FunctionName=HOOK_LAMBDA_NAME)
        lam.update_function_configuration(
            FunctionName=HOOK_LAMBDA_NAME,
            Role=role_arn,
            Handler="hook_handler.lambda_handler",
            Runtime="python3.12",
            Timeout=15,
            Environment=env_vars,
        )
        fn_arn = existing["Configuration"]["FunctionArn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            resp = lam.create_function(
                FunctionName=HOOK_LAMBDA_NAME,
                Runtime="python3.12",
                Role=role_arn,
                Handler="hook_handler.lambda_handler",
                Code={"ZipFile": zip_bytes},
                Description="Tether AgentCore lifecycle hook validator",
                Timeout=15,
                MemorySize=128,
                Environment=env_vars,
            )
            lam.get_waiter("function_active_v2").wait(FunctionName=HOOK_LAMBDA_NAME)
            fn_arn = resp["FunctionArn"]
        else:
            raise

    print(f"Hook Lambda ready: {fn_arn}")
    return fn_arn


def ensure_agentcore_role(session, account_id: str, region: str) -> str:
    iam = session.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }
    try:
        role_arn = iam.get_role(RoleName=AGENTCORE_ROLE_NAME)["Role"]["Arn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            role_arn = iam.create_role(
                RoleName=AGENTCORE_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description="Execution role for Tether AgentCore Gateway and Managed Harness",
            )["Role"]["Arn"]
        else:
            raise

    iam.put_role_policy(
        RoleName=AGENTCORE_ROLE_NAME,
        PolicyName="TetherAgentCoreExecutionPolicy",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock:*::foundation-model/*",
                        f"arn:aws:bedrock:*:{account_id}:inference-profile/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["lambda:InvokeFunction"],
                    "Resource": [
                        f"arn:aws:lambda:{region}:{account_id}:function:{DEFAULT_LAMBDA_NAME}",
                        f"arn:aws:lambda:{region}:{account_id}:function:{HOOK_LAMBDA_NAME}",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["bedrock-agentcore:InvokeGateway"],
                    "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:GetEvent",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                        "bedrock-agentcore:ListMemoryRecords",
                        "bedrock-agentcore:BatchCreateMemoryRecords",
                    ],
                    "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetResourceApiKey",
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/*",
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!*",
                },
            ],
        }),
    )
    print(f"AgentCore Role ready: {role_arn}. Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


def ensure_gateway(ctrl, role_arn: str, action_lambda_arn: str):
    gateways = ctrl.list_gateways().get("items", [])
    existing = next((g for g in gateways if g["name"] == GATEWAY_NAME), None)

    if existing:
        gw_id = existing["gatewayId"]
        gw_detail = ctrl.get_gateway(gatewayIdentifier=gw_id)
        gw_arn = gw_detail["gatewayArn"]
        print(f"Gateway exists: {gw_arn} ({gw_detail['status']})")
    else:
        print(f"Creating AgentCore Gateway: {GATEWAY_NAME}...")
        resp = ctrl.create_gateway(
            name=GATEWAY_NAME,
            description="MCP Gateway exposing Tether FinOps tools",
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
        )
        gw_id = resp["gatewayId"]
        gw_arn = resp["gatewayArn"]

    # Wait for Gateway READY
    for _ in range(30):
        status = ctrl.get_gateway(gatewayIdentifier=gw_id)["status"]
        if status == "READY":
            break
        if "FAILED" in status:
            raise RuntimeError(f"Gateway failed with status: {status}")
        time.sleep(3)

    targets = ctrl.list_gateway_targets(gatewayIdentifier=gw_id).get("items", [])
    existing_target = next((t for t in targets if t["name"] == TARGET_NAME), None)
    if not existing_target:
        print(f"Creating Gateway Target: {TARGET_NAME}...")
        ctrl.create_gateway_target(
            gatewayIdentifier=gw_id,
            name=TARGET_NAME,
            description="Tether Layer 3 Lambda boundary handler",
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": action_lambda_arn,
                        "toolSchema": {
                            "inlinePayload": [
                                {
                                    "name": "audit_waste",
                                    "description": "List EC2 instances and evaluate cleanup eligibility based on Environment and Owner tags.",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {},
                                    },
                                },
                                {
                                    "name": "stop_dev_instances",
                                    "description": "Safely stop up to 3 development EC2 instances. Rejects >3 targets, non-development instances, or non-stop modes.",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {
                                            "instance_ids": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "description": "List of up to 3 development EC2 instance IDs",
                                            },
                                            "mode": {
                                                "type": "string",
                                                "description": "Must be 'stop'",
                                            },
                                        },
                                        "required": ["instance_ids"],
                                    },
                                },
                            ]
                        },
                    }
                }
            },
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
    print(f"Gateway Ready: {gw_arn}")
    return gw_id, gw_arn


def _resolve_model_config(ctrl) -> dict:
    """Use AgentCore Identity Gemini provider if configured, else Bedrock model."""
    use_bedrock = os.environ.get("TETHER_USE_BEDROCK_MODEL", "false").lower() == "true"
    if not use_bedrock:
        providers = ctrl.list_api_key_credential_providers().get("credentialProviders", [])
        key_name = os.environ.get("TETHER_GEMINI_KEY_NAME", "tether-gemini-key")
        gemini_prov = next((p for p in providers if p.get("name") == key_name), None)
        if gemini_prov:
            gemini_model = os.environ.get("TETHER_GEMINI_MODEL", "gemini-3.5-flash-lite")
            print(f"Using AgentCore Identity Gemini provider '{key_name}' with model '{gemini_model}'.")
            return {
                "geminiModelConfig": {
                    "modelId": gemini_model,
                    "apiKeyArn": gemini_prov["credentialProviderArn"],
                    "maxTokens": 1024,
                    "temperature": 0.0,
                }
            }

    model_id = os.environ.get("TETHER_MODEL_ID", DEFAULT_MODEL_ID)
    print(f"Using Bedrock model '{model_id}'.")
    return {
        "bedrockModelConfig": {
            "modelId": model_id,
            "maxTokens": 1024,
            "temperature": 0.0,
            "apiFormat": "converse_stream",
        }
    }


def ensure_harness(ctrl, role_arn: str, gw_arn: str, hook_lambda_arn: str):
    prompt_path = os.path.join(ROOT_DIR, "agent", "system_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        system_prompt_text = f.read().strip()

    model_config = _resolve_model_config(ctrl)
    harnesses = ctrl.list_harnesses().get("harnesses", [])
    existing = next((h for h in harnesses if h.get("harnessName") == HARNESS_NAME), None)

    harness_kwargs = {
        "harnessName": HARNESS_NAME,
        "executionRoleArn": role_arn,
        "model": model_config,
        "systemPrompt": [{"text": system_prompt_text}],
        "tools": [
            {
                "type": "agentcore_gateway",
                "name": "tether_finops_gateway",
                "config": {
                    "agentCoreGateway": {
                        "gatewayArn": gw_arn,
                        "outboundAuth": {"awsIam": {}},
                    }
                },
            }
        ],
        "hooks": [
            {
                "beforeInvocation": {
                    "name": "guardrail_input_check",
                    "target": {
                        "lambda": {
                            "arn": hook_lambda_arn,
                            "timeoutSeconds": 10,
                            "failureMode": "deny",
                        }
                    },
                }
            },
            {
                "beforeToolCall": {
                    "name": "deterministic_tool_policy",
                    "target": {
                        "lambda": {
                            "arn": hook_lambda_arn,
                            "timeoutSeconds": 10,
                            "failureMode": "deny",
                        }
                    },
                }
            },
            {
                "afterInvocation": {
                    "name": "guardrail_output_check",
                    "target": {
                        "lambda": {
                            "arn": hook_lambda_arn,
                            "timeoutSeconds": 10,
                            "failureMode": "deny",
                        }
                    },
                }
            },
        ],
        "maxIterations": 6,
    }

    if existing:
        harness_id = existing["harnessId"]
        for _ in range(40):
            cur = ctrl.get_harness(harnessId=harness_id).get("harness", {})
            if cur.get("status") not in ("CREATING", "UPDATING"):
                break
            time.sleep(3)
        prev_versions = ctrl.list_harness_versions(harnessId=harness_id).get("harnessVersions", [])
        prev_max = max((int(v.get("harnessVersion", 0)) for v in prev_versions), default=0)
        print(f"Updating existing AgentCore Harness: {harness_id}...")
        update_kwargs = dict(harness_kwargs)
        update_kwargs.pop("harnessName", None)
        update_kwargs["harnessId"] = harness_id
        resp = ctrl.update_harness(**update_kwargs)
        harness_obj = resp.get("harness", resp)
        harness_arn = harness_obj.get("arn") or existing.get("arn")
        target_version = str(prev_max + 1)
    else:
        print(f"Creating AgentCore Managed Harness: {HARNESS_NAME}...")
        resp = ctrl.create_harness(**harness_kwargs)
        harness_obj = resp.get("harness", resp)
        harness_id = harness_obj["harnessId"]
        harness_arn = harness_obj["arn"]
        target_version = str(harness_obj.get("harnessVersion", "1"))

    for _ in range(80):
        versions = ctrl.list_harness_versions(harnessId=harness_id).get("harnessVersions", [])
        match = next(
            (v for v in versions if str(v.get("harnessVersion")) == target_version),
            None,
        )
        status = match.get("status", "") if match else ""
        if status == "READY":
            break
        if "FAILED" in status:
            raise RuntimeError(f"Harness failed: {match}")
        time.sleep(3)

    endpoints = ctrl.list_harness_endpoints(harnessId=harness_id).get("endpoints", [])
    existing_ep = next((e for e in endpoints if e.get("endpointName") == ENDPOINT_NAME), None)
    if not existing_ep:
        print(f"Creating Harness Endpoint: {ENDPOINT_NAME}...")
        ep_kwargs = {
            "harnessId": harness_id,
            "endpointName": ENDPOINT_NAME,
            "description": "Production endpoint for Tether Managed Harness",
        }
        if target_version:
            ep_kwargs["targetVersion"] = target_version
        ctrl.create_harness_endpoint(**ep_kwargs)
    elif target_version:
        print(f"Updating Harness Endpoint '{ENDPOINT_NAME}' to version {target_version}...")
        ctrl.update_harness_endpoint(
            harnessId=harness_id,
            endpointName=ENDPOINT_NAME,
            targetVersion=target_version,
        )

    for _ in range(40):
        eps = ctrl.list_harness_endpoints(harnessId=harness_id).get("endpoints", [])
        ep_obj = next((e for e in eps if e.get("endpointName") == ENDPOINT_NAME), None)
        if (
            ep_obj
            and ep_obj.get("status") == "READY"
            and (not target_version or str(ep_obj.get("liveVersion", "")) == target_version)
        ):
            break
        time.sleep(3)

    print(f"Harness Ready: {harness_arn}")
    return harness_id, harness_arn


def main():
    region = get_aws_region()
    session = get_aws_session()
    account_id = get_account_id(session)
    guardrail_id = resolve_guardrail_id(session)
    guardrail_version = os.environ.get("TETHER_GUARDRAIL_VERSION", DEFAULT_GUARDRAIL_VERSION)

    lam = session.client("lambda")
    ctrl = session.client("bedrock-agentcore-control")
    action_lambda_arn = lam.get_function(FunctionName=DEFAULT_LAMBDA_NAME)["Configuration"]["FunctionArn"]

    print(f"Deploying Tether onto AWS Bedrock AgentCore in {region} (Account {account_id})...")
    hook_lambda_arn = ensure_hook_lambda(session, account_id, region, guardrail_id, guardrail_version)
    agentcore_role_arn = ensure_agentcore_role(session, account_id, region)
    gw_id, gw_arn = ensure_gateway(ctrl, agentcore_role_arn, action_lambda_arn)
    harness_id, harness_arn = ensure_harness(ctrl, agentcore_role_arn, gw_arn, hook_lambda_arn)

    print("\n" + "=" * 68)
    print("AMAZON BEDROCK AGENTCORE DEPLOYMENT COMPLETE")
    print(f"Gateway:        {GATEWAY_NAME} ({gw_id})")
    print(f"Harness Name:   {HARNESS_NAME} ({harness_id})")
    print(f"Harness ARN:    {harness_arn}")
    print(f"Endpoint:       {ENDPOINT_NAME}")
    print(f"Hooks Lambda:   {hook_lambda_arn}")
    print("=" * 68)


if __name__ == "__main__":
    main()
