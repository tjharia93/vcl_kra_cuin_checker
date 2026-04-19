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

function isLocalPurchase(frm) {
    return (frm.doc.custom_purchase_invoice_type || '').trim() === VCL_KRA_TYPE;
}

function clearKraFields(frm) {
    VCL_KRA_FIELDS.forEach((f) => frm.set_value(f, null));
}

function fmtKes(value) {
    return format_currency(flt(value), 'KES');
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
                <th style="padding:6px 8px; text-align:right;">KRA iTax</th>
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

frappe.ui.form.on('Purchase Invoice', {
    custom_purchase_invoice_type(frm) {
        if (!isLocalPurchase(frm)) {
            clearKraFields(frm);
            return;
        }
        if (frm.doc.bill_no) {
            frm.trigger('bill_no');
        }
    },

    bill_no(frm) {
        if (!isLocalPurchase(frm)) {
            clearKraFields(frm);
            return;
        }

        const cuin = (frm.doc.bill_no || '').trim();
        if (!cuin) {
            clearKraFields(frm);
            return;
        }

        frappe.call({
            method: 'vcl_kra_validation.api.validate_cuin',
            args: { invoice_no: cuin },
            freeze: true,
            freeze_message: __('Validating CUIN on KRA iTax…'),
            callback(r) {
                const d = r.message || {};
                if (!d.valid) {
                    clearKraFields(frm);
                    frappe.msgprint({
                        title: __('KRA could not validate this CUIN'),
                        message:
                            __(
                                'KRA iTax did not recognise <b>{0}</b>.',
                                [cuin]
                            ) +
                            '<br><br><b>' + __('KRA response:') + '</b> ' +
                            frappe.utils.escape_html(d.error || 'invalid CUIN') +
                            '<br><br><p><b>' + __('What to do:') + '</b></p>' +
                            '<ol style="margin:4px 0 8px 18px; padding:0;">' +
                            '<li>' + __('Re-check the CUIN against the supplier\'s tax invoice — every digit matters.') + '</li>' +
                            '<li>' + __('Try the CUIN directly on <a href="https://itax.kra.go.ke/KRA-Portal/invoiceNumberChecker.htm" target="_blank">KRA iTax invoice checker</a> to confirm it does not exist there either.') + '</li>' +
                            '<li>' + __('If iTax also says "not found", <b>contact the supplier</b> — the eTIMS invoice may have been cancelled, re-issued, or never transmitted to KRA.') + '</li>' +
                            '</ol>' +
                            '<p>' + __('You can save this invoice as a Draft while you investigate, but Submit will be blocked until a valid CUIN is entered.') + '</p>',
                        indicator: 'red',
                    });
                    return;
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
                    frappe.show_alert(
                        {
                            message: __('KRA: {0} — Tax {1}, Total {2}', [
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
    },

    validate(frm) {
        if (!isLocalPurchase(frm)) return;

        const kra_total = frm.doc.custom_kra_total_amount;
        const kra_tax = frm.doc.custom_kra_tax_amount;
        if (!kra_total && !kra_tax) return; // no KRA data loaded — nothing to compare

        const gt = flt(frm.doc.base_grand_total);
        const tt = flt(frm.doc.base_total_taxes_and_charges);
        const totalsMismatch = Math.abs(gt - flt(kra_total)) > VCL_KRA_TOLERANCE;
        const taxesMismatch = Math.abs(tt - flt(kra_tax)) > VCL_KRA_TOLERANCE;

        if (!totalsMismatch && !taxesMismatch) return;

        frappe.msgprint({
            title: __('KRA totals need review'),
            message:
                '<p>' + __('The Purchase Invoice totals do not match KRA iTax for this CUIN. <b>This invoice needs to be reviewed before it can be submitted.</b>') + '</p>' +
                comparisonTable([
                    [__('Grand Total'), gt, kra_total],
                    [__('Total Taxes'), tt, kra_tax],
                ]) +
                reviewChecklist() +
                '<p style="margin-top:8px;"><i>' + __('You can save the invoice as a Draft now and continue the review later. Submit will remain blocked until the totals match (within KES {0}) or the supplier provides a corrected CUIN.', [VCL_KRA_TOLERANCE.toFixed(2)]) + '</i></p>',
            indicator: 'orange',
        });
    },

    before_submit(frm) {
        if (!isLocalPurchase(frm)) return;

        // Case A — bill_no missing entirely
        if (!frm.doc.bill_no) {
            frappe.throw({
                title: __('Cannot submit — Supplier Invoice No. is required'),
                message:
                    '<p>' + __('Every Local Purchase invoice must carry the supplier\'s KRA eTIMS CUIN in the <b>Supplier Invoice No.</b> field.') + '</p>' +
                    '<p>' + __('Enter the CUIN from the supplier\'s tax invoice and tab out — the form will validate it against KRA iTax automatically.') + '</p>',
                indicator: 'red',
            });
            return;
        }

        // Case B — bill_no set but KRA did not recognise it
        if (!frm.doc.custom_kra_total_amount) {
            frappe.throw({
                title: __('Cannot submit — KRA did not recognise this CUIN'),
                message:
                    '<p>' + __('Supplier Invoice No. <b>{0}</b> was not found on KRA iTax. The invoice cannot be submitted in this state.', [frm.doc.bill_no]) + '</p>' +
                    '<p><b>' + __('What to do:') + '</b></p>' +
                    '<ol style="margin:4px 0 8px 18px; padding:0;">' +
                    '<li>' + __('Verify the CUIN against the supplier\'s tax invoice — copy-paste rather than re-typing if possible.') + '</li>' +
                    '<li>' + __('Try the CUIN directly on <a href="https://itax.kra.go.ke/KRA-Portal/invoiceNumberChecker.htm" target="_blank">KRA iTax</a>. If iTax also says "not found", the eTIMS record does not exist.') + '</li>' +
                    '<li>' + __('<b>Contact the supplier</b> — the eTIMS invoice may have been cancelled, never transmitted, or re-issued. Request the latest valid CUIN.') + '</li>' +
                    '</ol>' +
                    '<p style="margin-top:8px;"><i>' + __('You can keep this invoice as a Draft while you investigate.') + '</i></p>',
                indicator: 'red',
            });
            return;
        }

        // Case C — KRA fields populated; check totals match (in KES base)
        const gt = flt(frm.doc.base_grand_total);
        const tt = flt(frm.doc.base_total_taxes_and_charges);
        const kt = flt(frm.doc.custom_kra_total_amount);
        const kx = flt(frm.doc.custom_kra_tax_amount);
        const totalsMismatch = Math.abs(gt - kt) > VCL_KRA_TOLERANCE;
        const taxesMismatch = Math.abs(tt - kx) > VCL_KRA_TOLERANCE;

        if (!totalsMismatch && !taxesMismatch) return; // all good — allow submit

        frappe.throw({
            title: __('Cannot submit — totals do not match KRA'),
            message:
                '<p>' + __('This Purchase Invoice does not match KRA iTax and <b>needs to be reviewed</b> before it can be submitted.') + '</p>' +
                comparisonTable([
                    [__('Grand Total'), gt, kt],
                    [__('Total Taxes'), tt, kx],
                ]) +
                reviewChecklist() +
                '<p style="margin-top:8px;"><i>' + __('Save as a Draft to keep your work; submit once the differences are resolved or the supplier confirms the figures.') + '</i></p>',
            indicator: 'red',
        });
    },
});
