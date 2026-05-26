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

import html
import re

import frappe
import requests

VCL_KRA_PIN = "P000606160U"  # Vimit Converters Limited

ITAX_URL = (
    "https://itax.kra.go.ke/KRA-Portal/middlewareController.htm?actionCode=fetchInvoiceDtl"
)
ITAX_REFERER = (
    "https://itax.kra.go.ke/KRA-Portal/invoiceNumberChecker.htm?actionCode=loadPageInvoiceNumber"
)
ETIMS_URL = "https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsInvoiceData"
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
    supplier_pin = (data.get("supplierPIN") or "").strip().upper()
    return {
        "valid": True,
        "invoice_no": invoice_no,
        "supplier_name": data.get("supplierName"),
        "supplier_pin": supplier_pin,
        "is_vcl_supplier": supplier_pin == VCL_KRA_PIN,
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
    # eTIMS accepts the CUIN in the Data query param with the slash swapped
    # for a dash (that's how KRA's own redirect from iTax rewrites it).
    data_param = invoice_no.replace("/", "-")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # eTIMS is noticeably slower than iTax (empirically 20-45s from Frappe
    # Cloud). Use a split connect/read timeout so we fail fast when the host
    # is unreachable but wait long enough for the receipt page to render.
    # One retry on read-timeout covers transient slowness without doubling
    # the best-case latency.
    last_error = None
    for attempt in (1, 2):
        try:
            resp = requests.get(
                ETIMS_URL,
                params={"Data": data_param},
                headers=headers,
                timeout=(10, 60),
            )
            break
        except requests.exceptions.ReadTimeout as e:
            last_error = e
            if attempt == 1:
                continue
            frappe.log_error(
                title="KRA CUIN validate (eTIMS): read timeout after retry",
                message=f"invoice_no={invoice_no}\n{e}",
            )
            return {
                "valid": False,
                "invoice_no": invoice_no,
                "error": "KRA eTIMS is responding slowly. The supplier's receipt exists on the portal but our request timed out. Please try again in a moment, or open the eTIMS link directly in your browser.",
                "source": "etims",
            }
        except requests.RequestException as e:
            frappe.log_error(
                title="KRA CUIN validate (eTIMS): network error",
                message=f"invoice_no={invoice_no}\n{e}",
            )
            return {
                "valid": False,
                "invoice_no": invoice_no,
                "error": f"KRA eTIMS unreachable: {str(e)[:200]}",
                "source": "etims",
            }

    if resp.status_code != 200:
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": f"KRA eTIMS returned HTTP {resp.status_code}",
            "body_snippet": (resp.text or "")[:400],
            "source": "etims",
        }

    body = resp.text or ""

    # Login/redirect detection — eTIMS public link should render the receipt
    # inline; if it ever starts requiring auth we'll see a login page instead.
    if "login" in (resp.url or "").lower() or "<title>Login" in body:
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": "KRA eTIMS portal required login — CUIN may be invalid or portal is down",
            "source": "etims",
        }

    return _parse_etims_html(body, invoice_no)


# ---- eTIMS HTML parsing ----------------------------------------------------
#
# The public eTIMS receipt page is a tiny, hand-rolled HTML document (see the
# sample captured 2026-04-20 for KRACU0100065004/379). Rather than add a
# BeautifulSoup dependency for ~10 fields we regex-extract the values. All
# patterns are deliberately lenient on whitespace and case because KRA has
# changed the HTML subtly in the past.

# Inside "topinfo detail" block — anchored on the next <hr> rather than a
# specific div-nesting depth, because KRA has changed the internal div layout
# between receipt types (sales vs credit note vs refund).
_PATTERN_TOPINFO = re.compile(
    r'<div class="topinfo detail">(.*?)<hr',
    re.DOTALL | re.IGNORECASE,
)
_PATTERN_MIDTIT = re.compile(
    r'<div class="midtit">\s*([^<]+?)\s*</div>', re.DOTALL | re.IGNORECASE
)
# Each total-detail row looks like:  <span class="tit lt">LABEL</span> : <span class="value rt">VALUE</span>
_PATTERN_TOTAL_ROW = re.compile(
    r'<span class="tit lt">\s*([^<]+?)\s*</span>\s*:\s*'
    r'<span class="value rt">\s*([^<]+?)\s*</span>',
    re.DOTALL | re.IGNORECASE,
)
# SCU block uses value lt (left-aligned) instead of value rt
_PATTERN_SCU_ROW = re.compile(
    r'<span class="tit lt"[^>]*>\s*([^<]+?)\s*</span>\s*'
    r'<span class="value lt">\s*([^<]+?)\s*</span>',
    re.DOTALL | re.IGNORECASE,
)
_PATTERN_BARE_DIV = re.compile(r"<div[^>]*>\s*([^<]+?)\s*</div>", re.DOTALL)
_PATTERN_LABEL_IN_DIV = re.compile(
    r"<div>\s*([A-Z ]+?)\s*:\s*([^<]+?)\s*</div>", re.IGNORECASE
)


def _parse_amt(raw: str):
    """Parse a KRA amount string ('43,664.72', '- 6,022.72', '-0', ' 0 ').

    Returns ``float`` or ``None`` if unparseable. Preserves sign (credit
    notes come through as negative values).
    """
    if raw is None:
        return None
    s = raw.strip().replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_etims_html(body: str, invoice_no: str) -> dict:
    """Pure function — parses an eTIMS receipt HTML page into the shared
    response dict shape. Exposed separately from ``_validate_etims`` so it
    can be unit-tested with ``bench execute`` by feeding in a captured
    sample::

        bench --site <site> execute \
            vcl_kra_validation.api._parse_etims_html \
            --kwargs "{'body': '<paste html>', 'invoice_no': 'KRACU.../379'}"
    """
    topinfo_match = _PATTERN_TOPINFO.search(body)
    if not topinfo_match:
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": "KRA eTIMS did not return a recognisable invoice page for this CUIN",
            "body_snippet": body[:400],
            "source": "etims",
        }
    topinfo = topinfo_match.group(1)

    # Supplier name: first bare-text <div> in topinfo that is not a labelled one.
    supplier_name = None
    for div_match in _PATTERN_BARE_DIV.finditer(topinfo):
        text = html.unescape(div_match.group(1)).strip()
        if not text:
            continue
        if ":" in text and text.split(":", 1)[0].strip().upper() in {
            "PIN",
            "INVOICE NUMBER",
            "CLIENT PIN",
            "CLIENT NAME",
        }:
            continue
        supplier_name = text
        break

    labels = {
        k.strip().upper(): html.unescape(v).strip()
        for k, v in _PATTERN_LABEL_IN_DIV.findall(topinfo)
    }
    buyer_pin = (labels.get("CLIENT PIN") or "").strip().upper()
    buyer_name = labels.get("CLIENT NAME")
    supplier_pin = (labels.get("PIN") or "").strip().upper()

    midtit_match = _PATTERN_MIDTIT.search(body)
    inv_type = html.unescape(midtit_match.group(1)).strip().upper() if midtit_match else None
    is_credit_note = bool(inv_type and "CREDIT" in inv_type)

    # Totals: iterate every (label, value) pair; keep only the ones we care
    # about. "TOTAL" (the overall figure) and "TOTAL TAX" are guaranteed;
    # "TOTAL AMOUNT B-16%" / "TOTAL AMOUNT A-Ex" / etc. only appear for
    # categories that had non-zero turnover. We sum the exempt + rated
    # amounts to approximate taxable_amt for parity with iTax's taxableAmt.
    totals = {}
    for label, value in _PATTERN_TOTAL_ROW.findall(body):
        key = html.unescape(label).strip().upper()
        amt = _parse_amt(html.unescape(value))
        if amt is not None:
            totals[key] = amt

    total_inv_amt = totals.get("TOTAL")
    tax_amt = totals.get("TOTAL TAX")
    # Taxable = sum of the rated amounts (exclude the plain TOTAL row and the
    # per-band tax rows). In practice the "TOTAL AMOUNT *" rows already net
    # to taxable amount; if KRA renames these labels we silently skip.
    taxable_amt = None
    rated_amounts = [v for k, v in totals.items() if k.startswith("TOTAL AMOUNT ")]
    if rated_amounts:
        taxable_amt = sum(rated_amounts)
    elif total_inv_amt is not None and tax_amt is not None:
        taxable_amt = total_inv_amt - tax_amt

    # SCU information — look for Date, Control Unit Number, Internal Data.
    # Labels in the HTML carry a trailing ":" and sometimes &nbsp;, so strip
    # them before keying the dict.
    def _clean_label(raw):
        return html.unescape(raw).strip().rstrip(":").strip().upper()

    scu = {
        _clean_label(label): html.unescape(value).strip()
        for label, value in _PATTERN_SCU_ROW.findall(body)
    }
    inv_date = scu.get("DATE")
    control_unit_number = scu.get("CONTROL UNIT NUMBER")

    # trader_system_inv_no: eTIMS doesn't carry a "supplier's own invoice
    # number" field. Use the slash suffix (the receipt number within the SCU)
    # which is the closest unique identifier and is what VCL quotes verbally.
    trader_system_inv_no = invoice_no.rsplit("/", 1)[-1] if "/" in invoice_no else None

    # Basic sanity gate: if we can't even get the totals + client PIN, treat
    # it as a failed parse rather than a valid-but-empty response.
    if not buyer_pin or total_inv_amt is None:
        return {
            "valid": False,
            "invoice_no": invoice_no,
            "error": "KRA eTIMS response did not contain expected receipt fields",
            "body_snippet": body[:400],
            "source": "etims",
        }

    return {
        "valid": True,
        "invoice_no": invoice_no,
        "supplier_name": supplier_name,
        "supplier_pin": supplier_pin,
        "is_vcl_supplier": supplier_pin == VCL_KRA_PIN,
        "buyer_name": buyer_name,
        "buyer_pin": buyer_pin,
        "is_vcl_buyer": buyer_pin == VCL_KRA_PIN,
        "trader_system_inv_no": trader_system_inv_no,
        "inv_date": inv_date,
        "inv_transmission_dt": inv_date,
        "inv_category": None,
        "inv_type": inv_type,
        "taxable_amt": taxable_amt,
        "tax_amt": tax_amt,
        "total_inv_amt": total_inv_amt,
        "source": "etims",
        "is_credit_note": is_credit_note,
        "control_unit_number": control_unit_number,
    }
