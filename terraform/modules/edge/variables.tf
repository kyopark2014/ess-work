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

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "alb_security_group_id" {
  type = string
}

variable "app_port" {
  type = number
}

variable "alb_idle_timeout_seconds" {
  type = number
}

variable "sse_origin_read_timeout_seconds" {
  type = number
}

variable "custom_header_name" {
  type = string
}

variable "origin_header_value" {
  type      = string
  sensitive = true
}

variable "cloudfront_key_group_id" {
  type = string
}

variable "s3_bucket_id" {
  type = string
}

variable "s3_bucket_arn" {
  type = string
}

variable "s3_bucket_regional_domain_name" {
  type    = string
  default = ""
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN in us-east-1 for CloudFront custom domain (empty uses default *.cloudfront.net cert)"
  type        = string
  default     = ""
}

variable "cloudfront_aliases" {
  description = "Alternate domain names (CNAMEs) for CloudFront; required when acm_certificate_arn is set"
  type        = list(string)
  default     = []
}
