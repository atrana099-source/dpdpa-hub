"""Declarative definitions for the registers that share one generic list/edit/import/export screen."""


def F(name, label, type="text", choices=None, required=False, show=True, filt=False, help=""):
    return dict(name=name, label=label, type=type, choices=choices or [], required=required, show=show, filt=filt, help=help)


YESNO = ["Yes", "No", "Partial", "To verify"]
PRIORITY = ["Critical", "High", "Medium", "Low"]

ENTITIES = {
    "ropa": dict(
        table="ropa", title="ROPA: Record of Processing Activities", singular="processing activity", code_prefix="R",
        blurb="Every processing activity, who it concerns, why, where the data goes and how long it is kept.",
        fields=[
            F("code", "ROPA ID", required=True), F("department", "Department", filt=True),
            F("activity", "Processing activity", required=True), F("data_principals", "Data principals", show=False),
            F("data_categories", "Data categories", "textarea", show=False), F("purpose", "Purpose", "textarea", show=False),
            F("lawful_basis", "Lawful basis", "textarea", show=False), F("systems", "Systems", show=False),
            F("recipients", "Recipients / processors", show=False),
            F("cross_border", "Cross-border", "select", ["Yes", "No", "Not asked", "To verify"], filt=True),
            F("retention", "Retention (as stated)", "textarea", show=False),
            F("retention_gap", "Retention gap?", "select", ["Yes", "No", "To verify"], filt=True),
            F("owner", "Owner (HOD)"),
            F("status", "Status", "select", ["Draft", "Reviewed by owner", "Approved by Legal"], filt=True),
            F("last_reviewed", "Last reviewed", "date"),
        ]),
    "retention": dict(
        table="retention", title="Retention schedule", singular="retention rule", code_prefix="T",
        blurb="How long each category of personal data is kept, why, and how it is disposed of. DPDP sets no fixed period for most companies; periods are purpose-based or follow a statutory floor.",
        fields=[
            F("code", "ID", required=True), F("data_category", "Data category", required=True),
            F("departments", "Departments"), F("data_principals", "Data principals", show=False),
            F("clock_starts", "Clock starts", show=False), F("statutory_min", "Statutory minimum", "textarea", show=False),
            F("proposed", "Proposed retention", "textarea"),
            F("basis_type", "Basis type", "select", ["Statutory floor", "Purpose-based", "Contract", "Legitimate use", "Other"], filt=True),
            F("disposal", "Disposal method", "textarea", show=False), F("current_practice", "Current practice", "textarea", show=False),
            F("ropa_ids", "ROPA IDs", show=False), F("owner", "Owner"),
            F("status", "Status", "select", ["Draft", "Proposed", "Approved by Legal", "Implemented"], filt=True),
            F("next_review", "Next review", "date"),
        ]),
    "gaps": dict(
        table="gaps", title="Gap register", singular="gap", code_prefix="G",
        blurb="Findings from the department assessments, mapped to the DPDP Act and Rules, with an action and an owner for each.",
        fields=[
            F("code", "ID", required=True), F("department", "Department", filt=True), F("theme", "Theme", filt=True),
            F("finding", "Finding", "textarea", required=True),
            F("type", "Type", "select", ["Gap", "Verify"], filt=True),
            F("priority", "Priority", "select", PRIORITY, filt=True), F("dpdp_ref", "DPDP reference", show=False),
            F("source", "Source", show=False), F("action_code", "Action ID"),
            F("status", "Status", "select", ["Open", "In progress", "Evidence received", "Closed"], filt=True),
            F("target_date", "Target date", "date"), F("owner", "Named owner"), F("remarks", "Evidence / remarks", "textarea", show=False),
        ]),
    "actions": dict(
        table="actions", title="Remediation actions", singular="action", code_prefix="A",
        blurb="The actions that close the gaps, with phase, due date, owner and progress.",
        fields=[
            F("code", "ID", required=True), F("action", "Action", "textarea", required=True),
            F("owner_function", "Owner (function)"), F("phase", "Phase", filt=True),
            F("due_date", "Due date", "date"), F("departments", "Departments in scope", show=False),
            F("status", "Status", "select", ["Not started", "In progress", "Blocked", "Done"], filt=True),
            F("pct", "% complete", "int"), F("named_owner", "Named owner"),
            F("evidence", "Evidence / link", show=False), F("remarks", "Remarks", "textarea", show=False),
        ]),
    "vendors": dict(
        table="vendors", title="Vendors and processors", singular="vendor", code_prefix=None,
        blurb="Everyone who handles personal data for us, and whether a data processing agreement is in place (DPDP s.8(2)).",
        fields=[
            F("name", "Vendor / processor", required=True), F("service", "Service"), F("data_shared", "Data shared", "textarea", show=False),
            F("role", "Role", "select", ["Processor", "Fiduciary", "Joint fiduciary", "Unknown"], filt=True),
            F("dpa_status", "DPA status", "select", ["Not signed", "Sent", "Signed", "Not required"], filt=True),
            F("cross_border", "Cross-border", "select", ["Yes", "No", "To verify"], filt=True),
            F("owner", "Owner"), F("last_review", "Last review", "date"), F("remarks", "Remarks", "textarea", show=False),
        ]),
    "departments": dict(
        table="departments", title="Departments", singular="department", code_prefix=None,
        blurb="Department-level status: interview, rating, HOD declaration and evidence pack.",
        fields=[
            F("name", "Department", required=True), F("hod", "HOD"), F("interview_date", "Interview date", "date"),
            F("rating", "Rating", "select", PRIORITY, filt=True),
            F("declaration_signed", "HOD declaration signed", "select", ["No", "Yes", "Partial"], filt=True),
            F("evidence_received", "Evidence pack received", "select", ["No", "Yes", "Partial"], filt=True),
            F("key_issue", "Key issue", "textarea"),
        ]),
    "purposes": dict(
        table="purposes", title="Consent purposes", singular="purpose", code_prefix=None, admin_write=True,
        blurb="Each specific purpose a person can consent to. Notices are built from these. Keep each purpose specific and in plain language.",
        fields=[
            F("code", "Code", required=True, help="Short, no spaces, e.g. MARKETING"), F("name", "Purpose name", required=True),
            F("description", "Plain-language description", "textarea", required=True),
            F("data_categories", "Data used", "textarea"),
            F("legal_basis", "Legal basis", "select", ["Consent (s.6)", "Legitimate use (s.7)"], filt=True),
            F("retention", "Retention"), F("status", "Status", "select", ["Active", "Retired"], filt=True),
        ]),
}
