// VCL KRA CUIN Validation — Purchase Invoice client script
// Installed via fixture by the vcl_kra_validation app. Do not edit in place;
// update the fixture source in the app repo and redeploy.

const VCL_KRA_TYPE = 'Local Purchase';
const VCL_KRA_FIELDS = [
    'custom_kra_supplier_name',
    'custom_kra_invoice_number',
    'custom_kra_tax_amount',
    'custom_kra_total_amount',
];
const VCL_KRA_TOLERANCE = 1.0; // KES 1

// Last CUIN we successfully sent to KRA. Used to skip duplicate calls when
// the user tabs out of bill_no and back in without changing the value.
let _vclKraLastCuin = null;

function isLocalPurchase(frm) {
    return (frm.doc.custom_purchase_invoice_type || '').trim() === VCL_KRA_TYPE;
}

// Mirrored from Supplier.custom_kra_cuin_exempt via fetch_from. When set,
// the supplier issues invoices outside the KRA eTIMS regime (e.g. Safaricom,
// EASY WATER SERVICES, insurance brokers) so all KRA validation is skipped.
function isKraExempt(frm) {
    return Boolean(frm.doc.custom_kra_cuin_exempt);
}

// KRA portal/iTax outage signatures returned by the server API. When KRA
// itself is broken (HTML served instead of JSON, network timeout, slow
// responses) we soft-fail: mark the invoice as pending verification, let
// it submit, and re-check at end of day via the scheduled job.
const VCL_KRA_DOWN_PATTERNS = [
    /Non-JSON response from KRA/i,
    /KRA portal unreachable/i,
    /KRA eTIMS portal unreachable/i,
    /responding slowly/i,
    /Read timed out/i,
    /Connection refused/i,
];

function isKraDownError(errorMsg) {
    if (!errorMsg) return false;
    return VCL_KRA_DOWN_PATTERNS.some((re) => re.test(errorMsg));
}

function clearKraFields(frm) {
    VCL_KRA_FIELDS.forEach((f) => frm.set_value(f, null));
}

function fmtKes(value) {
    return format_currency(flt(value), 'KES');
}

// Sum of VAT tax rows (account head contains "VAT", case-insensitive).
// Excludes non-VAT levies like ERC, WARMA, REP that vendors such as KPLC
// charge alongside VAT-rated supply.
function erpVatTotal(frm) {
    return (frm.doc.taxes || []).reduce((sum, row) => {
        const acc = (row.account_head || '').toLowerCase();
        return acc.includes('vat') ? sum + flt(row.base_tax_amount) : sum;
    }, 0);
}

// Returns -1 for Debit Notes / Credit Notes (is_return = 1), else 1. KRA
// reports the absolute invoice value; ERPNext returns are negative. Multiply
// KRA values by this before comparing or displaying so signs line up.
function kraSign(frm) {
    return frm.doc.is_return ? -1 : 1;
}

function comparisonTable(rows) {
    // rows: array of [label, erpnext_value, kra_value]
    const body = rows
        .map((r) => {
            const erp = flt(r[1]);
            const kra = flt(r[2]);
            const diff = erp - kra;
            const ok = Math.abs(diff) <= VCL_KRA_TOLERANCE;
            const colour = ok ? '#1f8b4c' : '#c0392b';
            const sign = diff > 0 ? '+' : '';
            return `<tr>
                <td style="padding:4px 8px;">${r[0]}</td>
                <td style="padding:4px 8px; text-align:right; font-variant-numeric:tabular-nums;">${fmtKes(erp)}</td>
                <td style="padding:4px 8px; text-align:right; font-variant-numeric:tabular-nums;">${fmtKes(kra)}</td>
                <td style="padding:4px 8px; text-align:right; font-variant-numeric:tabular-nums; color:${colour};">${sign}${fmtKes(diff)}</td>
            </tr>`;
        })
        .join('');
    return `<table style="width:100%; border-collapse:collapse; margin:8px 0;">
        <thead>
            <tr style="background:#f5f5f5;">
                <th style="padding:6px 8px; text-align:left;">Field</th>
                <th style="padding:6px 8px; text-align:right;">ERPNext (base, KES)</th>
                <th style="padding:6px 8px; text-align:right;">KRA</th>
                <th style="padding:6px 8px; text-align:right;">Difference</th>
            </tr>
        </thead>
        <tbody>${body}</tbody>
    </table>`;
}

function reviewChecklist() {
    return `<p style="margin-top:12px;"><b>Things to verify before submitting:</b></p>
        <ol style="margin:4px 0 8px 18px; padding:0;">
            <li>Each item's <b>rate</b> and <b>quantity</b> match the supplier's tax invoice.</li>
            <li><b>Tax rate</b> and <b>tax category</b> are set correctly (e.g. Domestic VAT 16%).</li>
            <li><b>Currency</b> and <b>exchange rate</b> are correct (KRA always reports KES; we compare base-currency totals).</li>
            <li>No items are missing or duplicated; check for rounding differences in line discounts.</li>
            <li>The supplier did not amend or re-issue the eTIMS invoice after this CUIN was generated.</li>
        </ol>
        <p style="margin-top:8px;">If you cannot reconcile the difference, <b>contact the supplier</b> to confirm the correct figures or request the latest CUIN.</p>`;
}

function runKraValidation(frm, cuin) {
    frappe.call({
        method: 'vcl_kra_validation.api.validate_cuin',
        args: { invoice_no: cuin },
        freeze: true,
        freeze_message: __('Validating CUIN on KRA…'),
        callback(r) {
            const d = r.message || {};
            if (!d.valid) {
                // Distinguish "KRA is currently down" from "CUIN truly invalid".
                if (isKraDownError(d.error)) {
                    // Soft-fail: mark the invoice as pending verification and
                    // let the user submit. The daily scheduled job will retry.
                    frm.set_value('custom_kra_pending_verification', 1);
                    frappe.msgprint({
                        title: __('KRA iTax is currently unavailable'),
                        message:
                            '<p>' + __('KRA could not be reached to validate <b>{0}</b> right now. This is on KRA\'s side, not yours.', [cuin]) + '</p>' +
                            '<p><b>' + __('KRA response:') + '</b> ' + frappe.utils.escape_html(d.error || '') + '</p>' +
                            '<p>' + __('This invoice has been flagged as <b>KRA pending verification</b>. You can save and submit normally — the daily verification job will re-check this CUIN at end of day and email the result to purchasing@vimit.com.') + '</p>',
                        indicator: 'orange',
                    });
                    return;
                }
                // Genuine "CUIN not found" — keep the hard block.
                clearKraFields(frm);
                const source = (d.source || 'itax') === 'etims' ? 'eTIMS' : 'iTax';
                frappe.msgprint({
                    title: __('KRA could not validate this CUIN'),
                    message:
                        __(
                            'KRA {0} did not recognise <b>{1}</b>.',
                            [source, cuin]
                        ) +
                        '<br><br><b>' + __('KRA response:') + '</b> ' +
                        frappe.utils.escape_html(d.error || 'invalid CUIN') +
                        '<br><br><p><b>' + __('What to do:') + '</b></p>' +
                        '<ol style="margin:4px 0 8px 18px; padding:0;">' +
                        '<li>' + __('Re-check the CUIN against the supplier\'s tax invoice — every digit matters.') + '</li>' +
                        '<li>' + __('Try the CUIN directly on <a href="https://itax.kra.go.ke/KRA-Portal/invoiceNumberChecker.htm" target="_blank">KRA iTax</a> (slash-containing CUINs redirect to <a href="https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsInvoiceData" target="_blank">eTIMS</a>).') + '</li>' +
                        '<li>' + __('If KRA also says "not found", <b>contact the supplier</b> — the eTIMS invoice may have been cancelled, re-issued, or never transmitted to KRA.') + '</li>' +
                        '</ol>' +
                        '<p>' + __('You can save this invoice as a Draft while you investigate, but Submit will be blocked until a valid CUIN is entered.') + '</p>',
                    indicator: 'red',
                });
                return;
            }
            // Valid response from KRA — clear pending flag if it was set.
            if (frm.doc.custom_kra_pending_verification) {
                frm.set_value('custom_kra_pending_verification', 0);
            }
            frm.set_value('custom_kra_supplier_name', d.supplier_name || '');
            frm.set_value('custom_kra_invoice_number', d.trader_system_inv_no || '');
            frm.set_value('custom_kra_tax_amount', d.tax_amt || 0);
            frm.set_value('custom_kra_total_amount', d.total_inv_amt || 0);

            if (!d.is_vcl_buyer) {
                frappe.msgprint({
                    title: __('KRA buyer mismatch — needs review'),
                    message:
                        __(
                            'This KRA invoice is made out to <b>{0}</b> (PIN <code>{1}</code>), not to Vimit Converters Limited (PIN <code>P000606160U</code>).',
                            [d.buyer_name || '(unknown)', d.buyer_pin || '-']
                        ) +
                        '<br><br><b>' + __('Do not record this Purchase Invoice without confirming with the supplier.') + '</b>' +
                        '<br><br>' + __('Most likely the supplier issued the eTIMS invoice to the wrong KRA PIN. Ask them to cancel it and re-issue against PIN <code>P000606160U</code>, then enter the new CUIN here.'),
                    indicator: 'red',
                });
            } else {
                const label = d.is_credit_note ? __('KRA (Credit Note)') : __('KRA');
                frappe.show_alert(
                    {
                        message: __('{0}: {1} — Tax {2}, Total {3}', [
                            label,
                            d.supplier_name,
                            fmtKes(d.tax_amt),
                            fmtKes(d.total_inv_amt),
                        ]),
                        indicator: 'green',
                    },
                    6
                );
            }
        },
    });
}

// Attach a real DOM blur handler to the bill_no input. Frappe's own
// bill_no(frm) handler fires on its internal debounced change — which can
// trigger while the user is still typing. We want validation to fire ONLY
// when focus actually leaves the field.
function attachKraBlurHandler(frm) {
    const field = frm.fields_dict && frm.fields_dict.bill_no;
    if (!field || !field.$input) return;
    field.$input.off('blur.vclKra').on('blur.vclKra', () => {
        if (!isLocalPurchase(frm) || isKraExempt(frm)) return;
        const cuin = (frm.doc.bill_no || '').trim();
        if (!cuin) {
            clearKraFields(frm);
            _vclKraLastCuin = null;
            return;
        }
        if (cuin === _vclKraLastCuin) return; // unchanged since last validation
        _vclKraLastCuin = cuin;
        runKraValidation(frm, cuin);
    });
}

frappe.ui.form.on('Purchase Invoice', {
    onload(frm) {
        attachKraBlurHandler(frm);
    },

    refresh(frm) {
        // Re-attach in case the input element was re-rendered by Frappe.
        attachKraBlurHandler(frm);
    },

    custom_purchase_invoice_type(frm) {
        if (!isLocalPurchase(frm)) {
            clearKraFields(frm);
            _vclKraLastCuin = null;
            return;
        }
        attachKraBlurHandler(frm);
        if (isKraExempt(frm)) return; // supplier flagged exempt — skip
        const cuin = (frm.doc.bill_no || '').trim();
        if (cuin && cuin !== _vclKraLastCuin) {
            _vclKraLastCuin = cuin;
            runKraValidation(frm, cuin);
        }
    },

    bill_no(frm) {
        // The DOM blur handler (attachKraBlurHandler) covers the typing flow,
        // but it does not fire when the user pastes a CUIN and clicks Save
        // without ever blurring the field. This Frappe-level handler is the
        // safety net for that path: it fires whenever bill_no commits
        // (programmatic set_value, paste followed by another field commit,
        // or Frappe's internal change cycle). Dedup via _vclKraLastCuin
        // keeps the blur handler from double-firing.
        const cuin = (frm.doc.bill_no || '').trim();
        if (!cuin) {
            clearKraFields(frm);
            _vclKraLastCuin = null;
            return;
        }
        if (!isLocalPurchase(frm) || isKraExempt(frm)) return;
        if (cuin === _vclKraLastCuin) return;
        _vclKraLastCuin = cuin;
        runKraValidation(frm, cuin);
    },

    validate(frm) {
        if (!isLocalPurchase(frm) || isKraExempt(frm)) return;
        if (frm.doc.custom_kra_pending_verification) return; // EOD job will re-verify

        const kra_tax = flt(frm.doc.custom_kra_tax_amount);
        const kra_total = flt(frm.doc.custom_kra_total_amount);
        if (!kra_total && !kra_tax) return; // no KRA data loaded — nothing to compare

        const sign = kraSign(frm);
        const kra_tax_signed = sign * kra_tax;
        const kra_total_signed = sign * kra_total;
        const erp_vat = erpVatTotal(frm);
        const erp_gt = flt(frm.doc.base_grand_total);

        if (Math.abs(erp_vat - kra_tax_signed) <= VCL_KRA_TOLERANCE) return; // VAT matches — non-VAT items are not validated

        const non_vat_diff = erp_gt - kra_total_signed;
        const non_vat_note =
            Math.abs(non_vat_diff) > VCL_KRA_TOLERANCE
                ? '<p style="margin-top:8px; color:#666;"><i>' +
                  __('Non-VAT items in ERPNext (e.g. exempt levies, fuel adjustments): {0}. These are outside KRA scope and are not compared.', [fmtKes(non_vat_diff)]) +
                  '</i></p>'
                : '';

        frappe.msgprint({
            title: __('KRA VAT does not match — needs review'),
            message:
                '<p>' + __('The VAT on this Purchase Invoice does not match KRA for this CUIN. <b>This needs to be reviewed before submission.</b>') + '</p>' +
                comparisonTable([
                    [__('VAT'), erp_vat, kra_tax_signed],
                ]) +
                non_vat_note +
                reviewChecklist() +
                '<p style="margin-top:8px;"><i>' + __('You can save the invoice as a Draft now and continue the review later. Submit will remain blocked until the VAT matches (within KES {0}).', [VCL_KRA_TOLERANCE.toFixed(2)]) + '</i></p>',
            indicator: 'orange',
        });
    },

    before_submit(frm) {
        if (!isLocalPurchase(frm) || isKraExempt(frm)) return;

        // Soft-fail path: KRA was unreachable at entry time, so the invoice
        // has been marked pending verification. Allow submit — the daily job
        // will re-verify and report to purchasing@vimit.com.
        if (frm.doc.custom_kra_pending_verification) return;

        // Case A — bill_no missing entirely
        if (!frm.doc.bill_no) {
            frappe.throw({
                title: __('Cannot submit — Supplier Invoice No. is required'),
                message:
                    '<p>' + __('Every Local Purchase invoice must carry the supplier\'s KRA CUIN in the <b>Supplier Invoice No.</b> field.') + '</p>' +
                    '<p>' + __('Enter the CUIN from the supplier\'s tax invoice and tab out — the form will validate it against KRA (iTax for digit-only CUINs, eTIMS for slash-containing CUINs) automatically.') + '</p>',
                indicator: 'red',
            });
            return;
        }

        // Case B — bill_no set but KRA did not recognise it
        if (!frm.doc.custom_kra_total_amount) {
            frappe.throw({
                title: __('Cannot submit — KRA did not recognise this CUIN'),
                message:
                    '<p>' + __('Supplier Invoice No. <b>{0}</b> was not found on KRA. The invoice cannot be submitted in this state.', [frm.doc.bill_no]) + '</p>' +
                    '<p><b>' + __('What to do:') + '</b></p>' +
                    '<ol style="margin:4px 0 8px 18px; padding:0;">' +
                    '<li>' + __('Verify the CUIN against the supplier\'s tax invoice — copy-paste rather than re-typing if possible.') + '</li>' +
                    '<li>' + __('Try the CUIN directly on <a href="https://itax.kra.go.ke/KRA-Portal/invoiceNumberChecker.htm" target="_blank">KRA iTax</a> (slashes redirect to eTIMS). If KRA also says "not found", the eTIMS record does not exist.') + '</li>' +
                    '<li>' + __('<b>Contact the supplier</b> — the eTIMS invoice may have been cancelled, never transmitted, or re-issued. Request the latest valid CUIN.') + '</li>' +
                    '</ol>' +
                    '<p style="margin-top:8px;"><i>' + __('You can keep this invoice as a Draft while you investigate.') + '</i></p>',
                indicator: 'red',
            });
            return;
        }

        // Case C — KRA fields populated; check VAT matches (in KES base, signed for returns)
        const sign = kraSign(frm);
        const kra_tax_signed = sign * flt(frm.doc.custom_kra_tax_amount);
        const kra_total_signed = sign * flt(frm.doc.custom_kra_total_amount);
        const erp_vat = erpVatTotal(frm);
        const erp_gt = flt(frm.doc.base_grand_total);

        if (Math.abs(erp_vat - kra_tax_signed) <= VCL_KRA_TOLERANCE) return; // VAT matches — allow submit

        const non_vat_diff = erp_gt - kra_total_signed;
        const non_vat_note =
            Math.abs(non_vat_diff) > VCL_KRA_TOLERANCE
                ? '<p style="margin-top:8px; color:#666;"><i>' +
                  __('Non-VAT items in ERPNext (e.g. exempt levies, fuel adjustments): {0}. These are outside KRA scope and are not compared.', [fmtKes(non_vat_diff)]) +
                  '</i></p>'
                : '';

        frappe.throw({
            title: __('Cannot submit — VAT does not match KRA'),
            message:
                '<p>' + __('The VAT on this Purchase Invoice does not match KRA and <b>needs to be reviewed</b> before it can be submitted.') + '</p>' +
                comparisonTable([
                    [__('VAT'), erp_vat, kra_tax_signed],
                ]) +
                non_vat_note +
                reviewChecklist() +
                '<p style="margin-top:8px;"><i>' + __('Save as a Draft to keep your work; submit once the VAT matches or the supplier confirms the figures.') + '</i></p>',
            indicator: 'red',
        });
    },
});
