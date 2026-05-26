// VCL KRA CUIN Validation — Sales Invoice client script
// Installed via fixture by the vcl_kra_validation app. Do not edit in place;
// update the fixture source in the app repo and redeploy.
//
// Fires when the user enters / pastes a CUIN in custom_cuin on a Sales
// Invoice. VCL is the SUPPLIER (issuer) of these eTIMS receipts, so the
// checks are:
//   1. KRA recognises the CUIN
//   2. KRA's supplier_pin matches VCL's PIN (i.e. the CUIN belongs to us)
//   3. KRA's buyer_pin matches the Customer's tax_id (we billed the right party)
//   4. KRA's VAT matches the SI VAT within KES 1
// All four are ADVISORY — no submit block. VCL workflow may submit a SI
// before the eTIMS receipt exists, and the daily 19:00 sweep
// (vcl_kra_validation.scheduled_tasks) is the end-of-day backstop.

const VCL_KRA_PIN = 'P000606160U';
const VCL_KRA_SI_FIELDS = [
    'custom_kra_buyer_name',
    'custom_kra_buyer_pin',
    'custom_kra_tax_amount',
    'custom_kra_total_amount',
];
const VCL_KRA_SI_TOLERANCE = 1.0; // KES 1

// Last CUIN we successfully sent to KRA. Used to skip duplicate calls when
// blur + Frappe change both fire for the same value.
let _vclKraSiLastCuin = null;

function siIsKraScope(frm) {
    // In eTIMS scope if the invoice carries any VAT. We check three signals
    // because legacy SIs in VCL's data are inconsistent:
    //   1. tax_category = 'Domestic VAT' (the canonical flag), OR
    //   2. taxes_and_charges template name contains 'Domestic VAT' (older SIs
    //      that have the template but never set the category field), OR
    //   3. there is at least one VAT tax row on the invoice (catches manual
    //      tax row entries that bypass the template).
    // Export / zero-rated SIs satisfy none of these and are correctly skipped.
    const cat = (frm.doc.tax_category || '').trim();
    if (cat === 'Domestic VAT') return true;
    const tpl = (frm.doc.taxes_and_charges || '').toLowerCase();
    if (tpl.includes('domestic vat')) return true;
    return siErpVatTotal(frm) > 0;
}

function siIsKraExempt(frm) {
    return Boolean(frm.doc.custom_kra_cuin_exempt);
}

const VCL_KRA_SI_DOWN_PATTERNS = [
    /Non-JSON response from KRA/i,
    /KRA portal unreachable/i,
    /KRA eTIMS portal unreachable/i,
    /responding slowly/i,
    /Read timed out/i,
    /Connection refused/i,
];

function siIsKraDownError(errorMsg) {
    if (!errorMsg) return false;
    return VCL_KRA_SI_DOWN_PATTERNS.some((re) => re.test(errorMsg));
}

function clearSiKraFields(frm) {
    VCL_KRA_SI_FIELDS.forEach((f) => frm.set_value(f, null));
}

function siFmtKes(value) {
    return format_currency(flt(value), 'KES');
}

// Sum of VAT tax rows (account head contains "VAT", case-insensitive).
// Mirrors the PI side — excludes non-VAT levies.
function siErpVatTotal(frm) {
    return (frm.doc.taxes || []).reduce((sum, row) => {
        const acc = (row.account_head || '').toLowerCase();
        return acc.includes('vat') ? sum + flt(row.base_tax_amount) : sum;
    }, 0);
}

function runSiKraValidation(frm, cuin) {
    frappe.call({
        method: 'vcl_kra_validation.api.validate_cuin',
        args: { invoice_no: cuin },
        freeze: true,
        freeze_message: __('Validating CUIN on KRA…'),
        callback(r) {
            const d = r.message || {};
            if (!d.valid) {
                if (siIsKraDownError(d.error)) {
                    frm.set_value('custom_kra_pending_verification', 1);
                    frappe.show_alert(
                        {
                            message: __('KRA portal unavailable — flagged for end-of-day re-check'),
                            indicator: 'orange',
                        },
                        6
                    );
                    return;
                }
                clearSiKraFields(frm);
                const source = (d.source || 'itax') === 'etims' ? 'eTIMS' : 'iTax';
                frappe.msgprint({
                    title: __('KRA could not validate this CUIN'),
                    message:
                        __('KRA {0} did not recognise <b>{1}</b>.', [source, cuin]) +
                        '<br><br><b>' + __('KRA response:') + '</b> ' +
                        frappe.utils.escape_html(d.error || 'invalid CUIN') +
                        '<br><br>' + __('Check the CUIN against the eTIMS receipt — every character matters. If KRA still says "not found", the receipt may not yet be transmitted or may have been cancelled / re-issued.'),
                    indicator: 'red',
                });
                return;
            }

            // Valid — clear pending flag if it was set.
            if (frm.doc.custom_kra_pending_verification) {
                frm.set_value('custom_kra_pending_verification', 0);
            }

            frm.set_value('custom_kra_buyer_name', d.buyer_name || '');
            frm.set_value('custom_kra_buyer_pin', d.buyer_pin || '');
            frm.set_value('custom_kra_tax_amount', d.tax_amt || 0);
            frm.set_value('custom_kra_total_amount', d.total_inv_amt || 0);

            // ---- Advisory checks ----
            const warnings = [];

            // is_vcl_supplier is true when KRA's supplier_pin matches VCL.
            // Older iTax receipts sometimes return an EMPTY supplier_pin even
            // though the supplier_name field reads 'VIMIT CONVERTERS LIMITED'
            // — so we fall back to a name match before raising the warning.
            const supplierLooksLikeVcl =
                d.is_vcl_supplier ||
                /vimit\s*converters/i.test(d.supplier_name || '');

            if (!supplierLooksLikeVcl) {
                warnings.push(
                    __(
                        'KRA shows the supplier on this CUIN as <b>{0}</b> (PIN <code>{1}</code>), not Vimit Converters Limited (PIN <code>{2}</code>). This CUIN does not belong to VCL.',
                        [d.supplier_name || '(unknown)', d.supplier_pin || '-', VCL_KRA_PIN]
                    )
                );
            }

            const customerPin = (frm.doc.tax_id || '').trim().toUpperCase();
            const kraBuyerPin = (d.buyer_pin || '').trim().toUpperCase();

            if (!customerPin) {
                warnings.push(
                    __(
                        'Customer <b>{0}</b> has no KRA PIN on file. KRA shows the buyer as <b>{1}</b> (PIN <code>{2}</code>). Update the Customer master with the correct PIN.',
                        [frm.doc.customer_name || frm.doc.customer, d.buyer_name || '(unknown)', kraBuyerPin || '-']
                    )
                );
            } else if (customerPin !== kraBuyerPin) {
                warnings.push(
                    __(
                        'KRA buyer PIN <code>{0}</code> does not match this customer\'s PIN on file <code>{1}</code>. Either the eTIMS receipt was issued against the wrong party, or the Customer master needs correction.',
                        [kraBuyerPin || '(none)', customerPin]
                    )
                );
            }

            const erpVat = siErpVatTotal(frm);
            const kraVat = flt(d.tax_amt);
            if (Math.abs(erpVat - kraVat) > VCL_KRA_SI_TOLERANCE) {
                warnings.push(
                    __(
                        'ERPNext VAT ({0}) and KRA VAT ({1}) differ by <b>{2}</b>. Confirm the eTIMS receipt was issued for the same line items / rates.',
                        [siFmtKes(erpVat), siFmtKes(kraVat), siFmtKes(erpVat - kraVat)]
                    )
                );
            }

            if (warnings.length) {
                frappe.msgprint({
                    title: __('KRA validated — please review {0} warning(s)', [warnings.length]),
                    message:
                        '<ul style="margin:0;padding-left:20px;"><li>' +
                        warnings.join('</li><li>') +
                        '</li></ul>' +
                        '<p style="margin-top:10px;color:#666;"><i>' +
                        __('These are advisory only. The Sales Invoice can still be submitted; the daily 19:00 verification job will include any unresolved issues in the report to purchasing@vimit.com.') +
                        '</i></p>',
                    indicator: 'orange',
                });
            } else {
                frappe.show_alert(
                    {
                        message: __('KRA: {0} — Tax {1}, Total {2}', [
                            d.buyer_name,
                            siFmtKes(d.tax_amt),
                            siFmtKes(d.total_inv_amt),
                        ]),
                        indicator: 'green',
                    },
                    6
                );
            }
        },
    });
}

// Attach a real DOM blur handler to the custom_cuin input — same pattern
// as the PI side. Fires on focus-leave; deduped via _vclKraSiLastCuin.
function attachSiKraBlurHandler(frm) {
    const field = frm.fields_dict && frm.fields_dict.custom_cuin;
    if (!field || !field.$input) return;
    field.$input.off('blur.vclKraSi').on('blur.vclKraSi', () => {
        if (!siIsKraScope(frm) || siIsKraExempt(frm)) return;
        const cuin = (frm.doc.custom_cuin || '').trim();
        if (!cuin) {
            clearSiKraFields(frm);
            _vclKraSiLastCuin = null;
            return;
        }
        if (cuin === _vclKraSiLastCuin) return;
        _vclKraSiLastCuin = cuin;
        runSiKraValidation(frm, cuin);
    });
}

frappe.ui.form.on('Sales Invoice', {
    onload(frm) {
        attachSiKraBlurHandler(frm);
    },

    refresh(frm) {
        // Re-attach in case the input was re-rendered.
        attachSiKraBlurHandler(frm);

        // Auto-validate on form load. If the SI is in eTIMS scope, has a
        // CUIN, and the KRA fields haven't been populated yet (older drafts
        // created before this script existed, or KRA was down at entry
        // time), fire validation once. Skip if we've already validated
        // this CUIN in this session or the fields already hold KRA data.
        const cuin = (frm.doc.custom_cuin || '').trim();
        if (!cuin) return;
        if (!siIsKraScope(frm) || siIsKraExempt(frm)) return;
        if (cuin === _vclKraSiLastCuin) return;
        if (flt(frm.doc.custom_kra_total_amount) > 0) {
            // Already validated previously and persisted — remember the
            // CUIN so the blur handler doesn't re-fire unnecessarily.
            _vclKraSiLastCuin = cuin;
            return;
        }
        _vclKraSiLastCuin = cuin;
        runSiKraValidation(frm, cuin);
    },

    custom_cuin(frm) {
        // Safety net for paste-then-save without blur. Mirrors the PI
        // bill_no(frm) fix. Dedup keeps blur + Frappe change from
        // double-firing.
        const cuin = (frm.doc.custom_cuin || '').trim();
        if (!cuin) {
            clearSiKraFields(frm);
            _vclKraSiLastCuin = null;
            return;
        }
        if (!siIsKraScope(frm) || siIsKraExempt(frm)) return;
        if (cuin === _vclKraSiLastCuin) return;
        _vclKraSiLastCuin = cuin;
        runSiKraValidation(frm, cuin);
    },

    tax_category(frm) {
        // If user switches to/from Domestic VAT, clear the KRA fields when
        // leaving scope so stale data doesn't linger.
        if (!siIsKraScope(frm)) {
            clearSiKraFields(frm);
            _vclKraSiLastCuin = null;
        }
    },
});
