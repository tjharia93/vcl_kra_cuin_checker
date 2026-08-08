"""Delete the on-form KRA checker on Sales Invoice (SA-06).

`VCL KRA CUIN Validation - Sales Invoice` fired on form *refresh* with
`freeze: true`, so every user who merely OPENED a submitted invoice sat behind
a blocking round trip to iTax. KRA is now checked by the nightly sweep, and
`VCL KRA Recheck - Sales Invoice` offers a manual re-check that only calls out
when a human presses the button.

Removing the fixture file alone is not enough — fixtures never delete, they
only import. The live Client Script record survives a deploy until something
removes it, which is what this patch is for. Disabling it in the UI instead
would be undone by the next `sync_fixtures`, which is exactly what the
script's own header warns about.

Idempotent: safe to re-run, and safe on a site where the record never existed.
"""

import frappe

DOOMED = "VCL KRA CUIN Validation - Sales Invoice"


def execute():
    if not frappe.db.exists("Client Script", DOOMED):
        return

    frappe.delete_doc("Client Script", DOOMED, force=True, ignore_missing=True)
    frappe.db.commit()
