# Neko Video maintenance

- Independent project: plugin ID `neko.video`; tool names use `neko_video_`.
- Preserve MIT attribution and NOTICE; independence does not erase provenance.
- `plugin.py` is discovery only. SDK adapters live in `neko_video/plugin.py`;
  domain services must not import the SDK or depend on a live MaiBot instance.
- No automatic migration from the former plugin, activation, paid model calls,
  QR login, credential import, QQ sends or large downloads during tests.
- Keep third-party API clients anonymous and separate from account-cookie clients.
- Retain DNS pinning, redirect/byte/pixel limits, scoped expiring media IDs,
  source hash verification and unknown-delivery no-retry behavior.
- Each tool description <=45 characters; total for all five <=190 characters.
- Use the real PluginContext in capability-contract tests and mock only the raw
  RPC boundary: message.get_by_id returns a normalized message dict, not the Host
  envelope. SDK/Runner and metadata-only probes do not prove paid model quality.
- Never commit config.toml, credentials, cookie files, parser tokens or runtime data.
- Run `python -m unittest discover -s tests -t . -q`, `ruff check .`,
  and `ruff format --check .`. Fixtures must use mocks and synthetic content.
- New installation defaults disabled. Admin allowlist empty means deny commands.
  Existing deployments and the old repository are outside automatic update scope.
