#!/usr/bin/env bash
# Restrict what the worker machines may reach. Run once after the worker app exists, then restart
# or redeploy its machines (Fly applies a policy only to machines started after it is created).
#
#   FLY_API_TOKEN=... ./scripts/fly_worker_network_policy.sh audit-worker
#
# What this does and does NOT do (from Fly's Network Policies docs, Machines API):
#   * Rules are allow-lists of protocol + port. Once an egress rule exists, every other egress
#     port is denied. The `action` can only be "allow".
#   * It CANNOT filter by destination address. It will not stop the worker from connecting to a
#     private or metadata IP on port 80/443. That protection is in app/fetch/safe_fetch.py
#     (DNS resolution, IP range blocking, per-redirect re-check), which must keep its SSRF tests.
#   * So this is defence in depth against a compromised worker (no SMTP, SSH, raw sockets on odd
#     ports), not a replacement for the application-level fetch guard.
#   * "Traffic routed through the Fly Proxy" is not affected, per the docs.
#
# Ports the worker legitimately needs: 80/443 (provider APIs and fetched pages), 5432 (Postgres),
# 6379 (Redis, including TLS Redis on 6379), 53 (DNS). If your Postgres or Redis uses other ports,
# add them here. This script has NOT been run against a real Fly account.
set -euo pipefail

app="${1:?usage: fly_worker_network_policy.sh <worker-app-name>}"
: "${FLY_API_TOKEN:?set FLY_API_TOKEN}"

curl --fail-with-body --silent --show-error --location \
  "https://api.machines.dev/v1/apps/${app}/network_policies" \
  --header "Authorization: Bearer ${FLY_API_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data '{
    "name": "worker-egress",
    "selector": { "all": true },
    "rules": [
      { "action": "allow", "direction": "egress", "ports": [
        { "protocol": "tcp", "port": 80 },
        { "protocol": "tcp", "port": 443 },
        { "protocol": "tcp", "port": 5432 },
        { "protocol": "tcp", "port": 6379 },
        { "protocol": "tcp", "port": 53 },
        { "protocol": "udp", "port": 53 }
      ] }
    ]
  }'
echo
echo "Policy created. Restart the worker machines: fly machine restart -a ${app} <machine-id>"
