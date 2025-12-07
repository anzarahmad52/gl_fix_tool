frappe.listview_settings['GL Correction'] = {
    add_fields: ["status"],

    get_indicator(doc) {
        if (doc.status === "Journal Entry Created") {
            return [
                __("Journal Entry Created"),
                "green",
                "status,=,Journal Entry Created"
            ];
        }
        if (doc.status === "Cancelled") {
            return [
                __("Cancelled"),
                "red",
                "status,=,Cancelled"
            ];
        }
        return [
            __("Draft"),
            "orange",
            "status,=,Draft"
        ];
    }
};
