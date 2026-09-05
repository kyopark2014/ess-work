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

locals {
  agent_runtime_name               = replace(var.project_name, "-", "_")
  agentcore_memory_name            = replace(var.project_name, "-", "_")
  # installer.py uses a shared fixed name (reuse across projects in the account).
  agentcore_websearch_gateway_name = var.agentcore_websearch_gateway_name
  agentcore_websearch_target_name  = "websearch"
  vector_index_name                = var.project_name
  embedding_model_arn = (
    "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
  )
  alb_origin_header_secret_name      = "${var.project_name}/cloudfront-alb-origin-header"
  session_signing_key_secret_name    = "${var.project_name}/session-signing-key"
  cloudfront_signing_key_secret_name = "${var.project_name}/cloudfront-signing-key"
  repo_root                          = abspath("${path.module}/..")
}
