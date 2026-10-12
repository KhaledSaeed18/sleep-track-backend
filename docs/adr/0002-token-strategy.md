# 0002. Token strategy: JWT access tokens and rotating refresh tokens

- Status: accepted
- Date: 2026-10-12

## Context

The frontend is a Next.js app that talks to the API through same-origin rewrites. We need sessions
that survive page reloads, support "remember me", can be revoked per device, and limit the damage of
a stolen token, without putting long-lived credentials where JavaScript can read them.

## Decision

- **Access token**: a JWT (HS256 by default) with a 15 minute lifetime, returned in the response body
  and kept in memory by the client. Claims: `sub`, `sid` (device session id), `role`, `jti`, `iat`,
  `nbf`, `exp`, `iss`, `aud`, `typ`.
- **Refresh token**: 256 bits of randomness, stored only as a SHA-256 digest, delivered in the
  `st_refresh` cookie (`HttpOnly`, `SameSite=Lax`, `Path=/api/v1/auth`, `Secure` outside development).
  Remember-me sessions get a persistent 30-day cookie, others a browser-session cookie with a one-day
  server-side expiry. Both expiries slide on use.
- **Device sessions**: each sign-in creates an `auth_session` owning a family of refresh tokens.
  Every refresh marks the presented token as used (under a row lock) and issues a successor.
- **Reuse detection**: presenting a used token revokes its session, records `token_reuse_detected`
  and emails the user. A configurable grace window (10 s) treats near-simultaneous reuse as two tabs
  racing and rejects it without revoking.
- **CSRF**: cookie-authenticated endpoints (`/auth/refresh`, `/auth/logout`) require an
  `X-CSRF-Token` header equal to the readable `st_csrf` cookie (double submit). Bearer-authenticated
  endpoints are not exposed to CSRF.
- **Revocation of access tokens**: revoking a session writes `auth:revoked:sid:<id>` to Redis with a
  TTL just above the access-token lifetime; the auth dependency checks it on every request. Signing
  out everywhere revokes each active session individually.

## Consequences

- A stolen access token is useful for at most 15 minutes, and less if the session is revoked. A stolen
  refresh token is single-use: the next legitimate refresh exposes the theft and kills the family.
- One Redis `EXISTS` per authenticated request. If Redis is unavailable the check fails open (and
  logs a warning): access tokens are short-lived and refreshes are still validated against PostgreSQL.
- Denylisting by session id rather than "tokens issued before T" avoids clock-granularity races
  (a new sign-in in the same second as a sign-out-everywhere stays valid).
- Clients must single-flight refresh calls; the grace window only absorbs accidental races.
- HS256 keeps deployment simple (one secret). Moving to asymmetric keys (RS256/EdDSA with a JWKS
  endpoint) is a contained change in `TokenCodec` if other services ever need to verify tokens.
