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

# Amazon S3 Files. Empty prefixes share one bucket-root file system.
# See: https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-files.html
output "shares_bucket_root" {
  value = local.share_root
}

output "file_system_id" {
  value = aws_s3files_file_system.this.id
}

output "file_system_arn" {
  value = aws_s3files_file_system.this.arn
}

output "access_point_arn" {
  value = aws_s3files_access_point.this.arn
}

output "app_data_file_system_id" {
  value = local.share_root ? aws_s3files_file_system.this.id : aws_s3files_file_system.app_data[0].id
}

output "app_data_file_system_arn" {
  value = local.share_root ? aws_s3files_file_system.this.arn : aws_s3files_file_system.app_data[0].arn
}

output "app_data_access_point_arn" {
  value = local.share_root ? aws_s3files_access_point.this.arn : aws_s3files_access_point.app_data[0].arn
}

output "agent_runtime_vpc_subnets" {
  value = var.private_subnet_ids
}

output "agent_runtime_security_groups" {
  value = [var.agent_runtime_security_group_id]
}
