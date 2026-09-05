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

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    null = {
      source = "hashicorp/null"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = coalesce(var.region, data.aws_region.current.region)
  ecr_repo   = "ecr-for-${var.project_name}"
  image_tag  = "tf-${substr(sha256(filesha256("${var.repo_root}/Dockerfile")), 0, 12)}"
  built_uri  = "${aws_ecr_repository.web.repository_url}:${local.image_tag}"
  image_uri  = var.skip_docker_build ? var.web_image_uri : local.built_uri

  task_cpu_units       = 1024
  task_memory_mib      = 2048
  health_check_retries = 3

  agent_runtime_name   = replace(var.project_name, "-", "_")
  cognito_user_pool_id = var.app_config.cognito_user_pool_id
  cognito_client_id    = var.app_config.cognito_client_id
  cognito_user_pool_arn = (
    "arn:aws:cognito-idp:${local.region}:${local.account_id}:userpool/${local.cognito_user_pool_id}"
  )
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "role-ecs-task-for-${var.project_name}-${local.region}"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role" "execution" {
  name               = "role-ecs-execution-for-${var.project_name}-${local.region}"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "execution-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = [
          var.session_signing_key_secret_arn,
          var.cloudfront_signing_key_secret_arn,
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = [var.secrets_kms_key_arn]
      },
    ]
  })
}

resource "aws_iam_role_policy" "task" {
  name = "task-policy"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockModelAndKnowledgeBase"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:ApplyGuardrail",
          "bedrock:GetInferenceProfile",
          "bedrock:GetFoundationModel",
          "bedrock:StartIngestionJob",
          "bedrock:ListIngestionJobs",
          "bedrock:GetIngestionJob",
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:${local.account_id}:inference-profile/*",
          "arn:aws:bedrock:${local.region}:${local.account_id}:guardrail/*",
          "arn:aws:bedrock:${local.region}:${local.account_id}:guardrail-profile/*",
          "arn:aws:bedrock:${local.region}:${local.account_id}:knowledge-base/*",
        ]
      },
      {
        # Inference needs concrete Get/List actions (not Get*/List*). CreateInference
        # and reads support project ARN; CallWithBearerToken is resource-less (*).
        # https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-mantle.html
        Sid    = "BedrockMantleInference"
        Effect = "Allow"
        Action = [
          "bedrock-mantle:GetProject",
          "bedrock-mantle:GetModel",
          "bedrock-mantle:GetInference",
          "bedrock-mantle:ListProjects",
          "bedrock-mantle:ListModels",
          "bedrock-mantle:ListTagsForResource",
          "bedrock-mantle:CreateInference",
        ]
        Resource = [
          "arn:aws:bedrock-mantle:*:${local.account_id}:project/*",
        ]
      },
      {
        Sid      = "BedrockMantleBearerToken"
        Effect   = "Allow"
        Action   = ["bedrock-mantle:CallWithBearerToken"]
        Resource = ["*"]
      },
      {
        Sid    = "ListAgentCoreRuntimes"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:ListAgentRuntimes",
          "bedrock-agentcore-control:ListAgentRuntimes",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "GetAndInvokeAgentRuntime"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:InvokeAgentRuntimeWithWebResponse",
          "bedrock-agentcore:GetAgentRuntime",
          "bedrock-agentcore-control:GetAgentRuntime",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${local.agent_runtime_name}",
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${local.agent_runtime_name}-*",
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${local.agent_runtime_name}/runtime-endpoint/*",
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${local.agent_runtime_name}-*/runtime-endpoint/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [var.s3_bucket_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${var.s3_bucket_arn}/*"]
      },
      {
        Sid    = "CognitoUserPoolAuth"
        Effect = "Allow"
        Action = [
          "cognito-idp:InitiateAuth",
          "cognito-idp:RespondToAuthChallenge",
          "cognito-idp:DescribeUserPool",
          "cognito-idp:DescribeUserPoolClient",
        ]
        Resource = [
          local.cognito_user_pool_arn,
          "${local.cognito_user_pool_arn}/client/${local.cognito_client_id}",
        ]
      },
      {
        # GetUser does not support resource-level permissions.
        Sid      = "CognitoGetUser"
        Effect   = "Allow"
        Action   = ["cognito-idp:GetUser"]
        Resource = ["*"]
      },
      {
        Sid    = "S3FilesAppDataClientAccess"
        Effect = "Allow"
        Action = [
          "s3files:ClientMount",
          "s3files:ClientWrite",
          "s3files:ClientRootAccess",
        ]
        Resource = [var.s3_files_file_system_arn]
        Condition = {
          ArnEquals = {
            "s3files:AccessPointArn" = var.s3_files_access_point_arn
          }
        }
      },
      {
        Sid      = "S3FilesAppDataGetAccessPoint"
        Effect   = "Allow"
        Action   = ["s3files:GetAccessPoint"]
        Resource = [var.s3_files_access_point_arn]
      },
      {
        Sid      = "S3FilesAppDataListMountTargets"
        Effect   = "Allow"
        Action   = ["s3files:ListMountTargets"]
        Resource = [var.s3_files_file_system_arn]
      }
    ]
  })
}

# App-data FS policy: ECS only (Runtime uses session FS).
resource "aws_s3files_file_system_policy" "this" {
  file_system_id = var.s3_files_file_system_id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = [aws_iam_role.task.arn]
      }
      Action = [
        "s3files:ClientMount",
        "s3files:ClientWrite",
        "s3files:ClientRootAccess",
      ]
      Condition = {
        StringEquals = {
          "s3files:AccessPointArn" = var.s3_files_access_point_arn
        }
      }
    }]
  })
}

resource "aws_kms_key" "ecs_logs" {
  description             = "CMK for ECS CloudWatch log group (${var.project_name})"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccountAdmin"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:*"
          }
        }
      },
    ]
  })

  tags = {
    Name = "kms-ecs-logs-${var.project_name}"
  }
}

resource "aws_kms_alias" "ecs_logs" {
  name          = "alias/${var.project_name}-ecs-logs"
  target_key_id = aws_kms_key.ecs_logs.key_id
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/app-for-${var.project_name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.ecs_logs.arn
}

resource "aws_kms_key" "ecr" {
  description             = "CMK for ECR repository encryption (${var.project_name})"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccountAdmin"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowECR"
        Effect = "Allow"
        Principal = {
          Service = "ecr.amazonaws.com"
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
            "kms:ViaService" = "ecr.${local.region}.amazonaws.com"
          }
        }
      },
    ]
  })

  tags = {
    Name = "kms-ecr-${var.project_name}"
  }
}

resource "aws_kms_alias" "ecr" {
  name          = "alias/${var.project_name}-ecr"
  target_key_id = aws_kms_key.ecr.key_id
}

resource "aws_ecr_repository" "web" {
  name                 = local.ecr_repo
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr.arn
  }
}

resource "null_resource" "docker_build" {
  count = var.skip_docker_build ? 0 : 1

  triggers = {
    dockerfile = filesha256("${var.repo_root}/Dockerfile")
    tag        = local.image_tag
    repo       = aws_ecr_repository.web.repository_url
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      REGION="${local.region}"
      REPO="${aws_ecr_repository.web.repository_url}"
      TAG="${local.image_tag}"
      CONTEXT="${var.repo_root}"
      # Sync capability lists into application/ (parity with installer)
      cp -f "$CONTEXT/runtime_agent/langgraph/mcp.list" "$CONTEXT/application/mcp.list" 2>/dev/null || true
      # Rebuild application/skills.list from skills/*/SKILL.md (runtime has no skills.list)
      SKILLS_DIR="$CONTEXT/runtime_agent/langgraph/skills"
      LIST_PATH="$CONTEXT/application/skills.list"
      : > "$LIST_PATH"
      if [ -d "$SKILLS_DIR" ]; then
        for d in "$SKILLS_DIR"/*; do
          [ -d "$d" ] && [ -f "$d/SKILL.md" ] || continue
          basename "$d"
        done | LC_ALL=C sort -u > "$LIST_PATH"
      fi
      aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${local.account_id}.dkr.ecr.${local.region}.amazonaws.com"
      docker buildx build --platform linux/arm64 --provenance=false --sbom=false \
        -t "$REPO:$TAG" \
        -f "$CONTEXT/Dockerfile" "$CONTEXT" --push
    EOT
  }

  depends_on = [aws_ecr_repository.web]
}

resource "aws_ecs_cluster" "this" {
  name = "cluster-for-${var.project_name}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = "task-for-${var.project_name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(local.task_cpu_units)
  memory                   = tostring(local.task_memory_mib)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  volume {
    name = "app-data"
    s3files_volume_configuration {
      file_system_arn  = var.s3_files_file_system_arn
      access_point_arn = var.s3_files_access_point_arn
      root_directory   = "/"
    }
  }

  container_definitions = jsonencode([{
    name      = "app"
    image     = local.image_uri
    essential = true
    portMappings = [{
      containerPort = var.app_port
      protocol      = "tcp"
    }]
    environment = [
      { name = "APP_CONFIG_JSON", value = jsonencode(var.app_config) },
      { name = "CLOUDFRONT_KEY_PAIR_ID", value = var.cloudfront_public_key_id },
      { name = "TASK_DB_MOUNT", value = var.app_data_mount_path },
      { name = "TASK_DB_PROJECT", value = var.project_name },
      # graph/settings/tasks.db on app-data; skills via S3 API.
      { name = "SESSION_STORAGE_DIR", value = var.app_data_mount_path },
    ]
    secrets = [
      {
        name      = "SESSION_SIGNING_KEY"
        valueFrom = var.session_signing_key_secret_arn
      },
      {
        name      = "CLOUDFRONT_SIGNING_PRIVATE_KEY"
        valueFrom = "${var.cloudfront_signing_key_secret_arn}:private_key_pem::"
      },
    ]
    mountPoints = [{
      sourceVolume  = "app-data"
      containerPath = var.app_data_mount_path
      readOnly      = false
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
        "awslogs-region"        = local.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:${var.app_port}/api/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = local.health_check_retries
      startPeriod = 60
    }
  }])

  depends_on = [
    null_resource.docker_build,
    aws_iam_role_policy.task,
    aws_iam_role_policy.execution_secrets,
    aws_s3files_file_system_policy.this,
  ]
}

resource "aws_ecs_service" "app" {
  name            = "service-for-${var.project_name}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "app"
    container_port   = var.app_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [aws_ecs_task_definition.app]
}
