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

output "s3_bucket_name" {
  value = aws_s3_bucket.storage.id
}

output "s3_bucket_arn" {
  value = aws_s3_bucket.storage.arn
}

output "knowledge_base_id" {
  value = aws_bedrockagent_knowledge_base.this.id
}

output "data_source_id" {
  value = aws_bedrockagent_data_source.docs.data_source_id
}

output "knowledge_base_role_arn" {
  value = aws_iam_role.kb.arn
}

output "collection_arn" {
  value = aws_opensearchserverless_collection.this.arn
}

output "opensearch_endpoint" {
  value = aws_opensearchserverless_collection.this.collection_endpoint
}

output "vector_index_name" {
  value = var.vector_index_name
}
