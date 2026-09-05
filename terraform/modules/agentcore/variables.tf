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

variable "project_name" {
  type = string
}

variable "agent_runtime_name" {
  type = string
}

variable "memory_name" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "agent_runtime_security_group_id" {
  type = string
}

variable "s3_files_file_system_id" {
  type = string
}

variable "s3_files_file_system_arn" {
  type = string
}

variable "s3_files_access_point_arn" {
  type = string
}

variable "s3_bucket_arn" {
  type = string
}

variable "session_storage_mount_path" {
  type = string
}

variable "knowledge_base_id" {
  type = string
}

variable "skip_docker_build" {
  type = bool
}

variable "runtime_image_uri" {
  type = string
}

variable "repo_root" {
  type = string
}

variable "region" {
  type = string
}

variable "websearch_gateway_url" {
  type        = string
  description = "AgentCore websearch gateway MCP URL (us-east-1)"
  default     = ""
}

variable "websearch_gateway_region" {
  type    = string
  default = "us-east-1"
}

variable "s3_bucket_name" {
  type        = string
  description = "Application storage bucket name (for APP_CONFIG_JSON)"
}

variable "sharing_url" {
  type        = string
  description = "CloudFront sharing URL for uploaded artifacts"
  default     = ""
}

variable "data_source_id" {
  type    = string
  default = ""
}

variable "knowledge_base_role_arn" {
  type    = string
  default = ""
}
