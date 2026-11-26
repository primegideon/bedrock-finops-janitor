"""
Unit tests for Tether Layer 3 boundary enforcement in lambda/action_handler.py.
"""

import importlib.util
import json
import os
import unittest
from unittest.mock import MagicMock, patch

HANDLER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "lambda", "action_handler.py")
)
SPEC = importlib.util.spec_from_file_location("action_handler", HANDLER_PATH)
action_handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action_handler)


class TestLayer3Boundaries(unittest.TestCase):
    def setUp(self):
        os.environ["MOCK_MODE"] = "true"

    def tearDown(self):
        os.environ.pop("MOCK_MODE", None)

    def test_audit_waste_classifies_eligibility_correctly(self):
        res = action_handler.audit_waste()
        self.assertEqual(res["status"], "success")
        by_id = {item["id"]: item for item in res["resources"]}
        self.assertTrue(by_id["i-0a1b2c3d4e5f00001"]["eligible_for_stop"])
        self.assertTrue(by_id["i-0a1b2c3d4e5f00003"]["eligible_for_stop"])
        self.assertFalse(by_id["i-0prod999999999999"]["eligible_for_stop"])
        self.assertFalse(by_id["i-0untagged88888888"]["eligible_for_stop"])

    def test_stop_valid_dev_instances_within_blast_radius(self):
        res = action_handler.stop_dev_instances(
            ["i-0a1b2c3d4e5f00001", "i-0a1b2c3d4e5f00002", "i-0a1b2c3d4e5f00003"]
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "STOPPED")
        self.assertEqual(len(res["instances"]), 3)

    def test_reject_blast_radius_exceeded(self):
        res = action_handler.stop_dev_instances(
            [
                "i-0a1b2c3d4e5f00001",
                "i-0a1b2c3d4e5f00002",
                "i-0a1b2c3d4e5f00003",
                "i-0a1b2c3d4e5f00004",
            ]
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["code"], "BLAST_RADIUS_EXCEEDED")

    def test_reject_production_instance(self):
        res = action_handler.stop_dev_instances(["i-0prod999999999999"])
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["code"], "ENVIRONMENT_BOUNDARY_VIOLATION")

    def test_reject_untagged_instance(self):
        res = action_handler.stop_dev_instances(["i-0untagged88888888"])
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["code"], "ENVIRONMENT_BOUNDARY_VIOLATION")

    def test_reject_forbidden_terminate_mode(self):
        res = action_handler.stop_dev_instances(
            ["i-0a1b2c3d4e5f00001"], action_mode="terminate"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["code"], "FORBIDDEN_ACTION")

    def test_openapi_action_group_event_dispatch(self):
        event = {
            "actionGroup": "TetherActions",
            "apiPath": "/stop-dev-instances",
            "httpMethod": "POST",
            "parameters": [
                {"name": "instance_ids", "value": '["i-0prod999999999999"]'}
            ],
        }
        resp = action_handler.lambda_handler(event, None)
        self.assertEqual(resp["response"]["httpStatusCode"], 400)
        body = json.loads(resp["response"]["responseBody"]["application/json"]["body"])
        self.assertEqual(body["code"], "ENVIRONMENT_BOUNDARY_VIOLATION")

    def test_live_audit_requires_low_cpu_and_running_state(self):
        os.environ["MOCK_MODE"] = "false"
        fake_ec2 = MagicMock()
        fake_paginator = MagicMock()
        fake_paginator.paginate.return_value = [
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-idle-dev",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {"Key": "Environment", "Value": "Development"},
                                    {"Key": "Name", "Value": "idle-box"},
                                ],
                            },
                            {
                                "InstanceId": "i-busy-dev",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {"Key": "Environment", "Value": "Development"},
                                    {"Key": "Name", "Value": "busy-box"},
                                ],
                            },
                            {
                                "InstanceId": "i-stopped-dev",
                                "State": {"Name": "stopped"},
                                "Tags": [
                                    {"Key": "Environment", "Value": "Development"},
                                    {"Key": "Name", "Value": "stopped-box"},
                                ],
                            },
                            {
                                "InstanceId": "i-idle-prod",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {"Key": "Environment", "Value": "Production"},
                                    {"Key": "Name", "Value": "prod-box"},
                                ],
                            },
                        ]
                    }
                ]
            }
        ]
        fake_ec2.get_paginator.return_value = fake_paginator

        fake_cw = MagicMock()
        fake_cw.get_metric_data.return_value = {
            "MetricDataResults": [
                {"Id": "cpu_0", "Values": [1.2, 2.4]},
                {"Id": "cpu_1", "Values": [42.0, 55.0]},
            ]
        }

        with (
            patch.object(action_handler, "_get_ec2_client", return_value=fake_ec2),
            patch.object(action_handler, "_get_cloudwatch_client", return_value=fake_cw),
        ):
            res = action_handler.audit_waste()

        self.assertEqual(res["mode"], "live")
        self.assertEqual(res["eligible_count"], 1)
        by_id = {item["id"]: item for item in res["resources"]}
        self.assertTrue(by_id["i-idle-dev"]["eligible_for_stop"])
        self.assertEqual(by_id["i-idle-dev"]["avg_cpu_percent"], 1.8)
        self.assertFalse(by_id["i-busy-dev"]["eligible_for_stop"])
        self.assertEqual(by_id["i-busy-dev"]["avg_cpu_percent"], 48.5)
        self.assertFalse(by_id["i-stopped-dev"]["eligible_for_stop"])
        self.assertIsNone(by_id["i-stopped-dev"]["avg_cpu_percent"])
        self.assertFalse(by_id["i-idle-prod"]["eligible_for_stop"])
        fake_cw.get_metric_data.assert_called_once()


if __name__ == "__main__":
    unittest.main()
