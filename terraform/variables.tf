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
  description = "Project name prefix (parity with installer project_name)"
  type        = string
  default     = "ess-work"
}

variable "region" {
  description = "Primary AWS region"
  type        = string
  default     = "us-west-2"
}

variable "agentcore_gateway_region" {
  description = "Region for AgentCore Web Search Gateway"
  type        = string
  default     = "us-east-1"
}

variable "agentcore_websearch_gateway_name" {
  description = "AgentCore Web Search Gateway name (installer default: gateway-websearch; shared across projects)"
  type        = string
  default     = "gateway-websearch"
}

variable "cognito_admin_password" {
  description = "Permanent password for Cognito admin user"
  type        = string
  sensitive   = true
}

variable "cognito_admin_username" {
  description = "Cognito admin username"
  type        = string
  default     = "admin"
}

variable "skip_docker_build" {
  description = "Skip local Docker build/push; requires runtime_image_uri and web_image_uri"
  type        = bool
  default     = false
}

variable "runtime_image_uri" {
  description = "Pre-built AgentCore Runtime image URI (required when skip_docker_build=true)"
  type        = string
  default     = ""
}

variable "web_image_uri" {
  description = "Pre-built Web UI image URI (required when skip_docker_build=true)"
  type        = string
  default     = ""
}

variable "app_port" {
  type    = number
  default = 8501
}

variable "sse_origin_read_timeout_seconds" {
  type    = number
  default = 60 # CloudFront OriginReadTimeout (account max typically 60–120s)
}

variable "alb_idle_timeout_seconds" {
  type    = number
  default = 600
}

variable "custom_header_name" {
  type    = string
  default = "X-Custom-Header"
}

variable "s3_files_session_prefix" {
  type    = string
  default = "agentcore-sessions/"
}

variable "s3_files_app_data_prefix" {
  type    = string
  default = "app-data/"
}

variable "session_storage_mount_path" {
  type    = string
  default = "/mnt/workspace"
}

variable "app_data_mount_path" {
  type    = string
  default = "/mnt/app-data"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN in us-east-1 for CloudFront (empty = default *.cloudfront.net cert)"
  type        = string
  default     = ""
}

variable "cloudfront_aliases" {
  description = "CloudFront alternate domain names; required when acm_certificate_arn is set"
  type        = list(string)
  default     = []
}
