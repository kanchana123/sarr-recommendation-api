data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

locals {
  account_id    = data.aws_caller_identity.current.account_id
  function_name = var.name_prefix
  state_prefix  = "collector/"
  state_key     = "${local.state_prefix}collector.sqlite"
  log_group     = "/aws/lambda/${local.function_name}"
}
