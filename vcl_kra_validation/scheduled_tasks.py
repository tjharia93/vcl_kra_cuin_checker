"""Scheduled tasks for KRA CUIN verification.

The daily verification job re-runs ``validate_cuin`` against:

  • every Local Purchase invoice posted today (plus older PIs still flagged
    ``custom_kra_pending_verification = 1``), and
  • every Domestic VAT Sales Invoice posted today (plus older SIs still
    flagged pending),

and emails the consolidated result to ``purchasing@vimit.com``. This is the
end-of-day control check Tanuj asked for: it catches both KRA outages at
posting time (UI or API) and customer-master gaps where a CUIN went out
without a buyer PIN attached.
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

PI_BUCKETS = ("pass", "vat_mismatch", "buyer_mismatch", "not_found", "kra_down")
SI_BUCKETS = (
    "pass",
    "vat_mismatch",
    "supplier_mismatch",
    "buyer_pin_mismatch",
    "no_customer_pin",
    "no_cuin_on_invoice",
    "not_found",
    "kra_down",
)

DOWN_PATTERNS = (
    "non-json response from kra",
    "kra portal unreachable",
    "kra etims portal unreachable",
    "responding slowly",
    "read timed out",
    "connection refused",
)


# ---------------------------------------------------------------------------
# Purchase Invoice sweep
# ---------------------------------------------------------------------------

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

    if any(p in err for p in DOWN_PATTERNS):
        return ("kra_down", {"error": result.get("error")})
    return ("not_found", {"error": result.get("error")})


# ---------------------------------------------------------------------------
# Sales Invoice sweep
# ---------------------------------------------------------------------------

_SI_FIELDS = [
    "name", "customer", "customer_name", "tax_id", "custom_cuin",
    "posting_date", "base_grand_total", "base_total_taxes_and_charges",
    "custom_kra_pending_verification",
]


def _candidates_sales(target_date: date) -> list:
    """Domestic-VAT Sales Invoices to verify today: those posted today PLUS
    any older SIs still flagged pending. Export / zero-rated SIs are skipped
    because they are out of eTIMS scope."""

    posted_today = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "posting_date": str(target_date),
            "tax_category": "Domestic VAT",
            "custom_kra_cuin_exempt": 0,
        },
        fields=_SI_FIELDS,
    )

    still_pending = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "tax_category": "Domestic VAT",
            "custom_kra_cuin_exempt": 0,
            "custom_kra_pending_verification": 1,
            "posting_date": ["<", str(target_date)],
        },
        fields=_SI_FIELDS,
    )

    seen, combined = set(), []
    for si in posted_today + still_pending:
        if si["name"] in seen:
            continue
        seen.add(si["name"])
        combined.append(si)
    return combined


def _erp_vat_sales(invoice_name: str) -> float:
    """Sum base_tax_amount of Sales Taxes rows whose account head contains 'VAT'."""
    taxes = frappe.get_all(
        "Sales Taxes and Charges",
        filters={"parent": invoice_name, "parenttype": "Sales Invoice"},
        fields=["account_head", "base_tax_amount"],
    )
    total = 0.0
    for row in taxes:
        if "vat" in (row["account_head"] or "").lower():
            total += flt(row["base_tax_amount"])
    return total


def _classify_sales(si: dict, result: dict | None) -> tuple:
    """Return (bucket, detail) for a Sales Invoice.

    Evaluation order:
      1. SI has no CUIN                          → no_cuin_on_invoice (terminal)
      2. KRA portals unreachable                 → kra_down
      3. CUIN not recognised by KRA              → not_found
      4. KRA shows supplier ≠ VCL                → supplier_mismatch
      5. SI has no customer PIN (Customer.tax_id) → no_customer_pin
      6. KRA buyer_pin ≠ SI.tax_id               → buyer_pin_mismatch
      7. |ERP VAT − KRA VAT| > tolerance         → vat_mismatch
      8. otherwise                               → pass
    """
    if not (si.get("custom_cuin") or "").strip():
        return ("no_cuin_on_invoice", {})

    if not result or not result.get("valid"):
        err = ((result or {}).get("error") or "").lower()
        if any(p in err for p in DOWN_PATTERNS):
            return ("kra_down", {"error": (result or {}).get("error")})
        return ("not_found", {"error": (result or {}).get("error")})

    if not result.get("is_vcl_supplier"):
        return ("supplier_mismatch", {
            "kra_supplier_name": result.get("supplier_name"),
            "kra_supplier_pin": result.get("supplier_pin"),
        })

    customer_pin = (si.get("tax_id") or "").strip().upper()
    erp_vat = _erp_vat_sales(si["name"])
    kra_tax = flt(result.get("tax_amt"))

    if not customer_pin:
        return ("no_customer_pin", {
            "kra_buyer_name": result.get("buyer_name"),
            "kra_buyer_pin": result.get("buyer_pin"),
            "erp_vat": erp_vat,
            "kra_vat": kra_tax,
        })

    kra_buyer = (result.get("buyer_pin") or "").strip().upper()
    if kra_buyer != customer_pin:
        return ("buyer_pin_mismatch", {
            "erp_buyer_pin": customer_pin,
            "kra_buyer_name": result.get("buyer_name"),
            "kra_buyer_pin": kra_buyer,
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
        "kra_buyer_name": result.get("buyer_name"),
    })


# ---------------------------------------------------------------------------
# Combined daily sweep
# ---------------------------------------------------------------------------

def daily_verify_kra_invoices(target_date: date | None = None) -> dict:
    """Run the daily KRA verification sweep across PI + SI. Emits a single
    summary email and updates ``custom_kra_pending_verification`` on each
    invoice verified.

    Returns a nested dict with per-doctype bucket counts — useful for tests
    and CLI runs.
    """

    target_date = target_date or getdate(today())

    # ----- Purchase Invoices -----
    pi_candidates = _candidates(target_date)
    pi_buckets: dict[str, list] = {k: [] for k in PI_BUCKETS}

    for pi in pi_candidates:
        try:
            result = validate_cuin(pi["bill_no"])
        except Exception as e:  # noqa: BLE001
            result = {"valid": False, "error": f"verifier crashed: {e}"}
        bucket, detail = _classify(pi, result)
        pi_buckets[bucket].append({**pi, **detail})

        new_flag = 0 if bucket == "pass" else 1
        if pi.get("custom_kra_pending_verification") != new_flag:
            frappe.db.set_value(
                "Purchase Invoice", pi["name"],
                "custom_kra_pending_verification", new_flag,
                update_modified=False,
            )

        time.sleep(PORTAL_THROTTLE_SECONDS)

    # ----- Sales Invoices -----
    si_candidates = _candidates_sales(target_date)
    si_buckets: dict[str, list] = {k: [] for k in SI_BUCKETS}

    for si in si_candidates:
        cuin = (si.get("custom_cuin") or "").strip()
        if not cuin:
            result = None
        else:
            try:
                result = validate_cuin(cuin)
            except Exception as e:  # noqa: BLE001
                result = {"valid": False, "error": f"verifier crashed: {e}"}
            time.sleep(PORTAL_THROTTLE_SECONDS)
        bucket, detail = _classify_sales(si, result)
        si_buckets[bucket].append({**si, **detail})

        new_flag = 0 if bucket == "pass" else 1
        if si.get("custom_kra_pending_verification") != new_flag:
            frappe.db.set_value(
                "Sales Invoice", si["name"],
                "custom_kra_pending_verification", new_flag,
                update_modified=False,
            )

    frappe.db.commit()
    _send_report(target_date, pi_candidates, pi_buckets, si_candidates, si_buckets)

    return {
        "purchase_invoice": {k: len(v) for k, v in pi_buckets.items()},
        "sales_invoice": {k: len(v) for k, v in si_buckets.items()},
    }


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------

def _details_table(title: str, rows: list, cols: list) -> str:
    if not rows:
        return ""
    header = "".join(
        f'<th style="padding:6px 8px;text-align:left;background:#f0f0f0;">{c[0]}</th>'
        for c in cols
    )
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


def _pi_summary_html(counts: dict, total: int) -> str:
    return f"""
    <table style="border-collapse:collapse;font-family:Arial,sans-serif;">
      <tr><th colspan="2" style="text-align:left;padding:6px 12px;background:#f0f0f0;">Purchase Invoices</th></tr>
      <tr><td style="padding:4px 12px;">Total verified</td><td style="padding:4px 12px;text-align:right;"><b>{total}</b></td></tr>
      <tr><td style="padding:4px 12px;color:#1f8b4c;">✓ Pass</td><td style="padding:4px 12px;text-align:right;color:#1f8b4c;">{counts['pass']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ VAT mismatch</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['vat_mismatch']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ Buyer PIN not VCL</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['buyer_mismatch']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ CUIN not found</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['not_found']}</td></tr>
      <tr><td style="padding:4px 12px;color:#888;">⏸ KRA still unreachable</td><td style="padding:4px 12px;text-align:right;color:#888;">{counts['kra_down']}</td></tr>
    </table>
    """


def _si_summary_html(counts: dict, total: int) -> str:
    return f"""
    <table style="border-collapse:collapse;font-family:Arial,sans-serif;margin-top:16px;">
      <tr><th colspan="2" style="text-align:left;padding:6px 12px;background:#f0f0f0;">Sales Invoices</th></tr>
      <tr><td style="padding:4px 12px;">Total verified</td><td style="padding:4px 12px;text-align:right;"><b>{total}</b></td></tr>
      <tr><td style="padding:4px 12px;color:#1f8b4c;">✓ Pass</td><td style="padding:4px 12px;text-align:right;color:#1f8b4c;">{counts['pass']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ VAT mismatch</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['vat_mismatch']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ Supplier ≠ VCL</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['supplier_mismatch']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ Buyer PIN mismatch</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['buyer_pin_mismatch']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ Customer master missing PIN</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['no_customer_pin']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ SI posted without CUIN</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['no_cuin_on_invoice']}</td></tr>
      <tr><td style="padding:4px 12px;color:#c0392b;">✗ CUIN not found</td><td style="padding:4px 12px;text-align:right;color:#c0392b;">{counts['not_found']}</td></tr>
      <tr><td style="padding:4px 12px;color:#888;">⏸ KRA still unreachable</td><td style="padding:4px 12px;text-align:right;color:#888;">{counts['kra_down']}</td></tr>
    </table>
    """


def _pi_detail_sections(buckets: dict) -> list:
    return [
        _details_table("Purchase: VAT mismatch", buckets["vat_mismatch"], [
            ("PI", lambda r: r["name"]),
            ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
            ("CUIN", lambda r: r["bill_no"]),
            ("ERP VAT", lambda r: f"{r['erp_vat']:,.2f}"),
            ("KRA VAT", lambda r: f"{r['kra_vat']:,.2f}"),
            ("Diff", lambda r: f"{r['diff']:+,.2f}"),
        ]),
        _details_table("Purchase: Buyer PIN not VCL", buckets["buyer_mismatch"], [
            ("PI", lambda r: r["name"]),
            ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
            ("CUIN", lambda r: r["bill_no"]),
            ("KRA buyer", lambda r: f"{r.get('kra_buyer_name') or '(unknown)'} ({r.get('kra_buyer_pin') or '-'})"),
        ]),
        _details_table("Purchase: CUIN not found on KRA", buckets["not_found"], [
            ("PI", lambda r: r["name"]),
            ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
            ("CUIN", lambda r: r["bill_no"]),
            ("KRA response", lambda r: (r.get("error") or "")[:200]),
        ]),
        _details_table("Purchase: KRA still unreachable (will retry tomorrow)", buckets["kra_down"], [
            ("PI", lambda r: r["name"]),
            ("Supplier", lambda r: r.get("supplier_name") or r.get("supplier")),
            ("CUIN", lambda r: r["bill_no"]),
            ("Last error", lambda r: (r.get("error") or "")[:200]),
        ]),
    ]


def _si_detail_sections(buckets: dict) -> list:
    return [
        _details_table("Sales: VAT mismatch", buckets["vat_mismatch"], [
            ("SI", lambda r: r["name"]),
            ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
            ("CUIN", lambda r: r.get("custom_cuin") or "-"),
            ("ERP VAT", lambda r: f"{r['erp_vat']:,.2f}"),
            ("KRA VAT", lambda r: f"{r['kra_vat']:,.2f}"),
            ("Diff", lambda r: f"{r['diff']:+,.2f}"),
        ]),
        _details_table("Sales: Supplier on KRA ≠ VCL", buckets["supplier_mismatch"], [
            ("SI", lambda r: r["name"]),
            ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
            ("CUIN", lambda r: r.get("custom_cuin") or "-"),
            ("KRA supplier", lambda r: f"{r.get('kra_supplier_name') or '(unknown)'} ({r.get('kra_supplier_pin') or '-'})"),
        ]),
        _details_table("Sales: Buyer PIN mismatch (ERP customer ≠ KRA buyer)", buckets["buyer_pin_mismatch"], [
            ("SI", lambda r: r["name"]),
            ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
            ("CUIN", lambda r: r.get("custom_cuin") or "-"),
            ("ERP PIN", lambda r: r.get("erp_buyer_pin") or "-"),
            ("KRA PIN", lambda r: r.get("kra_buyer_pin") or "-"),
            ("KRA buyer", lambda r: r.get("kra_buyer_name") or "(unknown)"),
        ]),
        _details_table("Sales: Customer master missing KRA PIN", buckets["no_customer_pin"], [
            ("SI", lambda r: r["name"]),
            ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
            ("CUIN", lambda r: r.get("custom_cuin") or "-"),
            ("KRA buyer", lambda r: f"{r.get('kra_buyer_name') or '(unknown)'} ({r.get('kra_buyer_pin') or '-'})"),
        ]),
        _details_table("Sales: SI posted without CUIN (eTIMS not transmitted)", buckets["no_cuin_on_invoice"], [
            ("SI", lambda r: r["name"]),
            ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
            ("Posting Date", lambda r: str(r.get("posting_date") or "")),
            ("Grand Total", lambda r: f"{flt(r.get('base_grand_total')):,.2f}"),
        ]),
        _details_table("Sales: CUIN not found on KRA", buckets["not_found"], [
            ("SI", lambda r: r["name"]),
            ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
            ("CUIN", lambda r: r.get("custom_cuin") or "-"),
            ("KRA response", lambda r: (r.get("error") or "")[:200]),
        ]),
        _details_table("Sales: KRA still unreachable (will retry tomorrow)", buckets["kra_down"], [
            ("SI", lambda r: r["name"]),
            ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
            ("CUIN", lambda r: r.get("custom_cuin") or "-"),
            ("Last error", lambda r: (r.get("error") or "")[:200]),
        ]),
    ]


def _send_report(
    target_date: date,
    pi_candidates: list, pi_buckets: dict,
    si_candidates: list, si_buckets: dict,
) -> None:
    pi_total = len(pi_candidates)
    pi_counts = {k: len(v) for k, v in pi_buckets.items()}
    si_total = len(si_candidates)
    si_counts = {k: len(v) for k, v in si_buckets.items()}

    pi_fail = pi_counts["vat_mismatch"] + pi_counts["buyer_mismatch"] + pi_counts["not_found"]
    si_fail = (
        si_counts["vat_mismatch"] + si_counts["supplier_mismatch"]
        + si_counts["buyer_pin_mismatch"] + si_counts["no_customer_pin"]
        + si_counts["no_cuin_on_invoice"] + si_counts["not_found"]
    )

    sections = [
        _pi_summary_html(pi_counts, pi_total),
        _si_summary_html(si_counts, si_total),
    ]
    sections.extend(_pi_detail_sections(pi_buckets))
    sections.extend(_si_detail_sections(si_buckets))

    body = (
        f'<p style="font-family:Arial,sans-serif;">'
        f"Daily KRA CUIN verification for <b>{target_date}</b>. "
        f"Local Purchase invoices and Domestic VAT Sales Invoices submitted today, "
        f"plus any earlier ones still flagged pending."
        f"</p>"
        + "".join(s for s in sections if s)
    )

    total_fail = pi_fail + si_fail
    indicator = "⚠ " if total_fail else "✓ "
    subject = (
        f"{indicator}KRA CUIN verification {target_date} — "
        f"PI {pi_counts['pass']}/{pi_total} pass, {pi_fail} fail · "
        f"SI {si_counts['pass']}/{si_total} pass, {si_fail} fail"
    )

    frappe.sendmail(
        recipients=REPORT_RECIPIENTS,
        subject=subject,
        message=body,
        reference_doctype="Sales Invoice" if si_total else "Purchase Invoice",
        now=True,
    )
