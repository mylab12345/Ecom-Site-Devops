# Local Kind cluster with registry mirror - Phase 3 local dev
# 1 control-plane + 2 workers, extraPortMappings for ingress, containerd registry mirror

# Local Docker registry for Kind (optional)
resource "docker_image" "registry" {
  count = var.enable_registry ? 1 : 0
  name  = "registry:2"
}

resource "docker_container" "registry" {
  count = var.enable_registry ? 1 : 0
  name  = var.registry_name
  image = docker_image.registry[0].image_id

  ports {
    internal = 5000
    external = var.registry_port
  }

  # Persist registry data
  volumes {
    container_path = "/var/lib/registry"
    volume_name    = docker_volume.registry[0].name
  }

  restart = "unless-stopped"

  # Healthcheck
  healthcheck {
    test         = ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:5000/v2/"]
    interval     = "10s"
    timeout      = "3s"
    retries      = 3
    start_period = "5s"
  }
}

resource "docker_volume" "registry" {
  count = var.enable_registry ? 1 : 0
  name  = "${var.registry_name}-data"
}

# Kind cluster
resource "kind_cluster" "ecom" {
  name       = var.cluster_name
  node_image = var.kind_image

  # Wait for cluster to be ready
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    # Networking: disable default CNI? No, keep kindnet
    networking {
      pod_subnet     = "10.244.0.0/16"
      service_subnet = "10.96.0.0/12"
      # api_server_port = 6443 (auto)
    }

    # Control-plane node
    node {
      role = "control-plane"

      # Extra port mappings for ingress and gateway
      extra_port_mappings {
        container_port = 80
        host_port      = var.http_port
        protocol       = "TCP"
      }
      extra_port_mappings {
        container_port = 443
        host_port      = var.https_port
        protocol       = "TCP"
      }
      extra_port_mappings {
        container_port = 30080
        host_port      = 8080
        protocol       = "TCP"
      }
      # Gateway NodePort for direct access
      extra_port_mappings {
        container_port = 30000
        host_port      = var.gateway_port
        protocol       = "TCP"
      }

      dynamic "extra_port_mappings" {
        for_each = var.extra_port_mappings
        content {
          container_port = extra_port_mappings.value.container_port
          host_port      = extra_port_mappings.value.host_port
          protocol       = lookup(extra_port_mappings.value, "protocol", "TCP")
        }
      }

      kubeadm_config_patches = [
        <<-EOT
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true,node-role.kubernetes.io/control-plane=,workload=critical,arch=${substr(var.kind_image, -5, 5) == "arm64" ? "arm64" : "amd64"}"
        EOT
      ]

      extra_mounts {
        host_path      = "/tmp"
        container_path = "/tmp"
      }
    }

    # Worker nodes (2 by default)
    dynamic "node" {
      for_each = range(var.workers)
      content {
        role  = "worker"
        image = var.kind_image

        kubeadm_config_patches = [
          <<-EOT
          kind: JoinConfiguration
          nodeRegistration:
            kubeletExtraArgs:
              node-labels: "workload=stateless,arch=${substr(var.kind_image, -5, 5) == "arm64" ? "arm64" : "amd64"},spot=false"
          EOT
        ]

        extra_mounts {
          host_path      = "/tmp"
          container_path = "/tmp"
        }
      }
    }

    # Containerd config for local registry mirror
    containerd_config_patches = var.enable_registry ? [
      <<-TOML
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:${var.registry_port}"]
        endpoint = ["http://${var.registry_name}:5000"]
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${var.registry_name}:5000"]
        endpoint = ["http://${var.registry_name}:5000"]
      TOML
    ] : []
  }

  depends_on = [docker_container.registry]
}

# Connect registry to kind network
resource "docker_container" "registry_network" {
  count = var.enable_registry ? 1 : 0
  # This null resource ensures registry is attached to kind network
  # The kind provider creates a network named "kind"
  # We use a null_resource with local-exec instead

  # Workaround: use docker_network to attach
  # Actually kind network is created by kind provider, we need to connect via provisioner
  # Simplified: registry is on bridge, kind uses kind network - we add extra hosts mapping via containerd mirror already handles via http://${var.registry_name}:5000
  # So we need to ensure registry container is on kind network
  # We'll use a separate resource to connect

  # Placeholder - real implementation uses null_resource
  name  = "placeholder-${count.index}"
  image = "alpine:latest"
  lifecycle {
    ignore_changes = all
  }
}

resource "null_resource" "registry_kind_network" {
  count = var.enable_registry ? 1 : 0

  triggers = {
    registry_id = docker_container.registry[0].id
    cluster_id  = kind_cluster.ecom.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      # Connect registry to kind network if not already connected
      docker network connect kind ${var.registry_name} 2>/dev/null || true
      # Also ensure kind nodes can resolve registry
      echo "Registry ${var.registry_name} connected to kind network"
    EOT
  }

  depends_on = [kind_cluster.ecom, docker_container.registry]
}

# ConfigMap for local registry hosting (for kind documentation)
resource "kubernetes_config_map" "local_registry_hosting" {
  count = var.enable_registry ? 1 : 0
  metadata {
    name      = "local-registry-hosting"
    namespace = "kube-public"
  }
  data = {
    "localRegistryHosting.v1" = <<-EOT
      host: "localhost:${var.registry_port}"
      help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
    EOT
  }
  depends_on = [kind_cluster.ecom]
}

# Namespace for ecom
resource "kubernetes_namespace" "ecom" {
  metadata {
    name = "ecom"
    labels = {
      name       = "ecom"
      managed-by = "terraform"
      env        = "local"
    }
  }
  depends_on = [kind_cluster.ecom]
}

# Optional: ingress-nginx via Helm (for local testing of ingress)
resource "helm_release" "ingress_nginx" {
  count      = var.install_ingress_nginx ? 1 : 0
  name       = "ingress-nginx"
  repository = "https://kubernetes.github.io/ingress-nginx"
  chart      = "ingress-nginx"
  version    = var.ingress_nginx_version
  namespace  = "ingress-nginx"
  create_namespace = true

  values = [
    <<-EOT
    controller:
      replicaCount: 1
      service:
        type: NodePort
      hostPort:
        enabled: true
      watchIngressWithoutClass: true
      config:
        hsts: "false"
      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          cpu: 500m
          memory: 512Mi
      extraArgs:
        default-ssl-certificate: "ingress-nginx/default-tls"
    EOT
  ]

  depends_on = [kind_cluster.ecom]
}
