// KRA Daily Verification — filters + status colouring.
frappe.query_reports['KRA Daily Verification'] = {
    filters: [
        {
            fieldname: 'from_date',
            label: __('From Date'),
            fieldtype: 'Date',
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
        },
        {
            fieldname: 'to_date',
            label: __('To Date'),
            fieldtype: 'Date',
            default: frappe.datetime.get_today(),
        },
        {
            fieldname: 'status',
            label: __('KRA Status'),
            fieldtype: 'Select',
            options: [
                '',
                'Matched',
                'Variance',
                'PIN mismatch',
                'Not found',
                'Unreachable',
                'Not checked',
            ].join('\n'),
        },
        {
            fieldname: 'company',
            label: __('Company'),
            fieldtype: 'Link',
            options: 'Company',
            default: frappe.defaults.get_user_default('Company'),
        },
    ],

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === 'status' && data) {
            const colour = {
                'Matched': 'green',
                'Variance': 'red',
                'PIN mismatch': 'red',
                'Not found': 'red',
                'Unreachable': 'orange',
            }[data.status] || 'gray';
            value = `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(data.status)}</span>`;
        }
        return value;
    },
};
