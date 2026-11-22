# Tether

[![Project Board](https://img.shields.io/badge/Project-Tracking_Board-blue)](https://github.com/users/primegideon/projects/6)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Amazon Bedrock](https://img.shields.io/badge/Platform-Amazon_Bedrock-orange)](https://aws.amazon.com/bedrock/)

Autonomous FinOps agent with deterministic boundary enforcement for Amazon Bedrock Managed Agents. Built for the AWS Builder Center Bedrock Agents Hackathon.

## Documentation

- [RULES.md](RULES.md): Boundary rules and safety constraints.
- [Technical Architecture](architecture/layered-defense-spec.md): Multi-layer security model spanning Bedrock Guardrails, Agent Prompts, and AWS IAM.

## Mission
Tether performs one defined task: audit and stop idle AWS development resources to reduce infrastructure costs without risking production systems.

### Security Model
1. Layer 1 (Bedrock Guardrail): Filters prompt injection, role overrides, and credential exfiltration.
2. Layer 2 (Managed Agent): Validates environment tags and executes only typed OpenAPI actions.
3. Layer 3 (IAM and Lambda): Enforces physical constraints in the execution role, including explicit denials for termination and mandatory development tag requirements.

## Project Tracking

- Issue #1: Boundary Rules and Layered Architecture Specification.
- Issue #2: Bedrock Managed Agent and Guardrails Configuration.
- Issue #3: Action Group Lambda Implementation.
- Issue #4: Adversarial Testing Suite.
- Issue #5: Hackathon Submission Documentation.

## License
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
