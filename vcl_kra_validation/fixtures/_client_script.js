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

function isLocalPurchase(frm) {
    return (frm.doc.custom_purchase_invoice_type || '').trim() === VCL_KRA_TYPE;
}

function clearKraFields(frm) {
    VCL_KRA_FIELDS.forEach((f) => frm.set_value(f, null));
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
                    frappe.show_alert(
                        { message: __('KRA: ') + (d.error || 'invalid CUIN'), indicator: 'red' },
                        10
                    );
                    return;
                }
                frm.set_value('custom_kra_supplier_name', d.supplier_name || '');
                frm.set_value('custom_kra_invoice_number', d.trader_system_inv_no || '');
                frm.set_value('custom_kra_tax_amount', d.tax_amt || 0);
                frm.set_value('custom_kra_total_amount', d.total_inv_amt || 0);

                if (!d.is_vcl_buyer) {
                    frappe.msgprint({
                        title: __('KRA buyer mismatch'),
                        message: __(
                            'This KRA invoice is made out to <b>{0}</b> (PIN {1}), not to Vimit Converters Limited. Do not record this Purchase Invoice without confirming with the supplier.',
                            [d.buyer_name || '(unknown)', d.buyer_pin || '-']
                        ),
                        indicator: 'red',
                    });
                } else {
                    frappe.show_alert(
                        {
                            message: __('KRA: {0} — Tax {1}, Total {2}', [
                                d.supplier_name,
                                format_currency(d.tax_amt, frm.doc.currency || 'KES'),
                                format_currency(d.total_inv_amt, frm.doc.currency || 'KES'),
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

        const tolerance = 1.0; // KES 1
        const diffs = [];

        const gt = flt(frm.doc.grand_total);
        const tt = flt(frm.doc.total_taxes_and_charges);

        if (Math.abs(gt - flt(kra_total)) > tolerance) {
            diffs.push(
                __('Grand Total {0} does not match KRA Total {1}', [
                    format_currency(gt, frm.doc.currency || 'KES'),
                    format_currency(kra_total, frm.doc.currency || 'KES'),
                ])
            );
        }
        if (Math.abs(tt - flt(kra_tax)) > tolerance) {
            diffs.push(
                __('Total Taxes {0} does not match KRA Tax {1}', [
                    format_currency(tt, frm.doc.currency || 'KES'),
                    format_currency(kra_tax, frm.doc.currency || 'KES'),
                ])
            );
        }

        if (diffs.length) {
            frappe.msgprint({
                title: __('KRA totals mismatch'),
                message:
                    __('Review the line items and taxes before submitting:') +
                    '<br>• ' +
                    diffs.join('<br>• '),
                indicator: 'orange',
            });
        }
    },

    before_submit(frm) {
        if (!isLocalPurchase(frm)) return;

        const errors = [];

        if (!frm.doc.bill_no) {
            errors.push(__('Supplier Invoice No. is required.'));
        } else if (!frm.doc.custom_kra_total_amount) {
            errors.push(
                __(
                    'Supplier Invoice No. <b>{0}</b> has not been validated against KRA iTax (or KRA did not recognise it). The invoice can be saved as a Draft, but cannot be submitted until a valid KRA CUIN populates the KRA fields below.',
                    [frm.doc.bill_no]
                )
            );
        } else {
            const tolerance = 1.0;
            const gt = flt(frm.doc.grand_total);
            const tt = flt(frm.doc.total_taxes_and_charges);
            const kt = flt(frm.doc.custom_kra_total_amount);
            const kx = flt(frm.doc.custom_kra_tax_amount);

            if (Math.abs(gt - kt) > tolerance) {
                errors.push(
                    __('Grand Total {0} does not match KRA Total {1}.', [
                        format_currency(gt, frm.doc.currency || 'KES'),
                        format_currency(kt, frm.doc.currency || 'KES'),
                    ])
                );
            }
            if (Math.abs(tt - kx) > tolerance) {
                errors.push(
                    __('Total Taxes {0} does not match KRA Tax {1}.', [
                        format_currency(tt, frm.doc.currency || 'KES'),
                        format_currency(kx, frm.doc.currency || 'KES'),
                    ])
                );
            }
        }

        if (errors.length) {
            frappe.throw({
                title: __('KRA validation — cannot submit'),
                message:
                    __('This Purchase Invoice cannot be submitted until the following are resolved:') +
                    '<br><br>• ' +
                    errors.join('<br><br>• ') +
                    '<br><br>' +
                    __('You can still Save the invoice as a Draft.'),
                indicator: 'red',
            });
        }
    },
});
