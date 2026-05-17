from . import __version__ as app_version  # noqa: F401

app_name = "vcl_kra_validation"
app_title = "VCL KRA Validation"
app_publisher = "Vimit Converters Limited"
app_description = "KRA eTIMS CUIN validation for ERPNext Purchase Invoices"
app_email = "it@vimitconverters.com"
app_license = "mit"
required_apps = ["frappe/frappe", "frappe/erpnext"]

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["name", "like", "Purchase Invoice-custom_kra_%"]],
    },
    {
        "dt": "Client Script",
        "filters": [["name", "=", "VCL KRA CUIN Validation"]],
    },
]

# Daily verification job — re-runs validate_cuin against every Local Purchase
# invoice posted that day (plus any older ones still flagged pending) and
# emails the result to purchasing@vimit.com. 19:00 EAT = 16:00 UTC.
scheduler_events = {
    "cron": {
        "0 16 * * *": ["vcl_kra_validation.scheduled_tasks.daily_verify_kra_invoices"],
    },
}
