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

variable "region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "ecs_security_group_id" {
  type = string
}

variable "app_port" {
  type = number
}

variable "app_data_mount_path" {
  type = string
}

variable "target_group_arn" {
  type = string
}

variable "s3_bucket_arn" {
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

variable "agent_runtime_role_arn" {
  type = string
}

variable "session_signing_key_secret_arn" {
  type = string
}

variable "cloudfront_signing_key_secret_arn" {
  type = string
}

variable "secrets_kms_key_arn" {
  type = string
}

variable "cloudfront_public_key_id" {
  type = string
}

variable "app_config" {
  type = any
}

variable "skip_docker_build" {
  type = bool
}

variable "web_image_uri" {
  type = string
}

variable "repo_root" {
  type = string
}
