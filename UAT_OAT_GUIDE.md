# UAT & OAT Guide — VCL KRA Validation

User Acceptance Testing (UAT) and Operational Acceptance Testing (OAT) plan for
the `vcl_kra_validation` Frappe app before it is promoted to the production
Vimit Converters Frappe Cloud site (`vimitconverters.frappe.cloud`).

Run UAT first with the Accounts team on a staging / clone site. Only once UAT
sign-off is obtained should OAT be executed by the IT / bench operator against
production.

---

## 1. Aims

### 1.1 UAT — User Acceptance Testing

**Goal:** Confirm the app meets the business requirement that every Purchase
Invoice (PI) entered into ERPNext is validated against the KRA iTax invoice
checker, and that the four KRA fields and mismatch warnings behave as the
Accounts team expect.

UAT aims to verify:

1. A valid KRA CUIN populates **KRA Supplier Name**, **KRA Invoice Number**,
   **KRA Tax Amount** and **KRA Total Amount** on the PI form.
2. An invalid or unknown CUIN is rejected with a clear error toast and the four
   fields are cleared.
3. A CUIN whose KRA record shows a buyer other than Vimit Converters
   (`P000606160U`) triggers the red "KRA buyer mismatch" popup.
4. A CUIN containing a `/` is rejected with the "not supported" error (eTIMS
   portal out-of-scope message).
5. On **Save**, if `grand_total` or `total_taxes_and_charges` differs from the
   KRA totals by more than KES 1, an orange "KRA totals mismatch" warning is
   shown (non-blocking in Phase 1).
6. The workflow is intuitive for the Accounts team without extra training.

**Exit criteria (UAT sign-off):** All scenarios in §3 pass, and the Accounts
Manager signs §6.1.

### 1.2 OAT — Operational Acceptance Testing

**Goal:** Confirm the app can be deployed, run, monitored, and rolled back
safely on Frappe Cloud without disrupting day-to-day ERPNext operations.

OAT aims to verify:

1. The app installs cleanly on the target bench group and site via Frappe Cloud.
2. Fixtures (Custom Fields + Client Script) sync automatically on install.
3. The whitelisted endpoint (`vcl_kra_validation.api.validate_cuin`) is
   reachable and returns within an acceptable latency (p95 < 3 s).
4. KRA iTax network failures degrade gracefully — the user sees a red toast
   rather than a 500 error, and the PI form remains usable.
5. The app can be disabled in under 1 minute (Client Script toggle) without a
   redeploy.
6. The app can be fully uninstalled, with all custom fields and the client
   script removed.
7. Frappe Error Log captures any runtime failures for audit.

**Exit criteria (OAT sign-off):** All scenarios in §4 pass, and the IT /
Bench operator signs §6.2.

---

## 2. Environments & prerequisites

| Item | UAT | OAT |
|---|---|---|
| Site | Staging clone of `vimitconverters.frappe.cloud` (or a test site on the same bench group) | `vimitconverters.frappe.cloud` (production) |
| Bench group | Test bench group | Production bench group |
| Role of tester | Accounts User / Accounts Manager | Frappe Cloud operator (System Manager) |
| Data | Any supplier + at least 5 real CUINs (mix of valid / invalid / `/` / non-VCL buyer) | Same, plus 1 known-valid VCL CUIN for smoke test |
| Tools | ERPNext web UI only | Browser + Frappe Cloud dashboard + `bench --site ... console` (via SSH or web terminal) |

Prerequisites before testing starts:

- App installed on the target site, fixtures synced (four custom fields visible
  on Purchase Invoice, Client Script `VCL KRA CUIN Validation` enabled).
- Tester has a login with the **Accounts User** role (UAT) or **System
  Manager** (OAT).
- Internet egress from the Frappe bench to `itax.kra.go.ke` is open.
- A short list of known CUINs (see §5) has been agreed with the Accounts
  Manager.

---

## 3. UAT test scenarios

Execute each scenario on the **Purchase Invoice** form. Record the result in
§6.1. Expected behaviours below are the definition of "pass".

### UAT-01 — Valid VCL-buyer CUIN populates all four fields

1. New Purchase Invoice → pick any supplier → enter the valid test CUIN in
   **Supplier Invoice No.** (`bill_no`).
2. Observe freeze overlay "Validating CUIN on KRA iTax…".
3. **Expected:**
   - Green toast "KRA: <Supplier> — Tax KES <x>, Total KES <y>".
   - `KRA Supplier Name`, `KRA Invoice Number`, `KRA Tax Amount`,
     `KRA Total Amount` are filled with KRA values.
   - No red popup.

### UAT-02 — Valid CUIN whose buyer is NOT VCL

1. Enter a CUIN whose `buyerPIN` on KRA is not `P000606160U`.
2. **Expected:** Red modal **"KRA buyer mismatch"** naming the actual buyer and
   PIN; the four fields are still populated (so the user can see the facts).
3. The user cannot save without consciously dismissing the warning.

### UAT-03 — Invalid CUIN

1. Enter `XXXXXXXXXXXXXXX` (gibberish).
2. **Expected:** Red toast "KRA: <KRA error message>". All four KRA fields are
   cleared / null.

### UAT-04 — CUIN containing `/`

1. Enter any CUIN with a `/` in it (eTIMS-style).
2. **Expected:** Red toast "KRA: CUINs containing '/' are not supported…".
   Fields cleared.

### UAT-05 — Clearing `bill_no` clears the KRA fields

1. After a successful UAT-01, delete the value in **Supplier Invoice No.** and
   tab out.
2. **Expected:** All four KRA fields are cleared to null.

### UAT-06 — Totals match → silent save

1. After UAT-01, add items + taxes so that `grand_total` equals the KRA total
   within KES 1 and `total_taxes_and_charges` equals the KRA tax within KES 1.
2. Click **Save**.
3. **Expected:** PI saves without the orange mismatch popup.

### UAT-07 — Totals mismatch → orange warning (non-blocking)

1. After UAT-01, enter items whose totals differ from the KRA totals by more
   than KES 1.
2. Click **Save**.
3. **Expected:** Orange modal **"KRA totals mismatch"** lists the specific
   differences (Grand Total vs KRA Total, Total Taxes vs KRA Tax).
4. Dismiss the modal → the PI still saves (Phase 1 is a warning only).

### UAT-08 — Re-entering the same CUIN

1. Re-enter the same valid CUIN used in UAT-01 on a fresh PI.
2. **Expected:** Same green toast + populated fields. No stale cache behaviour.

### UAT-09 — Network-unreachable simulation (optional, co-ordinate with IT)

1. IT temporarily blocks egress to `itax.kra.go.ke`, or tester enters a valid
   CUIN during a known KRA outage.
2. **Expected:** Red toast "KRA: KRA portal unreachable: <short message>".
   Form remains usable; user can save (Phase 1) or be blocked (Phase 2).

### UAT-10 — Print behaviour

1. Open a PI with the four KRA fields populated → **Print** preview.
2. **Expected:** Confirm whether the KRA fields should appear on the printed
   invoice. (Current default: supplier name and invoice number print; tax and
   total also print. Accounts to confirm if this is acceptable or if they
   want them hidden on print.)

---

## 4. OAT test scenarios

Execute these against the production bench group after UAT sign-off. Log
timing / observations in §6.2.

### OAT-01 — Fresh install on the bench group

1. Frappe Cloud → Bench Group → **Apps → Add App → Install from GitHub
   repository** → paste this repo URL, branch `main`.
2. **Deploys → New Deploy**.
3. **Expected:** Build completes in ≤ 5 min. No errors in the build log.

### OAT-02 — Site-level install

1. Site → **Apps → Install App → vcl_kra_validation**.
2. **Expected:** Install completes in < 30 s. Fixtures sync automatically
   (verify via §4.3).

### OAT-03 — Fixture verification

1. Go to **Setup → Customize Form → Purchase Invoice**: the section **KRA
   eTIMS Validation** and four `custom_kra_*` fields appear after `bill_date`.
2. Go to **Customization → Client Script**: `VCL KRA CUIN Validation` exists,
   `Enabled = Yes`, `DocType = Purchase Invoice`, `View = Form`.
3. **Expected:** All six Custom Field records and the Client Script are
   present with `module: "VCL KRA Validation"`.

### OAT-04 — Whitelisted endpoint smoke test

Run from any shell with an API token (see HANDOVER §7):

```bash
curl -s -X POST -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{"invoice_no":"<known-valid-CUIN>"}' \
  https://vimitconverters.frappe.cloud/api/method/vcl_kra_validation.api.validate_cuin \
  | python3 -m json.tool
```

**Expected:** HTTP 200, JSON contains `"valid": true`, `"supplier_name"`,
`"buyer_pin"`, `"is_vcl_buyer"`, etc. Response time < 3 s.

### OAT-05 — Latency check (5 calls)

Repeat OAT-04 five times. Record each elapsed time (`time curl ...`).

**Expected:** p95 < 3 s. No 5xx responses. No new rows in **Frappe → Error
Log**.

### OAT-06 — Failure path (invalid CUIN via API)

```bash
curl ... -d '{"invoice_no":"XXXXXXXX"}' ...
```

**Expected:** HTTP 200, JSON `{"valid": false, "error": "<KRA message>"}`. No
exception thrown, no row added to Error Log.

### OAT-07 — KRA unreachable simulation (optional)

Option A — temporarily break egress via bench proxy rules.
Option B — simulate by editing `api.py`'s `ITAX_URL` in a scratch site only.

**Expected:** JSON `{"valid": false, "error": "KRA portal unreachable: …"}`
with a `requests.RequestException` row captured in Error Log titled "KRA CUIN
validate: network error".

### OAT-08 — Concurrent load (sanity only)

Hit the endpoint 10× in parallel with a valid CUIN:

```bash
seq 10 | xargs -P10 -I{} curl -s ... -d '{"invoice_no":"..."}' ...
```

**Expected:** All 10 respond successfully. No 502/504 from the Frappe web
worker.

### OAT-09 — Disable without redeploy

1. **Customization → Client Script → VCL KRA CUIN Validation** → toggle
   **Enabled** off → Save.
2. Reload a Purchase Invoice and enter a CUIN.
3. **Expected:** No freeze overlay, no field population, no toast. Re-enable
   and retest to confirm reversibility.

### OAT-10 — Full uninstall / rollback

1. Site → Apps → `vcl_kra_validation` → **Uninstall**.
2. Wait for completion.
3. **Expected:**
   - Custom Fields `Purchase Invoice-custom_kra_*` are removed.
   - Client Script `VCL KRA CUIN Validation` is removed.
   - Existing Purchase Invoices load without errors (the custom-field values
     they previously held are gone; the PI itself is intact).
4. Reinstall to confirm the install path is idempotent.

### OAT-11 — Error Log audit

1. During OAT-01 through OAT-10, periodically check **Frappe → Error Log**.
2. **Expected:** Only the errors you deliberately triggered (OAT-07) appear,
   with descriptive titles. No unexpected tracebacks.

### OAT-12 — Documentation check

1. `README.md`, `HANDOVER.md` and this guide are present in the repo at the
   commit that was deployed.
2. Version in `vcl_kra_validation/__init__.py` matches the GitHub release /
   tag used for the deploy.

---

## 5. Test data — CUIN fixtures

Agreed with the Accounts Manager before the UAT window starts. Populate before
kick-off:

| Label | CUIN | Expected outcome |
|---|---|---|
| Valid, VCL-buyer | _fill in_ | UAT-01, UAT-06, UAT-07 |
| Valid, non-VCL buyer | _fill in_ | UAT-02 |
| Invalid / gibberish | `XXXXXXXXXXXXXXX` | UAT-03 |
| Slash CUIN (eTIMS) | _fill in_ | UAT-04 |

Record CUINs in a shared secure note — they are not secrets but belong to real
suppliers.

---

## 6. Sign-off

### 6.1 UAT sign-off (Accounts)

| ID | Scenario | Pass / Fail | Notes |
|---|---|---|---|
| UAT-01 | Valid VCL CUIN populates all fields | | |
| UAT-02 | Non-VCL buyer popup | | |
| UAT-03 | Invalid CUIN | | |
| UAT-04 | Slash CUIN | | |
| UAT-05 | Clearing `bill_no` clears fields | | |
| UAT-06 | Totals match → silent save | | |
| UAT-07 | Totals mismatch → orange warning | | |
| UAT-08 | Re-entering same CUIN | | |
| UAT-09 | Network unreachable (optional) | | |
| UAT-10 | Print behaviour confirmed | | |

**Accounts Manager:** ______________________  **Date:** ____________

### 6.2 OAT sign-off (IT / Bench operator)

| ID | Scenario | Pass / Fail | Notes |
|---|---|---|---|
| OAT-01 | Bench deploy | | |
| OAT-02 | Site install | | |
| OAT-03 | Fixtures present | | |
| OAT-04 | API smoke test | | |
| OAT-05 | Latency p95 < 3 s | | |
| OAT-06 | API failure path | | |
| OAT-07 | KRA unreachable (optional) | | |
| OAT-08 | Concurrent load | | |
| OAT-09 | Disable without redeploy | | |
| OAT-10 | Full uninstall | | |
| OAT-11 | Error Log audit | | |
| OAT-12 | Docs / version match | | |

**IT / Bench operator:** ______________________  **Date:** ____________

---

## 7. Defect handling

- Any **UAT fail** → log as a GitHub issue on this repo, tagged `uat` +
  severity. Block production deploy until fixed.
- Any **OAT fail** on OAT-01/02/03/04/10 → block production deploy.
- Any **OAT fail** on OAT-05/07/08/11 → raise a ticket, but production rollout
  may proceed with IT's judgement if the issue is non-blocking.
- Rollback procedure: see HANDOVER.md §8.

## 8. Post go-live monitoring (first week)

- Daily: check **Frappe → Error Log** for entries titled `KRA CUIN validate:*`.
- Weekly: ask the Accounts team for false-positive / false-negative reports on
  the orange mismatch warning.
- After two clean weeks, begin Phase 2 (convert warning to hard block — see
  HANDOVER §10).
