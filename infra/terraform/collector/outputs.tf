output "function_name" {
  value = aws_lambda_function.collector.function_name
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "state_key" {
  value = local.state_key
}

output "github_token_parameter" {
  value = aws_ssm_parameter.github_token.name
}

output "qdrant_api_key_parameter" {
  value = aws_ssm_parameter.qdrant_api_key.name
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "manual_run_command" {
  description = "Run a refresh now (asynchronously, like the schedule)."
  value       = "aws lambda invoke --region ${var.aws_region} --function-name ${aws_lambda_function.collector.function_name} --invocation-type Event --cli-binary-format raw-in-base64-out --payload '{\"command\":\"refresh\"}' /dev/null"
}
