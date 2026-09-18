variable "cluster_name" {
  description = "Kind cluster name"
  type        = string
  default     = "ecom-local"
}

variable "k8s_version" {
  description = "Kubernetes version for Kind (node image tag)"
  type        = string
  default     = "v1.29.2"
}

variable "kind_image" {
  description = "Kind node image (must match k8s_version)"
  type        = string
  default     = "kindest/node:v1.29.2"
}

variable "http_port" {
  description = "Host port for ingress HTTP (80 -> 8080 inside)"
  type        = number
  default     = 80
}

variable "https_port" {
  description = "Host port for ingress HTTPS"
  type        = number
  default     = 443
}

variable "gateway_port" {
  description = "Host port for ecom gateway (8080)"
  type        = number
  default     = 8080
}

variable "registry_port" {
  description = "Host port for local registry"
  type        = number
  default     = 5001
}

variable "registry_name" {
  description = "Local registry container name"
  type        = string
  default     = "ecom-registry"
}

variable "enable_registry" {
  description = "Create local Docker registry for Kind (kind-registry mirror)"
  type        = bool
  default     = true
}

variable "workers" {
  description = "Number of worker nodes"
  type        = number
  default     = 2
}

variable "extra_port_mappings" {
  description = "Additional port mappings for Kind nodes"
  type = list(object({
    container_port = number
    host_port      = number
    protocol       = optional(string, "TCP")
  }))
  default = [
    { container_port = 30080, host_port = 30080 },
    { container_port = 30443, host_port = 30443 }
  ]
}

variable "install_ingress_nginx" {
  description = "Install ingress-nginx via Helm after cluster creation"
  type        = bool
  default     = false
}

variable "ingress_nginx_version" {
  description = "ingress-nginx chart version"
  type        = string
  default     = "4.10.0"
}
