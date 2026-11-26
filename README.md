# Tether

[![Project Board](https://img.shields.io/badge/Project-Tracking_Board-blue)](https://github.com/users/primegideon/projects/6)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Amazon Bedrock AgentCore](https://img.shields.io/badge/Platform-Amazon_Bedrock_AgentCore-orange)](https://aws.amazon.com/bedrock/)

Autonomous FinOps agent with deterministic multi-layer boundary enforcement built on the **Amazon Bedrock AgentCore** managed harness. Created for the AWS Builder Center Hackathon: *"Agents, your rules: Bedrock Managed Agents build"*.

## Core Documentation

- [RULES.md](RULES.md): Plain-language boundary rules, refusal behaviors, and edge-case specifications.
- [Technical Architecture](architecture/layered-defense-spec.md): Three-layer defense model spanning Amazon Bedrock Guardrails, AgentCore prompts/schemas, and AWS IAM + Lambda.
- [Adversarial Stress Test Report](tests/STRESS_TEST_REPORT.md): Live AWS verification across 6 adversarial prompt and infrastructure boundary scenarios.
- [Live Demo & Stress Test Transcript](docs/LIVE_DEMO_OUTPUT.md): Captured live `Tether_Managed_Harness` execution showing hook evaluations, MCP Gateway calls, and IAM simulations.
- [Hackathon Submission Write-Up](docs/SUBMISSION_WRITEUP.md): Full build log, failures encountered during testing, fixes applied, and verification artifacts.

## Mission

Tether performs **one defined job**: audit idle AWS development resources and safely stop eligible instances to eliminate cloud waste without risking production systems.

### 3-Layer Security Architecture

1. **Layer 1 (Amazon Bedrock Guardrails):** Evaluates inputs and outputs via `before_invocation` and `after_invocation` lifecycle hooks with `Tether-Safety-Guardrail` (v2), blocking prompt injections, jailbreaks, privilege escalation, and AWS Access Key leakage.
2. **Layer 2 (Amazon Bedrock AgentCore Managed Harness):** Enforces single-purpose FinOps reasoning via `agent/system_prompt.txt`, typed tool schemas, and a `before_tool_call` lifecycle hook that validates tool allowlist, action mode, and blast-radius cap.
3. **Layer 3 (AWS Lambda & Scoped IAM):** Enforces deterministic runtime and IAM API constraints:
   - Blast-radius cap of 3 instances per execution in `lambda/action_handler.py`.
   - Tag condition (`aws:ResourceTag/Environment = Development` or `Owner = DevSandbox`).
   - Explicit IAM `Deny` on `ec2:TerminateInstances`, `ec2:DeleteVolume`, `rds:DeleteDBInstance`, `rds:DeleteDBCluster`, and `s3:DeleteBucket`.

## Repository Structure

```text
tether/
├── agent/
│   ├── agent_core.py               # AgentCore multi-turn runtime harness
│   ├── aws_config.py               # Shared AWS session and resource lookup
│   ├── guardrail_config.json       # Bedrock Guardrail definition
│   └── system_prompt.txt           # Agent operational contract
├── architecture/
│   └── layered-defense-spec.md     # Architecture diagram and IAM policy spec
├── docs/
│   ├── screenshots/
│   │   └── tether_live_demo.png    # Live AgentCore Playground screenshot
│   ├── LIVE_DEMO_OUTPUT.md         # Live CLI demo and stress test transcript
│   └── SUBMISSION_WRITEUP.md       # Builder Center submission write-up
├── lambda/
│   ├── action_handler.py           # Layer 3 Lambda boundary enforcement
│   ├── hook_handler.py             # AgentCore lifecycle hook validator
│   └── openapi_schema.json         # OpenAPI 3.0 tool specification
├── scripts/
│   ├── demo_cli.py                 # Live AWS and offline demo runner
│   ├── deploy_agentcore.py         # AgentCore Harness, Gateway, and Hooks deployment
│   ├── deploy_bedrock_agent.py     # Guardrail and IAM role deployment
│   └── deploy_lambda_layer.py      # Lambda and Layer 3 IAM deployment
├── tests/
│   ├── STRESS_TEST_REPORT.md       # Captured live AWS test outputs
│   ├── stress_test.py              # 6-scenario adversarial test suite
│   └── test_boundaries.py          # Automated Layer 3 unit tests
├── LICENSE                         # MIT License
└── RULES.md                        # Official boundary specification
```

## Quickstart

### 1. Run Unit Tests (Offline)
```bash
python -m unittest tests/test_boundaries.py -v
python tests/stress_test.py
```

### 2. Deploy to AWS (`us-east-1`)
Set your AWS profile (or use default credentials):
```bash
export AWS_PROFILE=your-aws-profile
export AWS_REGION=us-east-1

python scripts/deploy_bedrock_agent.py
python scripts/deploy_lambda_layer.py
python scripts/deploy_agentcore.py
```

### 3. Run Live AWS Demo & Adversarial Stress Tests
```bash
python scripts/demo_cli.py
python tests/stress_test.py --live
```

## Live Verification (AWS Console Playground)

![Tether Live Demo in Amazon Bedrock AgentCore Playground](docs/screenshots/tether_live_demo.png)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
