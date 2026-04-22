"""Whitelisted endpoint that validates a KRA CUIN via the public iTax or eTIMS
portals, depending on CUIN shape.

Called from the Purchase Invoice client script (see fixtures/client_script.json)
as `vcl_kra_validation.api.validate_cuin`.

Digit-only CUINs (e.g. ``0190438130000017933``) hit the iTax invoice checker,
which returns JSON. Slash-containing CUINs (e.g. ``KRACU0100065004/379``) hit
the newer eTIMS receipt portal, which returns HTML — parsed here with regex.
Both paths return the same response dict shape so the client script does not
need to know which portal served the data.
"""

import frappe
import requests

VCL_KRA_PIN = "P000606160U"  # Vimit Converters Limited

ITAX_URL = (
    "https://itax.kra.go.ke/KRA-Portal/middlewareController.htm?actionCode=fetchInvoiceDtl"
)
ITAX_REFERER = (
    "https://itax.kra.go.ke/KRA-Portal/invoiceNumberChecker.htm?actionCode=loadPageInvoiceNumber"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@frappe.whitelist()
def validate_cuin(invoice_no: str) -> dict:
    invoice_no = (invoice_no or "").strip()
    if not invoice_no:
        return {"valid": False, "error": "invoice_no is required"}

    if "/" in invoice_no:
        return _validate_etims(invoice_no)
    return _validate_itax(invoice_no)


def _validate_itax(invoice_no: str) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": ITAX_REFERER,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.post(
            ITAX_URL,
            data={"invNo": invoice_no},
            headers=headers,
            timeout=20,
        )
    except requests.RequestException as e:
        frappe.log_error(
            title="KRA CUIN validate (iTax): network error",
            message=f"invoice_no={invoice_no}\n{e}",
        )
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": f"KRA iTax unreachable: {str(e)[:200]}",
            "source": "itax",
        }

    try:
        data = resp.json()
    except ValueError:
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": f"Non-JSON response from KRA iTax (status {resp.status_code})",
            "body_snippet": (resp.text or "")[:400],
            "source": "itax",
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": "Unexpected KRA iTax response shape",
            "raw": data,
            "source": "itax",
        }

    err = data.get("errorDTO") or {}
    if err.get("msg"):
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": err.get("msg"),
            "source": "itax",
        }

    if not data.get("mwInvNo"):
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": "KRA iTax returned no invoice data",
            "source": "itax",
        }

    buyer_pin = (data.get("buyerPIN") or "").strip().upper()
    return {
        "valid": True,
        "invoice_no": invoice_no,
        "supplier_name": data.get("supplierName"),
        "buyer_name": data.get("buyerName"),
        "buyer_pin": buyer_pin,
        "is_vcl_buyer": buyer_pin == VCL_KRA_PIN,
        "trader_system_inv_no": data.get("traderSystemInvNo"),
        "inv_date": data.get("invDate"),
        "inv_transmission_dt": data.get("invTransmissionDt"),
        "inv_category": data.get("invCategory"),
        "inv_type": data.get("invType"),
        "taxable_amt": data.get("taxableAmt"),
        "tax_amt": data.get("taxAmt"),
        "total_inv_amt": data.get("totalInvAmt"),
        "source": "itax",
        "is_credit_note": False,
    }


def _validate_etims(invoice_no: str) -> dict:
    # eTIMS implementation follows in the next commit.
    return {
        "valid": False,
        "invoice_no": invoice_no,
        "error": "eTIMS support not yet implemented",
        "source": "etims",
    }
