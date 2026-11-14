# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | ✅        |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report privately via GitHub: [Security Advisories](https://github.com/italo-lombardi/DashSnap/security/advisories/new)

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive a response within 48 hours. If confirmed, a fix will be released as soon as possible and you will be credited in the changelog.

## Scope

DashSnap runs headless Chromium and accepts URLs to record. Key considerations:

- **Token security**: The `ha_token` and `http_header` tokens in `options.json` / env vars are sensitive. Do not expose the DashSnap API (port 8099) to untrusted networks.
- **URL validation**: DashSnap does not restrict which URLs can be recorded for configured targets. Untrusted (`public` / no-auth) captures are blocked from private/link-local/loopback/metadata addresses, but authenticated targets hit their configured `base_url` unchecked. Deploy behind a firewall or restrict access to trusted callers only.
- **Unauthenticated control plane on LAN** (`config.yaml` `ports: 8099/tcp`): the add-on maps port 8099 to the host, so the API is reachable on the LAN **bypassing Home Assistant's ingress authentication**. `POST /config` has no auth of its own — it rewrites `base_url` / `token` / `targets` and restarts the add-on. Anyone who can reach the host on port 8099 (other devices on your network, compromised IoT) can repoint `base_url` at a server they control and capture the injected HA token, force restarts, or read back (masked) config. **Mitigation:** keep the add-on on a trusted network only. To close it entirely, remove the `ports:` mapping from `config.yaml` — the integration (internal Docker network) and the sidebar UI (ingress) keep working; you only lose direct `http://<host-ip>:8099` browser access.
- **Docker**: Run with least privilege. Do not expose port 8099 publicly.
