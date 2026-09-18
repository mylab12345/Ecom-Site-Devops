{{/* Probe templates - separate file for readability, included via ecom-common.probes.* */}}
{{/* This file re-exports probe helpers for charts that want to override */}}

{{- define "ecom-common.probe.http" -}}
{{- $path := .path | default "/health" }}
{{- $port := .port }}
httpGet:
  path: {{ $path }}
  port: {{ $port }}
{{- end }}
