# MVP Yield Market Research

Snapshot date: 2026-07-13. Source: live DefiLlama protocol, pool, chain TVL, and fee endpoints.

## Selected execution markets

| Route | APY | Pool TVL | 7d APY change | Why it fits MVP |
|---|---:|---:|---:|---|
| USDC → Aave V3 Base | 3.14% | $29.65M | +0.03 | Existing Mom3 adapter, low-risk single exposure, useful cross-chain target |
| USDC → Aave V3 Arbitrum | 2.59% | $32.44M | -0.07 | Existing Mom3 adapter, deep liquidity, same execution shape as Base |

The runtime never hardcodes these APYs. It refreshes `https://yields.llama.fi/pools` and applies the market policy on each cache cycle.

## Protocol validation

| Protocol | Protocol TVL | 7d TVL change | MVP status |
|---|---:|---:|---|
| Aave V3 | $13.01B | +2.20% | Execution-ready |
| Morpho Blue | $7.05B | +0.97% | Discovery-only |
| Compound V3 | $1.13B | +1.62% | Discovery-only |

Base had about $4.37B chain TVL and $1.15M fees in the latest 24h snapshot. Arbitrum had about $1.23B chain TVL and $147K fees in the latest 24h snapshot. Both have real activity, while Base currently offers the stronger combination of APY and chain activity for the primary demo target.

## Why not LP or the highest APY pool?

The hackathon demo needs predictable execution and clear user consent. The MVP excludes:

- multi-asset LP exposure and impermanent loss;
- unknown or low-TVL pools;
- reward-heavy APY that can disappear quickly;
- vault share symbols that have not been mapped to a verified deposit contract;
- markets without a tested Particle UA transaction adapter.

## Next candidates

1. Compound V3 USDC on Arbitrum after adding a dedicated Comet adapter.
2. Curated Morpho USDC vaults on Base after pinning vault addresses, curator identity, collateral parameters, and caps.
3. x402 payment for premium strategy generation after the core Particle cross-chain deposit is stable.
