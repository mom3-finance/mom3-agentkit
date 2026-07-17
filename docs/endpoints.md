# Mom3 AgentKit Endpoint Guide

Mom3 AgentKit adalah FastAPI service untuk live yield catalog, market intelligence, portfolio analysis, chat, dan semantic execution intent. Endpoint ini tidak menyimpan private key dan tidak mengirim transaksi blockchain.

Base URL lokal: `http://localhost:8001`

FastAPI menyediakan schema terbaru secara otomatis:

- Swagger UI: `http://localhost:8001/docs`
- OpenAPI JSON: `http://localhost:8001/openapi.json`

Dokumen ini menjelaskan kontrak integrasi yang dipakai frontend dan backend. Untuk schema Pydantic paling lengkap, gunakan OpenAPI JSON dari process AgentKit yang sedang berjalan.

## System dan market read

| Method | Endpoint | Parameter | Respons utama |
| --- | --- | --- | --- |
| `GET` | `/health` | - | Status service, versi, chain MVP, protocol execution, status/model LLM. |
| `GET` | `/api/network-info` | `chain_id?` | Informasi chain, status MVP/Aave, dan LLM. |
| `GET` | `/api/yield-markets` | `chain_id?`, `execution_only?` | Catalog live market dengan `markets`. |
| `GET` | `/api/yield-markets/:marketId/chart` | Path `marketId` | Titik chart APY DefiLlama untuk pool. |
| `GET` | `/api/yield-markets/:marketId/position` | `user_address` wajib | Posisi on-chain user untuk market. |
| `GET` | `/api/market/aave` | `chain_id?`, default `42161` | On-chain Aave reserve read. |
| `GET` | `/api/yield-forecast` | `chain_id?` | Forecast APY market. |
| `GET` | `/api/liquidity-pulse` | `chain_id?` | Sinyal kesehatan likuiditas protocol. |

Gunakan `chain_id` dalam snake_case. Chain MVP saat ini Arbitrum One (`42161`) dan Base (`8453`).

Contoh:

```powershell
Invoke-RestMethod 'http://localhost:8001/api/yield-markets?chain_id=42161&execution_only=true'
Invoke-RestMethod 'http://localhost:8001/api/yield-markets/<market-id>/position?user_address=0x0000000000000000000000000000000000000000'
```

Response catalog memiliki bentuk umum berikut:

```json
{
  "timestamp": "2026-07-15T00:00:00Z",
  "chain_id": 42161,
  "markets": [
    {
      "market_id": "...",
      "pool_id": "...",
      "protocol": "Aave V3",
      "symbol": "USDC",
      "chain": "Arbitrum One",
      "chain_id": 42161,
      "apy": 4.2,
      "tvl": 1000000,
      "execution": { "enabled": true }
    }
  ]
}
```

`market_id` dari catalog adalah ID yang harus dipakai untuk chart, position, dan execution intent. Jangan membuat ID market sendiri di client.

## Strategy, portfolio, chat, dan execution intent

| Method | Endpoint | Body | Keterangan |
| --- | --- | --- | --- |
| `POST` | `/api/ai/strategy` | `risk_tolerance?`, `chain_id?`, `user_address?` | Rekomendasi yield explainable dari catalog dan policy. |
| `POST` | `/api/portfolio/analyze` | `user_address` wajib, `wallet_assets?` | Analisis wallet dan position lintas protocol. |
| `POST` | `/api/chat` | `message` wajib, `history?`, `chain_id?`, `user_address?` | Chat AI dengan konteks market. |
| `POST` | `/api/ai/execution-intent` | `market_id`, `action`, `amount`, `user_address` wajib | Semantic intent untuk backend validation. |

### Strategy

```json
{
  "risk_tolerance": "moderate",
  "chain_id": 42161,
  "user_address": "0x0000000000000000000000000000000000000000"
}
```

`risk_tolerance` hanya menerima `conservative`, `moderate`, atau `aggressive`; defaultnya `moderate`.

### Portfolio analysis

```json
{
  "user_address": "0x0000000000000000000000000000000000000000",
  "wallet_assets": [
    {
      "id": "usdc-arbitrum",
      "symbol": "USDC",
      "name": "USD Coin",
      "balance": 25.5,
      "amount_in_usd": 25.5,
      "chain": "Arbitrum One",
      "chain_id": 42161,
      "token_address": "0x..."
    }
  ]
}
```

`user_address` harus EVM address valid. Seluruh field asset memiliki default, tetapi frontend sebaiknya mengirim data lengkap agar hasil analisis lebih baik. Maksimal 500 asset per request.

### Chat

```json
{
  "message": "Apa risiko supply USDC di Aave?",
  "history": [{ "role": "user", "content": "Saya memakai Arbitrum." }],
  "chain_id": 42161,
  "user_address": "0x0000000000000000000000000000000000000000"
}
```

`message` wajib memiliki 1--2000 karakter. `history` adalah array object percakapan dan diteruskan ke orchestration chat.

### Execution intent

```json
{
  "market_id": "<market-id-dari-api-yield-markets>",
  "action": "supply",
  "amount": "25.5",
  "user_address": "0x0000000000000000000000000000000000000000"
}
```

`action` hanya `supply` atau `withdraw`. AgentKit memvalidasi policy market dan amount, lalu mengembalikan semantic intent. Backend Mom3 harus memvalidasi intent ini kembali dan membuat calldata; client tidak boleh menganggap intent AgentKit sebagai transaksi siap broadcast.

## Status dan error

| Status | Arti |
| --- | --- |
| `200` | Request berhasil. |
| `404` | Market atau Aave chain tidak ditemukan / tidak didukung. |
| `422` | Input atau policy intent tidak valid; position juga dapat mengembalikan ini untuk address invalid. |
| `502` | Live data provider, chain read, atau service internal sementara tidak tersedia. |
| `503` | Strategy tidak dapat dibuat, misalnya provider reasoning yang dibutuhkan tidak tersedia. |

Error FastAPI mengikuti format `{"detail":"..."}`.

## CORS dan batas keamanan

`CORS_ORIGINS` menentukan origin yang boleh memanggil service. Default lokalnya `http://localhost:3000`. AgentKit mengizinkan method `GET`, `POST`, dan `OPTIONS`.

Service ini tidak memiliki autentikasi aplikasi dan tidak boleh dipublikasikan tanpa network access control atau API gateway yang sesuai. AgentKit tidak pernah menerima private key, membuat signature, atau menyiarkan transaksi. APY bersifat variabel; respons bukan jaminan return maupun nasihat finansial.

Lihat [Backend endpoints](https://github.com/mom3-finance/mom3-backend/blob/mom3-dev-test/docs/endpoints.md) untuk execution boundary dan [Frontend endpoints](https://github.com/mom3-finance/mom3-frontend/blob/mom3-dev-test/docs/endpoints.md) untuk BFF endpoint yang dipakai browser.
