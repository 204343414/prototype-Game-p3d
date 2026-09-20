# ExecuteAction runtime evidence

Source: protected `prototypeenginef.dll` RTTI/vtable disassembly plus exact FIG hierarchy.

Generic RTTI classes exist as `fight::ExecuteTrack` and `fight::ExecuteAction`. ExecuteAction vtable is `0x10E9A790`; relevant callbacks include `0x10A8EE30` and `0x10A8EEC0`. The action stores a linked branch/runtime object at `+0x28`. `0x10A8EE30` resolves that object, evaluates the action condition owner at `+0x34/+0x38`, invokes the linked object vtable `+0x28`, and then registers/retains the result. `0x10A8EEC0` updates or clones the linked runtime and calls generic active-action machinery. ExecuteTrack metadata includes a `PropertyBranch` and `PropertyConditionGroup`.

This proves that zero-path 20-byte ExecuteAction records are not inert effects. They execute serialized linked child branches. The current FIG decoder preserves sibling child owners but has not yet recovered the compact branch-link field that maps each zero-path ExecuteAction to its child owner. This missing generic linkage explains several broad failures without requiring input-specific patches:

- E selects source node 103924 named `grab`, which contains the real GrabAction at 0.1-0.4 s. Its zero-path ExecuteAction must activate linked pose branches; default pose owner 105396 contains `alex_grap_hmn_failure`. The simulator selects the grab state but currently does not execute the linked pose branch.
- Sprint/jump nodes contain several zero-path ExecuteActions linking motion, pose, and effect branches. Moving Jump reaches the source OnEvent route, but no flip animation is exposed until ExecuteAction branch linkage is recovered.
- Sprint owner 328 likewise has two zero-path ExecuteActions alongside its source animation and child pose bank.

Do not directly select the grab or flip animation from keyboard input. Recover compact ExecuteTrack branch linkage and run it generically.
