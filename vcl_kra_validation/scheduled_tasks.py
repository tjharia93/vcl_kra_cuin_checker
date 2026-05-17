"""Scheduled tasks for KRA CUIN verification.

The daily verification job re-runs ``validate_cuin`` against every Local
Purchase invoice posted today (plus any older invoices still flagged
``custom_kra_pending_verification = 1``), and emails the result to
``purchasing@vimit.com``. This is the end-of-day control check Tanuj asked
for so that invoices submitted while KRA iTax was unavailable are still
verified before they leave the day's books.
"""

import time
from datetime import date

import frappe
from frappe.utils import flt, getdate, today

from vcl_kra_validation.api import validate_cuin

VCL_KRA_PIN = "P000606160U"
REPORT_RECIPIENTS = ["purchasing@vimit.com"]
VAT_TOLERANCE = 1.0  # KES
PORTAL_THROTTLE_SECONDS = 0.4  # be polite to KRA


def _candidates(target_date: date) -> list:
    """Local Purchase invoices to verify today: those posted today AND any
    older invoices still flagged as KRA-pending."""

    posted_today = frappe.get_all(
        "Purchase Invoice",
        filters={
            "docstatus": 1,
            "posting_date": str(target_date),
            "custom_purchase_invoice_type": "Local Purchase",
            "custom_kra_cuin_exempt": 0,
            "bill_no": ["is", "set"],
        },
        fields=[
            "name", "supplier", "supplier_name", "bill_no", "posting_date",
            "base_grand_total", "base_total_taxes_and_charges",
            "custom_kra_pending_verification",
        ],
    )

    still_pending = frappe.get_all(
        "Purchase Invoice",
        filters={
            "docstatus": 1,
            "custom_purchase_invoice_type": "Local Purchase",
            "custom_kra_cuin_exempt": 0,
            "custom_kra_pending_verification": 1,
            "posting_date": ["<", str(target_date)],
            "bill_no": ["is", "set"],
        },
        fields=[
            "name", "supplier", "supplier_name", "bill_no", "posting_date",
            "base_grand_total", "base_total_taxes_and_charges",
            "custom_kra_pending_verification",
        ],
    )

    seen = set()
    combined = []
    for pi in posted_today + still_pending:
        if pi["name"] in seen:
            continue
        seen.add(pi["name"])
        combined.append(pi)
    return combined


def _erp_vat(invoice_name: str) -> float:
    """Sum base_tax_amount of tax rows whose account head contains 'VAT'."""
    taxes = frappe.get_all(
        "Purchase Taxes and Charges",
        filters={"parent": invoice_name, "parenttype": "Purchase Invoice"},
        fields=["account_head", "base_tax_amount"],
    )
    total = 0.0
    for row in taxes:
        if "vat" in (row["account_head"] or "").lower():
            total += flt(row["base_tax_amount"])
    return total


def _classify(pi: dict, result: dict) -> tuple:
    """Return (bucket, detail) where bucket is one of:
    pass | vat_mismatch | buyer_mismatch | not_found | kra_down
    """
    err = (result.get("error") or "").lower()
    if result.get("valid"):
        erp_vat = _erp_vat(pi["name"])
        kra_tax = flt(result.get("tax_amt"))
        if not result.get("is_vcl_buyer"):
            return ("buyer_mismatch", {
                "kra_buyer_name": result.get("buyer_name"),
                "kra_buyer_pin": result.get("buyer_pin"),
                "erp_vat": erp_vat,
                "kra_vat": kra_tax,
            })
        if abs(erp_vat - kra_tax) > VAT_TOLERANCE:
            return ("vat_mismatch", {
                "erp_vat": erp_vat,
                "kra_vat": kra_tax,
                "diff": erp_vat - kra_tax,
            })
        return ("pass", {
            "erp_vat": erp_vat,
            "kra_vat": kra_tax,
            "kra_supplier_name": result.get("supplier_name"),
        })

    down_patterns = (
        "non-json response from kra", "kra portal unreachable",
        "kra etims portal unreachable", "responding slowly",
        "read timed out", "connection refused",
    )
    if any(p in err for p in down_patterns):
        return ("kra_down", {"error": result.get("error")})
    return ("not_found", {"error": result.get("error")})


def daily_verify_kra_invoices(target_date: date | None = None) -> dict:
    """Run the daily KRA verification sweep. Emits a summary email and
    updates ``custom_kra_pending_verification`` on each verified invoice.

    Returns a dict with bucket counts — useful for tests and CLI runs.
    """

    target_date = target_date or getdate(today())
    candidates = _candidates(target_date)

    buckets: dict[str, list] = {
        "pass": [], "vat_mismatch": [], "buyer_mismatch": [],
        "not_found": [], "kra_down": [],
    }

    for pi in candidates:
        try:
            result = validate_cuin(pi["bill_no"])
        except Exception as e:  # noqa: BLE001
            result = {"valid": False, "error": f"verifier crashed: {e}"}
        bucket, detail = _classify(pi, result)
        buckets[bucket].append({**pi, **detail})

        # Clear the pending flag on pass; set on every fail bucket so it
        # surfaces in tomorrow's catch-up sweep too.
        new_flag = 0 if bucket == "pass" else 1
        if pi.get("custom_kra_pending_verification") != new_flag:
            frappe.db.set_value(
                "Purchase Invoice", pi["name"],
                "custom_kra_pending_verification", new_flag,
                update_modified=False,
            )

        time.sleep(PORTAL_THROTTLE_SECONDS)

    frappe.db.commit()
    _send_report(target_date, candidates, buckets)
    return {k: len(v) for k, v in buckets.items()}


def _send_report(target_date: date, candidates: list, buckets: dict) -> None:
    total = len(candidates)
    counts = {k: len(v) for k, v in buckets.items()}

    summary_html = f"""
    <table style="border-collapse:collapse;font-family:Arial,sans-serif;">
      <tr><th colspan="2" style="text-align:left;padding:6px 12px;background:#f0f0f0;">Summary</th></tr>
      <tr><td style="padding:4px 12px;">Total verified</td><td style="padding:4px 12px;text-align:right;"><b>{total}</b></td></tr>
      <tr><td style="padding:4px 12px;color:#1f8b4c;">✓ Pass</td><td style="padding:4px 12px;text-align:right;color:#1f8b4c;">{counts['pass']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ VAT mismatch</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['vat_mismatch']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ Buyer PIN not VCL</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['buyer_mismatch']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ CUIN not found</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['not_found']}</td></tr>
      <tr><td style="padding:4px 12px;color:#888;">⏸ KRA still unreachable</td><td style="padding:4px 12px;text-align:right;color:#888;">{counts['kra_down']}</td></tr>
    </table>
    """

    sections = [summary_html]

    def _details_table(title: str, rows: list, cols: list) -> str:
        if not rows:
            return ""
        header = "".join(f'<th style="padding:6px 8px;text-align:left;background:#f0f0f0;">{c[0]}</th>' for c in cols)
        body = []
        for r in rows:
            cells = "".join(
                f'<td style="padding:4px 8px;border-top:1px solid #eee;">{c[1](r)}</td>'
                for c in cols
            )
            body.append(f"<tr>{cells}</tr>")
        return (
            f'<h3 style="font-family:Arial,sans-serif;margin-top:16px;">{title}</h3>'
            f'<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;">'
            f"<tr>{header}</tr>{''.join(body)}</table>"
        )

    sections.append(_details_table("VAT mismatch", buckets["vat_mismatch"], [
        ("PI", lambda r: r["name"]),
        ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
        ("CUIN", lambda r: r["bill_no"]),
        ("ERP VAT", lambda r: f"{r['erp_vat']:,.2f}"),
        ("KRA VAT", lambda r: f"{r['kra_vat']:,.2f}"),
        ("Diff", lambda r: f"{r['diff']:+,.2f}"),
    ]))
    sections.append(_details_table("Buyer PIN not VCL", buckets["buyer_mismatch"], [
        ("PI", lambda r: r["name"]),
        ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
        ("CUIN", lambda r: r["bill_no"]),
        ("KRA buyer", lambda r: f"{r.get('kra_buyer_name') or '(unknown)'} ({r.get('kra_buyer_pin') or '-'})"),
    ]))
    sections.append(_details_table("CUIN not found on KRA", buckets["not_found"], [
        ("PI", lambda r: r["name"]),
        ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
        ("CUIN", lambda r: r["bill_no"]),
        ("KRA response", lambda r: (r.get("error") or "")[:200]),
    ]))
    sections.append(_details_table("KRA still unreachable (will retry tomorrow)", buckets["kra_down"], [
        ("PI", lambda r: r["name"]),
        ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
        ("CUIN", lambda r: r["bill_no"]),
        ("Last error", lambda r: (r.get("error") or "")[:200]),
    ]))

    body = (
        f'<p style="font-family:Arial,sans-serif;">'
        f"Daily KRA CUIN verification for <b>{target_date}</b>. "
        f"Local Purchase invoices submitted today plus any earlier ones still flagged pending."
        f"</p>"
        + "".join(s for s in sections if s)
    )

    fail_count = counts["vat_mismatch"] + counts["buyer_mismatch"] + counts["not_found"]
    indicator = "⚠ " if fail_count else "✓ "
    subject = (
        f"{indicator}KRA CUIN verification {target_date} — "
        f"{counts['pass']} pass, {fail_count} fail, {counts['kra_down']} KRA down"
    )

    frappe.sendmail(
        recipients=REPORT_RECIPIENTS,
        subject=subject,
        message=body,
        reference_doctype="Purchase Invoice",
        now=True,
    )
