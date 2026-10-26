# Creates three sample products in the store through the gateway (no chat model needed).
# Needs the dashboard reachable, e.g.:  kubectl port-forward svc/tmg-team-mcp-gateway-web 8080:8080
#
#   .\deploy\scripts\seed-products.ps1 -Email you@example.com -Password "your-admin-password"
#
# Running it twice creates the products twice (WooCommerce does not de-duplicate by name).
param(
  [Parameter(Mandatory)][string]$Email,
  [Parameter(Mandatory)][string]$Password,
  [string]$BaseUrl = "http://127.0.0.1:8080"
)
$ErrorActionPreference = "Stop"

$login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/auth/login" -ContentType "application/json" `
  -Body (@{ email = $Email; password = $Password } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }

$products = @(
  @{ name = "Blue Ceramic Mug"; price = "349"; description = "350 ml glazed ceramic mug, dishwasher safe." },
  @{ name = "Steel Water Bottle"; price = "599"; description = "750 ml insulated stainless steel bottle, keeps drinks cold for 24 hours." },
  @{ name = "Canvas Tote Bag"; price = "449"; description = "Heavy cotton canvas tote with an inner pocket." }
)

foreach ($p in $products) {
  $body = @{ name = "woocommerce__create_product"; arguments = $p } | ConvertTo-Json -Depth 5
  $result = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/tools/call" -Headers $headers `
    -ContentType "application/json" -Body $body
  if ($result.is_error) { Write-Host "FAILED  $($p.name): $($result.text)" -ForegroundColor Red }
  else { Write-Host "created $($p.name)" -ForegroundColor Green }
}
