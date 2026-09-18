output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "vpc_cidr" {
  description = "VPC CIDR"
  value       = module.vpc.vpc_cidr_block
}

output "private_subnets" {
  description = "Private subnet IDs"
  value       = module.vpc.private_subnets
}

output "public_subnets" {
  description = "Public subnet IDs"
  value       = module.vpc.public_subnets
}

output "azs" {
  description = "Availability zones"
  value       = local.azs
}

output "cluster_name" {
  description = "EKS cluster name"
  value       = var.create_eks ? module.eks[0].cluster_name : var.cluster_name
}

output "cluster_arn" {
  description = "EKS cluster ARN"
  value       = var.create_eks ? module.eks[0].cluster_arn : null
}

output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = var.create_eks ? module.eks[0].cluster_endpoint : null
}

output "cluster_version" {
  description = "EKS cluster Kubernetes version"
  value       = var.create_eks ? module.eks[0].cluster_version : var.cluster_version
}

output "oidc_provider_arn" {
  description = "OIDC provider ARN for IRSA"
  value       = var.create_eks ? module.eks[0].oidc_provider_arn : null
}

output "node_groups" {
  description = "EKS managed node groups"
  value       = var.create_eks ? module.eks[0].eks_managed_node_groups : null
}

output "ebs_csi_irsa_role_arn" {
  description = "IAM role ARN for EBS CSI driver"
  value       = var.enable_ebs_csi && var.create_eks ? module.ebs_csi_irsa_role[0].iam_role_arn : null
}

output "cluster_autoscaler_irsa_role_arn" {
  description = "IAM role ARN for Cluster Autoscaler"
  value       = var.enable_cluster_autoscaler && var.create_eks ? module.cluster_autoscaler_irsa_role[0].iam_role_arn : null
}

output "aws_lbc_irsa_role_arn" {
  description = "IAM role ARN for AWS Load Balancer Controller"
  value       = var.create_eks ? module.aws_lbc_irsa_role[0].iam_role_arn : null
}

output "kubeconfig_command" {
  description = "Command to update kubeconfig"
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${var.cluster_name}"
}

output "configure_kubectl" {
  description = "Configure kubectl"
  value       = var.create_eks ? "aws eks --region ${var.region} update-kubeconfig --name ${module.eks[0].cluster_name}" : null
}

output "cost_optimization" {
  description = "Cost optimization summary"
  value = {
    graviton_saving  = "20% cheaper + 15% better perf per watt vs x86 (m7g/m6g vs m7i/m6i)"
    spot_saving      = var.enable_spot ? "Up to 70% discount for stateless workloads" : "Spot disabled"
    architecture     = "arm64 (Graviton) - multi-arch images linux/amd64,linux/arm64"
    ami_type         = var.ami_type
    instance_types   = var.graviton_instance_types
    capacity_types   = "${var.capacity_type_critical} (critical) + ${var.capacity_type_stateless} (stateless)"
    total_saving_est = "~40% vs x86 on-demand baseline"
  }
}

output "helm_values_overrides" {
  description = "Values to pass to helm-charts for scheduling"
  value = {
    scheduling = {
      architecture = "arm64"
      nodeSelector = {
        "kubernetes.io/arch" = "arm64"
      }
      tolerations_stateless = var.enable_spot ? [
        {
          key      = "spot"
          operator = "Equal"
          value    = "true"
          effect   = "NoSchedule"
        }
      ] : []
    }
    nodeGroups = {
      critical  = "${var.cluster_name}-critical"
      stateless = "${var.cluster_name}-stateless-spot"
    }
  }
}
