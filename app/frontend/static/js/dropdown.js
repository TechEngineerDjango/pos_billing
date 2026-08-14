// Reusable Alpine component for the app's themed select-replacement dropdown
// (button + chevron, options panel, optional search). getValue/setValue let
// the dropdown read/write a field that lives in the including page's own
// Alpine state, so the dropdown has no state of its own — same pattern as
// countryPicker in countries.js, generalized for arbitrary option lists
// instead of hardcoded countries.
document.addEventListener('alpine:init', () => {
    Alpine.data('themedDropdown', (options, getValue, setValue, opts = {}) => ({
        open: false,
        search: '',
        options,

        get filtered() {
            if (!opts.searchable) return this.options;
            const q = this.search.trim().toLowerCase();
            if (!q) return this.options;
            return this.options.filter(o => o.label.toLowerCase().includes(q));
        },

        // Falls back to the placeholder (not undefined/blank) if the
        // current value doesn't match any option — defensive, since a few
        // existing call sites only avoided this by coincidence.
        get selectedLabel() {
            return this.options.find(o => o.value === getValue())?.label ?? (opts.placeholder || '');
        },

        select(o) {
            setValue(o.value);
            this.open = false;
            this.search = '';
        },

        isSelected(o) {
            return o.value === getValue();
        },
    }));
});
