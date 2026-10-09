variable "aws_region" {
  description = "Region for all collector resources (same as the search API)."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "sarr-collector"
}

variable "lambda_zip_path" {
  description = "Zip built by scripts/build_collector_lambda.sh."
  type        = string
  default     = "../../../build/collector_lambda.zip"
}

variable "qdrant_url" {
  description = "Qdrant Cloud cluster URL (the API key goes in SSM, not here)."
  type        = string
}

variable "qdrant_collection" {
  description = "Collection whose payloads the collector refreshes."
  type        = string
  default     = "sarr"
}

variable "refresh_schedule" {
  description = "EventBridge schedule for the refresh run (UTC)."
  type        = string
  default     = "cron(0 7 * * ? *)"
}

variable "backfill_schedule" {
  description = "EventBridge schedule for re-seeding the frontier with newly popular packages (UTC)."
  type        = string
  default     = "cron(0 6 ? * SUN *)"
}

variable "schedules_enabled" {
  description = "Set false to keep the infrastructure but pause scheduled runs."
  type        = bool
  default     = true
}

variable "refresh_limit" {
  description = "Upper bound on packages per run; the Lambda time budget usually stops it first."
  type        = number
  default     = 5000
}

variable "backfill_top_n" {
  description = "Frontier size: the N most-starred GitHub-linked packages."
  type        = number
  default     = 10000
}

variable "stale_days" {
  description = "A package is due for a refresh when its last check is older than this."
  type        = number
  default     = 7
}

variable "with_downloads" {
  description = "Fetch 30-day downloads from pypistats (rate-limited to 60/minute; the slowest source)."
  type        = bool
  default     = true
}

variable "lambda_memory_mb" {
  type    = number
  default = 512
}

variable "lambda_timeout_s" {
  description = "Lambda maximum is 900. The handler stops refreshing 90 s before this."
  type        = number
  default     = 900

  validation {
    condition     = var.lambda_timeout_s >= 180 && var.lambda_timeout_s <= 900
    error_message = "lambda_timeout_s must be between 180 and 900."
  }
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alarm_email" {
  description = "Optional email subscribed to the alarm topic (confirm the subscription email)."
  type        = string
  default     = ""
}

variable "failed_packages_alarm_threshold" {
  description = "Alarm when one run fails on at least this many packages."
  type        = number
  default     = 50
}

variable "state_bucket_force_destroy" {
  description = "Allow terraform destroy to delete the state bucket with its frontier history."
  type        = bool
  default     = false
}
