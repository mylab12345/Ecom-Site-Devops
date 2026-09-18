terraform {
  required_version = ">= 1.7.0"

  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.4.0"
    }
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0.2"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.27"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
  }
}

provider "docker" {
  # Uses DOCKER_HOST env or local socket
}

provider "kind" {}

# Kubernetes provider will be configured after cluster creation via kubeconfig
provider "kubernetes" {
  config_path = kind_cluster.ecom.kubeconfig_path
}

provider "helm" {
  kubernetes {
    config_path = kind_cluster.ecom.kubeconfig_path
  }
}
