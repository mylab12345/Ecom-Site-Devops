# EKS Module - Graviton (ARM64) with Spot cost optimization
# Docs: https://registry.terraform.io/modules/terraform-aws-modules/eks/aws/latest
module "eks" {
  count   = var.create_eks ? 1 : 0
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.17"

  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Cluster endpoint access: public for dev, private for prod hardening later
  cluster_endpoint_public_access       = true
  cluster_endpoint_private_access      = true
  cluster_endpoint_public_access_cidrs = var.environment == "prod" ? ["0.0.0.0/0"] : ["0.0.0.0/0"] # tighten in prod via variable

  enable_irsa = var.enable_irsa

  # Cluster addons - pinned to Graviton compatible versions
  cluster_addons = {
    coredns = {
      most_recent = var.cluster_addons["coredns"] == null ? true : false
      addon_version = var.cluster_addons["coredns"]
      configuration_values = jsonencode({
        computeType = "ec2"
        resources = {
          limits = { cpu = "100m", memory = "150Mi" }
          requests = { cpu = "100m", memory = "150Mi" }
        }
      })
    }
    kube-proxy = {
      most_recent   = var.cluster_addons["kube-proxy"] == null ? true : false
      addon_version = var.cluster_addons["kube-proxy"]
    }
    vpc-cni = {
      most_recent   = var.cluster_addons["vpc-cni"] == null ? true : false
      addon_version = var.cluster_addons["vpc-cni"]
      configuration_values = jsonencode({
        env = {
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
    aws-ebs-csi-driver = {
      most_recent              = var.cluster_addons["ebs-csi"] == null ? true : false
      addon_version            = var.cluster_addons["ebs-csi"]
      service_account_role_arn = var.enable_ebs_csi ? module.ebs_csi_irsa_role[0].iam_role_arn : null
      configuration_values = jsonencode({
        controller = {
          resources = {
            requests = { cpu = "50m", memory = "128Mi" }
            limits   = { cpu = "200m", memory = "256Mi" }
          }
        }
      })
    }
  }

  # Node security group additional rules
  node_security_group_additional_rules = {
    ingress_self_all = {
      description = "Node to node all ports"
      protocol    = "-1"
      from_port   = 0
      to_port     = 0
      type        = "ingress"
      self        = true
    }
    egress_all = {
      description = "Node all egress"
      protocol    = "-1"
      from_port   = 0
      to_port     = 0
      type        = "egress"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  # EKS Managed Node Groups - Graviton optimized
  eks_managed_node_groups = {
    # Critical workloads: identity, order, payment, shipping, gateway
    # ON_DEMAND, Graviton, 2 replicas min, no spot eviction
    critical = {
      name           = "${var.cluster_name}-critical"
      description    = "Critical services (identity, order, payment, shipping, gateway) - ON_DEMAND Graviton"
      ami_type       = var.ami_type
      instance_types = var.critical_instance_types
      capacity_type  = var.capacity_type_critical

      min_size       = var.critical_min_size
      max_size       = var.critical_max_size
      desired_size   = var.critical_desired_size
      disk_size      = var.disk_size

      labels = {
        workload   = "critical"
        arch       = "arm64"
        lifecycle  = "on-demand"
        managed_by = "terraform"
        tier       = "critical"
      }

      taints = [] # critical can run anywhere, no taint

      tags = merge(local.common_tags, {
        Name         = "${var.cluster_name}-critical-ng"
        NodeGroup    = "critical"
        CapacityType = var.capacity_type_critical
        Arch         = "arm64"
      })
    }

    # Stateless workloads: product, inventory, cart, review, notification
    # SPOT, Graviton, mixed instance types, cost optimized ~70% saving
    stateless_spot = {
      name           = "${var.cluster_name}-stateless-spot"
      description    = "Stateless services (product, inventory, cart, review, notification) - SPOT Graviton mixed"
      ami_type       = var.ami_type
      instance_types = var.graviton_instance_types
      capacity_type  = var.enable_spot ? var.capacity_type_stateless : "ON_DEMAND"

      min_size       = var.stateless_min_size
      max_size       = var.stateless_max_size
      desired_size   = var.stateless_desired_size
      disk_size      = var.disk_size

      # Mixed instances policy is handled via instance_types list in managed NG
      # For true mixedInstancesPolicy with weights, use self-managed or Karpenter.
      # Here we rely on EKS managed NG spot diversification.

      labels = {
        workload   = "stateless"
        arch       = "arm64"
        lifecycle  = var.enable_spot ? "spot" : "on-demand"
        managed_by = "terraform"
        tier       = "stateless"
        spot       = var.enable_spot ? "true" : "false"
      }

      taints = var.enable_spot ? [
        {
          key    = "spot"
          value  = "true"
          effect = "NO_SCHEDULE"
        }
      ] : []

      tags = merge(local.common_tags, {
        Name         = "${var.cluster_name}-stateless-spot-ng"
        NodeGroup    = "stateless-spot"
        CapacityType = var.capacity_type_stateless
        Arch         = "arm64"
        CostSaving   = "70%"
      })
    }
  }

  # Cluster access - for dev we allow admin via current caller
  enable_cluster_creator_admin_permissions = true

  tags = local.common_tags
}

# EBS CSI Driver IRSA Role
module "ebs_csi_irsa_role" {
  count   = var.create_eks && var.enable_ebs_csi ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.39"

  role_name = "${var.cluster_name}-ebs-csi-driver"

  attach_ebs_csi_policy = true

  oidc_providers = {
    ex = {
      provider_arn               = module.eks[0].oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }

  tags = local.common_tags
}

# Cluster Autoscaler IRSA Role (optional, for HPA + CA scaling)
module "cluster_autoscaler_irsa_role" {
  count   = var.create_eks && var.enable_cluster_autoscaler ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.39"

  role_name                        = "${var.cluster_name}-cluster-autoscaler"
  attach_cluster_autoscaler_policy = true
  cluster_autoscaler_cluster_names = [module.eks[0].cluster_name]

  oidc_providers = {
    ex = {
      provider_arn               = module.eks[0].oidc_provider_arn
      namespace_service_accounts = ["kube-system:cluster-autoscaler"]
    }
  }

  tags = local.common_tags
}

# Karpenter IRSA (optional, for future Phase 3+ cost optimization)
# Uncomment if you want Karpenter instead of managed node groups for spot diversification
# module "karpenter_irsa" {
#   source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
#   version = "~> 5.39"
#   role_name                          = "${var.cluster_name}-karpenter"
#   attach_karpenter_controller_policy = true
#   karpenter_controller_cluster_name  = module.eks[0].cluster_name
#   karpenter_controller_node_iam_role_arn = module.eks[0].eks_managed_node_groups["critical"].iam_role_arn
#   oidc_providers = {
#     ex = {
#       provider_arn               = module.eks[0].oidc_provider_arn
#       namespace_service_accounts = ["karpenter:karpenter"]
#     }
#   }
# }

# Additional: AWS Load Balancer Controller IRSA Role (for ingress)
module "aws_lbc_irsa_role" {
  count   = var.create_eks ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.39"

  role_name                              = "${var.cluster_name}-aws-lbc"
  attach_load_balancer_controller_policy = true

  oidc_providers = {
    ex = {
      provider_arn               = module.eks[0].oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }

  tags = local.common_tags
}
