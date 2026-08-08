from . import __version__ as app_version  # noqa: F401

app_name = "vcl_kra_validation"
app_title = "VCL KRA Validation"
app_publisher = "Vimit Converters Limited"
app_description = "KRA eTIMS CUIN validation for ERPNext Purchase & Sales Invoices"
app_email = "it@vimitconverters.com"
app_license = "mit"
required_apps = ["frappe/frappe", "frappe/erpnext"]

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["name", "like", "Purchase Invoice-custom_kra_%"]],
    },
    {
        "dt": "Custom Field",
        "filters": [["name", "like", "Sales Invoice-custom_kra_%"]],
    },
    {
        "dt": "Client Script",
        "filters": [["name", "like", "VCL KRA%"]],
    },
]

after_install = "vcl_kra_validation.install.after_install"

# Daily verification job. The ONLY place invoices are checked against KRA —
# there is no on-form checker on Sales Invoice (see patches/retire_si_form_checker).
# Covers Local Purchase invoices posted that day, and every submitted VCL Sales
# Invoice carrying a CUIN that has not been verified since it last changed;
# emails the result to purchasing@vimit.com. 19:00 EAT = 16:00 UTC.
scheduler_events = {
    "cron": {
        "0 16 * * *": ["vcl_kra_validation.scheduled_tasks.daily_verify_kra_invoices"],
    },
}
