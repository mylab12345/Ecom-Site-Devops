{{/*
shipping helpers - local + ecom-common fallback
*/}}

{{- define "shipping.fullname" -}}
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

{{- define "shipping.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "shipping.selectorLabels" -}}
app.kubernetes.io/name: {{ include "shipping.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: shipping
{{- end -}}

{{- define "shipping.labels" -}}
helm.sh/chart: {{ include "shipping.chart" . }}
{{ include "shipping.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ecom-platform
eci.managed-by: jenkins-ci
eci.phase: "4"
eci.service: shipping
eci.tier: critical
{{- with .Values.scheduling }}
{{- if .architecture }}
eci.arch: {{ .architecture | quote }}
{{- end }}
{{- end }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "shipping.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "shipping.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "shipping.image" -}}
{{- $repo := .Values.image.repository -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion | toString -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{- define "shipping.probes.readiness" -}}
httpGet:
  path: {{ .Values.probes.readiness.path | default "/health" }}
  port: {{ .Values.containerPort }}
initialDelaySeconds: {{ .Values.probes.readiness.initialDelaySeconds | default 10 }}
periodSeconds: {{ .Values.probes.readiness.periodSeconds | default 10 }}
failureThreshold: {{ .Values.probes.readiness.failureThreshold | default 6 }}
successThreshold: 1
timeoutSeconds: 5
{{- end -}}

{{- define "shipping.probes.liveness" -}}
httpGet:
  path: {{ .Values.probes.liveness.path | default "/health" }}
  port: {{ .Values.containerPort }}
initialDelaySeconds: {{ .Values.probes.liveness.initialDelaySeconds | default 25 }}
periodSeconds: {{ .Values.probes.liveness.periodSeconds | default 20 }}
failureThreshold: {{ .Values.probes.liveness.failureThreshold | default 6 }}
successThreshold: 1
timeoutSeconds: 5
{{- end -}}

{{- define "shipping.nodeSelector" -}}
{{- if .Values.scheduling }}
{{- if eq .Values.scheduling.architecture "arm64" }}
kubernetes.io/arch: arm64
kubernetes.io/os: linux
{{- else if eq .Values.scheduling.architecture "amd64" }}
kubernetes.io/arch: amd64
{{- end }}
{{- end }}
{{- with .Values.nodeSelector }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "shipping.tolerations" -}}
{{- $tols := list }}
{{- if .Values.scheduling }}
{{- if .Values.scheduling.spotCapable }}
{{- $spot := dict "key" "spot" "operator" "Equal" "value" "true" "effect" "NoSchedule" }}
{{- $tols = append $tols $spot }}
{{- end }}
{{- end }}
{{- with .Values.tolerations }}
{{- $tols = concat $tols . }}
{{- end }}
{{- if $tols }}
{{ toYaml $tols }}
{{- end }}
{{- end -}}

{{- define "shipping.topologySpreadConstraints" -}}
{{- if .Values.topologySpreadConstraints }}
{{ toYaml .Values.topologySpreadConstraints }}
{{- else }}
- maxSkew: 1
  topologyKey: topology.kubernetes.io/zone
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      {{- include "shipping.selectorLabels" . | nindent 6 }}
- maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      {{- include "shipping.selectorLabels" . | nindent 6 }}
{{- end }}
{{- end -}}

{{- define "shipping.extraEnv" -}}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}
