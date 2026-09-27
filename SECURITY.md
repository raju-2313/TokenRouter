# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

Please do not open a public issue for security vulnerabilities.

Instead, email the maintainers or use GitHub's private vulnerability reporting.
Include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact

We will acknowledge receipt within 3 business days and provide a fix timeline.

## Security Practices

- API keys are loaded from environment variables and never logged.
- Diagnostics mask API keys (e.g., `sk-...abcd`).
- `GET /admin/usage` and diagnostics never expose keys or prompt contents.
- Prompt contents are not stored by default.
- Keep `config.yaml` and `.env` out of version control (see `.gitignore`).

## Provider Terms

You are responsible for complying with Clean APIs' Terms of Service,
rate limits, billing, and account restrictions. TokenRouter does not bypass
or work around provider limits.
