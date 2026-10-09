# Terraform creates the parameters with a placeholder and never manages the real
# values, so secrets stay out of Terraform state. Set them once after apply:
#   aws ssm put-parameter --overwrite --type SecureString \
#     --name /sarr-collector/github-token --value "$GITHUB_TOKEN"
# The Lambda refuses to run while a parameter still holds the placeholder.

locals {
  # Must match SECRET_PLACEHOLDER in src/sarr/collect/lambda_handler.py.
  secret_placeholder = "SET_ME"
}

resource "aws_ssm_parameter" "github_token" {
  name        = "/${var.name_prefix}/github-token"
  description = "GitHub token for the health collector (read-only public repo access is enough)."
  type        = "SecureString"
  value       = local.secret_placeholder

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "qdrant_api_key" {
  name        = "/${var.name_prefix}/qdrant-api-key"
  description = "Qdrant Cloud API key with write access to the search collection."
  type        = "SecureString"
  value       = local.secret_placeholder

  lifecycle {
    ignore_changes = [value]
  }
}
