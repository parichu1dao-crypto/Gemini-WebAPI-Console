# Preview scope and release gates

This is a single-administrator, loopback-only preview for feedback. It is not a
hosted multi-tenant product. No public ingress, billing, customer isolation or
availability SLA is included. Preserve the bundled AGPL license and upstream
attribution; commercialization decisions need a separate license/terms review.

Known limitations:

- Google may reject Cookie calls while its webpage works. No unlimited quota claim.
- Browser UI changes can break selectors; currently English UI is required.
- Model names are dynamically checked where supported; labels are not attestations.
- Managed browser strict paid-entitlement verification is not implemented.
- Browser conversation reuse may retain context. Use dedicated tabs.
- Unknown submissions need manual reconciliation; do not clear flags blindly.
- Browser token counts may be estimates. A successful call is not content QA.
- Image generation is not supported by the preview API.
- Raw logs/SQLite may contain sensitive content. Protect data/ with Windows ACLs.
- Session recovery can use only explicitly enrolled open browsers, never arbitrary
  browser-cookie discovery. All credentials are excluded from releases.

Release gates: clean offline unit suite; privacy scan; independent installation;
static asset smoke test; documented limitations; preserved license. Live account
verification is optional and must be reported separately, never fabricated by CI.

Future work: structured reconciliation, broader browser UI support, image retrieval,
dependency locking, and reproducible installer signing. New Gemini generations,
including Gemini 4 where upstream exposes them, are prioritized but cannot be
promised on a fixed date or for every account.
