terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.27"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Local backend for dev; switch to S3 + DynamoDB for team use:
  # backend \"s3\" {
  #   bucket         = \"ecom-terraform-state\"
  #   key            = \"aws-graviton/terraform.tfstate\"
  #   region         = \"us-east-1\"
  #   dynamodb_table = \"ecom-terraform-locks\"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.common_tags
  }
}

# Kubernetes and Helm providers are configured after EKS creation.
# They use the cluster's endpoint + CA + token via aws_eks_cluster_auth.
# For initial `terraform plan` without a cluster, set var.create_eks = false
# or use -target=module.vpc first.

data "aws_eks_cluster" "this" {
  count = var.create_eks ? 1 : 0
  name  = module.eks[0].cluster_name
}

data "aws_eks_cluster_auth" "this" {
  count = var.create_eks ? 1 : 0
  name  = module.eks[0].cluster_name
}

provider "kubernetes" {
  host                   = var.create_eks ? data.aws_eks_cluster.this[0].endpoint : "https://127.0.0.1"
  cluster_ca_certificate = var.create_eks ? base64decode(data.aws_eks_cluster.this[0].certificate_authority[0].data) : ""
  token                  = var.create_eks ? data.aws_eks_cluster_auth.this[0].token : ""

  # Avoid plan failures when cluster doesn't exist yet
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "echo"
    args        = ["no-cluster-yet"]
  }
}

provider "helm" {
  kubernetes {
    host                   = var.create_eks ? data.aws_eks_cluster.this[0].endpoint : "https://127.0.0.1"
    cluster_ca_certificate = var.create_eks ? base64decode(data.aws_eks_cluster.this[0].certificate_authority[0].data) : ""
    token                  = var.create_eks ? data.aws_eks_cluster_auth.this[0].token : ""
  }
}
