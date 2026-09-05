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

data "aws_s3_bucket" "storage" {
  bucket = var.s3_bucket_id
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_canonical_user_id" "current" {}
data "aws_cloudfront_log_delivery_canonical_user_id" "this" {}

resource "aws_s3_bucket" "cloudfront_logs" {
  bucket        = "cf-logs-${var.project_name}-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}"
  force_destroy = true

  tags = { Name = "cf-logs-for-${var.project_name}" }
}

resource "aws_s3_bucket_ownership_controls" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "cloudfront_logs" {
  depends_on = [aws_s3_bucket_ownership_controls.cloudfront_logs]
  bucket     = aws_s3_bucket.cloudfront_logs.id

  access_control_policy {
    owner {
      id = data.aws_canonical_user_id.current.id
    }

    grant {
      grantee {
        id   = data.aws_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }

    grant {
      grantee {
        id   = data.aws_cloudfront_log_delivery_canonical_user_id.this.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "cloudfront_logs" {
  bucket                  = aws_s3_bucket.cloudfront_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id

  rule {
    id     = "expire-logs"
    status = "Enabled"

    filter {
      prefix = "cloudfront/"
    }

    expiration {
      days = 90
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_lb" "alb" {
  #checkov:skip=CKV2_AWS_20:HTTP→HTTPS redirect is enforced at CloudFront; ALB is CloudFront-only HTTP origin
  #checkov:skip=CKV_AWS_91:ALB access logs omitted for this prototype; CloudFront standard logs cover viewer traffic
  name                       = "alb-for-${var.project_name}"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [var.alb_security_group_id]
  subnets                    = var.public_subnet_ids
  idle_timeout               = var.alb_idle_timeout_seconds
  # false so `terraform destroy` can remove the ALB without a manual attribute change
  enable_deletion_protection = false
  drop_invalid_header_fields = true

  tags = { Name = "alb-for-${var.project_name}" }
}

resource "aws_lb_target_group" "ecs" {
  #checkov:skip=CKV_AWS_378:Target group is HTTP because CloudFront origin uses http-only to this ALB
  name        = substr("tg-ecs-for-${var.project_name}", 0, 32)
  port        = var.app_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/api/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  # app_cookie on agent_user_id — avoids AWSALB/AWSALBCORS (no configurable Secure/HttpOnly)
  stickiness {
    type            = "app_cookie"
    cookie_name     = "agent_user_id"
    cookie_duration = 86400
    enabled         = true
  }

  tags = { Name = "tg-ecs-for-${var.project_name}" }
}

resource "aws_lb_listener" "http" {
  #checkov:skip=CKV_AWS_2:Viewer TLS terminates at CloudFront (redirect-to-https). ALB is an origin-only HTTP listener restricted to the CloudFront managed prefix list plus a shared secret origin header; end-users never hit the ALB directly. HTTPS on the ALB would require a public ACM cert and break the current http-only custom_origin_config.
  load_balancer_arn = aws_lb.alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener_rule" "origin_header" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ecs.arn
  }

  condition {
    http_header {
      http_header_name = var.custom_header_name
      values           = [var.origin_header_value]
    }
  }
}

resource "aws_cloudfront_origin_access_identity" "s3" {
  comment = "OAI for ${var.project_name}"
}

data "aws_iam_policy_document" "s3_oai" {
  statement {
    sid       = "AllowCloudFrontOAI"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.s3_bucket_arn}/*"]

    principals {
      type        = "AWS"
      identifiers = [aws_cloudfront_origin_access_identity.s3.iam_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "oai" {
  bucket = var.s3_bucket_id
  policy = data.aws_iam_policy_document.s3_oai.json
}

resource "aws_s3_bucket_cors_configuration" "storage" {
  bucket = var.s3_bucket_id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "POST", "PUT"]
    allowed_origins = ["https://${aws_cloudfront_distribution.this.domain_name}"]
  }
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name    = "${var.project_name}-security-headers"
  comment = "Security headers for ${var.project_name}; strip origin Server"

  security_headers_config {
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
    xss_protection {
      protection = true
      mode_block = true
      override   = true
    }
  }

  remove_headers_config {
    items {
      header = "Server"
    }
    items {
      header = "X-Powered-By"
    }
  }
}

resource "aws_cloudfront_distribution" "this" {
  #checkov:skip=CKV_AWS_174:AWS managed *.cloudfront.net default certificates cannot set minimum_protocol_version. When var.acm_certificate_arn is set, viewer_certificate.minimum_protocol_version is TLSv1.2_2021 (see block below). Viewer traffic is always redirect-to-https.
  enabled             = true
  comment             = "CloudFront-for-${var.project_name}"
  price_class         = "PriceClass_200"
  default_root_object = "index.html"
  is_ipv6_enabled     = true
  aliases             = var.cloudfront_aliases

  logging_config {
    include_cookies = false
    bucket          = aws_s3_bucket.cloudfront_logs.bucket_domain_name
    prefix          = "cloudfront/"
  }

  origin {
    domain_name = aws_lb.alb.dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = var.sse_origin_read_timeout_seconds
      origin_keepalive_timeout = 60
    }

    custom_header {
      name  = var.custom_header_name
      value = var.origin_header_value
    }
  }

  origin {
    domain_name = data.aws_s3_bucket.storage.bucket_regional_domain_name
    origin_id   = "s3"

    s3_origin_config {
      origin_access_identity = aws_cloudfront_origin_access_identity.s3.cloudfront_access_identity_path
    }
  }

  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id   = "216adef6-5c7f-47e4-b989-5492eafa07d3" # AllViewer
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  dynamic "ordered_cache_behavior" {
    for_each = toset(["/images/*", "/docs/*", "/artifacts/*"])
    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = "s3"
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD"]
      cached_methods           = ["GET", "HEAD"]
      compress                 = true
      cache_policy_id          = "658327ea-f89d-4fab-a63d-7e88639e58f6" # CachingOptimized
      response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
      trusted_key_groups       = [var.cloudfront_key_group_id]
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # ACM (us-east-1) enables TLSv1.2_2021; default cert cannot set MinimumProtocolVersion.
  viewer_certificate {
    cloudfront_default_certificate = var.acm_certificate_arn == ""
    acm_certificate_arn            = var.acm_certificate_arn != "" ? var.acm_certificate_arn : null
    ssl_support_method             = var.acm_certificate_arn != "" ? "sni-only" : null
    minimum_protocol_version       = var.acm_certificate_arn != "" ? "TLSv1.2_2021" : null
  }

  depends_on = [
    aws_s3_bucket_policy.oai,
    aws_s3_bucket_acl.cloudfront_logs,
  ]
}
