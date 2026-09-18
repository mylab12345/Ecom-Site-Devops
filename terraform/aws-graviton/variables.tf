variable "region" {
  description = "AWS region for EKS Graviton deployment"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name used for resource naming and cost allocation"
  type        = string
  default     = "ecom"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "ecom-eks-graviton"
}

variable "cluster_version" {
  description = "Kubernetes version for EKS (1.29+ required for Graviton best support)"
  type        = string
  default     = "1.29"
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to use (2 or 3 recommended)"
  type        = number
  default     = 3
  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "az_count must be between 2 and 4."
  }
}

variable "public_subnet_cidrs" {
  description = "Optional override for public subnet CIDRs (auto-calculated if empty)"
  type        = list(string)
  default     = []
}

variable "private_subnet_cidrs" {
  description = "Optional override for private subnet CIDRs (auto-calculated if empty)"
  type        = list(string)
  default     = []
}

variable "enable_nat_gateway" {
  description = "Enable NAT gateways for private subnets"
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "Use single NAT gateway for cost savings (dev) vs one per AZ (prod HA)"
  type        = bool
  default     = false
}

variable "enable_spot" {
  description = "Enable spot instances for stateless workloads (70% cost savings)"
  type        = bool
  default     = true
}

variable "graviton_instance_types" {
  description = "Graviton (ARM64) instance types for spot and on-demand node groups"
  type        = list(string)
  default     = ["m7g.medium", "m6g.medium", "m7g.large", "m6g.large"]
}

variable "critical_instance_types" {
  description = "Instance types for critical workloads (on-demand, Graviton)"
  type        = list(string)
  default     = ["m7g.large", "m6g.large", "m7g.xlarge"]
}

variable "enable_graviton_only" {
  description = "If true, only Graviton nodes are scheduled (arm64). Set false to allow mixed arch during migration."
  type        = bool
  default     = true
}

variable "critical_desired_size" {
  description = "Desired size for critical node group (identity, order, payment, shipping, gateway)"
  type        = number
  default     = 2
}

variable "critical_min_size" {
  type    = number
  default = 1
}

variable "critical_max_size" {
  type    = number
  default = 6
}

variable "stateless_desired_size" {
  description = "Desired size for stateless spot node group (product, inventory, cart, review, notification)"
  type        = number
  default     = 3
}

variable "stateless_min_size" {
  type    = number
  default = 1
}

variable "stateless_max_size" {
  type    = number
  default = 10
}

variable "capacity_type_critical" {
  description = "Capacity type for critical group"
  type        = string
  default     = "ON_DEMAND"
}

variable "capacity_type_stateless" {
  description = "Capacity type for stateless group (SPOT for cost savings)"
  type        = string
  default     = "SPOT"
}

variable "spot_max_price" {
  description = "Max spot price (empty means on-demand price)"
  type        = string
  default     = ""
}

variable "ami_type" {
  description = "EKS AMI type for Graviton"
  type        = string
  default     = "AL2_ARM_64"
}

variable "disk_size" {
  description = "Node disk size in GB"
  type        = number
  default     = 50
}

variable "create_eks" {
  description = "Create EKS cluster (set false to only create VPC for testing)"
  type        = bool
  default     = true
}

variable "enable_irsa" {
  description = "Enable IAM Roles for Service Accounts"
  type        = bool
  default     = true
}

variable "enable_cluster_autoscaler" {
  description = "Enable Cluster Autoscaler IAM role via IRSA"
  type        = bool
  default     = true
}

variable "enable_ebs_csi" {
  description = "Enable EBS CSI driver addon + IRSA"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional cost allocation tags"
  type        = map(string)
  default = {
    "cost-center" = "ecom-platform"
    "team"        = "platform"
    "iac"         = "terraform"
    "phase"       = "3"
    "arch"        = "arm64"
  }
}

variable "cluster_addons" {
  description = "Map of EKS addon versions (null means latest)"
  type        = map(string)
  default = {
    coredns    = null
    kube-proxy = null
    vpc-cni    = null
    ebs-csi    = null
  }
}
