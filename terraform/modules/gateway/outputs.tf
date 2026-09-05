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

output "gateway_id" {
  value = aws_bedrockagentcore_gateway.websearch.gateway_id
}

output "gateway_name" {
  value = var.gateway_name
}

output "gateway_url" {
  value = aws_bedrockagentcore_gateway.websearch.gateway_url
}

output "gateway_role_arn" {
  value = aws_iam_role.gateway.arn
}

output "gateway_arn" {
  value = aws_bedrockagentcore_gateway.websearch.gateway_arn
}
