# mom3 Agentkit

Mom3 Agentkit is the non-custodial AI and market-intelligence service behind Mom3. It discovers live yield markets, applies an MVP safety policy, recommends an explainable allocation, and prepares an allowlisted execution intent. Particle Universal Account and the user's EOA remain responsible for authorization and transaction submission.

```text
DefiLlama live pools
        ↓
MVP market policy
  stablecoin + single exposure + TVL threshold + protocol allowlist
        ↓
Mom3 Agent
  market catalog + forecast + liquidity health + strategy
        ↓
Execution intent
  exact chain + asset + Aave calls + amount policy
        ↓
Next.js → Particle Universal Account → EIP-7702 user approval
```

## MVP scope

Execution is intentionally limited to two tested routes:

- USDC supply to Aave V3 on Base.
- USDC supply to Aave V3 on Arbitrum.

Compound V3 and Morpho Blue are available for research/discovery only. They are not returned as executable strategies until Mom3 has a dedicated, verified transaction adapter for them.

## Structure

```text
app/
  api/
    routes.py                 FastAPI request/response boundary
  core/
    config.py                 Environment-backed application settings
  modules/
    agent_core/
      agent.py                Strategy, forecast, pulse, and chat orchestration
      execution.py            Allowlisted execution-intent builder
    market_intelligence/
      catalog.py              Live market curation and scoring
      policy.py               MVP protocol/chain/asset policy
    portfolio_intelligence/
      engine.py               Multi-factor portfolio indicators
      service.py              Parallel cross-protocol position scan
  services/                   Reusable data and algorithm adapters
  main.py                     Thin framework entrypoint
tests/
  test_market_catalog.py
  test_execution_intent.py
```

Framework entrypoints call business modules; business modules use data/algorithm services. Wallet signing never enters the Python service.

## Run

Use Python 3.12 for local development. The current FastAPI and Pydantic stack in this repo is not ready for Python 3.14 yet, and running it there can fail with `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`.

```powershell
cd mom3-agentkit
Remove-Item -Recurse -Force .venv
python3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001
```

If `python3.12` is not available on your machine yet, install Python 3.12 first and then recreate `.venv` with that interpreter before installing dependencies.

Configure the Next.js app:

```env
MOM3_AGENTKIT_URL=http://localhost:8001
```

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Service and MVP capability status |
| `GET` | `/api/yield-markets` | Curated live discovery catalog |
| `POST` | `/api/ai/strategy` | Executable cross-chain USDC strategy |
| `POST` | `/api/portfolio/analyze` | Wallet + live position portfolio intelligence |
| `POST` | `/api/ai/execution-intent` | Validate amount/market and prepare Aave calls |
| `GET` | `/api/yield-forecast` | Fast forecast from DefiLlama trend fields |
| `GET` | `/api/liquidity-pulse` | Market-depth and APY-trend health signal |
| `POST` | `/api/chat` | Context-aware AI explanation |
| `GET` | `/api/market/aave` | Optional direct on-chain Aave reserve read |

Quick checks:

```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod 'http://localhost:8001/api/yield-markets?execution_only=true'
Invoke-RestMethod http://localhost:8001/api/ai/strategy -Method Post -ContentType 'application/json' -Body '{"risk_tolerance":"moderate","chain_id":42161}'
```

## Safety boundary

- The agent never stores a user private key.
- Only allowlisted markets can create execution intents.
- An intent is not a transaction and cannot move funds.
- The frontend verifies the intent against the connected Universal Account.
- Particle creates the cross-chain quote and the user signs it.
- APY is variable and no response represents guaranteed return or financial advice.

See [ARCHITECTURE.md](ARCHITECTURE.md), [MVP_YIELD_RESEARCH.md](MVP_YIELD_RESEARCH.md), and [AI_FEATURES_DOCUMENTATION.md](AI_FEATURES_DOCUMENTATION.md).

## Production operations

Deployment, readiness checks, smoke tests, observability, rollback, and
security acceptance criteria are maintained in
[docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md). The machine-readable
contract is available at `/openapi.json` when the service is running; the human
endpoint guide is [docs/endpoints.md](docs/endpoints.md).
