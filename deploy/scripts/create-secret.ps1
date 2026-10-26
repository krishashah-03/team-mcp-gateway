# Creates the Kubernetes Secret the chart reads, generating the random values locally.
# Nothing is written to disk. Usage (from the repository root):
#
#   .\deploy\scripts\create-secret.ps1 -AdminEmail you@example.com -AdminPassword "at-least-10-chars" `
#       -WooKey ck_xxx -WooSecret cs_xxx [-GeminiKey AIza...] [-Namespace default] [-Name team-mcp-gateway-secrets]
#
# Re-running replaces the Secret, which changes the database password and encryption key: only do
# that on a fresh install (upstream credentials already stored are encrypted with the old key).
param(
  [Parameter(Mandatory)][string]$AdminEmail,
  [Parameter(Mandatory)][string]$AdminPassword,
  [Parameter(Mandatory)][string]$WooKey,
  [Parameter(Mandatory)][string]$WooSecret,
  [string]$GeminiKey = "",
  [string]$Namespace = "default",
  [string]$Name = "team-mcp-gateway-secrets"
)
$ErrorActionPreference = "Stop"
if ($AdminPassword.Length -lt 10) { throw "AdminPassword must be at least 10 characters" }

function New-Token([int]$bytes) {
  $b = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  [Convert]::ToBase64String($b).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}
# A Fernet key is 32 random bytes, URL-safe base64 with padding.
function New-FernetKey {
  $b = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  [Convert]::ToBase64String($b).Replace("+", "-").Replace("/", "_")
}

# Native-command errors must not abort the script here; we check the results ourselves.
$ErrorActionPreference = "Continue"
$ctx = kubectl config current-context 2>$null
if (-not $ctx) {
  throw "kubectl has no cluster selected. Enable one first (Docker Desktop > Settings > Kubernetes > Enable Kubernetes, or install k3s), then check 'kubectl get nodes'."
}
kubectl get nodes 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Cannot reach the cluster '$ctx'. Is it running? Check 'kubectl get nodes'."
}
if (kubectl get secret $Name -n $Namespace -o name 2>$null) {
  throw "Secret '$Name' already exists in '$Namespace'. Delete it first if you really want new keys."
}
$ErrorActionPreference = "Stop"

$kargs = @(
  "create", "secret", "generic", $Name, "-n", $Namespace,
  "--from-literal=POSTGRES_PASSWORD=$(New-Token 24)",
  "--from-literal=JWT_SECRET=$(New-Token 48)",
  "--from-literal=CREDENTIAL_ENCRYPTION_KEY=$(New-FernetKey)",
  "--from-literal=WOOCOMMERCE_MCP_TOKEN=$(New-Token 24)",
  "--from-literal=UTILITIES_MCP_TOKEN=$(New-Token 24)",
  "--from-literal=DOCS_MCP_TOKEN=$(New-Token 24)",
  "--from-literal=WOOCOMMERCE_CONSUMER_KEY=$WooKey",
  "--from-literal=WOOCOMMERCE_CONSUMER_SECRET=$WooSecret",
  "--from-literal=BOOTSTRAP_ADMIN_EMAIL=$AdminEmail",
  "--from-literal=BOOTSTRAP_ADMIN_PASSWORD=$AdminPassword"
)
if ($GeminiKey) { $kargs += "--from-literal=GEMINI_API_KEY=$GeminiKey" }
kubectl @kargs
