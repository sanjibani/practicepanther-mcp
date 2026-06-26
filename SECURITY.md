# Security policy

## Reporting a vulnerability

**Please do NOT file a public GitHub issue for security vulnerabilities.**

Email security reports to **schoudhury1991@gmail.com** with:

- A description of the vulnerability
- Steps to reproduce
- Affected versions (commit hash if possible)
- Your assessment of severity

You should receive an acknowledgment within **3 business days**. We aim to
publish a fix within **14 days** for critical issues and **30 days** for
non-critical issues.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅ Yes (current) |
| < 0.1   | ❌ No |

## Scope

In scope:

- Authentication bypass / token leakage
- Credential exposure in logs / errors
- SSRF via crafted URLs
- Injection in OAuth callback (`practicepanther-mcp-auth`)
- Dependency vulnerabilities (we run Dependabot weekly)

Out of scope:

- PracticePanther API bugs (file with [PracticePanther support](https://www.practicepanther.com/contact))
- MCP protocol-level issues (file with [modelcontextprotocol](https://github.com/modelcontextprotocol))

## Best practices for users

- Treat `PRACTICEPANTHER_ACCESS_TOKEN` and `PRACTICEPANTHER_REFRESH_TOKEN`
  as secrets — never commit them, never share them.
- Run `practicepanther-mcp-auth` on a trusted machine only.
- Use the smallest possible OAuth scope for your integration.
- Rotate the refresh_token every 60 days (or whenever prompted).
