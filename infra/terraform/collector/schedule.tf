locals {
  schedules = {
    refresh = {
      expression  = var.refresh_schedule
      description = "Nightly refresh: stale + popular packages first, within the Lambda time budget."
      input       = { command = "refresh" }
    }
    backfill = {
      expression  = var.backfill_schedule
      description = "Weekly re-seed: add newly popular GitHub-linked packages to the frontier."
      input       = { command = "backfill" }
    }
  }
}

resource "aws_cloudwatch_event_rule" "collector" {
  for_each = local.schedules

  name                = "${var.name_prefix}-${each.key}"
  description         = each.value.description
  schedule_expression = each.value.expression
  state               = var.schedules_enabled ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_target" "collector" {
  for_each = local.schedules

  rule  = aws_cloudwatch_event_rule.collector[each.key].name
  arn   = aws_lambda_function.collector.arn
  input = jsonencode(each.value.input)
}

resource "aws_lambda_permission" "events" {
  for_each = local.schedules

  statement_id  = "AllowEventBridge-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.collector[each.key].arn
}
