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
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_policy_document" "gateway_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "role-agentcore-gateway-websearch-for-${var.project_name}"
  assume_role_policy = data.aws_iam_policy_document.gateway_assume.json
}

resource "aws_iam_role_policy" "gateway" {
  name = "gateway-policy"
  role = aws_iam_role.gateway.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeGateway"
        Effect = "Allow"
        Action = ["bedrock-agentcore:InvokeGateway"]
        Resource = [
          "arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:gateway/*"
        ]
      },
      {
        Sid    = "InvokeWebSearchTool"
        Effect = "Allow"
        Action = ["bedrock-agentcore:InvokeWebSearch"]
        Resource = [
          "arn:aws:bedrock-agentcore:${data.aws_region.current.region}:aws:tool/web-search.v1"
        ]
      }
    ]
  })
}

resource "aws_bedrockagentcore_gateway" "websearch" {
  name            = var.gateway_name
  role_arn        = aws_iam_role.gateway.arn
  protocol_type   = "MCP"
  authorizer_type = "AWS_IAM"
  description     = "Web search gateway for ${var.project_name}"

  depends_on = [aws_iam_role_policy.gateway]
}

# Web-search connector target is not yet in hashicorp/aws gateway_target schema;
# provision via CloudFormation (same resource type as CDK CfnGatewayTarget).
resource "aws_cloudformation_stack" "websearch_target" {
  name = "${var.project_name}-websearch-gateway-target"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "AgentCore Web Search Gateway Target"
    Resources = {
      WebsearchTarget = {
        Type = "AWS::BedrockAgentCore::GatewayTarget"
        Properties = {
          Name              = var.target_name
          GatewayIdentifier = aws_bedrockagentcore_gateway.websearch.gateway_id
          Description       = "Managed Web Search connector for ${var.project_name}"
          TargetConfiguration = {
            Mcp = {
              Connector = {
                Source = {
                  ConnectorId = "web-search"
                }
                Configurations = [{
                  Name            = "WebSearch"
                  ParameterValues = {}
                }]
              }
            }
          }
          CredentialProviderConfigurations = [{
            CredentialProviderType = "GATEWAY_IAM_ROLE"
          }]
        }
      }
    }
    Outputs = {
      TargetId = {
        Value = { "Fn::GetAtt" = ["WebsearchTarget", "TargetId"] }
      }
    }
  })
}
