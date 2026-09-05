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

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "vpc-for-${var.project_name}"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "igw-for-${var.project_name}" }
}

resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name                  = "public-subnet-for-${var.project_name}-${count.index}"
    "aws-cdk:subnet-type" = "Public"
    "aws-cdk:subnet-name" = "public-subnet-for-${var.project_name}"
  }
}

resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 2)
  availability_zone = local.azs[count.index]

  tags = {
    Name                  = "private-subnet-for-${var.project_name}-${count.index}"
    "aws-cdk:subnet-type" = "Private"
    "aws-cdk:subnet-name" = "private-subnet-for-${var.project_name}"
  }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "nat-eip-for-${var.project_name}" }
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "nat-for-${var.project_name}" }

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "public-rt-for-${var.project_name}" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "private-rt-for-${var.project_name}" }
}

resource "aws_route" "private_nat" {
  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "alb" {
  name        = "alb-sg-for-${var.project_name}"
  description = "ALB security group for ${var.project_name}"
  vpc_id      = aws_vpc.this.id

  # Rules are separate resources to avoid alb↔ecs circular dependencies.
  tags = { Name = "alb-sg-for-${var.project_name}" }
}

resource "aws_security_group" "ecs" {
  #checkov:skip=CKV_AWS_382:ECS Web UI needs NAT egress for Cognito, Bedrock, and third-party APIs not covered by VPC endpoints
  name        = "ecs-sg-for-${var.project_name}"
  description = "ECS Web UI security group for ${var.project_name}"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "ecs-sg-for-${var.project_name}" }
}

resource "aws_security_group_rule" "alb_ingress_cloudfront" {
  type              = "ingress"
  description       = "CloudFront to ALB HTTP"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  security_group_id = aws_security_group.alb.id
  prefix_list_ids   = [data.aws_ec2_managed_prefix_list.cloudfront.id]
}

resource "aws_security_group_rule" "alb_egress_to_ecs" {
  type                     = "egress"
  description              = "ALB to ECS targets"
  from_port                = var.app_port
  to_port                  = var.app_port
  protocol                 = "tcp"
  security_group_id        = aws_security_group.alb.id
  source_security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "ecs_ingress_from_alb" {
  type                     = "ingress"
  description              = "ALB to ECS"
  from_port                = var.app_port
  to_port                  = var.app_port
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs.id
  source_security_group_id = aws_security_group.alb.id
}

resource "aws_security_group_rule" "ecs_egress_all" {
  type              = "egress"
  description       = "NAT egress for AWS APIs and agent dependencies"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.ecs.id
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group" "agent_runtime" {
  #checkov:skip=CKV_AWS_382:AgentCore Runtime needs NAT egress for Bedrock, web search, and MCP/tool HTTPS calls
  name        = "agent-runtime-sg-for-${var.project_name}"
  description = "AgentCore Runtime security group for ${var.project_name}"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "NAT egress for Bedrock, tools, and package fetches"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "agent-runtime-sg-for-${var.project_name}" }
}

resource "aws_security_group" "s3files_mount" {
  #checkov:skip=CKV_AWS_382:S3 Files mount targets require broad egress for service-managed control plane
  name        = "s3files-mount-sg-for-${var.project_name}"
  description = "S3 Files mount target SG for ${var.project_name}"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "NFS from ECS"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  ingress {
    description     = "NFS from Agent Runtime"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.agent_runtime.id]
  }

  egress {
    description = "Service-managed mount target egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "s3files-mount-sg-for-${var.project_name}" }
}

resource "aws_security_group" "vpce" {
  #checkov:skip=CKV_AWS_382:Interface VPC endpoints use AWS-managed ENIs; default egress retained for endpoint health
  name        = "vpce-sg-for-${var.project_name}"
  description = "VPC interface endpoints for ${var.project_name}"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "HTTPS from ECS"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  ingress {
    description     = "HTTPS from Agent Runtime"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.agent_runtime.id]
  }

  egress {
    description = "VPC endpoint ENI egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "vpce-sg-for-${var.project_name}" }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id, aws_route_table.public.id]

  tags = { Name = "vpce-s3-for-${var.project_name}" }
}

data "aws_region" "current" {}

locals {
  # Interface VPC endpoints for private AWS API access.
  # Terraform expands each short name to com.amazonaws.${region}.${service}.
  # bedrock-runtime — InvokeModel/Converse PrivateLink:
  #   https://docs.aws.amazon.com/bedrock/latest/userguide/vpc-interface-endpoints.html
  # bedrock-agentcore / bedrock-agentcore-control — AgentCore data/control planes:
  #   https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-interface-endpoints.html
  interface_services = [
    "ecr.api",
    "ecr.dkr",
    "logs",
    "secretsmanager",
    "bedrock-runtime",
    "bedrock-agentcore",
    "bedrock-agentcore-control",
  ]
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(local.interface_services)

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpce.id]

  tags = { Name = "vpce-${each.value}-for-${var.project_name}" }
}
