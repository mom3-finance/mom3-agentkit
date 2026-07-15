# Mom3 AI Features Documentation

Mom3 AI is a non-custodial cross-chain yield agent. It researches markets and prepares an execution plan; Particle Universal Account and EIP-7702 provide the wallet execution layer.

## Feature modules

### Market Intelligence

`MarketCatalog` loads the DefiLlama yield catalog and applies the MVP policy:

- Base and Arbitrum target chains;
- Aave V3, Compound V3, and Morpho Blue discovery allowlist;
- stablecoin and single-asset exposure only;
- no impermanent-loss markets;
- minimum TVL and maximum APY sanity bounds;
- a separate `execution.enabled` capability.

Only Aave V3 USDC on Base and Arbitrum are execution-enabled in the MVP.

### Yield Forecast

The hot strategy path derives a seven-day signal from DefiLlama's current APY, 7-day APY change, and prediction confidence. This avoids serial chart requests and keeps the interaction fast. The result includes:

```json
{
  "market_id": "7e0661bf-8cf3-45e6-9424-31916d4c7b84",
  "current_apy": 3.14,
  "forecast_7d": [3.14, 3.15, 3.15, 3.16, 3.16, 3.17, 3.17],
  "trend": "stable",
  "confidence": 0.75
}
```

This is a trend estimate, not a guaranteed return.

### Liquidity Health

The liquidity signal combines market depth, recent APY movement, and the market risk score. It intentionally labels its basis as `market depth and APY trend`; it does not claim to measure on-chain net flow when that data is unavailable.

### Strategy Composer

The strategy composer ranks only execution-ready markets and returns:

- cross-chain allocation;
- expected weighted APY;
- weighted risk and health scores;
- explanation based on live market data;
- exact market IDs for downstream execution;
- `primary_execution` capability metadata.

LLM reasoning is optional through `AGENT_LLM_STRATEGY_REASONING=true`. The default strategy path is deterministic and fast. The chatbot can still use the configured OpenAI-compatible model.

### AI Chat

The chat endpoint receives the current strategy, forecast, liquidity health, selected chain, and conversation history. Without an LLM key, it degrades to an honest heuristic response instead of failing the UI.

### Execution Intent

`POST /api/ai/execution-intent` validates:

- the market exists in the current live catalog;
- the market is execution-enabled;
- the amount is positive and within the MVP cap;
- the receiver is a valid EVM address;
- the exact Aave pool and USDC addresses come from the allowlist.

Example request:

```json
{
  "market_id": "7e0661bf-8cf3-45e6-9424-31916d4c7b84",
  "amount": "10",
  "user_address": "0x1111111111111111111111111111111111111111"
}
```

Example response:

```json
{
  "intent_id": "m3i_...",
  "action": "supply",
  "chain_id": 8453,
  "amount": "10.000000",
  "amount_atomic": "10000000",
  "asset": {
    "symbol": "USDC",
    "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "decimals": 6
  },
  "calls": [
    { "method": "approve" },
    { "method": "supply" }
  ],
  "policy": {
    "execution_mode": "user-confirmed",
    "requires_eip7702": true,
    "cross_chain_funding_supported": true
  }
}
```

The intent has no signature and cannot move funds.

## End-to-end integration

```text
User taps Search strategies
        ↓
Next.js POST /api/ai/strategy
        ↓
Mom3 Agent returns live executable opportunities
        ↓
User opens strategy detail and taps Review & execute
        ↓
Supply screen requests /api/ai/execution-intent
        ↓
Frontend verifies chain, receiver, asset, and action
        ↓
Particle UA ensures EIP-7702 delegation on the target chain
        ↓
createUniversalTransaction(
  expectTokens: [{ type: USDC, amount }],
  transactions: [approve, Aave supply]
)
        ↓
User reviews, signs, and submits
        ↓
Account balance and transaction history refresh
```

The `expectTokens` field lets Particle source USDC from the user's unified cross-chain balance. The application does not implement manual bridge steps.

## API reference

### Strategy

```http
POST /api/ai/strategy
Content-Type: application/json

{
  "risk_tolerance": "moderate",
  "chain_id": 42161
}
```

### Markets

```http
GET /api/yield-markets?execution_only=true
```

### Forecast

```http
GET /api/yield-forecast?chain_id=8453
```

### Liquidity health

```http
GET /api/liquidity-pulse?chain_id=8453
```

### Chat

```http
POST /api/chat
Content-Type: application/json

{
  "message": "Which executable USDC market is stronger?",
  "chain_id": 8453,
  "history": []
}
```

## Safety rules

1. AI output is educational market analysis, not guaranteed yield.
2. Discovery-only pools never receive an execution CTA.
3. Backend-generated intent is checked again by the connected frontend account.
4. EIP-7702 delegation is checked per target chain.
5. The user reviews and signs every transaction.
6. New protocols require a dedicated adapter and real small-value transaction test before allowlisting.
