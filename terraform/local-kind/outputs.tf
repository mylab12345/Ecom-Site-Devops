output "cluster_name" {
  description = "Kind cluster name"
  value       = kind_cluster.ecom.name
}

output "kubeconfig_path" {
  description = "Kubeconfig path"
  value       = kind_cluster.ecom.kubeconfig_path
}

output "kubeconfig" {
  description = "Kubeconfig content (sensitive)"
  value       = kind_cluster.ecom.kubeconfig
  sensitive   = true
}

output "endpoint" {
  description = "Cluster endpoint"
  value       = kind_cluster.ecom.endpoint
}

output "registry_endpoint" {
  description = "Local registry endpoint"
  value       = var.enable_registry ? "localhost:${var.registry_port}" : null
}

output "registry_container" {
  description = "Registry container name"
  value       = var.enable_registry ? var.registry_name : null
}

output "kubectl_command" {
  description = "Command to set kubeconfig"
  value       = "export KUBECONFIG=${kind_cluster.ecom.kubeconfig_path} && kubectl cluster-info"
}

output "nodes" {
  description = "Cluster nodes (1 control-plane + workers)"
  value       = "1 control-plane + ${var.workers} workers (Kind)"
}

output "port_mappings" {
  description = "Host to container port mappings"
  value = {
    http    = "${var.http_port} -> 80 (ingress)"
    https   = "${var.https_port} -> 443 (ingress)"
    gateway = "${var.gateway_port} -> 30000 (NodePort)"
  }
}

output "next_steps" {
  description = "Next steps after cluster creation"
  value       = <<-EOT
    1. Export kubeconfig: export KUBECONFIG=${kind_cluster.ecom.kubeconfig_path}
    2. Verify nodes: kubectl get nodes -o wide
    3. Build images: make ci-build REGISTRY=localhost:${var.registry_port}
    4. Push to local registry: make ci-push REGISTRY=localhost:${var.registry_port}
    5. Deploy helm charts: helm upgrade --install ecom ./helm-charts/<service> -n ecom
    6. Or use ArgoCD (Phase 5) once installed
  EOT
}
