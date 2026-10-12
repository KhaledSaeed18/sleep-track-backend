# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 2.x | Yes |
| 1.x | No |

## Reporting a vulnerability

Please report vulnerabilities privately, either through
[GitHub security advisories](https://github.com/KhaledSaeed18/sleep-track-backend/security/advisories/new)
or by email to the maintainer listed on the GitHub profile. Do not open a public issue.

Include the affected endpoint or component, steps to reproduce and the impact you observed. You can
expect an acknowledgement within 72 hours and a status update within a week. Please give us a
reasonable window to ship a fix before disclosing publicly.

## Security model

**Authentication and sessions**
- Passwords are hashed with Argon2id (pwdlib); hashes with outdated parameters are upgraded on
  sign-in. Login performs a dummy hash verification for unknown accounts so response timing does not
  reveal whether an email is registered.
- Password policy: 8-128 characters, at least one letter and one digit, not on a common-password list.
- Access tokens are 15-minute JWTs bound to a device session; refresh tokens are random, stored only as
  SHA-256 digests, rotated on every use, and reuse of a rotated token revokes the session and alerts the
  user. See [ADR 0002](docs/adr/0002-token-strategy.md).
- Cookies: refresh token `HttpOnly`, `SameSite=Lax`, scoped to `/api/v1/auth`, `Secure` in staging
  and production. Cookie-authenticated endpoints require a double-submit CSRF token.
- Email verification and password reset tokens are single-use, hashed, expiring (48 h and 30 min) and
  invalidated when a new one is issued. Password reset requests always return 202 and are processed in
  a background job, so neither the response nor its timing reveals whether an account exists.
- Password changes sign out other devices; password resets sign out every device.
- OAuth uses the authorization-code flow with PKCE (S256) and a single-use `state` bound to an
  `HttpOnly` cookie. Accounts are only linked by email when the provider reports the email as
  verified; an unverified local password is discarded on linking to prevent pre-registration takeover.

**Abuse protection**
- Redis sliding-window rate limits: login 10/min per IP and 5/min per email, registration 5/h per IP,
  password reset 3/h per email and 10/h per IP, demo accounts 5/h per IP, plus limits on refresh,
  verification and report emails. Responses include `Retry-After`.
- Request bodies are capped (2 MB by default) while streaming; CSV imports are capped at 5000 rows.
- CSV exports neutralise spreadsheet formula injection.

**Transport and headers**
- Strict CORS allow-list with credentials; `TrustedHostMiddleware` in production.
- `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP/CORP,
  a restrictive CSP for API responses and HSTS when cookies are secure.

**Data and errors**
- Every query is scoped to the authenticated user; cross-user access returns 404, and the integration
  suite tests this for each resource.
- Errors never include stack traces or internal messages. Logs redact passwords, tokens, cookies and
  authorization headers.
- A security audit log records sign-ins, failures, sign-outs, password and email events, token reuse,
  exports/imports and admin changes; users can review their own entries.
- Startup validation refuses to run in production with a short or default `SECRET_KEY`, `DEBUG`
  enabled, wildcard CORS or the in-memory email provider.

**Operational notes**
- Redis outages degrade gracefully: rate limiting and access-token revocation checks fail open (and
  log warnings) so authentication stays available; refresh tokens are always validated in PostgreSQL.
- Keep `/metrics` on a private network. Set `FORWARDED_ALLOW_IPS` to your proxy so client IPs used by
  rate limits and audit entries can't be spoofed.
- Dependabot keeps Python dependencies, GitHub Actions and the base image up to date.
