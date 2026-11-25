"""
Deploy Tether Layer 3 IAM Execution Role and AWS Lambda Action Handler.
Executes against AWS us-east-1 using the bedrock-janitor profile.
"""

import io
import json
import os
import time
import zipfile
import boto3
from botocore.exceptions import ClientError

PROFILE_NAME = "bedrock-janitor"
REGION_NAME = "us-east-1"
LAMBDA_ROLE_NAME = "Tether-Lambda-Execution-Role"
LAMBDA_FUNCTION_NAME = "Tether-Action-Handler"
LAMBDA_RUNTIME = "python3.12"


def get_session():
    return boto3.Session(profile_name=PROFILE_NAME, region_name=REGION_NAME)


def ensure_lambda_role(iam_client, account_id: str) -> str:
    """Create or update Tether-Lambda-Execution-Role with strict IAM boundaries."""
    trust_policy = {
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
        role_resp = iam_client.get_role(RoleName=LAMBDA_ROLE_NAME)
        role_arn = role_resp["Role"]["Arn"]
        print(f"IAM Role exists: {role_arn}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            print(f"Creating IAM Role: {LAMBDA_ROLE_NAME}...")
            role_resp = iam_client.create_role(
                RoleName=LAMBDA_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Scoped execution role for Tether Lambda Action Handler",
            )
            role_arn = role_resp["Role"]["Arn"]
        else:
            raise

    iam_client.attach_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    boundary_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowReadInventory",
                "Effect": "Allow",
                "Action": [
                    "ec2:DescribeInstances",
                    "ec2:DescribeVolumes",
                    "cloudwatch:GetMetricData",
                ],
                "Resource": "*",
            },
            {
                "Sid": "AllowStopDevByEnvironmentTag",
                "Effect": "Allow",
                "Action": "ec2:StopInstances",
                "Resource": f"arn:aws:ec2:{REGION_NAME}:{account_id}:instance/*",
                "Condition": {
                    "StringEquals": {
                        "aws:ResourceTag/Environment": "Development"
                    }
                },
            },
            {
                "Sid": "AllowStopDevByOwnerTag",
                "Effect": "Allow",
                "Action": "ec2:StopInstances",
                "Resource": f"arn:aws:ec2:{REGION_NAME}:{account_id}:instance/*",
                "Condition": {
                    "StringEquals": {
                        "aws:ResourceTag/Owner": "DevSandbox"
                    }
                },
            },
            {
                "Sid": "ExplicitDenyProtectedAndNonDev",
                "Effect": "Deny",
                "Action": "ec2:StopInstances",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "aws:ResourceTag/Environment": ["Production", "Staging", "Prod"]
                    }
                },
            },
            {
                "Sid": "ExplicitDenyProtectedFlag",
                "Effect": "Deny",
                "Action": "ec2:StopInstances",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "aws:ResourceTag/Protected": ["True", "true"]
                    }
                },
            },
            {
                "Sid": "ExplicitDenyDestructiveActions",
                "Effect": "Deny",
                "Action": [
                    "ec2:TerminateInstances",
                    "ec2:DeleteVolume",
                    "rds:DeleteDBInstance",
                    "rds:DeleteDBCluster",
                    "s3:DeleteBucket",
                ],
                "Resource": "*",
            },
        ],
    }

    iam_client.put_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyName="TetherLayer3PhysicalBoundaryPolicy",
        PolicyDocument=json.dumps(boundary_policy),
    )
    print("Attached TetherLayer3PhysicalBoundaryPolicy. Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


def build_lambda_zip(handler_path: str) -> bytes:
    """Package action_handler.py into an in-memory ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_path, arcname="action_handler.py")
    buf.seek(0)
    return buf.read()


def deploy_lambda_function(lambda_client, role_arn: str, handler_path: str) -> str:
    """Create or update the Tether-Action-Handler Lambda function."""
    zip_bytes = build_lambda_zip(handler_path)

    try:
        existing = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)
        print(f"Updating existing Lambda function: {LAMBDA_FUNCTION_NAME}...")
        resp = lambda_client.update_function_code(
            FunctionName=LAMBDA_FUNCTION_NAME,
            ZipFile=zip_bytes,
        )
        waiter = lambda_client.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=LAMBDA_FUNCTION_NAME)
        lambda_client.update_function_configuration(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Role=role_arn,
            Handler="action_handler.lambda_handler",
            Runtime=LAMBDA_RUNTIME,
            Timeout=30,
        )
        fn_arn = existing["Configuration"]["FunctionArn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"Creating Lambda function: {LAMBDA_FUNCTION_NAME}...")
            resp = lambda_client.create_function(
                FunctionName=LAMBDA_FUNCTION_NAME,
                Runtime=LAMBDA_RUNTIME,
                Role=role_arn,
                Handler="action_handler.lambda_handler",
                Code={"ZipFile": zip_bytes},
                Description="Tether Layer 3 FinOps action handler with runtime boundary enforcement",
                Timeout=30,
                MemorySize=128,
            )
            waiter = lambda_client.get_waiter("function_active_v2")
            waiter.wait(FunctionName=LAMBDA_FUNCTION_NAME)
            fn_arn = resp["FunctionArn"]
        else:
            raise

    print(f"Lambda ready: {fn_arn}")
    return fn_arn


def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    handler_path = os.path.join(root_dir, "lambda", "action_handler.py")

    session = get_session()
    account_id = session.client("sts").get_caller_identity()["Account"]
    print(f"Connected to AWS Account: {account_id} in {REGION_NAME}")

    iam_client = session.client("iam")
    lambda_client = session.client("lambda")

    role_arn = ensure_lambda_role(iam_client, account_id)
    fn_arn = deploy_lambda_function(lambda_client, role_arn, handler_path)

    print("\n" + "=" * 60)
    print("LAYER 3 DEPLOYMENT COMPLETE")
    print(f"Lambda Function: {LAMBDA_FUNCTION_NAME}")
    print(f"Function ARN:    {fn_arn}")
    print(f"IAM Role ARN:    {role_arn}")
    print("=" * 60)


if __name__ == "__main__":
    main()
