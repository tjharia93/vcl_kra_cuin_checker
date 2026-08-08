// VCL KRA Recheck — Sales Invoice
// Installed via fixture by the vcl_kra_validation app. Do not edit in place;
// update the fixture source in the app repo and redeploy.
//
// This REPLACES the old "VCL KRA CUIN Validation - Sales Invoice" script,
// which fired a blocking `freeze: true` call to KRA on every form refresh and
// locked the form each time anyone opened an invoice. KRA is now checked by
// the nightly sweep (vcl_kra_validation.scheduled_tasks) and nothing here
// touches the network unless a human asks for it.
//
// All this script does is render what the sweep already found and offer a
// manual re-check.

frappe.ui.form.on('Sales Invoice', {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;
        if (!(frm.doc.custom_cuin || '').trim()) return;
        if (frm.doc.custom_kra_cuin_exempt) return;

        const status = frm.doc.custom_kra_match_status;
        const indicator = {
            'Matched': 'green',
            'Variance': 'red',
            'PIN mismatch': 'red',
            'Not found': 'red',
            'Unreachable': 'orange',
        }[status];

        if (indicator) {
            frm.dashboard.add_indicator(__('KRA: {0}', [status]), indicator);
        } else {
            frm.dashboard.add_indicator(__('KRA: not yet checked'), 'grey');
        }

        frm.add_custom_button(__('Recheck KRA now'), () => {
            frappe.call({
                method: 'vcl_kra_validation.api.recheck_sales_invoice',
                args: { name: frm.doc.name },
                freeze: true,
                freeze_message: __('Asking KRA about this CUIN…'),
                callback(r) {
                    const d = r.message || {};
                    if (d.status === 'Matched') {
                        frappe.show_alert(
                            { message: __('KRA: matched on PIN, net, tax and gross.'), indicator: 'green' },
                            7
                        );
                    } else if (d.status === 'Unreachable') {
                        frappe.show_alert(
                            {
                                message: __('KRA did not answer — left as unreachable, the nightly sweep will retry.'),
                                indicator: 'orange',
                            },
                            7
                        );
                    } else {
                        frappe.msgprint({
                            title: __('KRA: {0}', [d.status || __('no verdict')]),
                            message: frappe.utils.escape_html(
                                d.detail || d.error || __('See the KRA section on this invoice.')
                            ),
                            indicator: 'red',
                        });
                    }
                    frm.reload_doc();
                },
            });
        }, __('KRA'));
    },
});
