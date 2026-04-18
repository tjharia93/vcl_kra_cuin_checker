"""One-off back-fill of KRA custom fields on historical Purchase Invoices.

Iterates Purchase Invoices whose ``bill_no`` is set but whose
``custom_kra_total_amount`` is empty, calls the existing
``vcl_kra_validation.api.validate_cuin`` helper once per row, and writes the
four KRA fields with ``frappe.db.set_value`` (which bypasses
``allow_on_submit`` and works on both draft and submitted PIs).

Designed to be invoked manually from ``bench execute`` or the Frappe Cloud
Site Console after UAT sign-off. Idempotent: a row is only picked up while
``custom_kra_total_amount`` is still empty, so a partial or interrupted run
can be resumed simply by re-invoking.

Example (dry-run, first 10 rows)::

    bench --site vimitconverters.frappe.cloud execute \\
        vcl_kra_validation.scripts.backfill_kra_fields.run \\
        --kwargs "{'dry_run': True, 'limit': 10}"
"""

from __future__ import annotations

import time
from typing import Optional

import frappe

from vcl_kra_validation.api import validate_cuin

PENDING_FILTERS = {
    "bill_no": ["is", "set"],
    "custom_kra_total_amount": ["is", "not set"],
    "docstatus": ["!=", 2],  # skip cancelled
}


def run(
    dry_run: bool = True,
    limit: Optional[int] = 50,
    sleep: float = 1.0,
) -> dict:
    """Back-fill KRA fields on historical Purchase Invoices.

    Args:
        dry_run: when True (default) print what would change but write nothing.
        limit: max rows to process in this invocation. ``None`` processes all
            remaining rows in one go (not recommended for the first run).
        sleep: seconds to pause between KRA iTax calls to avoid hammering the
            public endpoint.

    Returns:
        Summary dict with ``processed``, ``updated``, ``skipped``, ``errored``
        and ``remaining`` counts.
    """
    candidates = frappe.db.get_all(
        "Purchase Invoice",
        filters=PENDING_FILTERS,
        fields=["name", "bill_no", "docstatus"],
        order_by="creation asc",
        limit=limit,
    )

    print(
        f"[kra-backfill] dry_run={dry_run} limit={limit} "
        f"sleep={sleep}s candidates={len(candidates)}"
    )

    updated = skipped = errored = 0

    for row in candidates:
        name = row["name"]
        bill_no = (row["bill_no"] or "").strip()

        if "/" in bill_no:
            print(f"  SKIP {name} | {bill_no} | slash CUIN, eTIMS unsupported")
            skipped += 1
            continue

        try:
            result = validate_cuin(bill_no)
        except Exception as e:
            frappe.log_error(
                title="KRA backfill: exception",
                message=f"name={name} bill_no={bill_no}\n{e}",
            )
            print(f"  ERR  {name} | {bill_no} | {e}")
            errored += 1
            time.sleep(sleep)
            continue

        if not result.get("valid"):
            print(f"  SKIP {name} | {bill_no} | KRA: {result.get('error')}")
            skipped += 1
            time.sleep(sleep)
            continue

        values = {
            "custom_kra_supplier_name": result.get("supplier_name") or "",
            "custom_kra_invoice_number": result.get("trader_system_inv_no") or "",
            "custom_kra_tax_amount": result.get("tax_amt") or 0,
            "custom_kra_total_amount": result.get("total_inv_amt") or 0,
        }

        if dry_run:
            print(
                f"  WOULD {name} | {bill_no} | "
                f"{values['custom_kra_supplier_name']} | "
                f"tax={values['custom_kra_tax_amount']} "
                f"total={values['custom_kra_total_amount']}"
            )
        else:
            frappe.db.set_value(
                "Purchase Invoice",
                name,
                values,
                update_modified=False,
            )
            print(
                f"  OK   {name} | {bill_no} | "
                f"{values['custom_kra_supplier_name']} | "
                f"tax={values['custom_kra_tax_amount']} "
                f"total={values['custom_kra_total_amount']}"
            )

        updated += 1
        time.sleep(sleep)

    if not dry_run:
        frappe.db.commit()

    remaining = frappe.db.count("Purchase Invoice", filters=PENDING_FILTERS)
    summary = {
        "dry_run": dry_run,
        "processed": len(candidates),
        "updated": updated,
        "skipped": skipped,
        "errored": errored,
        "remaining": remaining,
    }
    print(f"[kra-backfill] done: {summary}")
    return summary
