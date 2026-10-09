data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "collector" {
  name               = "${var.name_prefix}-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

# Least privilege: its own log group, its own state prefix, its two parameters.
data "aws_iam_policy_document" "collector" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.collector.arn}:*"]
  }

  statement {
    sid = "ReadWriteFrontierState"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.state.arn}/${local.state_prefix}*"]
  }

  # Without ListBucket, S3 answers a missing frontier with 403 instead of 404,
  # and the first run couldn't tell "no state yet" from "access denied".
  statement {
    sid       = "DistinguishMissingState"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid     = "ReadSecrets"
    actions = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.github_token.arn,
      aws_ssm_parameter.qdrant_api_key.arn,
    ]
  }

  statement {
    sid       = "DecryptSecretsViaSsm"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "collector" {
  name   = "${var.name_prefix}-least-privilege"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector.json
}
