resource "aws_cloudwatch_log_group" "collector" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "collector" {
  function_name    = local.function_name
  description      = "SARR package health collector: refreshes GitHub/PyPI signals into Qdrant."
  role             = aws_iam_role.collector.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "sarr.collect.lambda_handler.handler"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_s

  environment {
    variables = {
      QDRANT_URL             = var.qdrant_url
      QDRANT_COLLECTION      = var.qdrant_collection
      QDRANT_API_KEY_PARAM   = aws_ssm_parameter.qdrant_api_key.name
      GITHUB_TOKEN_PARAM     = aws_ssm_parameter.github_token.name
      COLLECTOR_STATE_BUCKET = aws_s3_bucket.state.bucket
      COLLECTOR_STATE_KEY    = local.state_key
      COLLECTOR_STALE_DAYS   = tostring(var.stale_days)
      REFRESH_LIMIT          = tostring(var.refresh_limit)
      BACKFILL_TOP_N         = tostring(var.backfill_top_n)
      WITH_DOWNLOADS         = tostring(var.with_downloads)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.collector.name
  }

  depends_on = [aws_iam_role_policy.collector]
}

# Scheduled runs are async. A retry would race the S3 lock, and the next
# scheduled run picks up whatever is still due, so don't retry.
resource "aws_lambda_function_event_invoke_config" "collector" {
  function_name                = aws_lambda_function.collector.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 3600
}
