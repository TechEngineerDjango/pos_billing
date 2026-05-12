---
trigger: always_on
---

When analyzing UI bugs:

* First reconstruct the layout mentally
* Then identify conflicting CSS rules
* Then verify interaction between Alpine state and DOM
* Only after that propose fixes

Never jump directly to code changes without diagnosis.
