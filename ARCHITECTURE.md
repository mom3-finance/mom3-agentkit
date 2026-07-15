# Mom3 Agentkit Architecture

## System flow

```text
Next.js routes
  /api/ai/markets
  /api/ai/strategy
  /api/ai/execution-intent
        ↓
FastAPI routing layer
        ↓
Mom3 Agent
  ├─ MarketCatalog
  │   ├─ DefiLlama collector
  │   └─ MVP market policy
  ├─ Forecast and liquidity-health signals
  ├─ Explainable allocation
  └─ ExecutionIntentService
        ↓
Particle Universal Account
  ├─ ensure EIP-7702 delegation on the target chain
  ├─ source USDC from the user's unified balance
  ├─ quote approve + Aave supply calls
  └─ request user signature and submit
```

## Ownership rules

### `app/api`

Owns HTTP models, status codes, and serialization. No market selection or wallet logic belongs here.

### `app/modules/market_intelligence`

Owns the product decision about which markets Mom3 may display or execute. Discovery and execution are separate capabilities.

### `app/modules/agent_core`

Owns strategy orchestration and execution-intent validation. It produces deterministic data that can be independently checked by the frontend.

### `app/services`

Owns reusable integrations and algorithms such as DefiLlama, Aave reads, forecasting, optimization, and LLM access. These services do not decide which product markets are executable.

### Next.js frontend

Owns the wallet session, EIP-7702 authorization, quote review, user approval, signing, submission, and transaction result UX.

## Adding a new executable protocol

1. Add the protocol to discovery only.
2. Verify contract addresses and token semantics per chain.
3. Implement and test a dedicated transaction adapter.
4. Add it to the execution policy.
5. Return exact calls from the execution-intent service.
6. Render a review screen before signing.
7. Run a real small-value test before enabling it in recommendations.

Do not make a pool executable merely because DefiLlama reports a high APY.
