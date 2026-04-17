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
