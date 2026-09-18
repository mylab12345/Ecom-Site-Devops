# Main entrypoint - kept minimal, logic lives in vpc.tf and eks.tf
# This file exists for terraform fmt consistency and future root resources.

# Data source for current account
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Example: Create ecom namespace in EKS (optional, ArgoCD will manage later)
# resource "kubernetes_namespace" "ecom" {
#   count = var.create_eks ? 1 : 0
#   metadata {
#     name = "ecom"
#     labels = {
#       name        = "ecom"
#       managed-by  = "terraform"
#       environment = var.environment
#     }
#   }
#   depends_on = [module.eks]
# }

# Example: ECR repositories for 10 services (optional, if using ECR instead of DockerHub)
# Uncomment to create ECR repos for each microservice
# locals {
#   services = ["identity", "product", "inventory", "cart", "order", "payment", "shipping", "notification", "review", "gateway"]
# }
# resource "aws_ecr_repository" "ecom" {
#   for_each = toset(local.services)
#   name                 = "ecom-${each.value}"
#   image_tag_mutability = "MUTABLE"
#   image_scanning_configuration {
#     scan_on_push = true
#   }
#   tags = local.common_tags
# }
