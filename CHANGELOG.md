# Changelog

## 2026-10-25 — Cookie session reliability

- Preserve authenticated sessions; adopt explicitly refreshed credentials safely.
- Recover before strict-Pro checks; distinguish not-submitted failures from generation failures.
- Check supported Pro response metadata and reject unverifiable outputs without replay.
- Share HTTP/generation deadlines and report offline enrolled-browser recovery.
- Add explicit experimental process-session transport; legacy remains default, no automatic fallback.
- [Bilingual update and upgrade notes / 中英文更新及升级说明](docs/UPDATE_20261025.md).

## 0.2.0-preview.1 — 2026-10-17

- Standalone local application; fresh per-install secrets and non-destructive setup.
- Windows installer, one CLI, offline doctor, English/Chinese onboarding.
- Account enrollment, dedicated browser profiles, verification, rename and deletion.
- Activity reporting by date/account; token estimates explicitly distinguished.
- Client API keys, model scopes, audit records, drain controls and diagnostics.
- Cookie dynamic model catalog, request deduplication and bounded dispatch budgets.
- Browser transient failures distinguished from identity mismatch; unknown replies
  remain protected against blind resubmission. Same-tab conversation reuse.
- Public issue templates, Windows/Linux CI, release-file privacy checks.
- Bilingual release tour, illustrated dashboard documentation and explicit
  Cookie-to-browser recovery steps. Image generation added to the roadmap,
  not advertised as an available feature.
- Text-only preview: image API disabled. No promise of upstream availability,
  exact browser token counts, or cryptographic verification of model identity.

本版为公开反馈测试版，不是生产 SLA 承诺。仅发布程序代码，不含任何本机账号或运行数据。
