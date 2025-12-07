import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate


class StockValuationFix(Document):
    """
    Stock Valuation Fix

    Phase 1:
    - Fetch current Qty & Valuation Rate from Bin (Item + Warehouse)
    - Compute current total value
    - Allow user to enter target valuation rate
    - Compute target total value & difference

    Phase 2 – two options:
    A) Create Stock Reconciliation + Repost Item Valuation (legacy)
    B) Update source voucher item rate (e.g. Purchase Receipt Item)
       so Repost Item Valuation uses the corrected rate at source.
    """

    def validate(self):
        """Recalculate target totals on Save / Submit."""
        self.update_totals()

    def on_submit(self):
        """
        Require that we have at least fetched and previewed
        an adjustment before allowing dangerous actions.
        """
        if not self.qty_on_hand:
            frappe.throw(
                _("Qty on Hand is zero or not fetched. Please fetch current valuation first.")
            )

        if not self.target_valuation_rate:
            frappe.throw(_("Please set Target Valuation Rate before submitting."))

        if not self.status or self.status == "Draft":
            self.status = "Previewed"
            frappe.db.set_value(self.doctype, self.name, "status", self.status)

    def update_totals(self):
        """Recompute current/target totals if data is present."""
        qty = flt(self.qty_on_hand)
        cur_rate = flt(self.current_valuation_rate)
        tgt_rate = flt(self.target_valuation_rate)

        self.current_total_value = qty * cur_rate if qty and cur_rate else 0

        if qty and tgt_rate:
            self.target_total_value = qty * tgt_rate
            self.difference_value = self.target_total_value - self.current_total_value
        else:
            self.target_total_value = 0
            self.difference_value = 0
    @frappe.whitelist()
    def fetch_current_state(self):
        """
        Load current Qty and Valuation Rate from Bin for this Item + Warehouse.
        """

        if not self.company or not self.item_code or not self.warehouse:
            frappe.throw(
                _("Please set Company, Item and Warehouse before fetching current valuation.")
            )

        bin_doc = frappe.db.get_value(
            "Bin",
            {"item_code": self.item_code, "warehouse": self.warehouse},
            ["actual_qty", "valuation_rate"],
            as_dict=True,
        )

        if not bin_doc:
            frappe.msgprint(
                _(
                    "No Bin record found for Item {0} in Warehouse {1}. "
                    "Qty on Hand and Valuation Rate assumed as 0."
                ).format(self.item_code, self.warehouse),
                alert=True,
            )
            self.qty_on_hand = 0
            self.current_valuation_rate = 0
        else:
            self.qty_on_hand = flt(bin_doc.actual_qty)
            self.current_valuation_rate = flt(bin_doc.valuation_rate)

        self.update_totals()
        self.status = "Valuation Fetched"
        self.save(ignore_permissions=True)

        frappe.msgprint(
            _(
                "Fetched current valuation for Item {0} in Warehouse {1}. "
                "Qty: {2}, Rate: {3}."
            ).format(
                self.item_code,
                self.warehouse,
                self.qty_on_hand,
                self.current_valuation_rate,
            ),
            alert=True,
        )

        return {
            "qty_on_hand": self.qty_on_hand,
            "current_valuation_rate": self.current_valuation_rate,
            "current_total_value": self.current_total_value,
        }

    @frappe.whitelist()
    def preview_adjustment(self):
        """
        Recalculate target totals & difference based on target_valuation_rate.
        This does NOT create any stock or GL document yet.
        """

        if not self.target_valuation_rate:
            frappe.throw(_("Please enter Target Valuation Rate first."))

        if not self.qty_on_hand:
            frappe.throw(
                _("Qty on Hand is 0 or not set. Please fetch current valuation first.")
            )

        self.update_totals()
        self.status = "Previewed"
        self.save(ignore_permissions=True)

        frappe.msgprint(
            _(
                "Preview updated. Target total value: {0}, difference: {1}."
            ).format(self.target_total_value, self.difference_value),
            alert=True,
        )

        return {
            "target_valuation_rate": self.target_valuation_rate,
            "target_total_value": self.target_total_value,
            "difference_value": self.difference_value,
        }

    @frappe.whitelist()
    def create_revaluation_entry(self):
        """
        Create a Stock Reconciliation that adjusts valuation for this
        Item + Warehouse to the target valuation rate, WITHOUT changing qty.

        Logic:
        - Use current qty_on_hand as the qty in reconciliation
        - Use target_valuation_rate as the valuation_rate
        - Let ERPNext post the GL difference

        Behaviour:
        - For NON serial/batch items: create & submit Stock Reconciliation.
        - For serial/batch items: DO NOT create on server (validation needs bundle).
          Instead, return data so the client can open a NEW unsaved Stock
          Reconciliation form pre-filled with qty & rate, and you can use
          Serial & Batch Selector there.
        """

        if self.docstatus != 1:
            frappe.throw(_("Please submit this Stock Valuation Fix before creating revaluation."))

        if self.revaluation_document:
            frappe.throw(
                _("Revaluation already created: {0}").format(self.revaluation_document)
            )

        if not self.qty_on_hand:
            frappe.throw(_("Qty on Hand is zero. Nothing to revalue."))

        if not self.target_valuation_rate:
            frappe.throw(_("Please set Target Valuation Rate before creating revaluation."))

        self.update_totals()
        if abs(flt(self.difference_value)) < 0.0001:
            frappe.throw(_("Difference in value is zero. No revaluation required."))

        item_doc = frappe.get_doc("Item", self.item_code)
        is_tracked = bool(
            getattr(item_doc, "has_serial_no", 0) or getattr(item_doc, "has_batch_no", 0)
        )

        if is_tracked:
            return {
                "needs_manual": 1,
                "company": self.company,
                "item_code": self.item_code,
                "warehouse": self.warehouse,
                "qty": self.qty_on_hand,
                "valuation_rate": self.target_valuation_rate,
                "posting_date": self.posting_date or nowdate(),
                "posting_time": self.posting_time,
            }

        sr = frappe.new_doc("Stock Reconciliation")
        sr.company = self.company

        if hasattr(sr, "posting_date"):
            sr.posting_date = self.posting_date or nowdate()
        if hasattr(sr, "posting_time") and self.posting_time:
            sr.posting_time = self.posting_time

        sr.append(
            "items",
            {
                "item_code": self.item_code,
                "warehouse": self.warehouse,
                "qty": self.qty_on_hand,
                "valuation_rate": self.target_valuation_rate,
            },
        )

        sr.insert(ignore_permissions=True)
        sr.submit()

        self.revaluation_document = sr.name
        self.status = "Revaluation Created"
        self.save(ignore_permissions=True)

        frappe.msgprint(
            _(
                "Stock Reconciliation {0} created for Item {1} in Warehouse {2}. "
                "Difference in value: {3}. It has been submitted automatically."
            ).format(
                sr.name,
                self.item_code,
                self.warehouse,
                self.difference_value,
            ),
            alert=True,
        )

        self.add_comment(
            "Info",
            _(
                "Stock Reconciliation {0} created from Stock Valuation Fix {1}."
            ).format(sr.name, self.name),
        )

        return {"revaluation_document": sr.name, "needs_manual": 0}

    @frappe.whitelist()
    def update_source_entry(self):
        """
        Update the original source voucher item (e.g. Purchase Receipt Item)
        with the target_valuation_rate AND recalculate amounts / totals.

        Behaviour:
        - If source_row_name is set  -> update ONLY that row.
        - If source_row_name is empty -> update ALL matching rows
          with same Item + (optional) Warehouse.

        This avoids extra Stock Reconciliation / SLE and ensures
        Repost Item Valuation reads from the corrected source.
        """

        if self.docstatus != 1:
            frappe.throw(_("Please submit this Stock Valuation Fix before updating source entry."))

        if not self.target_valuation_rate:
            frappe.throw(_("Please set Target Valuation Rate before updating source entry."))

        if not self.source_voucher_type or not self.source_voucher_no:
            frappe.throw(_("Please set Source Voucher Type and Source Voucher No."))

        if self.source_voucher_type != "Purchase Receipt":
            frappe.throw(
                _("Update Source Entry Rate currently supports only Purchase Receipt. "
                  "Selected: {0}").format(self.source_voucher_type)
            )

        pr = frappe.get_doc("Purchase Receipt", self.source_voucher_no)
        new_rate = flt(self.target_valuation_rate)
        conversion_rate = flt(pr.get("conversion_rate") or 1)

        rows_to_update = []

        if self.source_row_name:
            for row in pr.items:
                if row.name == self.source_row_name:
                    rows_to_update.append(row)
                    break

            if not rows_to_update:
                frappe.throw(
                    _("Source Row Name {0} not found in Purchase Receipt {1}.")
                    .format(self.source_row_name, pr.name)
                )
        else:
            rows_to_update = [
                row for row in pr.items
                if row.item_code == self.item_code
                and (not row.warehouse or row.warehouse == self.warehouse)
            ]

            if not rows_to_update:
                frappe.throw(
                    _(
                        "No matching Purchase Receipt Item found in {0} "
                        "for Item {1} and Warehouse {2}. "
                        "Please set Source Row Name manually."
                    ).format(pr.name, self.item_code, self.warehouse)
                )

        updated_rows = []
        first_old_rate = None

        for row in rows_to_update:
            old_rate = flt(row.rate)

            if first_old_rate is None:
                first_old_rate = old_rate

            if abs(old_rate - new_rate) < 0.0000001:
                continue

            qty = flt(row.qty)
            amount = qty * new_rate
            base_rate = new_rate * conversion_rate
            base_amount = amount * conversion_rate

            row.rate = new_rate
            if hasattr(row, "valuation_rate"):
                row.valuation_rate = new_rate
            if hasattr(row, "amount"):
                row.amount = amount

            if hasattr(row, "base_rate"):
                row.base_rate = base_rate
            if hasattr(row, "base_amount"):
                row.base_amount = base_amount
            if hasattr(row, "net_rate"):
                row.net_rate = new_rate
            if hasattr(row, "net_amount"):
                row.net_amount = amount
            if hasattr(row, "base_net_rate"):
                row.base_net_rate = base_rate
            if hasattr(row, "base_net_amount"):
                row.base_net_amount = base_amount

            updated_rows.append((row.name, old_rate, new_rate))

        if not updated_rows:
            frappe.msgprint(
                _("All selected source rows already have the target rate {0}. No change made.").format(new_rate),
                alert=True,
            )
            return {"updated": 0, "new_rate": new_rate}

        pr.flags.ignore_validate_update_after_submit = True
        pr.flags.ignore_mandatory = True
        pr.flags.ignore_links = True

        try:

            if hasattr(pr, "calculate_taxes_and_totals"):
                pr.calculate_taxes_and_totals()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Stock Valuation Fix: calculate_taxes_and_totals failed")

        pr.save(ignore_permissions=True)

        if first_old_rate is not None:
            self.source_current_rate = first_old_rate
        self.flags.ignore_validate_update_after_submit = True
        self.save(ignore_permissions=True)

        row_summary = ", ".join(
            [f"{name} ({old} → {new_rate})" for name, old, _ in updated_rows]
        )

        pr.add_comment(
            "Info",
            _(
                "Item row(s) {0} rate and amounts updated via Stock Valuation Fix {1}."
            ).format(row_summary, self.name),
        )

        self.add_comment(
            "Info",
            _(
                "Updated Purchase Receipt {0} rows {1} to rate {2} and recalculated totals."
            ).format(pr.name, row_summary, new_rate),
        )

        frappe.msgprint(
            _(
                "Updated Purchase Receipt {0} row(s): {1}. "
                "Totals and taxes have been recalculated. "
                "You can now run Repost Item Valuation for this voucher "
                "to realign stock and GL."
            ).format(pr.name, row_summary),
            alert=True,
        )

        return {
            "updated": len(updated_rows),
            "rows": [name for name, _, _ in updated_rows],
            "new_rate": new_rate,
        }

    @frappe.whitelist()
    def repost_valuation(self):
        """
        Create Repost Item Valuation.

        Priority:
        1) If source_voucher_type/source_voucher_no are set
           → create RIV for that voucher (e.g. Purchase Receipt we corrected).
        2) Else if revaluation_document is set
           → create RIV for Stock Reconciliation (legacy path).
        3) Else → ask user to set source voucher or create SR.
        """

        if self.docstatus != 1:
            frappe.throw(_("Please submit this Stock Valuation Fix before reposting valuation."))

        if not frappe.db.exists("DocType", "Repost Item Valuation"):
            msg = _(
                "Repost Item Valuation DocType is not available in this system. "
                "You must adjust stock valuation manually or enable the RIV tool."
            )
            frappe.msgprint(msg, alert=True)
            self.add_comment("Info", msg)
            return {"created": 0}

        voucher_type = None
        voucher_no = None

        source_voucher_type = getattr(self, "source_voucher_type", None)
        source_voucher_no = getattr(self, "source_voucher_no", None)

        if source_voucher_type and source_voucher_no:
            voucher_type = source_voucher_type
            voucher_no = source_voucher_no

        elif self.revaluation_document:
            voucher_type = "Stock Reconciliation"
            voucher_no = self.revaluation_document

        if not voucher_type or not voucher_no:
            frappe.throw(
                _(
                    "Please set Source Voucher Type & Source Voucher No, "
                    "or create a Stock Reconciliation first."
                )
            )

        riv = frappe.new_doc("Repost Item Valuation")
        riv.company = self.company
        riv.voucher_type = voucher_type
        riv.voucher_no = voucher_no

        if riv.meta.has_field("posting_date"):
            riv.posting_date = self.posting_date or nowdate()

        riv.insert(ignore_permissions=True)
        riv.submit()

        self.flags.ignore_validate_update_after_submit = True
        self.riv_document = riv.name
        self.status = "Completed"
        self.save(ignore_permissions=True)

        log_msg = _(
            "Repost Item Valuation {0} created and submitted for {1} {2}."
        ).format(riv.name, voucher_type, voucher_no)
        self.add_comment("Info", log_msg)

        frappe.msgprint(
            _(
                "Repost Item Valuation <b>{0}</b> created for {1} {2}. "
                "It will be processed in the background."
            ).format(riv.name, voucher_type, voucher_no),
            alert=True,
        )

        return {"created": 1, "repost_name": riv.name}
   