{{/*
ecom-common library chart - shared helpers
All 10 services use these, so `kubectl get pods -l eci.phase=4` finds exactly what this pipeline rolled out.
*/}}

{{/* fullname */}}
{{- define "ecom-common.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* chart label */}}
{{- define "ecom-common.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* selector labels */}}
{{- define "ecom-common.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ecom-common.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .Chart.Name }}
{{- end -}}

{{/* common labels - includes eci markers for Jenkins CI tracking */}}
{{- define "ecom-common.labels" -}}
helm.sh/chart: {{ include "ecom-common.chart" . }}
{{ include "ecom-common.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ecom-platform
eci.managed-by: jenkins-ci
eci.phase: "4"
eci.service: {{ .Chart.Name }}
{{- with .Values.scheduling }}
{{- if .architecture }}
eci.arch: {{ .architecture | quote }}
{{- end }}
{{- end }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* service account name */}}
{{- define "ecom-common.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "ecom-common.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* image */}}
{{- define "ecom-common.image" -}}
{{- $repo := .Values.image.repository -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion | toString -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{/* probes - uses values.probes.readiness.path etc - same path Dockerfile HEALTHCHECK and Phase 2 smoke test use */}}
{{- define "ecom-common.probes.readiness" -}}
httpGet:
  path: {{ .Values.probes.readiness.path | default "/health" }}
  port: {{ .Values.containerPort }}
initialDelaySeconds: {{ .Values.probes.readiness.initialDelaySeconds | default 10 }}
periodSeconds: {{ .Values.probes.readiness.periodSeconds | default 10 }}
failureThreshold: {{ .Values.probes.readiness.failureThreshold | default 6 }}
successThreshold: 1
timeoutSeconds: 5
{{- end -}}

{{- define "ecom-common.probes.liveness" -}}
httpGet:
  path: {{ .Values.probes.liveness.path | default "/health" }}
  port: {{ .Values.containerPort }}
initialDelaySeconds: {{ .Values.probes.liveness.initialDelaySeconds | default 25 }}
periodSeconds: {{ .Values.probes.liveness.periodSeconds | default 20 }}
failureThreshold: {{ .Values.probes.liveness.failureThreshold | default 6 }}
successThreshold: 1
timeoutSeconds: 5
{{- end -}}

{{/* resources - with sane defaults for Graviton */}}
{{- define "ecom-common.resources" -}}
{{- if .Values.resources }}
{{ toYaml .Values.resources }}
{{- else }}
requests:
  cpu: 100m
  memory: 128Mi
limits:
  cpu: 500m
  memory: 512Mi
{{- end }}
{{- end -}}

{{/* nodeSelector - Graviton arm64 by default */}}
{{- define "ecom-common.nodeSelector" -}}
{{- if .Values.scheduling }}
{{- if eq .Values.scheduling.architecture "arm64" }}
kubernetes.io/arch: arm64
kubernetes.io/os: linux
{{- else if eq .Values.scheduling.architecture "amd64" }}
kubernetes.io/arch: amd64
kubernetes.io/os: linux
{{- end }}
{{- end }}
{{- with .Values.nodeSelector }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* tolerations - spot capable */}}
{{- define "ecom-common.tolerations" -}}
{{- $tolerations := list }}
{{- if .Values.scheduling }}
{{- if .Values.scheduling.spotCapable }}
{{- $spotTol := dict "key" "spot" "operator" "Equal" "value" "true" "effect" "NoSchedule" }}
{{- $tolerations = append $tolerations $spotTol }}
{{- end }}
{{- end }}
{{- with .Values.tolerations }}
{{- $tolerations = concat $tolerations . }}
{{- end }}
{{- if $tolerations }}
{{ toYaml $tolerations }}
{{- end }}
{{- end -}}

{{/* topologySpreadConstraints - spread across AZs */}}
{{- define "ecom-common.topologySpreadConstraints" -}}
{{- if .Values.topologySpreadConstraints }}
{{ toYaml .Values.topologySpreadConstraints }}
{{- else }}
- maxSkew: 1
  topologyKey: topology.kubernetes.io/zone
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      {{- include "ecom-common.selectorLabels" . | nindent 6 }}
- maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      {{- include "ecom-common.selectorLabels" . | nindent 6 }}
{{- end }}
{{- end -}}

{{/* podDisruptionBudget */}}
{{- define "ecom-common.pdb" -}}
{{- if .Values.podDisruptionBudget.enabled }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "ecom-common.fullname" . }}
  labels:
    {{- include "ecom-common.labels" . | nindent 4 }}
spec:
  {{- if .Values.podDisruptionBudget.minAvailable }}
  minAvailable: {{ .Values.podDisruptionBudget.minAvailable }}
  {{- else }}
  minAvailable: 1
  {{- end }}
  selector:
    matchLabels:
      {{- include "ecom-common.selectorLabels" . | nindent 6 }}
{{- end }}
{{- end -}}

{{/* securityContext - non-root, readOnlyRootFilesystem compatible */}}
{{- define "ecom-common.securityContext" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
fsGroup: 1000
seccompProfile:
  type: RuntimeDefault
{{- with .Values.securityContext }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "ecom-common.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 1000
fsGroup: 1000
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* container securityContext */}}
{{- define "ecom-common.containerSecurityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
    - ALL
readOnlyRootFilesystem: false
runAsNonRoot: true
runAsUser: 1000
{{- end -}}

{{/* env from config */}}
{{- define "ecom-common.env" -}}
{{- range $key, $value := .Values.config }}
- name: {{ $key }}
  value: {{ $value | quote }}
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* priorityClass */}}
{{- define "ecom-common.priorityClassName" -}}
{{- if .Values.scheduling }}
{{- if .Values.scheduling.priorityClass }}
{{- .Values.scheduling.priorityClass }}
{{- end }}
{{- end }}
{{- end -}}

{{/* service port */}}
{{- define "ecom-common.servicePort" -}}
{{ .Values.service.port | default .Values.containerPort }}
{{- end -}}
