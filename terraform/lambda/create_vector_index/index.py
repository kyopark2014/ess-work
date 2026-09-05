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

"""Create OpenSearch Serverless knn index for Bedrock Knowledge Base."""

from __future__ import annotations

import json
import time

import boto3
import requests
from requests_aws4auth import AWS4Auth

# AOSS access-policy changes are eventually consistent; ~20s covers typical
# control-plane propagation before data-plane CreateIndex succeeds.
ACCESS_POLICY_PROPAGATION_DELAY_SECONDS = 20
# Extra wait after re-applying access policy on 401/403 before retrying create.
ACCESS_POLICY_AUTH_RETRY_DELAY_SECONDS = 15
# CreateIndex retries while AOSS / IAM propagate (~24 * retry backoff ≈ several minutes).
MAX_INDEX_CREATE_ATTEMPTS = 24
# Brief pause after successful create so Bedrock Knowledge Base can observe the index.
INDEX_CREATE_SETTLE_SECONDS = 30
# GET polls after create until the index is readable (~2 minutes at 10s intervals).
MAX_READINESS_CHECKS = 12
INDEX_READY_CHECK_INTERVAL_SECONDS = 10
# Settle briefly when the index already exists (idempotent re-runs).
INDEX_ALREADY_EXISTS_SETTLE_SECONDS = 10
# HTTP timeouts for AOSS data-plane calls (create vs readiness GET).
OPENSEARCH_HTTP_TIMEOUT_SECONDS = 60
OPENSEARCH_READY_CHECK_TIMEOUT_SECONDS = 30

INDEX_BODY = {
    "settings": {
        "index": {
            "knn": True,
            "knn.algo_param.ef_search": 512,
        }
    },
    "mappings": {
        "properties": {
            "vector_field": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "faiss",
                    "parameters": {
                        "ef_construction": 512,
                        "m": 16,
                    },
                },
            },
            "AMAZON_BEDROCK_TEXT": {"type": "text"},
            "AMAZON_BEDROCK_METADATA": {"type": "text"},
        }
    },
}


def _iam_role_arn_from_caller(sts_arn: str) -> str:
    if ":assumed-role/" not in sts_arn:
        return sts_arn
    account = sts_arn.split(":")[4]
    role_name = sts_arn.split(":assumed-role/", 1)[1].split("/", 1)[0]
    try:
        iam = boto3.client("iam")
        return iam.get_role(RoleName=role_name)["Role"]["Arn"]
    except Exception as e:
        print(f"get_role failed for {role_name}: {e}")
        return f"arn:aws:iam::{account}:role/{role_name}"


def ensure_access_policy(region, policy_name, collection_name, principals):
    """Create or update the AOSS data-access policy for the given principals."""
    try:
        client = boto3.client("opensearchserverless", region_name=region)
    except Exception as e:
        raise RuntimeError(
            f"Failed to create opensearchserverless client for {region}"
        ) from e
    rules = [
        {
            "ResourceType": "collection",
            "Resource": [f"collection/{collection_name}"],
            "Permission": [
                "aoss:CreateCollectionItems",
                "aoss:DeleteCollectionItems",
                "aoss:UpdateCollectionItems",
                "aoss:DescribeCollectionItems",
            ],
        },
        {
            "ResourceType": "index",
            "Resource": [f"index/{collection_name}/*"],
            "Permission": [
                "aoss:CreateIndex",
                "aoss:DeleteIndex",
                "aoss:UpdateIndex",
                "aoss:DescribeIndex",
                "aoss:ReadDocument",
                "aoss:WriteDocument",
            ],
        },
    ]
    document = [{"Rules": rules, "Principal": sorted(set(principals))}]
    try:
        existing = client.get_access_policy(name=policy_name, type="data")
        detail = existing["accessPolicyDetail"]
        policy_version = detail["policyVersion"]
        current = detail.get("policy") or []
        if isinstance(current, str):
            current = json.loads(current)
        merged = set()
        for block in current:
            for principal in block.get("Principal") or []:
                merged.add(principal)
        needed = set(principals)
        print(f"policy={policy_name} current_principals={sorted(merged)}")
        print(f"needed_principals={sorted(needed)}")
        if needed.issubset(merged):
            print("principals already present; skipping update")
            return
        merged.update(needed)
        document = [{"Rules": rules, "Principal": sorted(merged)}]
        try:
            client.update_access_policy(
                name=policy_name,
                type="data",
                policyVersion=policy_version,
                policy=json.dumps(document),
            )
            print("access policy updated")
        except client.exceptions.ValidationException as e:
            if "No changes detected" not in str(e):
                raise
            print("access policy unchanged")
    except client.exceptions.ResourceNotFoundException:
        try:
            client.create_access_policy(
                name=policy_name,
                type="data",
                policy=json.dumps(document),
            )
            print("access policy created")
        except Exception as error:
            print(f"access policy create failed: {type(error).__name__}")
            raise RuntimeError("Failed to create OpenSearch access policy") from error


def _awsauth(region: str) -> AWS4Auth:
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    return AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        "aoss",
        session_token=credentials.token,
    )


def _put_index(endpoint: str, index_name: str, region: str) -> requests.Response:
    url = f"{endpoint.rstrip('/')}/{index_name}"
    try:
        return requests.put(
            url,
            auth=_awsauth(region),
            headers={"Content-Type": "application/json"},
            data=json.dumps(INDEX_BODY),
            timeout=OPENSEARCH_HTTP_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException:
        print("OpenSearch put_index request failed")
        raise RuntimeError("Failed to reach OpenSearch endpoint") from None


def wait_for_index_ready(endpoint: str, index_name: str, region: str) -> None:
    """Poll until the index responds 200 or readiness attempts are exhausted."""
    print("index created; waiting for readiness")
    time.sleep(INDEX_CREATE_SETTLE_SECONDS)
    for ready_attempt in range(MAX_READINESS_CHECKS):
        try:
            check = requests.get(
                f"{endpoint.rstrip('/')}/{index_name}",
                auth=_awsauth(region),
                timeout=OPENSEARCH_READY_CHECK_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as e:
            print(
                f"ready_check={ready_attempt} request failed: "
                f"{type(e).__name__}: {e}"
            )
            time.sleep(INDEX_READY_CHECK_INTERVAL_SECONDS)
            continue
        print(f"ready_check={ready_attempt} status={check.status_code}")
        if check.status_code == 200:
            return
        time.sleep(INDEX_READY_CHECK_INTERVAL_SECONDS)


def create_vector_index(
    *,
    endpoint: str,
    index_name: str,
    region: str,
    collection_name: str,
    policy_name: str,
    principals: list[str],
) -> None:
    """Ensure access policy, create the knn index, and wait until it is ready."""
    ensure_access_policy(region, policy_name, collection_name, principals)
    time.sleep(ACCESS_POLICY_PROPAGATION_DELAY_SECONDS)

    last_error = None
    for attempt in range(MAX_INDEX_CREATE_ATTEMPTS):
        response = _put_index(endpoint, index_name, region)
        print(f"attempt={attempt} status={response.status_code}")
        if response.status_code in (200, 201):
            wait_for_index_ready(endpoint, index_name, region)
            return
        if response.status_code in (400, 409) and (
            "resource_already_exists" in response.text
            or "already exists" in response.text.lower()
        ):
            time.sleep(INDEX_ALREADY_EXISTS_SETTLE_SECONDS)
            return
        if response.status_code in (401, 403):
            ensure_access_policy(region, policy_name, collection_name, principals)
            time.sleep(ACCESS_POLICY_AUTH_RETRY_DELAY_SECONDS)
            last_error = RuntimeError("Failed to create OpenSearch index after auth error")
            continue
        print("OpenSearch index create failed")
        raise RuntimeError("Failed to create OpenSearch index")

    raise last_error or RuntimeError(
        "Failed to create OpenSearch index after retries"
    )


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if endpoint.startswith("http") else "https://" + endpoint


def _resolve_data_access_principals(props: dict) -> list[str]:
    """Resolve the IAM principals that need AOSS data-access, deduped/ordered."""
    try:
        sts = boto3.client("sts")
        caller = sts.get_caller_identity()["Arn"]
    except Exception as e:
        raise RuntimeError("Failed to resolve caller identity via STS") from e
    self_role = _iam_role_arn_from_caller(caller)
    print(f"caller={caller} self_role={self_role}")
    candidates = [
        self_role,
        props.get("IndexFnRoleArn") or self_role,
        props.get("KbRoleArn") or "",
    ]
    return [p for p in candidates if p]


def handler(event, context):
    request_type = event["RequestType"]
    props = event["ResourceProperties"]
    endpoint = _normalize_endpoint(props["CollectionEndpoint"])
    index_name = props["IndexName"]
    collection_name = props.get("CollectionName") or index_name
    policy_name = props.get("AccessPolicyName") or f"{collection_name}-data"
    physical_id = f"{endpoint}/{index_name}"

    if request_type == "Delete":
        return {"PhysicalResourceId": physical_id}

    create_vector_index(
        endpoint=endpoint,
        index_name=index_name,
        region=props["Region"],
        collection_name=collection_name,
        policy_name=policy_name,
        principals=_resolve_data_access_principals(props),
    )
    return {"PhysicalResourceId": physical_id}
