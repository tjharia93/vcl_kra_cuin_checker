"""Install-time hooks.

Anything the app needs at install time belongs here, NOT in patches.txt:
`install_app()` finishes by stamping every line of patches.txt into the Patch
Log without executing it (frappe/installer.py), so a patch that reaches
outside the app's own doctypes silently never runs on a fresh site — and
migrate will never retry it, because the log already says it is done.

Both routes may run on the same site, so everything here must be idempotent.
"""

import frappe


def after_install():
    from vcl_kra_validation.patches.retire_si_form_checker import execute as retire_si_checker

    retire_si_checker()
    frappe.db.commit()
