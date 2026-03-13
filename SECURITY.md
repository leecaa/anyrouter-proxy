# Security Policy

## Supported Versions

Only the latest version is supported with security updates.

## Reporting a Vulnerability

Please do NOT report security vulnerabilities through public GitHub issues.

Instead, please send an email to the repository maintainer.
Provide a descriptive report of the issue, the steps you took to create the issue, and the affected version.

## Safe Usage Recommendations

- This proxy uses a transparent API key passthrough model — it does not store or validate API keys. Clients must provide their own upstream API key in each request.
- Keep the proxy bound to `127.0.0.1` (localhost) unless you have proper network-level access controls in place.
- Always use a strong `dashboard_password` to protect the web dashboard.
- Do not commit your `proxy_config.json` or `.env` files to public repositories.
