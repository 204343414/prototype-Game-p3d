# AnimationAction / LocoCrowd runtime evidence

Source: `prototypeenginef.dll` x86 metadata registration, RTTI/vtable disassembly, and decoded FIG records. This file distinguishes native facts from simulator policy.

## AnimationAction serialized fields

Native metadata registration at `0x10149180` names `timeBegin`, `timeEnd`, `animation`, `type`, `startFrame`, `endFrame`, `reverse`, `partition`, `priority`, `blendInTime`, and `blendOutTime`. Setter thunks prove runtime object offsets, including priority `+0x38`, type `+0x3C`, reverse byte `+0x40`, blend-in `+0x20`, and blend-out `+0x24`.

Correlation against 108-byte FIG AnimationAction records recovers:

- animation hash at serialized `+12`
- source start/end frame at `+32/+36`
- animation-type hash at `+40`
- priority at `+48`
- reverse flag at `+52`
- partition hash at `+60`
- blend-in/out seconds at `+100/+104`

Hash `0x25f64ae2e13c4a85` is exactly `Cyclic` under the game's hash function. Current decoded content contains two partition hashes; all current Alex AnimationAction records use `0x2e7c5ad2600aeb45`. Therefore simultaneously scheduled Alex locomotion and fight clips conflict in one native partition; rendering all at weight 1 was incorrect.

The renderer now uses the decoded type, partition, priority, and blend times. Within the shared Alex partition, an emitted fight AnimationAction suppresses the co-active locomotion action. Co-owned fight records remain preserved and blend using their source blend windows. This is partition arbitration from state output, not direct input-to-clip selection. Equal-priority native replacement ordering is not yet fully reduced.

## LocoCrowdAction

RTTI `.?AVLocoCrowdAction@motion@fightitems@proto@@` resolves to vtable `0x10D9CB84`:

- start callback thunk `0x1000D54E -> 0x100AE640`
- update/event callback thunk `0x10032916 -> 0x100AF2D0`

The 204-byte `prototype @192728` LocoCrowdAction contains exact animation hashes:

- serialized `+52/+60/+68`: `alex_amb_stand` (three slots)
- `+76/+84/+92`: `alex_loco_walk_n`, `alex_loco_walk_upper_left`, `alex_loco_walk_upper_right`
- `+100/+108/+116`: `alex_loco_run_n`, `alex_loco_run_upper_left`, `alex_loco_run_upper_right`
- `+172..+188`: five decoded float blend samples `0.1, 0.3, 0.5, 1.0, 1.5`

The family labels are proven by exact source clip names. The three directional-slot meanings and full native speed/interpolation formula are not yet fully reduced.

`prototype @192664` is a decoded default LocoCrowd partition structurally nested under the locomotion bank co-owned by the base node. The current single-active-leaf executor did not activate that parallel bank, causing WASD to translate a standing Alex. As an explicit bridge pending generic parallel-bank ownership, the controller now runs that exact owner as a separate decoded locomotion-driver executor and exposes its active LocoCrowd track. The renderer chooses idle/walk/run only from the hashes carried by that emitted action and current blackboard locomotion speed; mouse/keyboard still never select a clip directly.

Current simulator speed-family threshold (`run` at blackboard XZ speed >= 3) is an approximation. It must be replaced when `0x100AF2D0` is fully reduced.

## 2026-09-19 playback-mode and root-motion tranche

Repository hash correlation resolves AnimationAction `+40` values: `0x1b729cb6529f2e87 = Hold End Frame`, `0x25f64ae2e13c4a85 = Cyclic`, and `0x8b3e16d80559e678 = Not Cyclic`. The observed `0xa43298b89a5bf3c6` remains unknown and is retained as a hash, not guessed. The `+60` enum hashes resolve to `Legacy` (`0x2e7c5ad2600aeb45`) and `From Puppet Phase` (`0xd907d85ac26f2f87`); this corrects the earlier description of that value as an arbitrary partition identity, although the serialized field itself remains the animation partition/mode field.

Jump owner `3388` schedules `alex_loco_jump_charged_stand` at frames 0-10 with unknown mode `0xa432...`, then frames 10-12 with `Hold End Frame`. The web player now latches the final sampled pose for a Hold End Frame track while its owner remains active and no same-layer replacement appears. It no longer infers looping from package-level animation metadata.

The player now samples decoded `Motion_Root`, strips root translation out of the skeletal clip, and applies weighted per-frame root displacement once to the actor root. Fixed-speed WASD translation was removed. Actor heading is derived from input relative to OrbitControls azimuth, and the free orbit camera follows actor displacement. Root rotation and native collision/ground solving are still unresolved.

### From Animation correlation correction

Exhaustive hashing of all 113,340 ASCII strings extracted from the protected DLL found an exact preimage for `0xa43298b89a5bf3c6`: `From Animation`. This is therefore no longer unknown. Browser playback consults package `anim.cyclic` only when the serialized AnimationAction explicitly says `From Animation`; explicit `Cyclic`, `Not Cyclic`, and `Hold End Frame` remain authoritative. `alex_loco_jump_charged_stand` has decoded package `cyclic=false`, so its 0-10 segment is one-shot and its 10-12 segment then holds its end frame.

## 2026-09-19 partition-weight correction and complete playback inventory

The prior browser player incorrectly applied `blendInTime`/`blendOutTime` as an absolute weight against the Three.js skeleton bind pose. A single attack therefore faded toward the A-pose near its source blend window. The recovered data describes relative blending inside the selected animation partition; it does not request blending against bind pose. Selected same-partition actions now preserve their decoded relative envelopes but are normalized to a total partition weight of 1. This removes that simulator-created A-pose path without inventing an animation or extending an attack track.

`audit/alex-animation-playback-inventory.json` enumerates 1,160 AnimationAction records across 15 Alex state blocks and resolves playback only from serialized mode plus package metadata when the mode is `From Animation`. Counts: 870 From Animation, 134 Hold End Frame, 124 Cyclic, 32 Not Cyclic. Effective results against the nine assembled Alex packages: 847 one-shot, 134 hold-end-frame, 124 cyclic, and 55 package-dependent/missing. No animation-name heuristics are used.

The browser handedness conversion was also corrected: state blackboard Movement angle +90 means right/D, while applying +90 to native -Z forward in Three.js turns toward -X. Rendering now subtracts the blackboard angle, so A and D no longer turn in opposite directions.

## 2026-09-19 persistent partition ownership correction

User browser evidence exposed a second simulator-created blend defect. State snapshots legitimately emit a structural AnimationAction and the ambient LocoCrowdAction concurrently during transitions. Examples: sprint owner 328 emits `alex_loco_trans_dash` frames 0-2 then 2-7 while owner 192664 continues emitting its run slot; base Attack hold owners 22304 and 36536 retain ChargeAction ownership after their short source-frame animation segment is no longer listed as active.

The browser incorrectly averaged the structural transition with ambient run, and after latching a charged attack pose it started the ambient locomotion action underneath at weight 1. This produced the reported fist snaps and the screenshot combining a held heavy-punch frame with idle lower-body pose. The renderer now gives a scheduled structural AnimationAction ownership over ambient LocoCrowd in the same Legacy partition. A latched charged/Hold End Frame source record suppresses the ambient driver in that partition until a real same-layer replacement appears. This installs source ownership; it does not add idle, extend an attack, or invent a transition.

Observed sprint sequence is source-authored, not a simulator-designed state machine: Shift-only selects owner 101976 / `alex_loco_sprint_idle`; W+Shift selects owner 328 / `alex_loco_trans_dash` frames 0-2 and 2-7; owner 5332 then leaves locomotion to LocoCrowd run. Multiple source owners exist, but only one authoritative Legacy animation partition pose is now rendered at a time.

## Motion_Root transform composition correction

`alex_loco_trans_dash` proves root translation cannot be treated as an un-oriented vector: Motion_Root moves approximately +6.975 Z while its decoded root quaternion is approximately 180 degrees about Y. `alex_loco_run_n` moves -3.595 Z and has no animated root rotation. The previous translation-only extractor therefore sent the sprint start backward even though both source clips describe forward displacement in their own root frames.

The browser now extracts the complete Motion_Root transform. Per-frame translation is converted through the inverse sampled source-root orientation (`q_from^-1 * (p_to-p_from)`), root rotation delta is composed as `q_from^-1*q_to`, cyclic wrap composes both transforms, and root rotation/translation are removed from the skeleton clip before actor-root application. This is generic transform composition, not an animation-name sign exception.
