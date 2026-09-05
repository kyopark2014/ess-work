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
  # installer/CDK parity: {project_name}_langgraph
  ecr_repo    = "${var.project_name}_langgraph"
  runtime_dir = "${var.repo_root}/runtime_agent/langgraph"
  # Dockerfile COPYs from repo root (runtime_agent/langgraph + graph/lib).
  image_tag = "tf-${substr(sha256(join("", [
    filesha256("${local.runtime_dir}/Dockerfile"),
    filesha256("${local.runtime_dir}/langgraph_agent.py"),
    filesha256("${local.runtime_dir}/mcp_config.py"),
    filesha256("${local.runtime_dir}/utils.py"),
  ])), 0, 12)}"

  # config.json is dockerignored; inject via APP_CONFIG_JSON (max 5000 chars).
  runtime_app_config = {
    region                             = local.region
    projectName                        = var.project_name
    accountId                          = local.account_id
    s3_bucket                          = var.s3_bucket_name
    s3_arn                             = var.s3_bucket_arn
    sharing_url                        = var.sharing_url
    knowledge_base_id                  = var.knowledge_base_id
    knowledge_base_role                = var.knowledge_base_role_arn
    data_source_id                     = var.data_source_id
    memory_id                          = aws_bedrockagentcore_memory.this.id
    agentcore_memory_role              = aws_iam_role.memory.arn
    agentcore_websearch_gateway_url    = var.websearch_gateway_url
    agentcore_websearch_gateway_region = var.websearch_gateway_region
    s3_files_access_point_arn          = var.s3_files_access_point_arn
  }
  runtime_app_config_json = jsonencode({
    for k, v in local.runtime_app_config : k => v if v != null && v != ""
  })
}

data "aws_iam_policy_document" "runtime_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "AmazonBedrockAgentCoreRuntimeRoleFor${var.project_name}"
  assume_role_policy = data.aws_iam_policy_document.runtime_assume.json
}

resource "aws_iam_role_policy" "runtime" {
  name = "runtime-policy"
  role = aws_iam_role.runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
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
        Effect   = "Allow"
        Action   = ["s3files:GetAccessPoint"]
        Resource = [var.s3_files_access_point_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3files:ListMountTargets"]
        Resource = [var.s3_files_file_system_arn]
      },
      {
        Sid    = "BedrockModelInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:ApplyGuardrail",
          "bedrock:GetInferenceProfile",
          "bedrock:GetFoundationModel",
          "bedrock:Retrieve",
          "bedrock:RetrieveAndGenerate",
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
        # GPT 5.6 Sol/Terra/Luna go through bedrock-mantle (OpenAI-compatible).
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
        Sid    = "WorkloadAccessToken"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
          "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*"
        ]
      },
      {
        Sid    = "InvokeAgentCoreGatewayAndWebSearch"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeGateway",
          "bedrock-agentcore:InvokeWebSearch",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${var.websearch_gateway_region}:${local.account_id}:gateway/*",
          "arn:aws:bedrock-agentcore:${var.websearch_gateway_region}:aws:tool/web-search.v1",
        ]
      },
      {
        Sid    = "ListAgentCoreGatewaysAndRuntimes"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:ListGateways",
          "bedrock-agentcore:ListAgentRuntimes",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "GetAndInvokeAgentRuntime"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetAgentRuntime",
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:InvokeAgentRuntimeWithWebResponse",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${var.agent_runtime_name}",
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${var.agent_runtime_name}-*",
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${var.agent_runtime_name}/runtime-endpoint/*",
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:runtime/${var.agent_runtime_name}-*/runtime-endpoint/*",
        ]
      },
      {
        Sid    = "GetAgentCoreGateway"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetGateway",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${var.websearch_gateway_region}:${local.account_id}:gateway/*"
        ]
      },
      {
        Sid    = "AgentCoreMemoryAccess"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:DeleteEvent",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:ListMemoryRecords",
          "bedrock-agentcore:GetMemoryRecord",
          "bedrock-agentcore:ListActors",
          "bedrock-agentcore:ListSessions",
          "bedrock-agentcore:GetMemory",
          "bedrock-agentcore:UpdateMemory",
          "bedrock-agentcore:ListMemories",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:memory/*",
          "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:memory/${replace(var.project_name, "-", "_")}*",
        ]
      },
      {
        Sid      = "ProjectS3BucketMeta"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation"]
        Resource = [var.s3_bucket_arn]
      },
      {
        Sid      = "ProjectS3ListAllowedPrefixes"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [var.s3_bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "artifacts",
              "artifacts/*",
              "images",
              "images/*",
              "docs",
              "docs/*",
            ]
          }
        }
      },
      {
        Sid    = "ProjectS3Objects"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${var.s3_bucket_arn}/artifacts/*",
          "${var.s3_bucket_arn}/images/*",
          "${var.s3_bucket_arn}/docs/*",
        ]
      },
      {
        Sid    = "DenySensitiveS3Prefixes"
        Effect = "Deny"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:DeleteObjectVersion",
        ]
        Resource = [
          "${var.s3_bucket_arn}/app-data/*",
          "${var.s3_bucket_arn}/agentcore-sessions/*",
        ]
      },
      {
        Sid    = "SecretsManagerRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
        ]
        Resource = [
          "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:tavilyapikey-${var.project_name}*",
          "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:tavilyapikey-??????",
        ]
      },
      {
        # GetAuthorizationToken is a service-level action (Resource "*").
        Sid      = "ECRGetAuthorizationToken"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = ["*"]
      },
      {
        Sid    = "ECRImagePull"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = [aws_ecr_repository.runtime.arn]
      },
      {
        Sid    = "LogsAccess"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
        ]
        Resource = [
          "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/*",
          "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/*:log-stream:*",
        ]
      },
      {
        # OTEL → X-Ray / CloudWatch metrics. Parity with langgraph/installer.py +
        # AgentCore runtime execution-role docs (sampling APIs).
        Sid    = "CloudWatchMetricsAndXRay"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:PutAttributes",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "VpcNetworkInterface"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:DescribeSubnets",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeVpcs",
          "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses",
        ]
        Resource = ["*"]
      }
    ]
  })
}

data "aws_iam_policy_document" "memory_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "memory" {
  name               = "role-agentcore-memory-for-${var.project_name}-${local.region}"
  assume_role_policy = data.aws_iam_policy_document.memory_assume.json
}

resource "aws_iam_role_policy" "memory" {
  name = "memory-bedrock"
  role = aws_iam_role.memory.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      Resource = [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:*:inference-profile/*",
      ]
      Condition = {
        StringEquals = {
          "aws:ResourceAccount" = local.account_id
        }
      }
    }]
  })
}

resource "aws_bedrockagentcore_memory" "this" {
  name                      = var.memory_name
  description               = "Memory for ${var.project_name}"
  event_expiry_duration     = 365
  memory_execution_role_arn = aws_iam_role.memory.arn

  depends_on = [aws_iam_role_policy.memory]
}

resource "aws_bedrockagentcore_memory_strategy" "user_preference" {
  name       = "UserPreference"
  memory_id  = aws_bedrockagentcore_memory.this.id
  type       = "USER_PREFERENCE"
  namespaces = ["/users/{actorId}/preferences"]
}

# Memory strategies share one UpdateMemory API; create serially and wait until
# the Memory leaves UPDATING (API create returns before status is ACTIVE).
locals {
  # UpdateMemory is asynchronous: creating a strategy returns while the Memory
  # is still UPDATING, and a second create against an UPDATING Memory is
  # rejected. 60s empirically covers the UPDATING->ACTIVE transition for a
  # single strategy; there is no describe-and-wait primitive in the provider.
  memory_strategy_settle_seconds = 60
}

resource "null_resource" "wait_after_user_preference" {
  triggers = {
    strategy_id = aws_bedrockagentcore_memory_strategy.user_preference.memory_strategy_id
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = "sleep ${local.memory_strategy_settle_seconds}"
  }
}

resource "aws_bedrockagentcore_memory_strategy" "summary" {
  name       = "Summary"
  memory_id  = aws_bedrockagentcore_memory.this.id
  type       = "SUMMARIZATION"
  namespaces = ["/users/{actorId}/sessions/{sessionId}"]

  depends_on = [null_resource.wait_after_user_preference]
}

resource "null_resource" "wait_after_summary" {
  triggers = {
    strategy_id = aws_bedrockagentcore_memory_strategy.summary.memory_strategy_id
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = "sleep ${local.memory_strategy_settle_seconds}"
  }
}

resource "aws_bedrockagentcore_memory_strategy" "semantic" {
  name       = "Semantic"
  memory_id  = aws_bedrockagentcore_memory.this.id
  type       = "SEMANTIC"
  namespaces = ["/users/{actorId}/facts"]

  depends_on = [
    null_resource.wait_after_summary,
    aws_bedrockagentcore_memory_strategy.user_preference,
  ]
}

resource "aws_bedrock_guardrail" "this" {
  name                      = "guardrail-for-${var.project_name}"
  blocked_input_messaging   = "Sorry, your request cannot be processed."
  blocked_outputs_messaging = "Sorry, the model response was blocked."

  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
  }
}

resource "aws_kms_key" "ecr_runtime" {
  description             = "CMK for AgentCore runtime ECR encryption (${var.project_name})"
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
    Name = "kms-ecr-runtime-${var.project_name}"
  }
}

resource "aws_kms_alias" "ecr_runtime" {
  name          = "alias/${var.project_name}-ecr-runtime"
  target_key_id = aws_kms_key.ecr_runtime.key_id
}

resource "aws_ecr_repository" "runtime" {
  name                 = local.ecr_repo
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr_runtime.arn
  }
}

locals {
  built_image_uri = "${aws_ecr_repository.runtime.repository_url}:${local.image_tag}"
  container_uri   = var.skip_docker_build ? var.runtime_image_uri : local.built_image_uri
}

resource "null_resource" "docker_build" {
  count = var.skip_docker_build ? 0 : 1

  triggers = {
    dockerfile      = filesha256("${local.runtime_dir}/Dockerfile")
    dockerignore    = filesha256("${local.runtime_dir}/.dockerignore")
    langgraph_agent = filesha256("${local.runtime_dir}/langgraph_agent.py")
    mcp_config      = filesha256("${local.runtime_dir}/mcp_config.py")
    tag             = local.image_tag
    repo            = aws_ecr_repository.runtime.repository_url
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      REGION="${local.region}"
      REPO="${aws_ecr_repository.runtime.repository_url}"
      TAG="${local.image_tag}"
      # Match installer: build context is repo root (Dockerfile COPY paths).
      CONTEXT="${var.repo_root}"
      DOCKERFILE="${local.runtime_dir}/Dockerfile"
      aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${local.account_id}.dkr.ecr.${local.region}.amazonaws.com"
      docker buildx build --platform linux/arm64 --provenance=false --sbom=false \
        -t "$REPO:$TAG" \
        -f "$DOCKERFILE" "$CONTEXT" --push
    EOT
  }

  depends_on = [aws_ecr_repository.runtime]
}


# Session FS policy: Runtime only (ECS uses dedicated app-data FS).
resource "aws_s3files_file_system_policy" "session" {
  file_system_id = var.s3_files_file_system_id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = [aws_iam_role.runtime.arn]
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

resource "aws_bedrockagentcore_agent_runtime" "this" {
  agent_runtime_name = var.agent_runtime_name
  description        = "LangGraph AgentCore Runtime for ${var.project_name}"
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = local.container_uri
    }
  }

  network_configuration {
    network_mode = "VPC"
    network_mode_config {
      subnets         = var.private_subnet_ids
      security_groups = [var.agent_runtime_security_group_id]
    }
  }

  filesystem_configuration {
    s3_files_access_point {
      access_point_arn = var.s3_files_access_point_arn
      mount_path       = var.session_storage_mount_path
    }
  }

  environment_variables = {
    AWS_REGION                         = local.region
    AWS_DEFAULT_REGION                 = local.region
    KNOWLEDGE_BASE_ID                  = var.knowledge_base_id
    PROJECT_NAME                       = var.project_name
    MEMORY_ID                          = aws_bedrockagentcore_memory.this.id
    AGENTCORE_MEMORY_ROLE              = aws_iam_role.memory.arn
    AGENTCORE_WEBSEARCH_GATEWAY_URL    = var.websearch_gateway_url
    agentcore_websearch_gateway_url    = var.websearch_gateway_url
    AGENTCORE_WEBSEARCH_GATEWAY_REGION = var.websearch_gateway_region
    APP_CONFIG_JSON                    = local.runtime_app_config_json
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  depends_on = [
    aws_iam_role_policy.runtime,
    aws_bedrockagentcore_memory_strategy.user_preference,
    aws_bedrockagentcore_memory_strategy.summary,
    aws_bedrockagentcore_memory_strategy.semantic,
    null_resource.docker_build,
  ]
}
