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
    archive = {
      source = "hashicorp/archive"
    }
    null = {
      source = "hashicorp/null"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id       = data.aws_caller_identity.current.account_id
  region           = data.aws_region.current.region
  bucket_name      = "storage-for-${var.project_name}-${local.account_id}-${local.region}"
  collection_name  = substr(var.vector_index_name, 0, 32)
  policy_enc_name  = substr("${substr(var.project_name, 0, 20)}-enc", 0, 32)
  policy_net_name  = substr("${substr(var.project_name, 0, 20)}-net", 0, 32)
  policy_data_name = substr("${substr(var.project_name, 0, 20)}-data", 0, 32)
  lambda_build_dir = "${path.module}/.build/create_vector_index"
}

resource "aws_s3_bucket" "storage" {
  bucket        = local.bucket_name
  force_destroy = true

  tags = { Name = "storage-for-${var.project_name}" }
}

resource "aws_s3_bucket_versioning" "storage" {
  bucket = aws_s3_bucket.storage.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "storage" {
  bucket = aws_s3_bucket.storage.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "storage" {
  bucket                  = aws_s3_bucket.storage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CORS is applied in module.edge after CloudFront domain is known
# (restrict allowed_origins to https://<cloudfront-domain>).

resource "aws_s3_object" "docs_prefix" {
  bucket  = aws_s3_bucket.storage.id
  key     = "docs/"
  content = ""
}

data "aws_iam_policy_document" "kb_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock:${local.region}:${local.account_id}:knowledge-base/*"]
    }
  }
}

resource "aws_iam_role" "kb" {
  name               = "role-knowledge-base-for-${var.project_name}-${local.region}"
  assume_role_policy = data.aws_iam_policy_document.kb_assume.json
}

resource "aws_iam_role_policy" "kb" {
  name = "kb-policy-for-${var.project_name}"
  role = aws_iam_role.kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.storage.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.storage.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = [var.embedding_model_arn]
      }
    ]
  })
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name = local.policy_enc_name
  type = "encryption"
  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/${local.collection_name}"]
    }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = local.policy_net_name
  type = "network"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${local.collection_name}"]
      },
      {
        ResourceType = "dashboard"
        Resource     = ["collection/${local.collection_name}"]
      }
    ]
    AllowFromPublic = true
  }])
}

resource "aws_opensearchserverless_collection" "this" {
  name        = local.collection_name
  type        = "VECTORSEARCH"
  description = "Vector collection for ${var.project_name}"

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
  ]
}

resource "aws_iam_role_policy" "kb_aoss" {
  name = "kb-aoss-for-${var.project_name}"
  role = aws_iam_role.kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "OpenSearchServerlessAccess"
      Effect   = "Allow"
      Action   = ["aoss:APIAccessAll"]
      Resource = [aws_opensearchserverless_collection.this.arn]
    }]
  })
}

# --- Vector index creator Lambda ---
data "aws_iam_policy_document" "index_fn_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "index_fn" {
  name               = "role-create-vector-index-for-${var.project_name}"
  assume_role_policy = data.aws_iam_policy_document.index_fn_assume.json
}

resource "aws_iam_role_policy_attachment" "index_fn_basic" {
  role       = aws_iam_role.index_fn.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "index_fn_xray" {
  role       = aws_iam_role.index_fn.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "index_fn" {
  #checkov:skip=CKV_AWS_355:Justification — AOSS access-policy control-plane APIs (Get/Update/Create/ListAccessPolicy) and sts:GetCallerIdentity only support Resource "*"; data-plane aoss:APIAccessAll is scoped to this collection ARN and iam:GetRole is scoped to roles in-account. See AWS AOSS IAM docs.
  name = "index-fn-policy"
  role = aws_iam_role.index_fn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["aoss:APIAccessAll"]
        Resource = [aws_opensearchserverless_collection.this.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "aoss:GetAccessPolicy",
          "aoss:UpdateAccessPolicy",
          "aoss:CreateAccessPolicy",
          "aoss:ListAccessPolicies",
        ]
        Resource = ["*"]
      },
      {
        Effect   = "Allow"
        Action   = ["sts:GetCallerIdentity"]
        Resource = ["*"]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetRole"]
        Resource = [aws_iam_role.index_fn.arn]
      },
    ]
  })
}

resource "null_resource" "lambda_bundle" {
  triggers = {
    index_py     = filemd5("${var.lambda_source_dir}/index.py")
    requirements = filemd5("${var.lambda_source_dir}/requirements.txt")
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      rm -rf "${local.lambda_build_dir}"
      mkdir -p "${local.lambda_build_dir}"
      python3 -m pip install -r "${var.lambda_source_dir}/requirements.txt" -t "${local.lambda_build_dir}" --quiet
      cp "${var.lambda_source_dir}/index.py" "${local.lambda_build_dir}/"
    EOT
  }
}

data "archive_file" "index_fn" {
  type        = "zip"
  source_dir  = local.lambda_build_dir
  output_path = "${path.module}/.build/create_vector_index.zip"
  depends_on  = [null_resource.lambda_bundle]
}

resource "aws_lambda_function" "create_vector_index" {
  #checkov:skip=CKV_AWS_117:Lambda talks to public AOSS collection endpoint; VPC would require AOSS VPC policy changes
  #checkov:skip=CKV_AWS_116:One-shot custom-resource indexer; failed invokes are surfaced by CloudFormation
  #checkov:skip=CKV_AWS_272:Code signing not used for this deployment Lambda
  function_name = "create-vector-index-for-${var.project_name}"
  role          = aws_iam_role.index_fn.arn
  handler       = "index.handler"
  runtime       = "python3.12"
  architectures = ["arm64"]
  timeout       = 900
  memory_size   = 256

  filename         = data.archive_file.index_fn.output_path
  source_code_hash = data.archive_file.index_fn.output_base64sha256

  reserved_concurrent_executions = 2
  tracing_config {
    mode = "Active"
  }

  depends_on = [
    aws_iam_role_policy.index_fn,
    aws_iam_role_policy_attachment.index_fn_basic,
    aws_iam_role_policy_attachment.index_fn_xray,
  ]
}

resource "aws_opensearchserverless_access_policy" "data" {
  name = local.policy_data_name
  type = "data"
  policy = jsonencode([{
    Description = "KB and index creator"
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${local.collection_name}"]
        Permission = [
          "aoss:CreateCollectionItems",
          "aoss:DeleteCollectionItems",
          "aoss:UpdateCollectionItems",
          "aoss:DescribeCollectionItems",
        ]
      },
      {
        ResourceType = "index"
        Resource     = ["index/${local.collection_name}/*"]
        Permission = [
          "aoss:CreateIndex",
          "aoss:DeleteIndex",
          "aoss:UpdateIndex",
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument",
        ]
      }
    ]
    Principal = [
      aws_iam_role.kb.arn,
      aws_iam_role.index_fn.arn,
    ]
  }])

  depends_on = [aws_opensearchserverless_collection.this]
}

resource "aws_lambda_invocation" "create_vector_index" {
  function_name = aws_lambda_function.create_vector_index.function_name

  input = jsonencode({
    RequestType = "Create"
    ResourceProperties = {
      CollectionEndpoint = aws_opensearchserverless_collection.this.collection_endpoint
      CollectionName     = local.collection_name
      IndexName          = var.vector_index_name
      Region             = local.region
      AccessPolicyName   = local.policy_data_name
      IndexFnRoleArn     = aws_iam_role.index_fn.arn
      KbRoleArn          = aws_iam_role.kb.arn
    }
  })

  triggers = {
    collection = aws_opensearchserverless_collection.this.id
    index_name = var.vector_index_name
  }

  depends_on = [
    aws_opensearchserverless_access_policy.data,
    aws_iam_role_policy.kb_aoss,
  ]

  lifecycle {
    replace_triggered_by = [
      aws_lambda_function.create_vector_index,
    ]
  }
}

resource "aws_bedrockagent_knowledge_base" "this" {
  name     = var.project_name
  role_arn = aws_iam_role.kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = var.embedding_model_arn
      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions = 1024
        }
      }
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.this.arn
      vector_index_name = var.vector_index_name
      field_mapping {
        vector_field   = "vector_field"
        text_field     = "AMAZON_BEDROCK_TEXT"
        metadata_field = "AMAZON_BEDROCK_METADATA"
      }
    }
  }

  depends_on = [
    aws_lambda_invocation.create_vector_index,
    aws_iam_role_policy.kb,
    aws_iam_role_policy.kb_aoss,
  ]
}

resource "aws_bedrockagent_data_source" "docs" {
  name              = "${var.project_name}-s3-docs"
  knowledge_base_id = aws_bedrockagent_knowledge_base.this.id

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.storage.arn
      inclusion_prefixes = ["docs/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 300
        overlap_percentage = 20
      }
    }
  }
}
