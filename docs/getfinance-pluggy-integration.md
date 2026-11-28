---
topic: GetFinance ↔ Pluggy API Integration
read_when: Onboarding a new bank/account, debugging the GetFinance fetch, or when Pluggy item/account IDs change after a reconnect
last_synced_at: "2026-06-04"
related_issues: [df-6j6, df-1f1]
---

# GetFinance ↔ Pluggy Integration

How GetFinance ingests bank data from the [Pluggy](https://pluggy.ai) API, the shape of
that data, and — most importantly — **how to link an account to its stable identity so
that Pluggy's churning UUIDs stop forcing manual re-mapping on every reconnect.**

> All facts here are verified against the **live API** (sandbox `MeuPluggy` connector,
> 2026-06-04) and the official docs. AI agents: Pluggy publishes an LLM-friendly index at
> <https://docs.pluggy.ai/llms.txt> (every page has a `.md` twin; endpoints are in OpenAPI).

Source of truth in code: `workspaces/GetFinance/scripts/functions/functions.py` (API client)
and `workspaces/GetFinance/scripts/landing/landing2raw.py` (fetch loop).

---

## 1. Authentication

Two token types, both derived from the dashboard `CLIENT_ID` / `CLIENT_SECRET`:

| Token | How | TTL | Purpose |
|-------|-----|-----|---------|
| **API Key** | `POST /auth` `{clientId, clientSecret}` → `{apiKey}` | **2 h** | Backend. Read all product data; create connect tokens; CRUD items; configure webhooks; review connectors. **This is what GetFinance uses.** |
| **Connect Token** | `POST /connect_token` (needs an API Key) | 30 min | Frontend only (the Connect widget). **Cannot read product data.** |

Every data request sends the API Key in the `X-API-KEY` header. GetFinance caches the
token for 7200 s (`functions.get_api_token`). Credentials live in
`workspaces/GetFinance/.env`:

```
CLIENT_ID=...
CLIENT_SECRET=...
ITEM_IDS=<item-uuid>[,<item-uuid>...]   # comma-separated; one entry per connection
```

---

## 2. Endpoints GetFinance calls

| Endpoint | Wrapper in `functions.py` | Notes |
|----------|---------------------------|-------|
| `POST /auth` | `get_api_token()` | 2 h cache |
| `GET /items/{id}` | (probe only) | item status + `connector` block |
| `GET /accounts?itemId={id}` | `list_accounts(item_id)` | all accounts for one connection |
| `GET /transactions?accountId={id}&pageSize=500&page=N` | `list_transactions(account_id)` | ⚠️ **deprecated** (page-based) — removed after **2026-12-31**; migrate to `GET /v2/transactions` (see below). Loops until `len ≥ total` |
| `GET /investments?itemId={id}&pageSize=500&page=N` | `list_investments(item_id)` | paginated |

The fetch loop (`landing2raw.py`) is: **for each `item_id` in `ITEM_IDS`** → list accounts →
per account list transactions; then list investments. Empty results are tolerated (an empty
account contributes nothing downstream).

### ⚠️ Transactions pagination is deprecated — migrate to `GET /v2/transactions` (df-1f1)

`list_transactions` uses the **page-based** `GET /transactions?…&page=N`. Pluggy has
**deprecated** this endpoint and will **remove it after 2026-12-31** in favour of
**cursor-based** `GET /v2/transactions`. The new contract:

- Request: `GET /v2/transactions?accountId={id}&pageSize=500` (500 is the max/default).
- Response: `{ "results": [...], "next": "<query-string>" | null }`.
- Loop: follow `next` (a ready-made query string) until it is `null` — do **not** compute
  `page=N` or compare `len ≥ total`. (`GET /investments` is still page-based today.)

### Related endpoints (available, not yet wired)

| Endpoint | What it gives | Why GetFinance might want it |
|----------|---------------|------------------------------|
| `GET /accounts/{id}/balance` | Real-time balance straight from the bank, **without** a full item sync (`{balance, blockedBalance, automaticallyInvestedBalance, currencyCode, updateDateTime}`) | Point-in-time liquidity between syncs — see `fact_balance_*`. Shares the institution's rate-limit quota (§7). |
| `GET /identity?itemId={id}` | Authoritative holder identity (`document`, `taxNumber`, `birthDate`, full name, emails, phones) | Stronger natural key when account-level `owner`/`taxNumber` are null (§4). |
| `GET /investments/{id}/transactions` | Per-holding investment movements (buys/sells/yield) | GetFinance currently captures only positions (U7), not movements. |

---

## 3. The Account object (full field reference)

Captured live. **Stable** = survives a delete+re-create of the connection (it reflects the
real-world account); **Churns** = a fresh Pluggy UUID is minted on re-create.

| Field | Example (Itaú checking; made-up values) | Stability | Meaning |
|-------|-----------------------------------------|-----------|---------|
| `id` | `11111111-…` | **Churns** | Pluggy account UUID |
| `itemId` | `22222222-…` | **Churns** | Pluggy connection UUID |
| `type` | `BANK` / `CREDIT` | Stable | top-level kind |
| `subtype` | `CHECKING_ACCOUNT` / `CREDIT_CARD` | Stable | (not carried past raw — A17) |
| `number` | `00012345-6` (bank) / `1234` (card last-4) | **Stable** | account / card number |
| `name` | `itau` | semi | connector-supplied label |
| `marketingName` | `null` | semi | product name |
| `balance` | `1000.00` | n/a | current balance |
| `currencyCode` | `BRL` | Stable | |
| `taxNumber` | `123.456.789-00` | **Stable** | holder CPF/CNPJ (often null) |
| `owner` | `FULANO DE TAL` | **Stable** | account holder name |
| `bankData.transferNumber` | `341/0001/00012345-6` | **Stable** | **`bankCode/agency/account-digit`** (BANK only) |
| `bankData.closingBalance` | `1000.00` | n/a | |
| `bankData.automaticallyInvestedBalance` | `1000.00` | n/a | auto-applied balance |
| `bankData.overdraft*` | `500 / 0 / 0` | n/a | cheque-especial limits |
| `creditData` | `{level, brand, creditLimit, availableCreditLimit, balanceDueDate, minimumPayment, disaggregatedCreditLimits[], …}` | n/a | CREDIT only; U4 parses `availableCreditLimit` |

The `itemId` also carries a **`connector`** block (from `GET /items/{id}`):
`connector.id` (int, **stable per institution**), `connector.name`, `connector.type`
(`PERSONAL_BANK`…), `connector.country`.

---

## 4. ⭐ ID churn and stable linking — the core problem

**Pain:** every time a connection is deleted and re-created, `item_id` and every
`account.id` change, forcing a manual edit of `config/institutions.yaml`.

### When do IDs actually change?

| Event | Trigger | item_id / account_id |
|-------|---------|----------------------|
| Credential refresh / MFA | `PATCH /items/{id}` with new creds (item was `LOGIN_ERROR` / `WAITING_USER_INPUT`) | **Preserved** — same item, same accounts |
| Delete + re-create | `DELETE /items/{id}` then connect again | **New UUIDs** — old data permanently wiped |
| Sandbox idle cleanup | sandbox item `updatedAt` > **30 days** → auto-deleted | **New UUIDs** on re-create |
| Connector deprecated | 30-day window, then auto-deleted | **New UUIDs** on re-connect to a new connector |

So a normal reconnect that *just refreshes credentials keeps the IDs*. Churn happens on
**delete-and-recreate** (and the sandbox 30-day auto-delete is the most likely cause for
this project). In every churn case the **real bank account is unchanged**, so its
natural-identity fields stay identical.

### The stable natural key (answer to "link independent of ID")

| Account type | Recommended natural key | Why stable |
|--------------|-------------------------|-----------|
| **BANK** | `bankData.transferNumber` → `bankCode/agency/account` | It *is* the real-world account coordinates. Globally unique. |
| **CREDIT** | `(connector.id, number)` — i.e. institution + card last-4 (optionally `+ owner`) | Cards expose no agency; last-4 + institution is the stable handle |
| fallback | `(connector.id, number, owner)` | works when `bankData` is absent |

**The bank code is the institution, for free.** The first `transferNumber` segment is the
Brazilian COMPE code — verified live: `341`→Itaú, `077`→Inter. Common codes:

| COMPE | Bank | COMPE | Bank |
|------|------|------|------|
| 001 | Banco do Brasil | 237 | Bradesco |
| 033 | Santander | 260 | Nubank (Nu Pagamentos) |
| 077 | Banco Inter | 290 | PagBank |
| 104 | Caixa Econômica | 336 | C6 Bank |
| 212 | Banco Original | 341 | Itaú Unibanco |

> Caveat: `transferNumber` format and population **vary by connector** (some return only
> `agency/account`, some omit `bankData` entirely). Parse defensively and fall back to
> `connector.id` for the institution. Confirm against real (non-sandbox) connectors before
> relying on it for every bank.

When the account-level `owner` / `taxNumber` come back null (common), `GET /identity?itemId={id}`
is the authoritative source for the holder: it returns `document`, `taxNumber`, `birthDate`,
the full name, and contact info for the whole item. Use it to enrich the natural key rather
than trusting the per-account fields.

### Proposed redesign (not yet implemented)

Re-key `config/institutions.yaml` (and `investments.yaml`) by the **natural key** instead of
`pluggy_account_id`, and resolve the surrogate `account_uuid` from it. Then on any churn the
new `account.id` auto-relinks to the same `account_uuid` (history in the curated Delta
tables continues seamlessly) and the institution derives from the bank code / `connector` —
**no manual YAML edit needed**. The only thing that still must be captured per connection is
the new `item_id` (see §5).

---

## 5. You cannot list items (must persist item_ids yourself)

Official: *"Listing existing connections is not provided due to security reasons. We request
all our customers to track all their connections in the datasource referencing the `itemId`
of Pluggy."* Confirmed live — `GET /items` (no id) → **401**.

So `ITEM_IDS` (the persisted list) is unavoidable. Ways to capture a new `item_id` without
hand-copying:

- **Connect widget `onSuccess`** returns the created item — capture it there.
- **Webhooks**: subscribe to `item/created` (also `item/updated`, `item/deleted`,
  `item/login_succeeded`) by passing `webhookUrl` when creating the connect token / item, or
  via `POST /webhooks`. Pluggy `POST`s JSON; you must reply `2xx` within 5 s (retries up to 9×).
- **Reality for this local tool**: no public HTTPS endpoint → webhooks are impractical;
  capture the `item_id` once from the dashboard / widget and append it to `ITEM_IDS`. The
  natural-key linking (§4) removes the *institutions.yaml* churn, which is the bulk of the pain.

`clientUserId` can tag items at creation (carried from the connect token), but does **not**
enable listing them.

---

## 6. Item lifecycle & statuses

`item.status` (health) + `item.executionStatus` (last run detail):

| status | executionStatus | meaning / action |
|--------|-----------------|------------------|
| `UPDATED` | `SUCCESS` | all products fetched |
| `UPDATED` | `PARTIAL_SUCCESS` | some products failed — see `executionReport` |
| `OUTDATED` | `ERROR` | fatal; nothing retrieved |
| `LOGIN_ERROR` | — | bad/expired creds → must `PATCH /items/{id}` with new creds |
| `WAITING_USER_INPUT` | — | MFA / device challenge pending |
| `UPDATING` | — | in progress |

Deletion always emits the `item/deleted` webhook. Open Finance **consents** expire (default
~12 months) and must be renewed via reconnect (`GET /consents?itemId=` to inspect).

---

## 7. Data freshness, rate limits & partial data

GetFinance's "daily" job *reads* stored product data — it never triggers a bank sync. So the
freshness of what you fetch depends entirely on **when the item last synced**, and large
backfills can hit rate limits. Three realities to design around:

### Freshness — who re-syncs the item?

Stored data is only as fresh as the item's last sync. An item re-syncs two ways:

- **Auto-sync** (Pluggy, automatic): every **24 / 12 / 8 h** depending on subscription,
  Pluggy collects the last few days of data and appends it. **Production-only premium
  feature.** `GET /items/{id}.nextAutoSyncAt` shows the next run (**`null` = no auto-sync**).
  On `LOGIN_ERROR` the item is dropped from auto-sync until reconnected; other errors retry
  hourly up to 5×, then drop.
- **Manual** `PATCH /items/{id}`: triggers a fresh sync (optionally updating credentials).
  Rate-limited (20/min). Pluggy explicitly says **don't** build a batch `PATCH` loop for
  daily refresh — use auto-sync instead.

> **Implication:** if the connected item has **no auto-sync** (sandbox, or a non-Production
> app → `nextAutoSyncAt` is `null`), a daily GetFinance run just re-reads the *same* stored
> data. Real freshness then depends on a manual reconnect / `PATCH`. Confirm `nextAutoSyncAt`
> before assuming the daily fetch sees new transactions.

### Rate limits

Per-IP, per-minute, counted independently per endpoint; exceeding → `429 Too Many Requests`.

| Endpoint | Max req/min |
|----------|-------------|
| `POST /auth` | 360 |
| `GET /transactions` (+ `/{id}`) | 360 |
| `GET /investments` (+ `/{id}`) | 360 |
| `GET /investments/{id}/transactions` | 360 |
| `PATCH /items` | 20 (user-triggered updates only) |

A `429` carries `RateLimit-Limit`, `RateLimit-Reset` (seconds until reset), and `Retry-After`
(always `60`); the counter clears after one minute. `GET /accounts/{id}/balance` (real-time
balance) shares the **institution's** sync quota, not these. GetFinance is low-volume today,
but `list_transactions` paginates with **no backoff** — a multi-account, multi-page backfill
can approach 360/min. If a fetch returns `429`, sleep `Retry-After` and retry.

### Partial data — warnings vs. errors

A *successful* response can still be incomplete. Pluggy attaches a **`statusDetail`** per
product with warnings shaped `{code, message, providerMessage?}` — e.g. `ACCT_006` (>260
accounts hard cap), `ACCT_001/002/003/004` (missing permission / pending / temporarily
unavailable / unavailable). **Errors** mean the whole request failed (no data); **warnings**
mean it succeeded but some data is missing. GetFinance treats empty results as benign, but a
warning is different: the data exists, Pluggy just couldn't collect it this run — worth
logging rather than silently dropping.

---

## 8. Connectors

`GET /connectors` lists every institution; `GET /connectors/{id}` for one. `connector.id` is
stable. **Direct** connectors (scraping/sandbox) — e.g. `MeuPluggy` sandbox = **200**.
**Open Finance** (regulated) connectors have id **≥ 601** (Itaú OF = 601) and use an OAuth
redirect flow (create item → open returned URL → user logs in at the bank → callback).

---

## 9. How GetFinance onboards a new bank *today* (current manual process)

Three coordinated edits (until the §4 redesign lands):

1. `workspaces/GetFinance/.env` → append the new `item_id` to `ITEM_IDS`.
2. `config/institutions.yaml` → add `institutions:` `"<item_id>": <Bank Name>`.
3. `config/institutions.yaml` → add `accounts:` `"<pluggy_account_id>": {account_uuid, alias}`.
   (Investments: `config/investments.yaml`, keyed by `pluggy_holding_id`.)

Accounts/holdings **not** mapped are quarantined into `cleansed.accounts_unmapped` /
`cleansed.investments_unmapped` (anti-join in `cleansed2curated_accounts.py`) and never reach
curated/facts — a *mapped* account whose `itemId` is missing from `institutions:` instead
fails the job loudly. See `config/institutions.yaml` (the LOCKED source-of-truth header) for the
reconnect re-point convention.

---

## References

- Live evidence: account objects of one Itaú item and one Inter item, 2026-06-04.
- Pluggy docs: [Auth](https://docs.pluggy.ai/reference/auth) ·
  [Item](https://docs.pluggy.ai/docs/item) ·
  [Item lifecycle](https://docs.pluggy.ai/docs/item-lifecycle) ·
  [Auto-sync](https://docs.pluggy.ai/docs/item#auto-sync) ·
  [Creating an Item](https://docs.pluggy.ai/docs/creating-an-item) ·
  [Webhooks](https://docs.pluggy.ai/docs/webhooks) ·
  [Transactions v2 (cursor)](https://docs.pluggy.ai/reference/transactions-list-by-cursor) ·
  [Real-time balance](https://docs.pluggy.ai/docs/real-time-balance) ·
  [Identity](https://docs.pluggy.ai/reference/identity-find-by-item) ·
  [Rate limits](https://docs.pluggy.ai/docs/rate-limits) ·
  [Warnings & status codes](https://docs.pluggy.ai/docs/warnings-status-codes) ·
  [Accounts coverage](https://docs.pluggy.ai/docs/accounts-coverage) ·
  [llms.txt index](https://docs.pluggy.ai/llms.txt)
- Code: `scripts/functions/functions.py`, `scripts/landing/landing2raw.py`,
  `scripts/cleansed2curated/cleansed2curated_accounts.py`, `config/institutions.yaml`.
</content>
</invoke>
