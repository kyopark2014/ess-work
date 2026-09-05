# Copyright 2026 Amazon.com, Inc. or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for create_vector_index helpers."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import index as cvi  # noqa: E402


class HelperTests(unittest.TestCase):
    def test_normalize_endpoint_adds_https(self) -> None:
        self.assertEqual(
            cvi._normalize_endpoint("example.aoss.amazonaws.com"),
            "https://example.aoss.amazonaws.com",
        )
        self.assertEqual(
            cvi._normalize_endpoint("https://example.aoss.amazonaws.com"),
            "https://example.aoss.amazonaws.com",
        )

    def test_iam_role_arn_passthrough(self) -> None:
        arn = "arn:aws:iam::123456789012:role/MyRole"
        self.assertEqual(cvi._iam_role_arn_from_caller(arn), arn)

    @patch("index.boto3.client")
    def test_iam_role_arn_from_assumed_role(self, mock_client) -> None:
        iam = MagicMock()
        iam.get_role.return_value = {
            "Role": {"Arn": "arn:aws:iam::123456789012:role/MyRole"}
        }
        mock_client.return_value = iam
        assumed = (
            "arn:aws:sts::123456789012:assumed-role/MyRole/session-name"
        )
        self.assertEqual(
            cvi._iam_role_arn_from_caller(assumed),
            "arn:aws:iam::123456789012:role/MyRole",
        )

    @patch("index.boto3.client")
    def test_resolve_principals_includes_self_and_kb(self, mock_client) -> None:
        sts = MagicMock()
        sts.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::123456789012:role/IndexFn"
        }
        mock_client.return_value = sts
        principals = cvi._resolve_data_access_principals(
            {
                "IndexFnRoleArn": "arn:aws:iam::123456789012:role/IndexFn",
                "KbRoleArn": "arn:aws:iam::123456789012:role/KbRole",
            }
        )
        self.assertIn("arn:aws:iam::123456789012:role/IndexFn", principals)
        self.assertIn("arn:aws:iam::123456789012:role/KbRole", principals)

    @patch("index.boto3.client")
    def test_resolve_principals_sts_failure(self, mock_client) -> None:
        mock_client.side_effect = RuntimeError("no creds")
        with self.assertRaises(RuntimeError):
            cvi._resolve_data_access_principals({})


if __name__ == "__main__":
    unittest.main()
