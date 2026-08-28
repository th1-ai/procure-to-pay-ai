# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

This agent does **not** use a PMS or WhatsApp/chat messaging - `make doctor`
still prints a `pms adapter` and `messaging adapter` line (every repo in this
family runs the same generic health check), but they are not relevant here.
Only `email` and `sheets` matter, plus the three small readers below.

## Email - `systems.email.adapter`

Used for every send this agent makes: a vendor query on a held invoice, a
vendor confirmation once a PO is created, an approval-chase reminder, and a
letter of award.

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Logs to `data/exports/sent_email.jsonl`. What `make demo` uses. |
| `imap` | universal | mailbox + app password | Any provider. **Start here.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

**`imap`.** In `.env`:
```
EMAIL_ADDRESS=accounts-payable@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
```

**`gmail`.** Google Cloud Console: enable the Gmail API, configure the
consent screen, create an OAuth client of type **Desktop app**, download the
JSON to `credentials.json`. Then
`pip install google-api-python-client google-auth-oauthlib` and run
`make doctor`; a browser opens once and writes `token.json`.

**Who a send goes to.** `config/agent.yaml: vendor_emails` (invoice queries,
vendor confirmations, award letters) and `approver_emails` (approval chases)
map a name/role to an address. Both ship blank - a send with no address on
file is refused with the approval kept, never guessed.

## Sheets - `systems.sheets.adapter`

Where the payment-batch log and `tools/report.py`'s numbers can be exported.

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Writes `data/exports/<sheet>.csv`. |
| `google` | built | service account JSON | A live shared spreadsheet. |

For `google`: enable the Sheets API, create a service account and a JSON
key, save it as `service_account.json`, and share your spreadsheet with the
service account's email as an Editor. Set `systems.sheets.spreadsheet_id`.

## Purchase orders, requisitions, invoices and goods receipts

None of these four has a core adapter yet -
`core.adapters.get_stub("procurement", settings)` in `core/adapters/base.py`
is a pure interface, with no fixture-backed data behind it. This repo ships
three small readers in `tools/readers.py` instead, the same shape
`finance-filing-ai`'s `tools/po_ledger.py` uses for the same reason.

| Config key | `mock` (default) | `csv` |
|---|---|---|
| `po_reader.adapter` | `fixtures/hotel/purchase-orders.json`, seeded once | `data/imports/purchase_orders.csv` |
| `invoice_reader.adapter` | `fixtures/inbound/invoices/*.json` | `data/imports/invoices.csv` |
| `receipt_reader.adapter` | `fixtures/inbound/goods-receipts/*.json` | `data/imports/goods_receipts.csv` |

Requisitions have no separate `adapter` key - they follow `po_reader.adapter`
(`fixtures/inbound/requisitions/*.json` on `mock`,
`data/imports/requisitions.csv` on `csv`).

**CSV columns:**
- `purchase_orders.csv`: `po_ref, vendor, amount_eur, description, received`
- `requisitions.csv`: `id, title, department, requested_by, vendor, amount_eur, budget_line`
- `invoices.csv`: `id, vendor, invoice_no, amount_eur, currency, po_ref, description`
- `goods_receipts.csv`: `id, po_ref, note`

**Invoices are already structured on purpose.** This agent does not read a
raw email or PDF and does not extract fields with a model - that is
Finance Filing AI's job (`finance-filing-ai`, "The Bookkeeper"). Export
already-extracted invoices from that agent's finances sheet, from your own
ERP/AP system, or maintain `invoices.csv` by hand for a small property. See
`docs/how-it-works.md`, "Where this agent starts and stops".

**Real ERP integration.** If your procurement or accounting system has an
API, ask the hotel's Claude session:

> Read `docs/integrations.md` and `tools/readers.py`. I want purchase
> orders and invoices read from **<your system>** instead of CSV. Its API
> docs are at **<url>** and I have credentials in `.env` as `<VAR names>`.
> Copy the shape of `read_purchase_orders`/`read_invoices` in
> `tools/readers.py`, add a `<system>` branch alongside `mock`/`csv`, and
> stop before wiring it into `tools/run.py` so I can check the reads first.

## Everything else

`pos`, `accounting`, `reviews`, `calendar`, `payments`, `procurement` and
`locks` are **stubs**: the interface exists, nothing is implemented. The
`payments` family is deliberately never going to be implemented in this
repo - see `docs/safety.md`. If you need one of the others, use the recipe
below.

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose, and your Claude Code session can do this
with you in an afternoon. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need an email adapter for **<your system>**. Its API docs are at
> **<url>** and I have credentials in `.env` as `<VAR names>`. Copy
> `core/adapters/email_imap.py` as the shape, implement `ping`,
> `capabilities`, `fetch_unread` and `send` first, register it in
> `core/adapters/__init__.py`, and stop before anything else so I can check
> the reads with `make doctor`.

### The five steps

**1. Copy the closest existing adapter.** `core/adapters/email_imap.py` for
a mailbox, `sheets_csv.py` for a spreadsheet export.

**2. Implement `ping()` and `capabilities()` first.**
```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```
`make doctor` reads both.

**3. Implement the reads.** Map the vendor's fields onto the dataclasses in
`core/adapters/base.py` (`EmailMessage`). Money is a float in the hotel's
currency.

**4. Implement the writes, each with the guard.**
```python
from core.adapters.base import guarded_write

@guarded_write("send_email")
def send(self, to, subject, body_md, **kwargs) -> dict:
    ...
```
The decorator is not optional. Without it your adapter can write while the
agent is in shadow mode, which defeats the entire safety model.

**5. Register it.** One line in `core/adapters/__init__.py`:
```python
REGISTRY["email"]["yoursystem"] = "core.adapters.email_yoursystem:YourSystemEmail"
```
Then set `systems.email.adapter: yoursystem` in `config/hotel.yaml` and run
`make doctor`.

### Rules that matter

- **`ping()` never raises.** It returns `HealthCheck(ok=False, ...)` with a
  hint. A broken adapter must still produce a readable doctor table.
- **Every write is decorated.** No exceptions.
- **Never log a credential.** `core/log.py` masks anything whose key looks
  like a secret, but do not rely on it.
- **Write a test.** Copy `tests/test_core_adapters_mock_csv.py`. It should
  run with no network: feed your parser a fixture, check the dataclass that
  comes out.

### `core/` is shared

`core/` is identical in all 28 agents in this family. If you change
something in `core/`, keep it generic - a hotel-specific tweak belongs in
`tools/` or in your own adapter file, not in the shared runtime.
