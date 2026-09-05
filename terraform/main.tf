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

check "docker_uris_when_skip" {
  assert {
    condition = (
      !var.skip_docker_build ||
      (var.runtime_image_uri != "" && var.web_image_uri != "")
    )
    error_message = "skip_docker_build=true requires runtime_image_uri and web_image_uri."
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

module "gateway" {
  source = "./modules/gateway"

  providers = {
    aws = aws.gateway
  }

  project_name = var.project_name
  gateway_name = local.agentcore_websearch_gateway_name
  target_name  = local.agentcore_websearch_target_name
}

module "network" {
  source = "./modules/network"

  project_name = var.project_name
  vpc_cidr     = var.vpc_cidr
  app_port     = var.app_port
}

module "data" {
  source = "./modules/data"

  project_name        = var.project_name
  vector_index_name   = local.vector_index_name
  embedding_model_arn = local.embedding_model_arn
  lambda_source_dir   = "${path.module}/lambda/create_vector_index"
}

module "auth" {
  source = "./modules/auth"

  project_name                       = var.project_name
  cognito_admin_username             = var.cognito_admin_username
  cognito_admin_password             = var.cognito_admin_password
  alb_origin_header_secret_name      = local.alb_origin_header_secret_name
  session_signing_key_secret_name    = local.session_signing_key_secret_name
  cloudfront_signing_key_secret_name = local.cloudfront_signing_key_secret_name
}

module "storage" {
  source = "./modules/storage"

  project_name                    = var.project_name
  s3_bucket_arn                   = module.data.s3_bucket_arn
  private_subnet_ids              = module.network.private_subnet_ids
  s3files_mount_security_group_id = module.network.s3files_mount_security_group_id
  s3_files_session_prefix         = var.s3_files_session_prefix
  s3_files_app_data_prefix        = var.s3_files_app_data_prefix
  agent_runtime_security_group_id = module.network.agent_runtime_security_group_id
}

module "edge" {
  source = "./modules/edge"

  project_name                    = var.project_name
  vpc_id                          = module.network.vpc_id
  public_subnet_ids               = module.network.public_subnet_ids
  alb_security_group_id           = module.network.alb_security_group_id
  app_port                        = var.app_port
  alb_idle_timeout_seconds        = var.alb_idle_timeout_seconds
  sse_origin_read_timeout_seconds = var.sse_origin_read_timeout_seconds
  custom_header_name              = var.custom_header_name
  origin_header_value             = module.auth.alb_origin_header_value
  cloudfront_key_group_id         = module.auth.cloudfront_key_group_id
  s3_bucket_id                    = module.data.s3_bucket_name
  s3_bucket_arn                   = module.data.s3_bucket_arn
  acm_certificate_arn             = var.acm_certificate_arn
  cloudfront_aliases              = var.cloudfront_aliases
}

module "agentcore" {
  source = "./modules/agentcore"

  project_name                    = var.project_name
  agent_runtime_name              = local.agent_runtime_name
  memory_name                     = local.agentcore_memory_name
  private_subnet_ids              = module.network.private_subnet_ids
  agent_runtime_security_group_id = module.network.agent_runtime_security_group_id
  s3_files_file_system_id         = module.storage.file_system_id
  s3_files_file_system_arn        = module.storage.file_system_arn
  s3_files_access_point_arn       = module.storage.access_point_arn
  s3_bucket_arn                   = module.data.s3_bucket_arn
  s3_bucket_name                  = module.data.s3_bucket_name
  sharing_url                     = module.edge.sharing_url
  session_storage_mount_path      = var.session_storage_mount_path
  knowledge_base_id               = module.data.knowledge_base_id
  knowledge_base_role_arn         = module.data.knowledge_base_role_arn
  data_source_id                  = module.data.data_source_id
  skip_docker_build               = var.skip_docker_build
  runtime_image_uri               = var.runtime_image_uri
  repo_root                       = local.repo_root
  region                          = var.region
  websearch_gateway_url           = module.gateway.gateway_url
  websearch_gateway_region        = var.agentcore_gateway_region
}

locals {
  app_config = {
    projectName                        = var.project_name
    accountId                          = data.aws_caller_identity.current.account_id
    region                             = var.region
    knowledge_base_id                  = module.data.knowledge_base_id
    data_source_id                     = module.data.data_source_id
    knowledge_base_role                = module.data.knowledge_base_role_arn
    collectionArn                      = module.data.collection_arn
    opensearch_url                     = module.data.opensearch_endpoint
    vector_bucket_name                 = ""
    vector_bucket_arn                  = ""
    vector_index_name                  = module.data.vector_index_name
    vector_index_arn                   = ""
    s3_bucket                          = module.data.s3_bucket_name
    s3_arn                             = module.data.s3_bucket_arn
    sharing_url                        = module.edge.sharing_url
    cognito_user_pool_id               = module.auth.cognito_user_pool_id
    cognito_user_pool_name             = module.auth.cognito_user_pool_name
    cognito_client_id                  = module.auth.cognito_client_id
    cognito_client_name                = module.auth.cognito_client_name
    cognito_admin_username             = module.auth.cognito_admin_username
    cognito_region                     = var.region
    agentcore_websearch_gateway_name   = module.gateway.gateway_name
    agentcore_websearch_gateway_region = var.agentcore_gateway_region
    agentcore_websearch_gateway_id     = module.gateway.gateway_id
    agentcore_websearch_gateway_url    = module.gateway.gateway_url
    agentcore_websearch_gateway_role   = module.gateway.gateway_role_arn
    s3_files_file_system_id            = module.storage.file_system_id
    s3_files_access_point_arn          = module.storage.access_point_arn
    s3_files_mount_path                = var.session_storage_mount_path
    s3_files_app_data_file_system_id   = module.storage.app_data_file_system_id
    s3_files_app_data_access_point_arn = module.storage.app_data_access_point_arn
    s3_files_app_data_mount_path       = var.app_data_mount_path
    agent_runtime_vpc_subnets          = module.network.private_subnet_ids
    agent_runtime_security_groups      = [module.network.agent_runtime_security_group_id]
    agent_runtime_arn                  = module.agentcore.agent_runtime_arn
    agent_runtime_role                 = module.agentcore.agent_runtime_role_arn
    memory_id                          = module.agentcore.memory_id
    agentcore_memory_role              = module.agentcore.agentcore_memory_role_arn
    guardrail_id                       = module.agentcore.guardrail_id
    guardrail_arn                      = module.agentcore.guardrail_arn
    guardrail_name                     = module.agentcore.guardrail_name
    guardrail_version                  = module.agentcore.guardrail_version
  }
}

module "compute" {
  source = "./modules/compute"

  project_name                      = var.project_name
  region                            = var.region
  vpc_id                            = module.network.vpc_id
  private_subnet_ids                = module.network.private_subnet_ids
  ecs_security_group_id             = module.network.ecs_security_group_id
  app_port                          = var.app_port
  app_data_mount_path               = var.app_data_mount_path
  target_group_arn                  = module.edge.target_group_arn
  s3_bucket_arn                     = module.data.s3_bucket_arn
  s3_files_file_system_id           = module.storage.app_data_file_system_id
  s3_files_file_system_arn          = module.storage.app_data_file_system_arn
  s3_files_access_point_arn         = module.storage.app_data_access_point_arn
  agent_runtime_role_arn            = module.agentcore.agent_runtime_role_arn
  session_signing_key_secret_arn    = module.auth.session_signing_key_secret_arn
  cloudfront_signing_key_secret_arn = module.auth.cloudfront_signing_key_secret_arn
  secrets_kms_key_arn               = module.auth.secrets_kms_key_arn
  cloudfront_public_key_id          = module.auth.cloudfront_public_key_id
  app_config                        = local.app_config
  skip_docker_build                 = var.skip_docker_build
  web_image_uri                     = var.web_image_uri
  repo_root                         = local.repo_root
}
