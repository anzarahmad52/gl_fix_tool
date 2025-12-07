frappe.ui.form.on('Stock Valuation Fix', {
    refresh(frm) {
        set_status_indicator(frm);

        const is_submitted = frm.doc.docstatus === 1;

        // Enable / disable Phase 2 buttons
        frm.toggle_enable('create_revaluation_entry', is_submitted);
        frm.toggle_enable('repost_valuation', is_submitted);
        frm.toggle_enable('update_source_entry', is_submitted);

        // Setup query for Source Row Name (auto-filter by PR + item + warehouse)
        setup_source_row_query(frm);
    },

    company(frm) {
        setup_source_row_query(frm);
    },

    item_code(frm) {
        setup_source_row_query(frm);
    },

    warehouse(frm) {
        setup_source_row_query(frm);
    },

    source_voucher_type(frm) {
        // Clear row when changing voucher type
        frm.set_value('source_row_name', '');
        setup_source_row_query(frm);
    },

    source_voucher_no(frm) {
        // Clear row when changing voucher no
        frm.set_value('source_row_name', '');
        setup_source_row_query(frm);
    },

    fetch_current_state(frm) {
        if (!frm.doc.company || !frm.doc.item_code || !frm.doc.warehouse) {
            frappe.msgprint({
                message: __('Please set Company, Item and Warehouse first.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.call({
            method: 'fetch_current_state',
            doc: frm.doc,
            freeze: true,
            freeze_message: __('Fetching current valuation...'),
            callback: function (r) {
                if (!r.exc) {
                    frm.reload_doc();
                }
            }
        });
    },

    preview_adjustment(frm) {
        if (!frm.doc.target_valuation_rate) {
            frappe.msgprint({
                message: __('Please enter Target Valuation Rate first.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.call({
            method: 'preview_adjustment',
            doc: frm.doc,
            freeze: true,
            freeze_message: __('Recalculating totals...'),
            callback: function (r) {
                if (!r.exc) {
                    frm.reload_doc();
                }
            }
        });
    },

    // 🔍 Show Serial/Batch Bundles
    show_bundles(frm) {
        if (!frm.doc.item_code || !frm.doc.warehouse) {
            frappe.msgprint({
                message: __('Please select Item and Warehouse first.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.call({
            method: 'get_serial_batch_summary',
            doc: frm.doc,
            freeze: true,
            freeze_message: __('Fetching Serial & Batch Bundles...'),
            callback: function (r) {
                // Server shows an HTML table via msgprint.
                // Nothing else required on client side.
            }
        });
    },

    create_revaluation_entry(frm) {
        if (frm.doc.docstatus !== 1) {
            frappe.msgprint({
                message: __('Please submit this document first.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.confirm(
            __('This will create a Stock Reconciliation for this Item and Warehouse. Continue?'),
            () => {
                frappe.call({
                    method: 'create_revaluation_entry',
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __('Creating Stock Reconciliation...'),
                    callback: function (r) {
                        if (r.exc) return;

                        const msg = r.message || {};

                        // 🔹 Serial / Batch-tracked items:
                        if (msg.needs_manual) {
                            frappe.msgprint({
                                message: __(
                                    'Item is serial/batch tracked. A new Stock Reconciliation will be opened. ' +
                                    'Please use the Serial & Batch Selector to choose bundles and then submit.'
                                ),
                                indicator: 'blue'
                            });

                            frappe.new_doc('Stock Reconciliation').then(doc => {
                                // Header
                                doc.company = msg.company;
                                if (msg.posting_date) {
                                    doc.posting_date = msg.posting_date;
                                }
                                if (msg.posting_time) {
                                    doc.posting_time = msg.posting_time;
                                }

                                // One pre-filled row
                                let row = frappe.model.add_child(doc, 'Stock Reconciliation Item', 'items');
                                row.item_code = msg.item_code;
                                row.warehouse = msg.warehouse;
                                row.qty = msg.qty;
                                row.valuation_rate = msg.valuation_rate;

                                frappe.set_route('Form', 'Stock Reconciliation', doc.name);
                            });

                            return;
                        }

                        // 🔹 Non-serial/non-batch items:
                        frm.reload_doc();
                        frappe.show_alert({
                            message: __('Stock Reconciliation created and submitted.'),
                            indicator: 'green'
                        });
                    }
                });
            }
        );
    },

    // 🧷 Update rate on the original source voucher (e.g. Purchase Receipt Item)
    update_source_entry(frm) {
        if (frm.doc.docstatus !== 1) {
            frappe.msgprint({
                message: __('Please submit this document first.'),
                indicator: 'orange'
            });
            return;
        }

        if (!frm.doc.source_voucher_type || !frm.doc.source_voucher_no) {
            frappe.msgprint({
                message: __('Please set Source Voucher Type and Source Voucher No first.'),
                indicator: 'orange'
            });
            return;
        }

        if (!frm.doc.target_valuation_rate) {
            frappe.msgprint({
                message: __('Please enter Target Valuation Rate first.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.confirm(
            __('This will update the item rate on the original {0} {1}. Continue?', [
                frm.doc.source_voucher_type,
                frm.doc.source_voucher_no
            ]),
            () => {
                frappe.call({
                    method: 'update_source_entry',
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __('Updating source entry item rate...'),
                    callback: function (r) {
                        if (!r.exc) {
                            frm.reload_doc();
                            frappe.show_alert({
                                message: __('Source entry rate updated successfully.'),
                                indicator: 'green'
                            });
                        }
                    }
                });
            }
        );
    },

    repost_valuation(frm) {
        if (frm.doc.docstatus !== 1) {
            frappe.msgprint({
                message: __('Please submit this document first.'),
                indicator: 'orange'
            });
            return;
        }

        // Decide which voucher we will repost
        let target_label = '';
        if (frm.doc.source_voucher_type && frm.doc.source_voucher_no) {
            target_label = `${frm.doc.source_voucher_type} ${frm.doc.source_voucher_no}`;
        } else if (frm.doc.revaluation_document) {
            target_label = `Stock Reconciliation ${frm.doc.revaluation_document}`;
        } else {
            frappe.msgprint({
                message: __('Please set Source Voucher Type & Source Voucher No, or create a Stock Reconciliation first.'),
                indicator: 'orange'
            });
            return;
        }

        frappe.confirm(
            __('This will create a Repost Item Valuation document for {0}. Continue?', [target_label]),
            () => {
                frappe.call({
                    method: 'repost_valuation',
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __('Creating Repost Item Valuation...'),
                    callback: function (r) {
                        if (!r.exc) {
                            frm.reload_doc();
                            frappe.show_alert({
                                message: __('Repost Item Valuation created.'),
                                indicator: 'green'
                            });
                        }
                    }
                });
            }
        );
    }
});

function set_status_indicator(frm) {
    const status = frm.doc.status || 'Draft';

    if (status === 'Completed') {
        frm.page.set_indicator(__('Completed'), 'green');
    } else if (status === 'Revaluation Created') {
        frm.page.set_indicator(__('Revaluation Created'), 'green');
    } else if (status === 'Revaluation Drafted') {
        frm.page.set_indicator(__('Revaluation Drafted'), 'orange');
    } else if (status === 'Previewed') {
        frm.page.set_indicator(__('Previewed'), 'blue');
    } else if (status === 'Valuation Fetched') {
        frm.page.set_indicator(__('Valuation Fetched'), 'blue');
    } else {
        frm.page.set_indicator(__('Draft'), 'orange');
    }
}

function setup_source_row_query(frm) {
    // Only meaningful when we are working with Purchase Receipt
    frm.set_query('source_row_name', function () {
        if (!frm.doc.source_voucher_type ||
            frm.doc.source_voucher_type !== 'Purchase Receipt' ||
            !frm.doc.source_voucher_no) {
            return {};
        }

        let filters = {
            parent: frm.doc.source_voucher_no
        };

        if (frm.doc.item_code) {
            filters['item_code'] = frm.doc.item_code;
        }
        if (frm.doc.warehouse) {
            filters['warehouse'] = frm.doc.warehouse;
        }

        return {
            filters: filters
        };
    });
}
