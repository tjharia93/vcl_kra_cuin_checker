"""KRA Daily Verification (SA-07).

What the nightly sweep found, per invoice, with the counts and money behind
each verdict. The point of the totals is the question Tanuj actually asked:
is iTax genuinely rejecting VCL's receipts, or is it simply not answering?
"Unreachable" is broken out from "Not found" everywhere for exactly that
reason — they are very different problems and only one of them is ours.
"""

import frappe
from frappe import _
from frappe.utils import flt

from vcl_kra_validation.scheduled_tasks import (
    STATUS_MATCHED,
    STATUS_NOT_FOUND,
    STATUS_PIN_MISMATCH,
    STATUS_UNREACHABLE,
    STATUS_VARIANCE,
    SI_STATUSES,
    VCL_COMPANY,
)

STATUS_INDICATOR = {
    STATUS_MATCHED: "green",
    STATUS_VARIANCE: "red",
    STATUS_PIN_MISMATCH: "red",
    STATUS_NOT_FOUND: "red",
    STATUS_UNREACHABLE: "orange",
}

NOT_CHECKED = "Not checked"


def execute(filters=None):
    filters = frappe._dict(filters or {})
    data = _fetch(filters)
    return (
        _columns(),
        data,
        None,
        _chart(data),
        _summary(data),
    )


def _fetch(filters):
    conditions = {
        "docstatus": 1,
        "company": filters.get("company") or VCL_COMPANY,
        "custom_cuin": ["is", "set"],
        "custom_kra_cuin_exempt": 0,
    }
    if filters.get("from_date") and filters.get("to_date"):
        conditions["posting_date"] = ["between", [filters.from_date, filters.to_date]]
    elif filters.get("from_date"):
        conditions["posting_date"] = [">=", filters.from_date]
    elif filters.get("to_date"):
        conditions["posting_date"] = ["<=", filters.to_date]

    if filters.get("status"):
        if filters.status == NOT_CHECKED:
            conditions["custom_kra_match_status"] = ["in", ["", None]]
        else:
            conditions["custom_kra_match_status"] = filters.status

    rows = frappe.get_all(
        "Sales Invoice",
        filters=conditions,
        fields=[
            "name", "posting_date", "customer", "customer_name", "tax_id",
            "custom_cuin", "custom_kra_match_status", "custom_kra_pin_match",
            "custom_kra_buyer_pin", "custom_kra_checked_on",
            "base_net_total", "base_grand_total",
            "custom_kra_net_amount", "custom_kra_tax_amount",
            "custom_kra_total_amount",
            "custom_kra_net_variance", "custom_kra_tax_variance",
            "custom_kra_gross_variance",
        ],
        order_by="posting_date desc, name desc",
    )

    for r in rows:
        r["status"] = r.get("custom_kra_match_status") or NOT_CHECKED
        # ERPNext VAT is not stored on the invoice — it is the sum of the VAT
        # tax rows. Recover it from the stored variance rather than re-summing
        # the child table once per row, which would be a query per invoice.
        if r.get("custom_kra_tax_variance") is not None and r.get("custom_kra_tax_amount") is not None:
            r["erp_tax"] = flt(r["custom_kra_tax_variance"]) + flt(r["custom_kra_tax_amount"])
        else:
            r["erp_tax"] = None
    return rows


def _columns():
    return [
        {"label": _("Sales Invoice"), "fieldname": "name", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 160},
        {"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
        {"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 200},
        {"label": _("CUIN"), "fieldname": "custom_cuin", "fieldtype": "Data", "width": 175},
        {"label": _("KRA Status"), "fieldname": "status", "fieldtype": "Data", "width": 115},
        {"label": _("PIN"), "fieldname": "custom_kra_pin_match", "fieldtype": "Data", "width": 120},
        {"label": _("ERP Net"), "fieldname": "base_net_total", "fieldtype": "Currency", "width": 115},
        {"label": _("KRA Net"), "fieldname": "custom_kra_net_amount", "fieldtype": "Currency", "width": 115},
        {"label": _("Net Var"), "fieldname": "custom_kra_net_variance", "fieldtype": "Currency", "width": 105},
        {"label": _("ERP VAT"), "fieldname": "erp_tax", "fieldtype": "Currency", "width": 105},
        {"label": _("KRA VAT"), "fieldname": "custom_kra_tax_amount", "fieldtype": "Currency", "width": 105},
        {"label": _("VAT Var"), "fieldname": "custom_kra_tax_variance", "fieldtype": "Currency", "width": 100},
        {"label": _("ERP Gross"), "fieldname": "base_grand_total", "fieldtype": "Currency", "width": 120},
        {"label": _("KRA Gross"), "fieldname": "custom_kra_total_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Gross Var"), "fieldname": "custom_kra_gross_variance", "fieldtype": "Currency", "width": 105},
        {"label": _("Checked On"), "fieldname": "custom_kra_checked_on", "fieldtype": "Datetime", "width": 155},
    ]


def _buckets(data):
    out = {s: [] for s in SI_STATUSES}
    out[NOT_CHECKED] = []
    for r in data:
        out.setdefault(r["status"], []).append(r)
    return out


def _summary(data):
    buckets = _buckets(data)
    cards = [{
        "label": _("Invoices"),
        "value": len(data),
        "datatype": "Int",
    }]
    for status in list(SI_STATUSES) + [NOT_CHECKED]:
        rows = buckets.get(status) or []
        cards.append({
            "label": status,
            "value": len(rows),
            "datatype": "Int",
            "indicator": STATUS_INDICATOR.get(status, "grey"),
        })
        cards.append({
            "label": _("{0} — gross").format(status),
            "value": sum(flt(r.get("base_grand_total")) for r in rows),
            "datatype": "Currency",
            "indicator": STATUS_INDICATOR.get(status, "grey"),
        })
    return cards


def _chart(data):
    buckets = _buckets(data)
    labels = [s for s in list(SI_STATUSES) + [NOT_CHECKED] if buckets.get(s)]
    if not labels:
        return None
    return {
        "data": {
            "labels": labels,
            "datasets": [{
                "name": _("Invoices"),
                "values": [len(buckets[s]) for s in labels],
            }],
        },
        "type": "bar",
    }
