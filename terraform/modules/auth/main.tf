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

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_cognito_user_pool" "this" {
  name = var.project_name

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  tags = {
    Name = "user-pool-${var.project_name}"
  }
}

resource "aws_cognito_user_pool_client" "web_ui" {
  name         = "${var.project_name}-web-ui"
  user_pool_id = aws_cognito_user_pool.this.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  generate_secret = false
}

resource "aws_cognito_user" "admin" {
  user_pool_id = aws_cognito_user_pool.this.id
  username     = var.cognito_admin_username
  password     = var.cognito_admin_password

  message_action = "SUPPRESS"

  lifecycle {
    ignore_changes = [password]
  }
}

resource "random_password" "origin_header" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "origin_header" {
  name                    = var.alb_origin_header_secret_name
  description             = "CloudFront to ALB origin verification header for ${var.project_name}"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.arn
}

resource "aws_secretsmanager_secret_version" "origin_header" {
  secret_id     = aws_secretsmanager_secret.origin_header.id
  secret_string = random_password.origin_header.result
}

resource "random_password" "session_signing" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "session_signing" {
  name                    = var.session_signing_key_secret_name
  description             = "HMAC session signing key for ${var.project_name}"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.arn
}

resource "aws_secretsmanager_secret_version" "session_signing" {
  secret_id     = aws_secretsmanager_secret.session_signing.id
  secret_string = random_password.session_signing.result
}

# CloudFront signed-cookie RSA material (replaces CDK Lambda custom resource)
resource "tls_private_key" "cloudfront" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "aws_cloudfront_public_key" "this" {
  name        = "${var.project_name}-cf-public-key"
  encoded_key = tls_private_key.cloudfront.public_key_pem
  comment     = "Signing public key for ${var.project_name}"
}

resource "aws_cloudfront_key_group" "this" {
  name  = "${var.project_name}-cf-key-group"
  items = [aws_cloudfront_public_key.this.id]
}

resource "aws_kms_key" "secrets" {
  description             = "CMK for Secrets Manager secrets (${var.project_name})"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccountAdmin"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowSecretsManager"
        Effect = "Allow"
        Principal = {
          Service = "secretsmanager.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:CreateGrant",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${data.aws_region.current.id}.amazonaws.com"
          }
        }
      },
    ]
  })

  tags = {
    Name = "kms-secrets-${var.project_name}"
  }
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${var.project_name}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

resource "aws_secretsmanager_secret" "cloudfront_signing" {
  name                    = var.cloudfront_signing_key_secret_name
  description             = "CloudFront signing key material for ${var.project_name}"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.arn
}

resource "aws_secretsmanager_secret_version" "cloudfront_signing" {
  secret_id = aws_secretsmanager_secret.cloudfront_signing.id
  secret_string = jsonencode({
    private_key_pem = tls_private_key.cloudfront.private_key_pem
    public_key_pem  = tls_private_key.cloudfront.public_key_pem
    public_key_id   = aws_cloudfront_public_key.this.id
    key_group_id    = aws_cloudfront_key_group.this.id
  })
}
