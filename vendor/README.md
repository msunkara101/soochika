# Vendored files

| File | Version | Source | SHA-256 | Licence |
|---|---|---|---|---|
| `axe.min.js` | axe-core 4.10.2 | https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js | `b511cd9dec01c76f4b2ad1723b66b6db37d4c2eb4ed199076e1829d9ee7b75e3` | MPL-2.0 (Deque Systems) |

Pinned so audit results don't change between runs unless we upgrade on purpose.
To upgrade: download the new build, update this table, re-run `uv run pytest tests/a11y_auditor`,
and note the version change in the progress log (scores may shift).
