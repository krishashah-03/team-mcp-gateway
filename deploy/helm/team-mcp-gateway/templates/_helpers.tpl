{{- define "tmg.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 40 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 40 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "tmg.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* selector labels: call with (dict "root" . "component" "gateway") */}}
{{- define "tmg.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* image reference: call with (dict "root" . "name" .Values.image.gateway) */}}
{{- define "tmg.image" -}}
{{- $reg := .root.Values.image.registry -}}
{{- if $reg -}}{{ printf "%s/%s:%s" (trimSuffix "/" $reg) .name .root.Values.image.tag }}
{{- else -}}{{ printf "%s:%s" .name .root.Values.image.tag }}{{- end -}}
{{- end -}}

{{- define "tmg.secretKey" -}}
secretKeyRef:
  name: {{ .root.Values.existingSecret }}
  key: {{ .key }}
{{- if .optional }}
  optional: true
{{- end }}
{{- end -}}

{{/* Hardened container settings shared by every workload */}}
{{- define "tmg.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}

{{- define "tmg.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "tmg.pullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
