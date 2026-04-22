# VCL KRA Validation

Custom Frappe app for Vimit Converters Limited that validates KRA Control Unit
Invoice Numbers (CUINs) against the public iTax and eTIMS portals when a
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

- **iTax** (digit-only CUINs): `https://itax.kra.go.ke/KRA-Portal/middlewareController.htm?actionCode=fetchInvoiceDtl`. Public, no login, no API key; a single POST with `Referer` + `X-Requested-With` headers returns JSON.
- **eTIMS** (slash-containing CUINs, e.g. `KRACU0100065004/379`): `https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsInvoiceData?Data=<cuin-with-"/"-replaced-by-"-">`. Public HTML page; parsed server-side with regex. Credit notes return signed (negative) amounts which the client compares against the base-currency totals of return Purchase Invoices.
- `validate_cuin` dispatches on CUIN shape: `/`-containing → eTIMS, otherwise → iTax. Both return the same response dict shape with an additional `source: "itax" | "etims"` key.

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
