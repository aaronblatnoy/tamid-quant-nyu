## What
<!-- One sentence: what does this PR change? -->

## Why
<!-- Link the issue or describe the motivation. -->

## How
<!-- Brief implementation notes. Architectural decisions worth calling out. -->

## Risk
- **Reversibility**: <!-- easy to revert? data migration involved? -->
- **Real-money impact**: <!-- does this change anything in packages/trade? -->
- **Backtest impact**: <!-- could this change historical backtest results? -->

## Verification
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run ruff check .` passes
- [ ] `uv run basedpyright` passes
- [ ] `uv run pytest` passes
- [ ] Tested against local postgres+redis (if integration code touched)
- [ ] Risk gate unchanged (if `packages/trade/` touched) **OR** documented why it changed

## Linked issues
Closes #
