# Security Policy

## Supported Versions

Only the latest version is supported with security updates.

## Reporting a Vulnerability

Please do NOT report security vulnerabilities through public GitHub issues.

Instead, please send an email to the repository maintainer. 
Provide a descriptive report of the issue, the steps you took to create the issue, and the affected version.

## Safe Usage Recommendations

- Do not expose your proxy directly to the public internet without proper authentication.
- Always use a strong `dashboard_password` and configure `api_auth_key` to restrict access to the endpoints.
- Do not commit your `proxy_config.json` or `.env` files to public repositories.