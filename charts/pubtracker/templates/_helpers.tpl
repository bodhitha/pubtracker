{{/*
Name of the app's resources. Uses only the release name and global values, because the oauth2-proxy
subchart calls it (and pubtracker.secretName) from its own templated values.
*/}}
{{- define "pubtracker.fullname" -}}
{{- if contains "pubtracker" .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-pubtracker" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "pubtracker.secretName" -}}
{{- dig "pubtracker" "existingSecret" "" (.Values.global | default dict) | default (include "pubtracker.fullname" .) -}}
{{- end -}}

{{- define "pubtracker.host" -}}
{{- required "Set global.pubtracker.host to the public hostname" (dig "pubtracker" "host" "" (.Values.global | default dict)) -}}
{{- end -}}

{{- define "pubtracker.selectorLabels" -}}
app.kubernetes.io/name: pubtracker
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "pubtracker.labels" -}}
{{ include "pubtracker.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "pubtracker.image" -}}
{{- $repo := required "Set image.repository to where you pushed the image" .Values.image.repository -}}
{{- printf "%s:%s" $repo (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{/* The oauth2-proxy subchart's name, service and pod labels, following its own naming rules. */}}
{{- define "pubtracker.proxyName" -}}
{{- (index .Values "oauth2-proxy").nameOverride | default "oauth2-proxy" -}}
{{- end -}}

{{- define "pubtracker.proxyFullname" -}}
{{- $proxy := index .Values "oauth2-proxy" -}}
{{- $name := include "pubtracker.proxyName" . -}}
{{- if $proxy.fullnameOverride -}}
{{- $proxy.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* OpenShift picks UIDs and fsGroups itself and refuses pods that set others. */}}
{{- define "pubtracker.onOpenShift" -}}
{{- $mode := toString .Values.openshift -}}
{{- if or (eq $mode "true") (and (eq $mode "auto") (.Capabilities.APIVersions.Has "security.openshift.io/v1")) -}}
true
{{- end -}}
{{- end -}}

{{- define "pubtracker.podSecurityContext" -}}
{{- $ctx := deepCopy .Values.podSecurityContext -}}
{{- if include "pubtracker.onOpenShift" . -}}
{{- $ctx = omit $ctx "fsGroup" "runAsUser" "runAsGroup" "supplementalGroups" -}}
{{- end -}}
{{- toYaml $ctx -}}
{{- end -}}

{{- define "pubtracker.containerSecurityContext" -}}
{{- $ctx := deepCopy .Values.containerSecurityContext -}}
{{- if include "pubtracker.onOpenShift" . -}}
{{- $ctx = omit $ctx "runAsUser" "runAsGroup" -}}
{{- end -}}
{{- toYaml $ctx -}}
{{- end -}}

{{/* Environment shared by the app and its jobs. */}}
{{- define "pubtracker.env" -}}
- name: DJANGO_SECRET_KEY
  valueFrom:
    secretKeyRef: {name: {{ include "pubtracker.secretName" . }}, key: django-secret-key}
- name: AUTH_MODE
  value: proxy
- name: AUTH_PROXY_SECRET
  valueFrom:
    secretKeyRef: {name: {{ include "pubtracker.secretName" . }}, key: proxy-password}
- name: SQLITE_PATH
  value: /data/pubtracker.sqlite3
- name: DJANGO_ALLOWED_HOSTS
  value: {{ printf "%s,%s,localhost,127.0.0.1" (include "pubtracker.host" .) (include "pubtracker.fullname" .) | quote }}
- name: DJANGO_CSRF_TRUSTED_ORIGINS
  value: {{ printf "https://%s" (include "pubtracker.host" .) | quote }}
- name: SITE_TITLE
  value: {{ .Values.siteTitle | quote }}
{{- with .Values.inspire.url }}
- name: INSPIRE_URL
  value: {{ . | quote }}
{{- end }}
{{- with .Values.inspire.timeout }}
- name: INSPIRE_TIMEOUT
  value: {{ . | quote }}
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* Jobs mount the app's ReadWriteOnce volume, so they must run on the app pod's node. */}}
{{- define "pubtracker.nextToApp" -}}
podAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector:
        matchLabels:
          {{- include "pubtracker.selectorLabels" . | nindent 10 }}
      topologyKey: kubernetes.io/hostname
{{- end -}}

{{- define "pubtracker.dataClaim" -}}
{{- .Values.persistence.existingClaim | default (printf "%s-data" (include "pubtracker.fullname" .)) -}}
{{- end -}}

{{/* Refuse settings that would let anyone with an account at the sign-in service edit every entry. */}}
{{- define "pubtracker.validate" -}}
{{- $proxy := index .Values "oauth2-proxy" -}}
{{- $args := $proxy.extraArgs | default dict -}}
{{- $domains := $proxy.config.emailDomains | default list -}}
{{- $emails := and $proxy.authenticatedEmailsFile.enabled (or $proxy.authenticatedEmailsFile.restricted_access $proxy.authenticatedEmailsFile.template) -}}
{{- $groups := or (hasKey $args "allowed-group") (hasKey $args "allowed-groups") -}}
{{- if not (get $args "oidc-issuer-url") -}}
{{- fail "Set oauth2-proxy.extraArgs.oidc-issuer-url to your sign-in service's OIDC issuer" -}}
{{- end -}}
{{- /* oauth2-proxy admits an address that is on the list OR matches a domain, so "*" admits everyone */}}
{{- if and (has "*" $domains) (not $groups) -}}
{{- fail "oauth2-proxy.config.emailDomains [\"*\"] would let anyone with an account in, even with an email list. Name your domains instead, or keep \"*\" and set extraArgs.allowed-group" -}}
{{- end -}}
{{- if and (empty $domains) (not $emails) -}}
{{- fail "Nobody could sign in: list emails in oauth2-proxy.authenticatedEmailsFile.restricted_access or set oauth2-proxy.config.emailDomains" -}}
{{- end -}}
{{- if and (not $proxy.config.existingSecret) (eq (toString $proxy.config.clientID) "XXXXXXX") -}}
{{- fail "Set oauth2-proxy.config.existingSecret to a Secret with client-id, client-secret and cookie-secret" -}}
{{- end -}}
{{- if and .Values.ingress.tls.enabled .Values.ingress.tls.certManager.enabled (not .Values.ingress.tls.certManager.issuerRef.name) -}}
{{- fail "Set ingress.tls.certManager.issuerRef.name, or turn off ingress.tls.certManager and supply ingress.tls.secretName" -}}
{{- end -}}
{{- end -}}
