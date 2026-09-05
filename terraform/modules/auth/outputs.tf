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

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "cognito_user_pool_name" {
  value = var.project_name
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web_ui.id
}

output "cognito_client_name" {
  value = "${var.project_name}-web-ui"
}

output "cognito_admin_username" {
  value = var.cognito_admin_username
}

output "alb_origin_header_secret_arn" {
  value = aws_secretsmanager_secret.origin_header.arn
}

output "alb_origin_header_value" {
  value     = random_password.origin_header.result
  sensitive = true
}

output "session_signing_key_secret_arn" {
  value = aws_secretsmanager_secret.session_signing.arn
}

output "cloudfront_signing_key_secret_arn" {
  value = aws_secretsmanager_secret.cloudfront_signing.arn
}

output "secrets_kms_key_arn" {
  value = aws_kms_key.secrets.arn
}

output "cloudfront_public_key_id" {
  value = aws_cloudfront_public_key.this.id
}

output "cloudfront_key_group_id" {
  value = aws_cloudfront_key_group.this.id
}
