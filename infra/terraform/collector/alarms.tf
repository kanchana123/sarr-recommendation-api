resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# A run crashed: lock held, secrets missing, Qdrant or S3 unreachable, timeout.
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.name_prefix}-errors"
  alarm_description   = "Health collector Lambda run failed. Check ${local.log_group}."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.collector.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# The schedule didn't fire (rule disabled, permission removed, account issue).
resource "aws_cloudwatch_metric_alarm" "missed_run" {
  count = var.schedules_enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-missed-run"
  alarm_description   = "Health collector has not been invoked in 24 hours."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.collector.function_name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# The run finished but many packages failed (e.g. a bad GitHub token returns 401
# for every repo). Published by the handler as an Embedded Metric Format log line.
resource "aws_cloudwatch_metric_alarm" "package_failures" {
  alarm_name          = "${var.name_prefix}-package-failures"
  alarm_description   = "One collector run failed on ${var.failed_packages_alarm_threshold}+ packages."
  namespace           = "SARR/Collector"
  metric_name         = "failed"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.failed_packages_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}
