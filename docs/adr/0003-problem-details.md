# 0003. RFC 9457 problem details for errors

- Status: accepted
- Date: 2026-10-12

## Context

FastAPI's default errors are inconsistent: `{"detail": "..."}` for HTTP errors and a list of Pydantic
errors for validation failures. The frontend needs one shape to render messages, map field errors to
form inputs and branch on specific failures (`session_overlap`, `token_expired`) without parsing
human-readable text.

## Decision

Every error response uses `application/problem+json` as defined by RFC 9457, extended with:

- `code`: a stable, snake_case machine identifier (also the slug of `type`).
- `request_id`: the same value as the `X-Request-ID` response header, for support and log correlation.
- `errors`: for validation failures, a list of `{field, message, code}` with dotted field paths.

Services raise typed exceptions derived from `AppError`; each class fixes the status, code and title.
One set of handlers converts `AppError`, `RequestValidationError` and Starlette's `HTTPException`.
Unexpected exceptions are logged with a traceback and returned as a generic 500 with no internals.

## Consequences

- Clients switch on `code`, display `detail` and attach `errors[].message` to inputs.
- The OpenAPI document declares problem responses per operation (`ProblemDetail` schema, rewritten to
  the `application/problem+json` media type), so generated clients know the shape.
- Codes are part of the public contract: renaming one is a breaking change, adding one is not.
- Some failures deliberately use 400 or 422 instead of 401 (an expired email link, a wrong current
  password in a form) so that a client's "401 means refresh and retry" logic isn't triggered by
  something that is not a session problem.
