import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate, now


class GLCorrection(Document):
    """
    GL Correction DocType

    Core idea:
    - We DO NOT create any extra Journal Entry.
    - We:
        * fetch original GL Entries
        * store a full snapshot in child rows
        * allow you to edit debit/credit/account/cost center
        * on Submit -> mark as "Applied" (approved correction)
        * "Apply GL Updates" -> directly update GL Entry rows (all amount fields)
        * "Rollback GL Updates" -> restore from snapshot
        * "Repost Item Valuation" -> create RIV doc + log via comments
        * "Validate GL Consistency" -> compare GL Entries vs correction
    """

    def validate(self):
        """Run on every Save/Submit."""
        self.update_totals()
        self.validate_totals()

    def on_submit(self):
        """
        On submit:
        - Just mark this correction as Applied (approved).
        - Actual GL changes happen only when "Apply GL Updates" is clicked.
        """
        self.status = "Applied"
        frappe.db.set_value(self.doctype, self.name, "status", "Applied")

    def on_cancel(self):
        """
        On cancel:
        - Mark this document as Cancelled.
        - We do NOT touch GL Entries automatically.
        """
        self.status = "Cancelled"
        frappe.db.set_value(self.doctype, self.name, "status", "Cancelled")

    def update_totals(self):
        """Sum all child rows and update total_debit / total_credit / difference."""
        total_debit = 0.0
        total_credit = 0.0

        for row in self.entries or []:
            total_debit += flt(row.debit)
            total_credit += flt(row.credit)

        self.total_debit = flt(total_debit)
        self.total_credit = flt(total_credit)
        self.difference = flt(total_debit - total_credit)

    def validate_totals(self):
        """Ensure debits and credits are balanced before submit."""
        if not self.entries:
            frappe.throw(_("Please add at least one row in Entries table."))

        if abs(flt(self.difference)) > 0.0001:
            frappe.throw(
                _(
                    "Total Debit and Total Credit must be equal. "
                    "Difference is {0}."
                ).format(self.difference)
            )

    @frappe.whitelist()
    def fetch_gl_entries(self):
        """
        Fetch GL Entries from the selected voucher (voucher_type + voucher_no)
        and load them into the 'entries' child table as a starting point.

        Also captures a snapshot of original amounts in fields:
          - original_account / original_cost_center
          - original_debit / original_credit
          - original_*_in_account_currency
          - original_*_in_transaction_currency
        """

        if self.docstatus != 0:
            frappe.throw(_("You can only fetch GL Entries while the document is in Draft."))

        if not self.company or not self.voucher_type or not self.voucher_no:
            frappe.throw(_("Please set Company, Voucher Type and Voucher No first."))

        gl_entries = frappe.get_all(
            "GL Entry",
            filters={
                "company": self.company,
                "voucher_type": self.voucher_type,
                "voucher_no": self.voucher_no,
                "is_cancelled": 0,
            },
            fields=[
                "name",
                "account",
                "party_type",
                "party",
                "cost_center",
                "debit",
                "credit",
                "debit_in_account_currency",
                "credit_in_account_currency",
                "debit_in_transaction_currency",
                "credit_in_transaction_currency",
            ],
            order_by="posting_date asc, name asc",
        )

        if not gl_entries:
            frappe.throw(
                _(
                    "No GL Entries found for {0} {1} in company {2}."
                ).format(self.voucher_type, self.voucher_no, self.company)
            )

        self.set("entries", [])

        for gle in gl_entries:
            self.append(
                "entries",
                {

                    "account": gle.account,
                    "party_type": gle.party_type,
                    "party": gle.party,
                    "cost_center": gle.cost_center,
                    "debit": flt(gle.debit),
                    "credit": flt(gle.credit),
                    "reference_gl_entry": gle.name,

                    "original_account": gle.account,
                    "original_cost_center": gle.cost_center,
                    "original_debit": flt(gle.debit),
                    "original_credit": flt(gle.credit),
                    "original_debit_in_account_currency": flt(gle.debit_in_account_currency),
                    "original_credit_in_account_currency": flt(gle.credit_in_account_currency),
                    "original_debit_in_transaction_currency": flt(gle.debit_in_transaction_currency),
                    "original_credit_in_transaction_currency": flt(gle.credit_in_transaction_currency),
                },
            )

        self.update_totals()
        self.save(ignore_permissions=True)

        frappe.msgprint(
            _(
                "Fetched {0} GL Entries from {1} {2}."
            ).format(len(gl_entries), self.voucher_type, self.voucher_no),
            alert=True,
        )

        return {
            "count": len(gl_entries),
            "total_debit": self.total_debit,
            "total_credit": self.total_credit,
        }

    @frappe.whitelist()
    def apply_gl_updates(self):
        """
        Directly update existing GL Entry rows based on 'entries' child table.
        This updates ALL relevant amount fields:

          - debit
          - debit_in_account_currency
          - debit_in_transaction_currency
          - credit
          - credit_in_account_currency
          - credit_in_transaction_currency

        AND also updates:
          - account
          - cost_center

        Uses child field:
          - reference_gl_entry (Link GL Entry)
        """

        if self.docstatus != 1:
            frappe.throw(_("Apply GL Updates is only allowed after submission (docstatus = 1)."))

        updated = 0

        for row in self.entries or []:
            gle_name = row.reference_gl_entry
            if not gle_name:
                continue

            update_gl_entry_amounts(gle_name, flt(row.debit), flt(row.credit))

            updates = {}

            if row.account:
                current_account = frappe.db.get_value("GL Entry", gle_name, "account")
                if current_account != row.account:
                    updates["account"] = row.account

            if row.cost_center:
                current_cc = frappe.db.get_value("GL Entry", gle_name, "cost_center")
                if current_cc != row.cost_center:
                    updates["cost_center"] = row.cost_center

            if updates:
                frappe.db.set_value("GL Entry", gle_name, updates, update_modified=False)

            updated += 1

        self.status = "GL Updated"
        frappe.db.set_value(self.doctype, self.name, "status", "GL Updated")

        frappe.db.commit()

        self.add_comment(
            "Info",
            _(
                "Apply GL Updates executed. {0} GL Entry row(s) updated."
            ).format(updated),
        )

        frappe.msgprint(
            _(
                "Updated {0} GL Entry row(s) directly. "
                "Please verify using the General Ledger report."
            ).format(updated),
            alert=True,
        )

        return {"updated": updated}

    @frappe.whitelist()
    def validate_gl_state(self):
        """
        Check whether current GL Entry values still match what is in this
        GL Correction (after apply_gl_updates).
        """

        if self.docstatus != 1:
            frappe.throw(_("Validation is only meaningful after submission."))

        mismatches = []

        for row in self.entries or []:
            if not row.reference_gl_entry:
                continue

            gle = frappe.db.get_value(
                "GL Entry",
                row.reference_gl_entry,
                [
                    "account",
                    "cost_center",
                    "debit",
                    "credit",
                    "debit_in_account_currency",
                    "credit_in_account_currency",
                    "debit_in_transaction_currency",
                    "credit_in_transaction_currency",
                ],
                as_dict=True,
            )

            if not gle:
                mismatches.append(f"{row.reference_gl_entry}: GL Entry not found")
                continue

            diffs = []

            if abs(flt(gle.debit) - flt(row.debit)) > 0.0001:
                diffs.append(f"debit {gle.debit} != {row.debit}")
            if abs(flt(gle.credit) - flt(row.credit)) > 0.0001:
                diffs.append(f"credit {gle.credit} != {row.credit}")
            if gle.account != row.account:
                diffs.append(f"account {gle.account} != {row.account}")
            if gle.cost_center != row.cost_center:
                diffs.append(f"cost_center {gle.cost_center} != {row.cost_center}")

            if diffs:
                mismatches.append(
                    f"GL Entry {row.reference_gl_entry}: " + ", ".join(diffs)
                )

        if mismatches:
            msg = _(
                "Found differences between GL Entries and this correction:"
            ) + "<br><br>" + "<br>".join(mismatches)
            frappe.msgprint(msg, indicator="orange")

            self.add_comment(
                "Info",
                _("GL consistency check FAILED with {0} mismatch(es).").format(
                    len(mismatches)
                ),
            )

            return {"ok": False, "details": mismatches}

        frappe.msgprint(
            _("All GL Entries match this GL Correction document."),
            indicator="green",
        )

        self.add_comment("Info", _("GL consistency check passed. All entries match."))

        return {"ok": True}

    @frappe.whitelist()
    def rollback_gl_updates(self):
        """
        Restore GL Entries back to their original values captured when
        'Fetch Original GL Entries' was executed.
        """

        if self.docstatus != 1:
            frappe.throw(_("Rollback is only allowed after submission."))

        restored = 0

        for row in self.entries or []:
            if not row.reference_gl_entry:
                continue

            restore_gl_entry_originals(row)

            if hasattr(row, "original_debit"):
                row.debit = flt(row.original_debit)
            if hasattr(row, "original_credit"):
                row.credit = flt(row.original_credit)

            restored += 1

        self.update_totals()

        self.flags.ignore_validate_update_after_submit = True

        self.status = "Rolled Back"


        self.save(ignore_permissions=True)
        frappe.db.commit()

        self.add_comment(
            "Info",
            _("Rollback executed. Restored {0} GL Entry row(s) to original values.").format(
                restored
            ),
        )

        frappe.msgprint(
            _("Restored {0} GL Entry row(s) back to original values.").format(restored),
            alert=True,
        )

        return {"restored": restored}

    @frappe.whitelist()
    def repost_valuation(self):
        """
        Trigger Repost Item Valuation for this voucher IF the tool exists.

        Tracking log:
        - If not available -> add comment that RIV tool is missing.
        - If created -> add comment with RIV name, time, voucher.
        """

        if self.docstatus != 1:
            frappe.throw(_("Please submit this GL Correction before reposting valuation."))

        if not self.company or not self.voucher_type or not self.voucher_no:
            frappe.throw(_("Please set Company, Voucher Type and Voucher No first."))


        if not frappe.db.exists("DocType", "Repost Item Valuation"):
            msg = _(
                "Repost Item Valuation DocType is not available in this system. "
                "GL entries are updated, but stock valuation must be adjusted "
                "manually (e.g. Stock Reconciliation) or by enabling this tool."
            )
            frappe.msgprint(msg, alert=True, indicator="orange")
            self.add_comment("Info", msg)
            return {"created": 0}


        if self.voucher_type not in (
            "Purchase Receipt",
            "Stock Entry",
            "Purchase Invoice",
            "Sales Invoice",
        ):
            warn_msg = _(
                "Repost Item Valuation is normally used for stock-related vouchers. "
                "Current Voucher Type: {0}"
            ).format(self.voucher_type)
            frappe.msgprint(warn_msg, alert=True, indicator="orange")
            self.add_comment("Info", warn_msg)

        riv = frappe.new_doc("Repost Item Valuation")
        riv.company = self.company
        riv.voucher_type = self.voucher_type
        riv.voucher_no = self.voucher_no

        if riv.meta.has_field("posting_date"):
            riv.posting_date = self.posting_date or nowdate()

        riv.insert(ignore_permissions=True)
        riv.submit()

        log_msg = _(
            "Repost Item Valuation {0} created and submitted for {1} {2} at {3}."
        ).format(riv.name, self.voucher_type, self.voucher_no, now())
        self.add_comment("Info", log_msg)

        frappe.msgprint(
            _(
                "Repost Item Valuation <b>{0}</b> created for {1} {2}. "
                "It will be processed in the background."
            ).format(riv.name, self.voucher_type, self.voucher_no),
            alert=True,
        )

        return {"created": 1, "repost_name": riv.name}


def update_gl_entry_amounts(gl_entry_name, new_debit, new_credit):
    """
    Update all relevant amount fields on a GL Entry in a consistent way:

      - debit
      - debit_in_account_currency
      - debit_in_transaction_currency
      - credit
      - credit_in_account_currency
      - credit_in_transaction_currency
    """

    fields = [
        "debit",
        "credit",
        "debit_in_account_currency",
        "credit_in_account_currency",
        "debit_in_transaction_currency",
        "credit_in_transaction_currency",
    ]

    gle = frappe.db.get_value("GL Entry", gl_entry_name, fields, as_dict=True)
    if not gle:
        frappe.throw(_("GL Entry {0} not found").format(gl_entry_name))

    old_debit = flt(gle.debit)
    old_credit = flt(gle.credit)

    nd = flt(new_debit)
    nc = flt(new_credit)

    updates = {}
    if old_debit or nd:
        updates["debit"] = nd
        debit_factor = nd / old_debit if old_debit else None
        old_d_acc = flt(gle.debit_in_account_currency)
        if old_d_acc or debit_factor is not None:
            if old_debit and debit_factor is not None:
                updates["debit_in_account_currency"] = flt(old_d_acc * debit_factor)
            else:
                updates["debit_in_account_currency"] = nd
        old_d_trn = flt(gle.debit_in_transaction_currency)
        if old_d_trn or debit_factor is not None:
            if old_debit and debit_factor is not None:
                updates["debit_in_transaction_currency"] = flt(old_d_trn * debit_factor)
            else:
                updates["debit_in_transaction_currency"] = 0 if nd == 0 else old_d_trn
    if old_credit or nc:
        updates["credit"] = nc
        credit_factor = nc / old_credit if old_credit else None
        old_c_acc = flt(gle.credit_in_account_currency)
        if old_c_acc or credit_factor is not None:
            if old_credit and credit_factor is not None:
                updates["credit_in_account_currency"] = flt(old_c_acc * credit_factor)
            else:
                updates["credit_in_account_currency"] = nc
        old_c_trn = flt(gle.credit_in_transaction_currency)
        if old_c_trn or credit_factor is not None:
            if old_credit and credit_factor is not None:
                updates["credit_in_transaction_currency"] = flt(old_c_trn * credit_factor)
            else:
                updates["credit_in_transaction_currency"] = 0 if nc == 0 else old_c_trn

    if updates:
        frappe.db.set_value("GL Entry", gl_entry_name, updates, update_modified=False)


def restore_gl_entry_originals(row):
    """
    Restore GL Entry fields from snapshot stored on GL Correction Line row.
    """

    if not row.reference_gl_entry:
        return

    updates = {}

    if getattr(row, "original_account", None):
        updates["account"] = row.original_account
    if getattr(row, "original_cost_center", None):
        updates["cost_center"] = row.original_cost_center

    if hasattr(row, "original_debit"):
        updates["debit"] = flt(row.original_debit)
    if hasattr(row, "original_credit"):
        updates["credit"] = flt(row.original_credit)
    if hasattr(row, "original_debit_in_account_currency"):
        updates["debit_in_account_currency"] = flt(row.original_debit_in_account_currency)
    if hasattr(row, "original_credit_in_account_currency"):
        updates["credit_in_account_currency"] = flt(row.original_credit_in_account_currency)
    if hasattr(row, "original_debit_in_transaction_currency"):
        updates["debit_in_transaction_currency"] = flt(row.original_debit_in_transaction_currency)
    if hasattr(row, "original_credit_in_transaction_currency"):
        updates["credit_in_transaction_currency"] = flt(row.original_credit_in_transaction_currency)

    if updates:
        frappe.db.set_value(
            "GL Entry",
            row.reference_gl_entry,
            updates,
            update_modified=False,
        )