"""Scheduled tasks for KRA CUIN verification.

The daily 19:00 EAT job is the *only* place invoices are checked against KRA.
There is no on-form checker on Sales Invoice any more (see SA-06) — a user
opening an invoice never blocks on a call to iTax.

It covers two sweeps:

  • **Purchase Invoices** — every Local Purchase invoice posted today, plus
    older PIs still flagged ``custom_kra_pending_verification = 1``.

  • **Sales Invoices** — every SUBMITTED Vimit Converters Limited invoice that
    carries a ``custom_cuin``, is not ``custom_kra_cuin_exempt``, and has not
    been checked since it was last modified. For each one we record what KRA
    says (buyer PIN, net, tax, gross), the variance against ERPNext, and an
    overall match status.

Both write with ``frappe.db.set_value(..., update_modified=False)``. Every KRA
custom field is ``allow_on_submit: 0``, so an ordinary save cannot touch a
submitted invoice; ``db.set_value`` bypasses that and leaves ``modified`` (and
therefore the audit trail and the re-check predicate) undisturbed. The
precedent is ``scripts/backfill_kra_fields.py``.

A consolidated summary is emailed to ``purchasing@vimit.com``.
"""

import time
from datetime import date

import frappe
from frappe.utils import flt, get_datetime, getdate, now_datetime, today

from vcl_kra_validation.api import validate_cuin

VCL_KRA_PIN = "P000606160U"
VCL_COMPANY = "Vimit Converters Limited"
REPORT_RECIPIENTS = ["purchasing@vimit.com"]

# ERPNext carries a rounding adjustment on most invoices — ACC-SINV-2026-00053
# is 95,444.80 against a rounded 95,445. Twenty cents is not a variance.
VAT_TOLERANCE = 1.0  # KES

PORTAL_THROTTLE_SECONDS = 0.4  # be polite to KRA

# Sales backlog is ~8,000 invoices. At the throttle above (plus KRA's own
# latency) a full pass would run for hours, so each nightly run takes a slice,
# oldest-unchecked first, and the backlog drains over successive nights.
SI_RUN_LIMIT = 400

PI_BUCKETS = ("pass", "vat_mismatch", "buyer_mismatch", "not_found", "kra_down")

# Overall match status written to Sales Invoice.custom_kra_match_status.
STATUS_MATCHED = "Matched"
STATUS_VARIANCE = "Variance"
STATUS_PIN_MISMATCH = "PIN mismatch"
STATUS_NOT_FOUND = "Not found"
STATUS_UNREACHABLE = "Unreachable"

SI_STATUSES = (
    STATUS_MATCHED,
    STATUS_VARIANCE,
    STATUS_PIN_MISMATCH,
    STATUS_NOT_FOUND,
    STATUS_UNREACHABLE,
)

# Only a clean match is terminal. Everything else is re-checked on the next
# run: Unreachable because KRA was down, the rest because they are the kind of
# thing somebody fixes upstream (a customer PIN, a re-issued receipt) and we
# want the invoice to clear itself when they do.
SI_TERMINAL_STATUSES = (STATUS_MATCHED,)

PIN_MATCH = "Match"
PIN_MISMATCH = "Mismatch"
PIN_NO_CUSTOMER = "No customer PIN"
PIN_NO_KRA = "No KRA PIN"

DOWN_PATTERNS = (
    "non-json response from kra",
    "kra portal unreachable",
    "kra itax unreachable",
    "kra etims portal unreachable",
    "kra etims unreachable",
    "responding slowly",
    "read timed out",
    "connection refused",
    "returned http 500",
    "returned http 502",
    "returned http 503",
    "returned http 504",
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
    "posting_date", "is_return", "modified",
    "base_net_total", "base_grand_total", "base_total_taxes_and_charges",
    "custom_kra_pending_verification",
    "custom_kra_match_status", "custom_kra_checked_on",
]


def _candidates_sales(limit: int | None = SI_RUN_LIMIT) -> list:
    """Submitted VCL Sales Invoices due a KRA check.

    In scope: ``docstatus = 1``, company Vimit Converters Limited, a
    ``custom_cuin`` on the invoice, not ``custom_kra_cuin_exempt``.

    Deliberately NOT filtered on ``tax_category``. The old sweep required
    ``tax_category = "Domestic VAT"``; on the live site 3,741 of the 8,094
    submitted VCL invoices that carry a CUIN have that field blank (every
    QBO-synced invoice does), so that filter silently dropped 46% of the
    population. A CUIN is itself the signal that the invoice went through
    eTIMS — nothing else needs to agree.

    Due a check when we have never checked it, when it has been modified
    since we last checked, or when the last verdict was not terminal.
    Oldest-unchecked first so a backlog drains deterministically.
    """
    rows = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "company": VCL_COMPANY,
            "custom_cuin": ["is", "set"],
            "custom_kra_cuin_exempt": 0,
        },
        fields=_SI_FIELDS,
        order_by="posting_date asc, name asc",
    )

    due = [r for r in rows if _is_due(r)]
    return due[:limit] if limit else due


def _is_due(si: dict) -> bool:
    checked_on = si.get("custom_kra_checked_on")
    if not checked_on:
        return True
    if (si.get("custom_kra_match_status") or "") not in SI_TERMINAL_STATUSES:
        return True
    modified = si.get("modified")
    if modified and get_datetime(modified) > get_datetime(checked_on):
        return True
    return False


def _erp_vat_sales(invoice_name: str) -> float:
    """Sum ``base_tax_amount`` of tax rows whose account head contains 'VAT'.

    This is the server-side port of ``siErpVatTotal()`` from the retired Sales
    Invoice client script. Non-VAT levies (catering, tourism, etc.) are
    excluded on purpose — KRA's ``taxAmt`` is VAT only.
    """
    taxes = frappe.get_all(
        "Sales Taxes and Charges",
        filters={"parent": invoice_name, "parenttype": "Sales Invoice"},
        fields=["account_head", "base_tax_amount"],
    )
    return sum(
        flt(row["base_tax_amount"])
        for row in taxes
        if "vat" in (row["account_head"] or "").lower()
    )


def _customer_pin(si: dict) -> str:
    """The customer's KRA PIN, read from the Customer master.

    NOT ``Sales Invoice.tax_id``. That field is a snapshot taken when the
    invoice was created, and on the live site it is blank on 3,872 of the
    8,094 submitted invoices that carry a CUIN — every one sampled had the PIN
    sitting on the Customer record all along. Comparing against the invoice
    copy would report a PIN problem on roughly half the book that isn't one.
    Falls back to the invoice snapshot if the master has nothing.
    """
    customer = si.get("customer")
    if customer:
        pin = _customer_pin_cache.get(customer, _SENTINEL)
        if pin is _SENTINEL:
            pin = frappe.db.get_value("Customer", customer, "tax_id") or ""
            _customer_pin_cache[customer] = pin
        if pin:
            return pin.strip().upper()
    return (si.get("tax_id") or "").strip().upper()


_SENTINEL = object()
_customer_pin_cache: dict = {}


def _is_down(result: dict | None) -> bool:
    err = ((result or {}).get("error") or "").lower()
    return any(p in err for p in DOWN_PATTERNS)


def _compare_sales(si: dict, result: dict | None) -> dict:
    """Compare what KRA holds for this CUIN against what ERPNext holds.

    Returns a flat dict carrying the status, the PIN verdict, KRA's three
    amounts, and the three variances (always ERPNext minus KRA, so a positive
    variance means ERPNext is the higher of the two).

    Precedence, most serious first:
      1. KRA portal did not answer          → Unreachable  (never hardens)
      2. KRA does not recognise the CUIN    → Not found
      3. the CUIN is not VCL's to begin with → PIN mismatch
      4. buyer PIN disagrees / is missing    → PIN mismatch
      5. any amount off by more than KES 1   → Variance
      6. otherwise                           → Matched
    """
    erp_net = flt(si.get("base_net_total"))
    erp_tax = _erp_vat_sales(si["name"])
    erp_gross = flt(si.get("base_grand_total"))

    out = {
        "status": None,
        "pin_match": None,
        "kra_buyer_name": None,
        "kra_buyer_pin": None,
        "kra_net": None,
        "kra_tax": None,
        "kra_gross": None,
        "net_variance": None,
        "tax_variance": None,
        "gross_variance": None,
        "erp_net": erp_net,
        "erp_tax": erp_tax,
        "erp_gross": erp_gross,
        "error": (result or {}).get("error"),
        "detail": None,
    }

    if _is_down(result):
        out["status"] = STATUS_UNREACHABLE
        return out

    if not result or not result.get("valid"):
        out["status"] = STATUS_NOT_FOUND
        return out

    kra_net = flt(result.get("taxable_amt"))
    kra_tax = flt(result.get("tax_amt"))
    kra_gross = flt(result.get("total_inv_amt"))
    kra_buyer_pin = (result.get("buyer_pin") or "").strip().upper()
    customer_pin = _customer_pin(si)

    # Round to cents. Without this a 95,444.80 against 95,445.00 stores a
    # gross variance of -0.19999999999708962, which reads as precision we do
    # not have.
    out.update({
        "kra_buyer_name": result.get("buyer_name"),
        "kra_buyer_pin": kra_buyer_pin,
        "kra_net": round(kra_net, 2),
        "kra_tax": round(kra_tax, 2),
        "kra_gross": round(kra_gross, 2),
        "net_variance": round(erp_net - kra_net, 2),
        "tax_variance": round(erp_tax - kra_tax, 2),
        "gross_variance": round(erp_gross - kra_gross, 2),
    })

    # Is this CUIN even ours? Older iTax receipts sometimes return an empty
    # supplier_pin while supplier_name still reads VIMIT CONVERTERS LIMITED,
    # so fall back to a name match — but ONLY when there is no PIN to go on.
    # The retired client script applied the fallback unconditionally, which
    # meant a receipt belonging to a different taxpayer whose name contained
    # "Vimit Converters" would sail through on the name alone.
    supplier_pin = (result.get("supplier_pin") or "").strip().upper()
    if supplier_pin:
        supplier_is_vcl = supplier_pin == VCL_KRA_PIN
    else:
        supplier_is_vcl = "vimit converters" in (result.get("supplier_name") or "").lower()
    if not supplier_is_vcl:
        out["status"] = STATUS_PIN_MISMATCH
        out["pin_match"] = PIN_MISMATCH
        out["detail"] = (
            f"KRA shows the supplier on this CUIN as "
            f"{result.get('supplier_name') or '(unknown)'} "
            f"(PIN {result.get('supplier_pin') or '-'}), not Vimit Converters "
            f"Limited (PIN {VCL_KRA_PIN})."
        )
        return out

    if not customer_pin:
        out["status"] = STATUS_PIN_MISMATCH
        out["pin_match"] = PIN_NO_CUSTOMER
        out["detail"] = (
            f"Customer has no KRA PIN on file; KRA holds "
            f"{kra_buyer_pin or '-'} for this receipt."
        )
        return out

    if not kra_buyer_pin:
        out["status"] = STATUS_PIN_MISMATCH
        out["pin_match"] = PIN_NO_KRA
        out["detail"] = "KRA returned no buyer PIN for this receipt."
        return out

    if kra_buyer_pin != customer_pin:
        out["status"] = STATUS_PIN_MISMATCH
        out["pin_match"] = PIN_MISMATCH
        out["detail"] = (
            f"ERPNext customer PIN {customer_pin} vs KRA buyer PIN {kra_buyer_pin}."
        )
        return out

    out["pin_match"] = PIN_MATCH

    off = [
        (label, var)
        for label, var in (
            ("net", out["net_variance"]),
            ("tax", out["tax_variance"]),
            ("gross", out["gross_variance"]),
        )
        if abs(var) > VAT_TOLERANCE
    ]
    if off:
        out["status"] = STATUS_VARIANCE
        out["detail"] = ", ".join(f"{label} {var:+,.2f}" for label, var in off)
        return out

    out["status"] = STATUS_MATCHED
    return out


def _persist_sales(si: dict, cmp: dict, checked_at) -> None:
    """Write the verdict onto the submitted invoice.

    Every KRA field is ``allow_on_submit: 0``, so this MUST go through
    ``frappe.db.set_value`` with ``update_modified=False`` — a normal save
    would be rejected, and bumping ``modified`` would both dirty the audit
    trail and make ``_is_due`` re-queue the invoice forever.
    """
    values = {
        "custom_kra_match_status": cmp["status"],
        "custom_kra_pin_match": cmp["pin_match"] or "",
        "custom_kra_checked_on": checked_at,
        "custom_kra_pending_verification": 0 if cmp["status"] == STATUS_MATCHED else 1,
    }

    # On Unreachable we learned nothing about the invoice — keep whatever KRA
    # figures we already had rather than blanking them to zero.
    if cmp["status"] != STATUS_UNREACHABLE and cmp["kra_gross"] is not None:
        values.update({
            "custom_kra_buyer_name": cmp["kra_buyer_name"] or "",
            "custom_kra_buyer_pin": cmp["kra_buyer_pin"] or "",
            "custom_kra_net_amount": cmp["kra_net"],
            "custom_kra_tax_amount": cmp["kra_tax"],
            "custom_kra_total_amount": cmp["kra_gross"],
            "custom_kra_net_variance": cmp["net_variance"],
            "custom_kra_tax_variance": cmp["tax_variance"],
            "custom_kra_gross_variance": cmp["gross_variance"],
        })

    frappe.db.set_value(
        "Sales Invoice", si["name"], values, update_modified=False
    )


def verify_sales_invoice(name: str) -> dict:
    """Re-check one Sales Invoice against KRA and persist the verdict.

    Used by the sweep and by the on-demand 'Recheck KRA now' button.
    """
    si = frappe.db.get_value(
        "Sales Invoice", name, _SI_FIELDS, as_dict=True
    )
    if not si:
        frappe.throw(f"Sales Invoice {name} not found")

    cuin = (si.get("custom_cuin") or "").strip()
    if not cuin:
        return {"status": None, "error": "This invoice has no CUIN."}

    try:
        result = validate_cuin(cuin)
    except Exception as e:  # noqa: BLE001
        result = {"valid": False, "error": f"verifier crashed: {e}"}

    cmp = _compare_sales(si, result)
    _persist_sales(si, cmp, now_datetime())
    return cmp

# ---------------------------------------------------------------------------
# Combined daily sweep
# ---------------------------------------------------------------------------

def daily_verify_kra_invoices(
    target_date: date | None = None,
    si_limit: int | None = SI_RUN_LIMIT,
) -> dict:
    """Run the daily KRA verification sweep across PI + SI, email one summary,
    and return per-doctype counts (useful for tests and CLI runs)."""

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
    si_candidates = _candidates_sales(si_limit)
    si_buckets: dict[str, list] = {k: [] for k in SI_STATUSES}

    for si in si_candidates:
        try:
            result = validate_cuin((si.get("custom_cuin") or "").strip())
        except Exception as e:  # noqa: BLE001
            result = {"valid": False, "error": f"verifier crashed: {e}"}

        cmp = _compare_sales(si, result)
        _persist_sales(si, cmp, now_datetime())
        si_buckets[cmp["status"]].append({**si, **cmp})

        time.sleep(PORTAL_THROTTLE_SECONDS)

    frappe.db.commit()

    si_backlog = len(_candidates_sales(limit=None))

    _send_report(
        target_date,
        pi_candidates, pi_buckets,
        si_candidates, si_buckets, si_backlog,
    )

    return {
        "purchase_invoice": {k: len(v) for k, v in pi_buckets.items()},
        "sales_invoice": {k: len(v) for k, v in si_buckets.items()},
        "sales_invoice_backlog": si_backlog,
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



_SI_STATUS_STYLE = {
    STATUS_MATCHED: ("#1f8b4c", "✓"),
    STATUS_VARIANCE: ("#c0392b", "✗"),
    STATUS_PIN_MISMATCH: ("#c0392b", "✗"),
    STATUS_NOT_FOUND: ("#c0392b", "✗"),
    STATUS_UNREACHABLE: ("#888", "⏸"),
}


def _si_summary_html(buckets: dict, total: int, backlog: int) -> str:
    """Counts AND the gross value behind each bucket — a hundred unreachable
    invoices matters differently at 2m than at 200m."""
    rows = []
    for status in SI_STATUSES:
        items = buckets[status]
        colour, mark = _SI_STATUS_STYLE[status]
        value = sum(flt(r.get("base_grand_total")) for r in items)
        rows.append(
            f'<tr>'
            f'<td style="padding:4px 12px;color:{colour};">{mark} {status}</td>'
            f'<td style="padding:4px 12px;text-align:right;color:{colour};">{len(items)}</td>'
            f'<td style="padding:4px 12px;text-align:right;color:{colour};">{value:,.2f}</td>'
            f'</tr>'
        )
    return f"""
    <table style="border-collapse:collapse;font-family:Arial,sans-serif;margin-top:16px;">
      <tr><th colspan="3" style="text-align:left;padding:6px 12px;background:#f0f0f0;">Sales Invoices</th></tr>
      <tr>
        <th style="padding:4px 12px;text-align:left;"></th>
        <th style="padding:4px 12px;text-align:right;">Count</th>
        <th style="padding:4px 12px;text-align:right;">Gross (KES)</th>
      </tr>
      <tr><td style="padding:4px 12px;">Checked this run</td>
          <td style="padding:4px 12px;text-align:right;"><b>{total}</b></td>
          <td style="padding:4px 12px;text-align:right;"><b>{sum(flt(r.get('base_grand_total')) for b in buckets.values() for r in b):,.2f}</b></td></tr>
      {''.join(rows)}
      <tr><td colspan="3" style="padding:6px 12px;color:#666;font-size:12px;border-top:1px solid #ddd;">
        {backlog:,} invoice(s) still awaiting a check after this run
        (cap is {SI_RUN_LIMIT} per night, oldest first).
      </td></tr>
    </table>
    """


def _si_detail_sections(buckets: dict) -> list:
    amount_cols = [
        ("SI", lambda r: r["name"]),
        ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
        ("CUIN", lambda r: r.get("custom_cuin") or "-"),
        ("ERP net", lambda r: f"{flt(r.get('erp_net')):,.2f}"),
        ("KRA net", lambda r: f"{flt(r.get('kra_net')):,.2f}"),
        ("ERP VAT", lambda r: f"{flt(r.get('erp_tax')):,.2f}"),
        ("KRA VAT", lambda r: f"{flt(r.get('kra_tax')):,.2f}"),
        ("ERP gross", lambda r: f"{flt(r.get('erp_gross')):,.2f}"),
        ("KRA gross", lambda r: f"{flt(r.get('kra_gross')):,.2f}"),
        ("Off by", lambda r: r.get("detail") or ""),
    ]
    pin_cols = [
        ("SI", lambda r: r["name"]),
        ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
        ("CUIN", lambda r: r.get("custom_cuin") or "-"),
        ("Verdict", lambda r: r.get("pin_match") or "-"),
        ("Detail", lambda r: r.get("detail") or ""),
    ]
    error_cols = [
        ("SI", lambda r: r["name"]),
        ("Customer", lambda r: r.get("customer_name") or r.get("customer")),
        ("CUIN", lambda r: r.get("custom_cuin") or "-"),
        ("Posting Date", lambda r: str(r.get("posting_date") or "")),
        ("KRA response", lambda r: (r.get("error") or "")[:200]),
    ]
    return [
        _details_table("Sales: amount variance", buckets[STATUS_VARIANCE], amount_cols),
        _details_table("Sales: PIN mismatch", buckets[STATUS_PIN_MISMATCH], pin_cols),
        _details_table("Sales: CUIN not found on KRA", buckets[STATUS_NOT_FOUND], error_cols),
        _details_table(
            "Sales: KRA unreachable (will retry tomorrow)",
            buckets[STATUS_UNREACHABLE], error_cols,
        ),
    ]


def _send_report(
    target_date: date,
    pi_candidates: list, pi_buckets: dict,
    si_candidates: list, si_buckets: dict,
    si_backlog: int,
) -> None:
    pi_total = len(pi_candidates)
    pi_counts = {k: len(v) for k, v in pi_buckets.items()}
    si_total = len(si_candidates)
    si_counts = {k: len(v) for k, v in si_buckets.items()}

    pi_fail = pi_counts["vat_mismatch"] + pi_counts["buyer_mismatch"] + pi_counts["not_found"]
    si_fail = (
        si_counts[STATUS_VARIANCE]
        + si_counts[STATUS_PIN_MISMATCH]
        + si_counts[STATUS_NOT_FOUND]
    )

    sections = [
        _pi_summary_html(pi_counts, pi_total),
        _si_summary_html(si_buckets, si_total, si_backlog),
    ]
    sections.extend(_pi_detail_sections(pi_buckets))
    sections.extend(_si_detail_sections(si_buckets))

    body = (
        f'<p style="font-family:Arial,sans-serif;">'
        f"Daily KRA CUIN verification for <b>{target_date}</b>. "
        f"Local Purchase invoices posted today, and submitted Sales Invoices "
        f"carrying a CUIN that have not been verified since they were last "
        f"changed. Each Sales Invoice is checked on customer PIN, net, tax and "
        f"gross against what KRA holds for the CUIN, within KES 1."
        f"</p>"
        + "".join(s for s in sections if s)
    )

    if si_counts[STATUS_UNREACHABLE] and si_counts[STATUS_UNREACHABLE] == si_total:
        body += (
            '<p style="font-family:Arial,sans-serif;color:#a05000;">'
            "<b>Every</b> Sales Invoice in this run came back unreachable — that "
            "points at the KRA portal being down rather than at the invoices. "
            "Nothing has been marked as failing; they will all be retried on the "
            "next run."
            "</p>"
        )

    total_fail = pi_fail + si_fail
    indicator = "⚠ " if total_fail else "✓ "
    subject = (
        f"{indicator}KRA CUIN verification {target_date} — "
        f"PI {pi_counts['pass']}/{pi_total} pass, {pi_fail} fail · "
        f"SI {si_counts[STATUS_MATCHED]}/{si_total} matched, {si_fail} fail, "
        f"{si_counts[STATUS_UNREACHABLE]} unreachable"
    )

    frappe.sendmail(
        recipients=REPORT_RECIPIENTS,
        subject=subject,
        message=body,
        reference_doctype="Sales Invoice" if si_total else "Purchase Invoice",
        now=True,
    )
