# VPC Module - Production grade with cost tags and ELB tags for AWS Load Balancer Controller
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr

  azs             = local.azs
  public_subnets  = local.public_subnets
  private_subnets = local.private_subnets

  enable_nat_gateway   = var.enable_nat_gateway
  single_nat_gateway   = var.single_nat_gateway
  enable_dns_hostnames = true
  enable_dns_support   = true

  # Tags required for EKS + AWS Load Balancer Controller discovery
  public_subnet_tags = {
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
    "kubernetes.io/role/elb"                    = "1"
    "Type"                                      = "public"
    "Tier"                                      = "public"
  }

  private_subnet_tags = {
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
    "kubernetes.io/role/internal-elb"           = "1"
    "Type"                                      = "private"
    "Tier"                                      = "private"
    "karpenter.sh/discovery"                    = var.cluster_name
  }

  tags = merge(local.common_tags, {
    Name = "${var.cluster_name}-vpc"
  })

  vpc_tags = {
    Name = "${var.cluster_name}-vpc"
  }
}

# Additional security group for EKS nodes (optional hardening)
resource "aws_security_group" "eks_nodes_extra" {
  name_prefix = "${var.cluster_name}-nodes-extra-"
  description = "Extra SG for EKS nodes - allows internal ecom traffic"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "Node to node"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.cluster_name}-nodes-extra-sg"
  })
}
