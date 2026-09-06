# API hardening notes

## Versioning
- Prefer `/api/v1/...` for public routes and keep the gateway as the single public entry point.

## Security
- Protect sensitive routes with JWT authentication and scope-based RBAC controls.
- Add rate limiting and request IDs in front of public endpoints.

## Observability
- Correlate logs with the `x-request-id` header and propagate it across services.
- Hook OpenTelemetry tracing into the gateway and downstream service calls.

## Contracts
- Keep request/response schemas explicit with Pydantic models and validate them in tests.
