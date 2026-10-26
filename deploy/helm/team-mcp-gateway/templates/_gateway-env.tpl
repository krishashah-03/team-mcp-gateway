{{/* Environment shared by the gateway container, its migration init container and the migration Job */}}
{{- define "tmg.gatewayEnv" -}}
{{- $fn := include "tmg.fullname" . -}}
{{- if .Values.postgres.enabled }}
- name: POSTGRES_PASSWORD
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" . "key" "POSTGRES_PASSWORD") | nindent 4 }}
- name: DATABASE_URL
  value: postgresql+psycopg://{{ .Values.postgres.user }}:$(POSTGRES_PASSWORD)@{{ $fn }}-postgres:5432/{{ .Values.postgres.database }}
{{- else }}
- name: DATABASE_URL
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" . "key" "DATABASE_URL") | nindent 4 }}
{{- end }}
- name: JWT_SECRET
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" . "key" "JWT_SECRET") | nindent 4 }}
- name: CREDENTIAL_ENCRYPTION_KEY
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" . "key" "CREDENTIAL_ENCRYPTION_KEY") | nindent 4 }}
{{- range $k := list "BOOTSTRAP_ADMIN_EMAIL" "BOOTSTRAP_ADMIN_PASSWORD" "OPENROUTER_API_KEY" "GEMINI_API_KEY" "METRICS_TOKEN" }}
- name: {{ $k }}
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" $ "key" $k "optional" true) | nindent 4 }}
{{- end }}
- name: SEED_WOOCOMMERCE_URL
  value: http://{{ $fn }}-woocommerce-mcp:8001/mcp
- name: SEED_WOOCOMMERCE_TOKEN
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" . "key" "WOOCOMMERCE_MCP_TOKEN") | nindent 4 }}
- name: SEED_UTILITIES_URL
  value: http://{{ $fn }}-utilities-mcp:8003/mcp
- name: SEED_UTILITIES_TOKEN
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" . "key" "UTILITIES_MCP_TOKEN") | nindent 4 }}
- name: SEED_DOCS_URL
  value: http://{{ $fn }}-docs-mcp:8004/mcp
- name: SEED_DOCS_TOKEN
  valueFrom:
    {{- include "tmg.secretKey" (dict "root" . "key" "DOCS_MCP_TOKEN") | nindent 4 }}
# Production network policy: upstream URLs may only point at these three servers.
- name: UPSTREAM_ALLOW_PRIVATE_NETWORKS
  value: "false"
- name: UPSTREAM_ALLOWED_HOSTS
  value: {{ $fn }}-woocommerce-mcp,{{ $fn }}-utilities-mcp,{{ $fn }}-docs-mcp
- name: GATEWAY_ALLOWED_HOSTS
  value: {{ include "tmg.publicHosts" . | quote }}
# Only the dashboard's proxy can reach the gateway, so trust its forwarded address.
- name: FORWARDED_ALLOW_IPS
  value: "*"
- name: LOG_FORMAT
  value: {{ .Values.gateway.logFormat | quote }}
- name: LLM_PROVIDER
  value: {{ .Values.gateway.llmProvider | quote }}
- name: OPENROUTER_MODEL
  value: {{ .Values.gateway.openrouterModel | quote }}
- name: OPENROUTER_FALLBACK_MODELS
  value: {{ .Values.gateway.openrouterFallbackModels | quote }}
- name: GEMINI_MODEL
  value: {{ .Values.gateway.geminiModel | quote }}
{{- range $k, $v := .Values.gateway.extraEnv }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "tmg.publicHosts" -}}
{{- $h := .Values.publicHosts -}}
{{- if and .Values.ingress.enabled .Values.ingress.host (not (contains .Values.ingress.host $h)) -}}
{{ printf "%s,%s" $h .Values.ingress.host }}
{{- else -}}{{ $h }}{{- end -}}
{{- end -}}
