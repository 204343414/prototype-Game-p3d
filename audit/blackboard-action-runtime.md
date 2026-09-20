# Blackboard action runtime boundary

Source: `prototypeenginef.dll`, x86 RTTI/vtable callback disassembly.

## Transformation permission

`TransformationAllowedAction` vtable `0x10DA807C`:

- start callback `0x10122530` sends permission byte `1`;
- teardown callback `0x101225B0` sends permission byte `0`.

`TransformationDisallowedAction` vtable `0x10DA80AC`:

- start callback `0x10122670` sends permission byte `0`;
- teardown callback `0x101226F0` sends permission byte `1`.

The executor now applies these messages at source start/end/owner teardown, including retained-owner and interruption teardown. A condition-blocked track produces neither the start mutation nor a false teardown restoration.

## Motion state

`MotionStateAction` vtable `0x10DA7098`, start callback `0x10119E30`. It sends the descriptor name at `+0x10` plus the dword at `+0x1C`; no teardown callback is overridden. Source records decode to a state hash and message value. The executor updates the shared motion-state blackboard only on start/fire and does not invent restoration.

## Persistent integer variables

`VariableIntSetAction` start callback `0x10128E40` resolves the current integer and dispatches operation/operand through `0x10128170`. The eight operators are assignment, add, subtract, multiply, divide, remainder, wrapping increment, and wrapping decrement. Decoded source fields now mutate shared integer state and emit a trace. Missing current values remain unresolved for non-assignment operators.

## Name variables

`VariableNameSetAction` vtable `0x10DA8B08`, start callback `0x10128FC0`. It sends descriptor destination `+0x10`, value `+0x18`, and notify byte `+0x24`. The 28-byte source record therefore decodes as header, time, destination hash, value hash, notify. The executor applies the exact destination/value pair on start/fire.

## Temporary integers

`VariableIntTemporarySetAction` start `0x10129430` and teardown `0x10129610` evaluate two distinct serialized integer expressions against the same destination. RTTI identifies `VariableIntTemporarySetTrack`; constructor `0x101C5C10` establishes descriptor fields `+0x10/+0x18`, timing `+0x20/+0x24`, operation ids `+0x28/+0x2C`, operands `+0x30/+0x34`, and context-lookup byte `+0x38`. Matching those fields to the callbacks and source stream recovers the 56-byte layout: header/begin/end, destination hash, context hash, context-lookup flag, start operator/name, teardown operator/name, start operand, teardown operand. All 13 current Alex records use local context, `= 1` on start, and `= 0` on end/teardown. The action retains receiver/context state; it does **not** generically restore an arbitrary previous integer value. The executor now applies both exact expressions. Nonlocal context lookup remains conservatively blocked until that receiver provider exists.
