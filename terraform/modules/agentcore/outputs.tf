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

output "agent_runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

output "agent_runtime_id" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_id
}

output "agent_runtime_role_arn" {
  value = aws_iam_role.runtime.arn
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_arn" {
  value = aws_bedrock_guardrail.this.guardrail_arn
}

output "guardrail_name" {
  value = aws_bedrock_guardrail.this.name
}

output "guardrail_version" {
  value = aws_bedrock_guardrail.this.version
}

output "runtime_image_uri" {
  value = local.container_uri
}

output "memory_id" {
  value = aws_bedrockagentcore_memory.this.id
}

output "memory_arn" {
  value = aws_bedrockagentcore_memory.this.arn
}

output "agentcore_memory_role_arn" {
  value = aws_iam_role.memory.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.runtime.repository_url
}
