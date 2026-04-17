# VCL KRA Validation

Custom Frappe app for Vimit Converters Limited that validates KRA eTIMS Control
Unit Invoice Numbers (CUINs) against the public iTax invoice checker when a
Purchase Invoice is being entered in ERPNext.

## What it does

On every Purchase Invoice form, when the user fills in the **Supplier Invoice No.**
(`bill_no`) field, the app:

1. Calls a whitelisted server method that POSTs the CUIN to
   `https://itax.kra.go.ke/KRA-Portal/middlewareController.htm?actionCode=fetchInvoiceDtl`.
2. Parses the JSON response from KRA.
3. Populates four read-only custom fields on the form:
   - `custom_kra_supplier_name` — supplier name as registered with KRA
   - `custom_kra_invoice_number` — the supplier's own invoice number (KRA's `traderSystemInvNo`)
   - `custom_kra_tax_amount` — VAT amount from KRA
   - `custom_kra_total_amount` — grand total from KRA
4. Alerts the user (red banner) if the KRA buyer PIN on the invoice is not
   Vimit Converters Limited (`P000606160U`).
5. On `validate` (before save), warns if the ERPNext-computed `grand_total` or
   `total_taxes_and_charges` does not match the KRA values within KES 1.

## Components

- `vcl_kra_validation/api.py` — whitelisted `validate_cuin` method.
- `vcl_kra_validation/fixtures/custom_field.json` — four custom fields + section/column breaks.
- `vcl_kra_validation/fixtures/client_script.json` — the Purchase Invoice client script.
- `vcl_kra_validation/hooks.py` — fixture registration.

## KRA endpoint notes

- The iTax invoice checker is **public**: no login, no API key.
- Cookies/session are **not** required. A single POST with the correct
  `Referer` and `X-Requested-With` headers returns the JSON payload.
- CUINs containing `/` (slash) are routed by KRA to the new eTIMS portal at
  `https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsInvoiceData?Data=...`;
  those are **not** handled by this app — `validate_cuin` returns an error
  with `error = "CUINs containing '/' are not supported..."`.

## Install on a Frappe Cloud bench

1. Push this repo to GitHub (private is fine — add the Frappe Cloud deploy key
   to the repo's Deploy Keys so the bench can clone).
2. In the Frappe Cloud dashboard: **Bench Group → Apps → Add App → Install from
   GitHub → paste the repo URL**.
3. Deploy the bench group. After deploy, install the app on the
   `vimitconverters.frappe.cloud` site.
4. After install, fixtures sync automatically — the four custom fields and the
   client script appear on Purchase Invoice.

## Rollback

If anything goes wrong, uninstall the app from the site. The custom fields are
marked `fieldname: custom_kra_*` so they are easy to identify and remove. The
client script is named `VCL KRA CUIN Validation` — delete that record from the
Client Script list to disable immediately without a deploy.
