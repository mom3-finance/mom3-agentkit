# AgentKit Production Readiness

Dokumen ini adalah contract operasional untuk deployment Mom3 AgentKit. AgentKit
adalah service FastAPI stateless yang melakukan market intelligence dan membuat
semantic execution intent. AgentKit tidak menyimpan private key, tidak signing,
dan tidak broadcast transaksi.

## Arsitektur produksi

```mermaid
flowchart LR
  FE[Next.js BFF] -->|HTTPS internal/API gateway| AK[AgentKit FastAPI]
  BE[Mom3 Backend] -->|market ingest + intent validation| AK
  AK --> LL[DefiLlama / market provider]
  AK --> RPC[Read-only EVM RPC]
  AK --> DB[(MongoDB pool catalog/history)]
  AK -. optional .-> LLM[OpenAI-compatible LLM]
  AK --> H[/health + /docs]
  FE -->|sign only| UA[Particle Universal Account]
```

Boundary penting:

- Frontend mengelola session, quote, review, delegation, signature, dan UX.
- Backend adalah execution boundary terakhir: allowlist, amount, receiver, dan
  calldata harus divalidasi ulang di sana.
- AgentKit hanya mengembalikan data market, analisis, dan intent yang belum
  ditandatangani.

## Runtime contract

| Item | Production rule |
| --- | --- |
| Runtime | Python 3.12; gunakan image `python:3.12-slim` |
| Port | `8001` di jaringan internal |
| Entrypoint | `python -m uvicorn app.main:app --host 0.0.0.0 --port 8001` |
| Readiness | `GET /health`; status `200` harus berarti dependency minimum siap |
| API schema | `/openapi.json`; Swagger hanya dibuka lewat jaringan internal |
| State | Tidak ada private-key atau transaction state di filesystem lokal |
| Access | Batasi dengan private network/API gateway; service tidak memiliki app auth |

## Environment dan secret policy

Gunakan `.env.example` sebagai daftar konfigurasi. Secret production wajib
berasal dari secret manager atau environment deployment, bukan Git.

- `AGENT_APIKEY`, RPC credential, MongoDB URI, dan `MARKET_INGEST_TOKEN` adalah
  secret/server-only.
- `CORS_ORIGINS` harus berisi origin production yang eksplisit, bukan `*`.
- `MVP_MAX_INTENT_AMOUNT_USD`, `MVP_MIN_TVL_USD`, dan `MVP_MAX_APY` adalah
  policy variables dan harus direview sebelum diubah.
- Token internal AgentKit dan backend harus sama secara byte-for-byte, tetapi
  jangan menaruh nilainya di frontend.

## Deploy checklist

1. Pastikan Python 3.12 dan dependency lock/install berhasil.
2. Isi environment production dari secret manager dan validasi chain/RPC.
3. Jalankan unit test: `python -m pytest -q`.
4. Build image dan jalankan container dengan port internal `8001`.
5. Cek `GET /health`, `/openapi.json`, dan `GET /api/yield-markets?execution_only=true`.
6. Cek backend dapat memanggil catalog dan intent AgentKit menggunakan token
   internal yang benar.
7. Publikasikan hanya melalui gateway yang memberi TLS, access control, timeout,
   dan request-size limit.
8. Simpan image tag, commit SHA, environment version, dan hasil smoke test.

## Smoke test produksi

```powershell
$base = 'https://agentkit.example.internal'
Invoke-RestMethod "$base/health"
Invoke-RestMethod "$base/api/yield-markets?execution_only=true"
Invoke-RestMethod "$base/api/yield-forecast?chain_id=42161"
```

Untuk intent, gunakan wallet address dummy yang valid dan amount kecil pada
environment staging. Jangan menjalankan broadcast dari AgentKit; keberhasilan
smoke test hanya berarti validasi intent dan data read berfungsi.

## Observability dan failure modes

Log wajib memuat timestamp, route, status, latency, request/correlation ID,
chain ID, dan dependency yang gagal. Jangan log API key, full wallet payload,
atau data sensitif.

| Signal | Meaning | Action |
| --- | --- | --- |
| `/health` non-200 | Dependency minimum belum siap | cek Mongo/RPC/env/container logs |
| `502` | Provider/read internal gagal | retry dengan backoff; cek provider quota |
| `503` | Strategy dependency tidak tersedia | degrade ke deterministic path atau page operator |
| catalog kosong | policy/provider/sync bermasalah | cek market sync dan `MARKET_INGEST_TOKEN` |
| intent ditolak | market/action/amount tidak sesuai policy | jangan bypass; cek allowlist dan contract address |

Tambahkan alert untuk health failure berulang, catalog kosong, latency p95,
provider error rate, dan perubahan policy/allowlist.

## Rollback dan perubahan policy

Gunakan immutable image tag berbasis commit SHA. Rollback berarti mengembalikan
image tag terakhir yang sehat, memeriksa `/health`, lalu mengulang smoke test.
Perubahan protocol, address, decimals, atau execution policy wajib melalui
review kode + test + staging small-value test sebelum allowlist production.

## Security acceptance criteria

- [ ] Tidak ada private key, seed phrase, atau signer di proses AgentKit.
- [ ] Execution market terpisah jelas dari discovery market.
- [ ] Semua address/decimals berasal dari policy terverifikasi.
- [ ] CORS dan network ingress dibatasi.
- [ ] Dependency dan image dipindai pada CI.
- [ ] Error eksternal tidak membocorkan secret atau stack trace ke client.
- [ ] APY dan forecast selalu diperlakukan sebagai estimasi variabel.
