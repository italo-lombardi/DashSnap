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
- **URL validation**: DashSnap does not restrict which URLs can be recorded. Deploy behind a firewall or restrict access to trusted callers only.
- **Docker**: Run with least privilege. Do not expose port 8099 publicly.
