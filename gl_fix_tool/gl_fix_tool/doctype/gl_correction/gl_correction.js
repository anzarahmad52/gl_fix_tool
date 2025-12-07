frappe.ui.form.on('GL Correction', {
    refresh(frm) {
        set_status_indicator(frm);

        const is_submitted = frm.doc.docstatus === 1;


        frm.toggle_enable('apply_gl_updates', is_submitted);
        frm.toggle_enable('repost_valuation', is_submitted);
        frm.toggle_enable('validate_gl', is_submitted);
        frm.toggle_enable('rollback_gl', is_submitted);
    },

    fetch_gl_entries(frm) {
        if (!frm.doc.company || !frm.doc.voucher_type || !frm.doc.voucher_no) {
            frappe.msgprint({
                message: __('Please select Company, Voucher Type and Voucher No first.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.call({
            method: 'fetch_gl_entries',
            doc: frm.doc,
            freeze: true,
            freeze_message: __('Fetching GL Entries...'),
            callback: function (r) {
                if (!r.exc) {
                    frm.reload_doc();
                }
            }
        });
    },

    apply_gl_updates(frm) {
        if (frm.doc.docstatus !== 1) {
            frappe.msgprint({
                message: __('You can only apply GL updates after submission.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.confirm(
            __('This will directly update existing GL Entry rows. Are you sure?'),
            () => {
                frappe.call({
                    method: 'apply_gl_updates',
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __('Applying GL updates...'),
                    callback: function (r) {
                        if (!r.exc) {
                            frappe.show_alert({
                                message: __('GL Entries updated. Please verify General Ledger.'),
                                indicator: 'green'
                            });
                            frm.set_value('status', 'GL Updated');
                            frm.refresh_field('status');
                            set_status_indicator(frm);
                        }
                    }
                });
            }
        );
    },

    repost_valuation(frm) {
        if (frm.doc.docstatus !== 1) {
            frappe.msgprint({
                message: __('Please submit this Correction before reposting valuation.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.confirm(
            __('This will create a Repost Item Valuation document for this voucher. Continue?'),
            () => {
                frappe.call({
                    method: 'repost_valuation',
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __('Triggering Repost Item Valuation...'),
                    callback: function (r) {
                        if (!r.exc) {
                            frappe.show_alert({
                                message: __('Repost Item Valuation triggered (check timeline log).'),
                                indicator: 'green'
                            });
                        }
                    }
                });
            }
        );
    },

    validate_gl(frm) {
        if (frm.doc.docstatus !== 1) {
            frappe.msgprint({
                message: __('Validation is only meaningful after submission.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.call({
            method: 'validate_gl_state',
            doc: frm.doc,
            freeze: true,
            freeze_message: __('Validating GL Entries...'),
            callback: function (r) {
                // server shows messages / comments
            }
        });
    },

    rollback_gl(frm) {
        if (frm.doc.docstatus !== 1) {
            frappe.msgprint({
                message: __('Rollback is only allowed after submission.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.confirm(
            __('This will restore all linked GL Entries back to their original values. Are you absolutely sure?'),
            () => {
                frappe.call({
                    method: 'rollback_gl_updates',
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __('Rolling back GL Entries...'),
                    callback: function (r) {
                        if (!r.exc) {
                            frm.reload_doc();
                            frappe.show_alert({
                                message: __('Rollback completed. Please verify General Ledger.'),
                                indicator: 'green'
                            });
                            frm.set_value('status', 'Rolled Back');
                            frm.refresh_field('status');
                            set_status_indicator(frm);
                        }
                    }
                });
            }
        );
    }
});

frappe.ui.form.on('GL Correction Line', {
    debit(frm, cdt, cdn) {
        update_totals(frm);
    },
    credit(frm, cdt, cdn) {
        update_totals(frm);
    },
    entries_remove(frm) {
        update_totals(frm);
    }
});

function update_totals(frm) {
    let total_debit = 0.0;
    let total_credit = 0.0;

    (frm.doc.entries || []).forEach(row => {
        total_debit += flt(row.debit || 0);
        total_credit += flt(row.credit || 0);
    });

    frm.set_value('total_debit', total_debit);
    frm.set_value('total_credit', total_credit);
    frm.set_value('difference', total_debit - total_credit);

    frm.refresh_field('total_debit');
    frm.refresh_field('total_credit');
    frm.refresh_field('difference');
}

function set_status_indicator(frm) {
    let status = frm.doc.status || 'Draft';

    if (status === 'Applied') {
        frm.page.set_indicator(__('Applied'), 'green');
    } else if (status === 'GL Updated') {
        frm.page.set_indicator(__('GL Updated'), 'blue');
    } else if (status === 'Rolled Back') {
        frm.page.set_indicator(__('Rolled Back'), 'orange');
    } else if (status === 'Cancelled') {
        frm.page.set_indicator(__('Cancelled'), 'red');
    } else {
        frm.page.set_indicator(__('Draft'), 'orange');
    }
}
