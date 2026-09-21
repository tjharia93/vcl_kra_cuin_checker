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

# RETIRED 21/09/2026, on Tanuj's instruction: the nightly KRA sweep no longer
# runs. Two jobs were firing at 16:00 UTC — this one and a "VCL KRA EOD Daily"
# Server Script written as a stand-in before the app deployed — so KRA was
# swept twice a night and two reports went out. Both were stopped, not just
# the duplicate: verification is now on-form only, at the moment the CUIN is
# entered, and nothing re-checks an invoice afterwards. That means an invoice
# flagged custom_kra_pending_verification stays pending until somebody looks
# at it; as at that date 58 submitted invoices were in that state.
#
# scheduled_tasks.daily_verify_kra_invoices is deliberately LEFT IN THE APP so
# it can still be run on demand. To bring the nightly job back, restore:
#
#     scheduler_events = {
#         "cron": {
#             "0 16 * * *": ["vcl_kra_validation.scheduled_tasks.daily_verify_kra_invoices"],
#         },
#     }
#
# and deploy + migrate. The Scheduled Job Type row on the site was also set to
# stopped, so re-adding the hook alone is not enough — un-stop it as well.
