# Account admission and safe retries

Authentication, model entitlement, quota and failure cooldown are separate states.
`GET /health` reports each account's authentication and `admission` status.
An authenticated Pro account can still be in a temporary failure cooldown.

`POST /v1/chat/completions` returns HTTP 503 with `error.type=not_submitted`
when no account can admit the request. This means no generation was dispatched.
The error includes `submitted=false`, `retry_after` (seconds) and per-account
reasons. Clients should retain the job, wait cancellably, then try admission
again. This rejection must not consume a chapter/content failure allowance.
Do not apply this retry rule to generic 503 errors or HTTP 409.

HTTP 409 `submission_uncertain` means the request may already have generated a
response. Reconcile before retrying. Never turn it into an automatic resubmit.

Failure cooldowns are stored separately from quota records, in the private
`data/quota_state.sqlite3` database (`account_health` table). Restarting the
service does not clear them. Real quota limits are not bypassed. Cookie session
expiry requires an identity-verified credential refresh; choosing Pro in the
browser alone does not prove an exported Cookie session is valid.

RPC diagnostics record safe error codes/categories, not credential values or
full generated content. This improves diagnosis but does not guarantee that
upstream long-text errors have been eliminated.
