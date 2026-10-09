terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State is local by default. For shared use, configure an S3 backend, e.g.
  #   terraform init -backend-config=backend.hcl
  # with bucket / key / region / use_lockfile = true in backend.hcl.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "sarr"
      Component = "health-collector"
      ManagedBy = "terraform"
    }
  }
}
