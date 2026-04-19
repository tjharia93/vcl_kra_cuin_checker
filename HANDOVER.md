# VCL KRA Validation — Developer Handover

This document tells a Frappe developer everything they need to push this app to
GitHub and deploy it to the Vimit Converters Frappe Cloud bench. The app is
already fully scaffolded at `/opt/vcl/apps/vcl_kra_validation/` on the VCL
server. Read `README.md` first for a functional overview.

---

## 1. Target environment

- **Frappe Cloud site:** `https://vimitconverters.frappe.cloud`
- **Frappe version:** 16.15.0
- **ERPNext version:** 16.14.0
- **Python:** 3.10+
- **App requires:** `frappe`, `erpnext` (already installed on the bench)

No additional pip packages are needed — `requests` is already part of the
Frappe bench Python environment.

## 2. What the app does

When a user enters the **Supplier Invoice No.** (`bill_no`) on a Purchase
Invoice form, a Client Script calls a whitelisted Python endpoint
(`vcl_kra_validation.api.validate_cuin`). That endpoint POSTs the CUIN to the
public KRA iTax invoice checker:

```
POST https://itax.kra.go.ke/KRA-Portal/middlewareController.htm?actionCode=fetchInvoiceDtl
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
Referer: https://itax.kra.go.ke/KRA-Portal/invoiceNumberChecker.htm?actionCode=loadPageInvoiceNumber
X-Requested-With: XMLHttpRequest
Accept: application/json, text/javascript, */*; q=0.01
User-Agent: Mozilla/5.0 ...

Body: invNo=<CUIN>
```

KRA responds with JSON:

```json
{
  "traderSystemInvNo": "123908",
  "mwInvNo": "0190438130000017933",
  "totalInvAmt": 114121.96,
  "invDate": "19/03/2026",
  "supplierName": "AWANAD ENTERPRISES LIMITED",
  "taxableAmt": 98381,
  "taxAmt": 15740.96,
  "buyerName": "VIMIT CONVERTERS LIMITED",
  "buyerPIN": "P000606160U",
  "invTransmissionDt": "19/03/2026 11:08:57",
  "invCategory": "Tax Invoice",
  "invType": "Original",
  "errorDTO": {}
}
```

For invalid CUINs, `errorDTO.msg` is populated. This app maps that response
into four read-only custom fields on Purchase Invoice:

| Custom fieldname              | Label                 | Source in KRA JSON     |
|-------------------------------|-----------------------|------------------------|
| `custom_kra_supplier_name`    | KRA Supplier Name     | `supplierName`         |
| `custom_kra_invoice_number`   | KRA Invoice Number    | `traderSystemInvNo`    |
| `custom_kra_tax_amount`       | KRA Tax Amount        | `taxAmt`               |
| `custom_kra_total_amount`     | KRA Total Amount      | `totalInvAmt`          |

The Client Script also:
- Shows a **red `msgprint`** if the KRA `buyerPIN` is not Vimit Converters'
  PIN `P000606160U`. (This means the invoice was issued to someone else —
  do not accept it.)
- On `validate` (before save) compares `frm.doc.grand_total` to
  `custom_kra_total_amount` and `frm.doc.total_taxes_and_charges` to
  `custom_kra_tax_amount`; shows an **orange `msgprint`** if they differ by
  more than KES 1. This is a **warning**, not a block — Phase 2 will convert
  it to `frappe.throw()` to hard-block submit.

### Known limitation

KRA CUIns that contain a `/` are routed by iTax to the newer eTIMS portal at
`https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsInvoiceData` with
the slash replaced by a dash. Those are **not** handled by this app — the
endpoint returns an error with message "CUINs containing '/' are not
supported...". If VCL starts receiving many slash-containing CUIns, the app
needs a second code path for the eTIMS portal.

## 3. Repo layout

```
vcl_kra_validation/
├── HANDOVER.md              ← this file
├── README.md
├── license.txt
├── pyproject.toml           ← flit build, modern Frappe convention
├── .gitignore
└── vcl_kra_validation/      ← the Python package
    ├── __init__.py          ← __version__ = "0.1.0"
    ├── hooks.py             ← registers fixtures
    ├── modules.txt          ← "VCL KRA Validation"
    ├── patches.txt
    ├── api.py               ← @frappe.whitelist() validate_cuin
    ├── config/__init__.py
    ├── templates/__init__.py
    ├── public/.gitkeep
    └── fixtures/
        ├── custom_field.json   ← 6 docs: section, 2 data fields, column break, 2 currency fields
        ├── client_script.json  ← single Client Script doc
        └── _client_script.js   ← SOURCE of the JS (regenerate client_script.json from this — see §6)
```

## 4. Git + GitHub push

1. Initialize git and make the first commit:

   ```bash
   cd /opt/vcl/apps/vcl_kra_validation
   git init -b main
   git add .
   git commit -m "Initial commit: KRA CUIN validation app v0.1.0"
   ```

2. Create the GitHub repo under the VCL org (private recommended). Then:

   ```bash
   git remote add origin git@github.com:<org>/vcl-kra-validation.git
   git push -u origin main
   ```

3. If the repo is private, add a Frappe Cloud deploy key:
   - On Frappe Cloud dashboard: **Bench Group → General → Deploy Key** (copy the public key).
   - On the GitHub repo: **Settings → Deploy Keys → Add deploy key** (paste, read-only).

## 5. Install on Frappe Cloud

1. **Bench Group → Apps → Add App** → choose "Install from a GitHub repository"
   → paste the repo URL and branch (`main`) → Save.
2. **Bench Group → Deploys → New Deploy**. Wait 3–5 minutes for the build.
3. After deploy completes: **Site → Apps → Install App** → pick
   `vcl_kra_validation` → Install.
4. Fixtures sync automatically during install. Verify:
   - Go to **Setup → Customize Form → Purchase Invoice**: you should see the
     "KRA eTIMS Validation" section and four custom fields after `bill_date`.
   - Go to **Customization → Client Script**: `VCL KRA CUIN Validation` exists
     and is enabled.

## 6. Editing the Client Script

The fixture lives in `fixtures/client_script.json` but the readable source is
`fixtures/_client_script.js`. To update the script:

1. Edit `_client_script.js`.
2. Regenerate the fixture JSON:
   ```bash
   cd /opt/vcl/apps/vcl_kra_validation
   python3 -c '
   import json
   js = open("vcl_kra_validation/fixtures/_client_script.js").read()
   doc = [{
     "docstatus": 0, "doctype": "Client Script", "name": "VCL KRA CUIN Validation",
     "dt": "Purchase Invoice", "enabled": 1, "view": "Form",
     "module": "VCL KRA Validation", "script": js
   }]
   open("vcl_kra_validation/fixtures/client_script.json","w").write(
       json.dumps(doc, indent=1, ensure_ascii=False))
   '
   ```
3. Commit, push, redeploy the bench, run `bench --site <site> migrate` (Frappe
   Cloud does this automatically during deploy).

## 7. Testing

### Whitelisted endpoint (works from any shell with an API token)

```bash
curl -s -X POST -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{"invoice_no":"0190438130000017933"}' \
  https://vimitconverters.frappe.cloud/api/method/vcl_kra_validation.api.validate_cuin \
  | python3 -m json.tool
```

Expected: `valid: true`, `supplier_name: "AWANAD ENTERPRISES LIMITED"`,
`buyer_name: "VIMIT CONVERTERS LIMITED"`, `is_vcl_buyer: true`,
`tax_amt: 15740.96`, `total_inv_amt: 114121.96`.

### UI smoke test

1. Open a new Purchase Invoice.
2. Pick any supplier.
3. Enter `0190438130000017933` in **Supplier Invoice No.**.
4. A freeze overlay says "Validating CUIN on KRA iTax…" for ~1s.
5. The four KRA fields populate; a green toast shows
   "KRA: AWANAD ENTERPRISES LIMITED — Tax KES 15,740.96, Total KES 114,121.96".
6. Add an item and taxes whose totals do NOT match KRA → click Save → an
   orange "KRA totals mismatch" popup appears.

### Negative test

- Enter `XXXXXXXXXXXXXXX` in **Supplier Invoice No.** → red toast
  "KRA: <KRA error message>".
- Enter something with a `/` → red toast "KRA: CUINs containing '/' are
  not supported…".

## 8. Rollback

- **Disable without deploy:** Frappe desk → Client Script list → open
  "VCL KRA CUIN Validation" → toggle Enabled off → Save. The script stops
  firing immediately. Custom fields remain (harmless — read-only).
- **Full uninstall:** Frappe Cloud → Site → Apps → click the app → Uninstall.
  This runs `bench --site <site> uninstall-app vcl_kra_validation`, which
  removes the Client Script record and the Custom Field records because they
  were created by this app's fixtures.

## 9. Secrets / credentials

None. The KRA iTax invoice checker is a public page with no login, no API
key, no rate limiting advertised. The only "secret-ish" value in the code is
the hardcoded VCL KRA PIN `P000606160U`, which is a public tax identifier (it
appears on every KRA-registered VCL invoice).

## 10. Phase 2 (when ready)

After the team has used the app for a week or two and is comfortable with the
warning banners, convert the mismatch warnings in the Client Script `validate`
handler to a hard block:

```js
// replace frappe.msgprint(...) with:
frappe.throw({ title: __('KRA totals mismatch'), message: ... });
```

And enforce that the KRA fields are populated (i.e. `bill_no` was validated)
before Submit is allowed. That can be done in the same `validate` handler:

```js
if (!frm.doc.custom_kra_total_amount) {
    frappe.throw(__('Supplier Invoice No. must be a valid KRA CUIN before saving.'));
}
```

At that point, consider bumping `__version__` to `0.2.0` in
`vcl_kra_validation/__init__.py` and tagging the release.

## 11. Back-filling historical Purchase Invoices

Installing the app adds the four KRA custom fields to every existing Purchase
Invoice row as **NULL** — the Client Script only fires when a user edits a
form, so old records are not auto-populated. Run the back-fill script below
**after UAT sign-off** to retrofit historical PIs.

Script: `vcl_kra_validation/scripts/backfill_kra_fields.py`
Entry point: `vcl_kra_validation.scripts.backfill_kra_fields.run`

Behaviour:
- Picks only PIs where `bill_no` is set AND `custom_kra_total_amount` is empty
  (idempotent: reruns skip rows already populated).
- Works on both draft (`docstatus=0`) and submitted (`docstatus=1`) PIs via
  `frappe.db.set_value` (bypasses `allow_on_submit`). Cancelled PIs are skipped.
- Calls `validate_cuin` once per row; `sleep` seconds between calls to avoid
  hammering KRA iTax.
- Slash-CUIns and rows KRA can't find are logged as SKIP and left untouched.
- Exceptions are captured to **Frappe → Error Log** and counted as ERR.

### Step-by-step (Frappe Cloud)

1. **Prerequisite:** this branch is merged to `main` and the bench has been
   redeployed so the script ships in the live build.

2. **Dry-run the first 10 rows** (no writes — just prints what it would do):

   - *Path A — Dashboard Console (no SSH):*
     Frappe Cloud dashboard → Site → **Consoles → Bench Console** → paste:
     ```python
     from vcl_kra_validation.scripts.backfill_kra_fields import run
     run(dry_run=True, limit=10)
     ```

   - *Path B — SSH `bench execute` (Private Bench / SSH access):*
     ```bash
     bench --site vimitconverters.frappe.cloud execute \
       vcl_kra_validation.scripts.backfill_kra_fields.run \
       --kwargs "{'dry_run': True, 'limit': 10}"
     ```

3. **Review the output.** Expect lines like:
   ```
   WOULD ACC-PINV-2026-00042 | 0190438130000017933 | AWANAD ENTERPRISES LIMITED | tax=15740.96 total=114121.96
   ```
   plus a final summary `{'updated': 10, 'skipped': 0, 'errored': 0, 'remaining': N}`.
   If the output looks wrong, stop and investigate — nothing was written.

4. **Apply the first 10 for real:**
   ```python
   run(dry_run=False, limit=10)
   ```
   Open 1–2 of the updated PIs in Desk and confirm the four KRA fields are
   populated.

5. **Work through the rest in batches of 50** (pause ~1 s between KRA calls):
   ```python
   run(dry_run=False, limit=50, sleep=1.0)
   ```
   Re-invoke until the summary reports `remaining=0`. Each call is safely
   resumable — if a batch is interrupted, the next call picks up where it
   left off because already-populated rows no longer match the filter.

6. **Audit:** after completion, check **Frappe → Error Log** for any rows
   titled "KRA backfill: exception" and triage.
