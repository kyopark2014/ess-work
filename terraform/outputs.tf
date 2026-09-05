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

output "sharing_url" {
  description = "CloudFront URL for the Web UI"
  value       = module.edge.sharing_url
}

output "cognito_admin_username" {
  value = module.auth.cognito_admin_username
}

output "cognito_user_pool_id" {
  value = module.auth.cognito_user_pool_id
}

output "cognito_client_id" {
  value = module.auth.cognito_client_id
}

output "knowledge_base_id" {
  value = module.data.knowledge_base_id
}

output "data_source_id" {
  value = module.data.data_source_id
}

output "s3_bucket" {
  value = module.data.s3_bucket_name
}

output "agent_runtime_arn" {
  value = module.agentcore.agent_runtime_arn
}

output "memory_id" {
  value = module.agentcore.memory_id
}

output "gateway_id" {
  value = module.gateway.gateway_id
}

output "gateway_url" {
  value = module.gateway.gateway_url
}

output "gateway_region" {
  value = var.agentcore_gateway_region
}

output "web_image_uri" {
  value = module.compute.web_image_uri
}

output "runtime_image_uri" {
  value = module.agentcore.runtime_image_uri
}

output "ecs_cluster_name" {
  value = module.compute.ecs_cluster_name
}

output "ecs_service_name" {
  value = module.compute.ecs_service_name
}

output "app_config" {
  description = "Normalized config map (same keys as application/config.json)"
  value       = local.app_config
  sensitive   = true
}

output "config_for_write" {
  description = "Flat outputs for scripts/write_config.py"
  value = merge(local.app_config, {
    knowledge_base_role   = module.data.knowledge_base_role_arn
    collectionArn         = module.data.collection_arn
    opensearch_url        = module.data.opensearch_endpoint
    s3_arn                = module.data.s3_bucket_arn
    agent_runtime_role    = module.agentcore.agent_runtime_role_arn
    agentcore_memory_role = module.agentcore.agentcore_memory_role_arn
    latest_image_tag = (
      length(split(":", module.compute.web_image_uri)) > 1
      ? element(split(":", module.compute.web_image_uri), length(split(":", module.compute.web_image_uri)) - 1)
      : ""
    )
    build_number = (
      length(split(":", module.compute.web_image_uri)) > 1
      ? element(split(":", module.compute.web_image_uri), length(split(":", module.compute.web_image_uri)) - 1)
      : ""
    )
  })
  sensitive = true
}
