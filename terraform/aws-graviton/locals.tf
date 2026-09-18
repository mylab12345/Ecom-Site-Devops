locals {
  name = "${var.project}-${var.environment}-${var.cluster_name}"

  common_tags = merge(var.tags, {
    Project     = var.project
    Environment = var.environment
    Cluster     = var.cluster_name
    ManagedBy   = "terraform"
    CostOpt     = "graviton-spot-${var.enable_spot}"
  })

  # Derive AZs from region if not provided
  # Using data source in vpc.tf
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Auto-calculate subnets if not provided
  # Public: 10.0.0.0/20, 10.0.16.0/20, 10.0.32.0/20 ...
  # Private: 10.0.64.0/18 etc - simplified via cidrsubnet
  public_subnets = length(var.public_subnet_cidrs) > 0 ? var.public_subnet_cidrs : [
    for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, i)
  ]

  private_subnets = length(var.private_subnet_cidrs) > 0 ? var.private_subnet_cidrs : [
    for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, i + var.az_count + 4)
  ]
}
