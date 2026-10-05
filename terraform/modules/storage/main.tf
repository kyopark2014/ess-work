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

locals {
  # Empty or "/" means the bucket root. Passing "/" to the API scopes keys
  # that literally start with "/".
  session_prefix  = trim(var.s3_files_session_prefix, "/")
  app_data_prefix = trim(var.s3_files_app_data_prefix, "/")
  share_root      = local.session_prefix == local.app_data_prefix
}

data "aws_iam_policy_document" "sync_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["elasticfilesystem.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sync" {
  name               = "role-s3files-sync-for-${var.project_name}"
  assume_role_policy = data.aws_iam_policy_document.sync_assume.json
}

resource "aws_iam_role_policy" "sync" {
  name = "s3files-sync-policy"
  role = aws_iam_role.sync.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:ListBucketVersions",
          "s3:GetObjectVersion",
          "s3:DeleteObjectVersion",
        ]
        Resource = [
          var.s3_bucket_arn,
          "${var.s3_bucket_arn}/*",
        ]
      },
      {
        Sid    = "EventBridgeS3FilesSyncRules"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:DeleteRule",
          "events:PutTargets",
          "events:RemoveTargets",
          "events:DescribeRule",
        ]
        Resource = [
          "arn:aws:events:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:rule/${var.project_name}-*",
          "arn:aws:events:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:rule/s3files-*",
        ]
      }
    ]
  })
}

resource "aws_s3files_file_system" "this" {
  # Runtime mount. Omit prefix so an empty value is the bucket root.
  bucket                = var.s3_bucket_arn
  role_arn              = aws_iam_role.sync.arn
  prefix                = local.session_prefix != "" ? "${local.session_prefix}/" : null
  accept_bucket_warning = true

  depends_on = [aws_iam_role_policy.sync]
}

resource "aws_s3files_mount_target" "this" {
  count = length(var.private_subnet_ids)

  file_system_id  = aws_s3files_file_system.this.id
  subnet_id       = var.private_subnet_ids[count.index]
  security_groups = [var.s3files_mount_security_group_id]
}

resource "aws_s3files_access_point" "this" {
  file_system_id = aws_s3files_file_system.this.id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/"
    creation_permissions {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }

  depends_on = [aws_s3files_mount_target.this]
}

resource "aws_s3files_file_system" "app_data" {
  # Separate ECS file system only when its prefix differs from Runtime.
  # The bucket root can have only one file system.
  count = local.share_root ? 0 : 1

  bucket                = var.s3_bucket_arn
  role_arn              = aws_iam_role.sync.arn
  prefix                = local.app_data_prefix != "" ? "${local.app_data_prefix}/" : null
  accept_bucket_warning = true

  depends_on = [aws_iam_role_policy.sync]
}

resource "aws_s3files_mount_target" "app_data" {
  count = local.share_root ? 0 : length(var.private_subnet_ids)

  file_system_id  = aws_s3files_file_system.app_data[0].id
  subnet_id       = var.private_subnet_ids[count.index]
  security_groups = [var.s3files_mount_security_group_id]
}

resource "aws_s3files_access_point" "app_data" {
  count          = local.share_root ? 0 : 1
  file_system_id = aws_s3files_file_system.app_data[0].id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/"
    creation_permissions {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }

  depends_on = [aws_s3files_mount_target.app_data]
}
