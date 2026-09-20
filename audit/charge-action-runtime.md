# ChargeAction / ChargeClearAction runtime recovery

## Source-proven callback behavior

- `ChargeAction` vtable `0x10D9BC7C`: start callback `0x100340C7 -> 0x100A13B0`; phase/update callback `0x1000F420 -> 0x100A1540`.
- `ChargeClearAction` vtable `0x10D9BCDC`: start callback `0x100315E8 -> 0x100A1800`; teardown callback `0x10033D9D -> 0x100A18B0`.
- Charge updates are emitted by an active action callback. They are not a generic consequence of any physical button being held.
- ChargeClear descriptor byte `+0x18`: nonzero sends zero charge from the start callback; zero sends it from teardown. The two callbacks test opposite values.
- All decoded current ChargeAction records identify one of the source charge channels. Example hash `0xf4ed1107d85675d4` resolves exactly to `Attack`.

## Serialized record correlation

Current Alex ChargeAction records are 32 bytes: common record/timing prefix, channel hash at raw `+12`, raw word at `+20`, float at `+24`, and tail at `+28`. Hammer hold `@21760` has `Attack`, end `0.4`, `+20 = 1`, `+24 = 1.0`, `+28 = 0`. Blade hold `@1172` has end `0.5` and the same remaining fields.

ChargeClear records are 16 bytes. Raw `+12` correlates with the runtime descriptor phase flag: examples with `1` clear at start and examples with `0` clear at teardown. This serialized-to-descriptor correlation is strongly evidenced but is not claimed as recovered constructor field-copy code.

## Simulator implementation and claim boundary

- `actor_state_runtime.py` now exposes the source fields and explicitly labels the normalized linear rate as inferred.
- `FightExecutor` accrues charge only while a condition-approved, source-owned ChargeAction track is active. It uses that track channel and source interval; unrelated held buttons now accrue input duration only.
- The numeric update formula is now reduced from `0x100A13B0` and `0x100A1540`. Start clamps the queried channel value to `[0,1]` and optionally resets it. For positive duration, a target below `1.0` produces `(target - clampedStart) / duration`; a target at least `1.0` produces `target / duration`. Nonpositive duration leaves `target` as the per-second rate. Update emits the fixed start-time rate multiplied by `dt`. The blackboard receiver is modeled with `[0,1]` clamping.
- This distinction is source-relevant: three Alex `Throw` records transition from `1.0` to `0.8`; they now decay continuously over their second interval rather than snapping to `0.8`. Across the current Alex blocks there are 73 ChargeAction records: 59 Attack, 12 Throw, one Jump, and one unresolved wall channel.
- ChargeClear runs in the callback phase selected by the correlated flag, including owner teardown. It no longer relies on controller-level button resets.
