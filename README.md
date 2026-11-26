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

![Tether 3-Layer AWS AgentCore Architecture](architecture/tether-architecture.gif)

*(Static captures: [PNG](architecture/tether-architecture.png) · [SVG](architecture/tether-architecture.svg) · [IAM & Policy Spec](architecture/layered-defense-spec.md))*

1. **Layer 1 (Amazon Bedrock Guardrails):** Evaluates inputs and outputs via `before_invocation` and `after_invocation` lifecycle hooks with `Tether-Safety-Guardrail` (v2), blocking prompt injections, jailbreaks, privilege escalation, and AWS Access Key leakage.
2. **Layer 2 (Amazon Bedrock AgentCore Managed Harness):** Enforces single-purpose FinOps reasoning via `agent/system_prompt.txt`, typed tool schemas, and a `before_tool_call` lifecycle hook that validates tool allowlist, action mode, and blast-radius cap.
3. **Layer 3 (AWS Lambda & Scoped IAM):** Enforces deterministic runtime and IAM API constraints:
   - Blast-radius cap of 3 instances per execution in `lambda/action_handler.py`.
   - 24-hour CloudWatch `CPUUtilization` idle verification (`< 5.0%`) on running dev instances.
   - Tag condition (`aws:ResourceTag/Environment = Development` or `Owner = DevSandbox`).
   - Explicit IAM `Deny` on `ec2:TerminateInstances`, `ec2:DeleteVolume`, `rds:DeleteDBInstance`, `rds:DeleteDBCluster`, and `s3:DeleteBucket`.

### Estimated FinOps Impact (Realistic Scope)

Using standard AWS `us-east-1` Linux On-Demand compute rates (`m5.large` at `$0.096/hr`, `t3.large` at `$0.0832/hr`):
- A typical engineer uses a dev host ~50 hrs/week (~215 hrs/month out of 730 hrs/month). Left idle nights and weekends (`< 5%` CloudWatch CPU), roughly **~500 hrs/month** per host is idle compute burn.
- Stopping just **3 idle `m5.large` development instances** (Tether's single-run `MAX_BLAST_RADIUS = 3` cap) avoids `3 × 500 × $0.096 ≈ $144/month` (**`~$1,728/year`**) in EC2 compute spend (~`$1,498/year` on 3 `t3.large` hosts).
- **Safe-state design:** `ec2:StopInstances` drops EC2 hourly compute billing to `$0/hr` while keeping attached EBS volumes intact (EBS storage continues billing normally so developer data is never lost). The primary value of Tether's 3-layer isolation is making automated nightly cleanup safe enough for teams to actually enable.

## Repository Structure

```text
tether/
├── agent/
│   ├── agent_core.py               # AgentCore multi-turn runtime harness
│   ├── aws_config.py               # Shared AWS session and resource lookup
│   ├── guardrail_config.json       # Bedrock Guardrail definition
│   └── system_prompt.txt           # Agent operational contract
├── architecture/
│   ├── layered-defense-spec.md     # Architecture diagram and IAM policy spec
│   ├── tether-architecture.gif     # Animated Eraser AWS-icon 3-layer flow diagram
│   ├── tether-architecture.png     # Static Eraser high-res architecture export
│   └── tether-architecture.svg     # Genuine Eraser SVG vector export
├── docs/
│   ├── demos/
│   │   ├── step1_audit.gif               # Live EC2 + CloudWatch idle audit demo
│   │   ├── step2_stop_dev.gif            # Live dev EC2 stop via AgentCore hooks
│   │   ├── step3_block_prod.gif          # Urgent production outage refusal demo
│   │   └── step4_prompt_injection.gif    # Layer 1 Guardrail prompt injection block
│   ├── screenshots/
│   │   ├── step1_audit.png               # Audit step capture
│   │   ├── step2_stop_dev.png            # Dev stop step capture
│   │   ├── step3_block_prod.png          # Prod block step capture
│   │   ├── step4_prompt_injection.png    # Injection block step capture
│   │   └── tether_live_demo.png          # Live AgentCore Playground screenshot
│   ├── LIVE_DEMO_OUTPUT.md               # Live CLI demo and stress test transcript
│   └── SUBMISSION_WRITEUP.md             # Builder Center submission write-up
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
│   └── test_boundaries.py          # Automated Layer 2 hook and Layer 3 unit tests
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

End-to-end live session against `Tether_Managed_Harness` with temporary `t3.micro` instances in `us-east-1` (`tether-demo-dev-idle` tagged `Environment=Development` and `tether-demo-prod-critical` tagged `Environment=Production`):

### 1. Live EC2 + CloudWatch Idle Audit
Tether inspects running instances and 24-hour CloudWatch `CPUUtilization`, marking the idle dev host (`0.41%` avg CPU) eligible while excluding production. ([Screenshot](docs/screenshots/step1_audit.png))

![Step 1: Live EC2 and CloudWatch Idle Audit](docs/demos/step1_audit.gif)

### 2. Live Development Instance Stop (`Allowed` Hooks)
Invokes `Tether-Action-Target` through `before_invocation`, `before_tool_call`, and `after_invocation` lifecycle hooks to stop `tether-demo-dev-idle`. ([Screenshot](docs/screenshots/step2_stop_dev.png))

![Step 2: Live Development Instance Stop](docs/demos/step2_stop_dev.gif)

### 3. Urgent Production Outage Social-Engineering Refusal
Refuses an emergency request attempting to stop `tether-demo-prod-critical` (`Environment=Production`, `Protected=True`). ([Screenshot](docs/screenshots/step3_block_prod.png))

![Step 3: Production Refusal Under Urgency](docs/demos/step3_block_prod.gif)

### 4. Direct Prompt Injection Blocked at Layer 1
Stops a `SYSTEM OVERRIDE` termination prompt deterministically in `before_invocation` (`guardrail_input_check: Denied`) before the LLM or any tool executes. ([Screenshot](docs/screenshots/step4_prompt_injection.png) · [Playground Trace](docs/screenshots/tether_live_demo.png))

![Step 4: Prompt Injection Blocked at Layer 1](docs/demos/step4_prompt_injection.gif)

## Future Work

- **Multi-Account AWS Organizations Governance:** Support cross-account audits via scoped STS `AssumeRole` into member sandbox accounts while retaining centralized Guardrails and `before_tool_call` hooks.
- **Scheduled Sweeps + Human Approval Workflows:** Run nightly Amazon EventBridge audits and post interactive Slack/SNS approval cards whenever `eligible_count > 3` (matching [RULES.md](RULES.md) Rule 4).
- **Additional Safe Non-Prod Targets:** Extend the same tag-isolation and explicit-deny IAM pattern to stop idle dev/staging RDS instances (`rds:StopDBInstance`).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
