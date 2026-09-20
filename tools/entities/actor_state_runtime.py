"""State-first runtime model for the Prototype player simulator.

This module deliberately models ownership before execution. It retains ordered
condition tokens and every co-owned track; it does not infer that source order
is stage order. Execution semantics will be added only behind regression tests.
"""
from __future__ import annotations
import struct
from typing import Any
import fig_timeline as ft
from event_mask_runtime import decode_event_mask,unknown_event_bits

H=ft.hash64
STRUCT={H(b"store"):"store",H(b"bank"):"bank",H(b"node"):"node"}
K_COND=H(b"conditions");K_TRACKS=H(b"tracks")

def _walk(nodes):
 for n in nodes:
  yield n
  yield from _walk(n.children)

ALEX_DLL_NAMES = """
2hnd
Blades
Default
Effects
Pose
WOIPlayReaction
WOIShowTargetName
absoluteHeight
action
activate
aiMessage
aiPeriodicMessage
alex
allowGrabbing
animationBlendVerticalAim
animationBlendVerticalAimStrafe
apc_m2_marine
areDisguiseActionsEnabled
attack
attributeName
autoTargetClear
autoTargetDistance
autoTargetPosition
available
base
blind
bloodtox
bottom
brake
brawler
buttonHintShow
buttonHintShowing
camera
cameraHint
cameraReset
cameraTypeSelect
causeDamage
characterCloudState
clear
clearCollisionRecord
collisionEnableOnJoint
conditionalExecute
consumeByParent
consumeMe
curePoison
cyclic
damage
death
deathOnEnd
decal
default
defense
detachFromParent
detect
detectedObjectAngle
detectedObjectConditionGroup
detectedObjectDistance
detectedObjectFacing
detectedObjectIsSeen
detectedObjectIsTarget
devastator
devastatorAvailable
devastatorTargetConditionGroup
devastatorTargetDistance
devastatorTargetExecute
devastatorTargetGroundSpike
direction
disableRagdoll
disableTimeWithNoDamageCounter
disguise
distanceToJoint
done
down
dramaticCamera
drift
driving
edgeDetect
edgeNormal
edgePosition
edgeSynchronize
effect
effectConsume
effects
enableButtonHints
enableLimbIK
end
entry
exit
faction
factionType
fall
female
firearm
float
flyingAttack
flyingAutoTarget
flyingBrake
flyingGroundAttack
footstep
footstepDamage
forceEmotionalState
from
gender
giver
grab
grabObjectFromChildGrabSlot
grabObjectFromParentGrabSlot
grabSetGrabbingTimer
grabSlotDetach
grabSlotDetachThrow
grabSlotEnableStuckTest
grabSlotExecute
grabSlotExecuteUsingChild
grabSlotSwap
grabSlotUpdateAttachment
grabbed
grabbedDetach
grabbedExecuteOnParent
grabbedExecuteUsingParent
grabbedGrabbableClass
grabbedUpdateAttachment
grappleLocoSteer
ground
groundSlide
groundSpikeAnimateClusterRadii
groundSpikeClusterDescend
groundSpikeConfigureAutoOffshoots
groundSpikeParentConditionGroup
groundSpikeSpawnCluster
groundSpikeTestState
gv
hasMarker
hasPhysics
healthSetMultiplier
heavy
height
heli_bh_marine
helicopter
hideHUD
high
hint
hitReaction
holdOnAlertEvents
huge
human
hunter
idle
intersectionProperties
invulnerable
isDisguiseActionEnabled
isGrabbable
isUndamageable
isWOITarget
jet
joint
jointLookAt
jump
jumpTo
keyframed
land
landing
leader
leader_hunter
left
light
loaded
locoClimb
locoCrowd
locoSteer
locoStrafe
low
male
master
mixer
motion
motionTrail
moveAtVelocity
movement
north
npcLOD
obstacleAvoid
obstacleAvoidExit
obstacleFaceHeightSlope
obstacleHeight
obstacleTest
obstacleTimeToObstacle
obstacleVelocity
offset
on
orient
padRumbleClear
padRumbleSet
panic
parent
patsyCreate
pc
physics
physicsKeyframedEnable
pose
powerRagdollByPuppet
primitiveDisplaySimple
prop
prototype
punchinCamera
radialBlur
ragdollEnabled
ragdollStoreJointVelocities
range
reactionEventConditionGroup
reactionHitExecute
reactionHitFromMuscleMass
reactionPushbackOrient
reactionReceiverIsTarget
reactionThrowHitType
ready
redirect
repeat
right
scenarioStringEvent
script
scriptedCamera
scrubber
se
sequencerTime
setVisibility
shaderAnimation
shared
shieldDeploy
shieldHealth
shieldReflect
shieldState
short
shoulderConFixup
shove
show
showDisguiseActionHUD
slowMotion
smartHintSetTimer
soldier
spawnGib
spawnObject
spawner
speed
sprint
sprintVelocityClear
start
stats
stealth
stuck
stun
supersoldier
supportingLimb
supportingLimbExtraLength
supportingLimbSetActive
supportingLimbSurfaceIntersectionProperties
supportingLimbTest
supportingLimbTestSurfaceNormalVelocityArc
supportingLimbTestSwitchSurfaces
supportingSurfaceConditionGroup
supportingSurfaceDistance
supportingSurfaceNormalAngle
supportingSurfaceStick
supportingSurfaceVelocityAngle
supreme_hunter
surfaceMoveAtVelocity
tank
tank_ram_marine
target
targetClass
targetConditionGroup
targetIsHuman
targetable
tendril
tendrilDevastator
tendrilFromParent
threat
threatSendToTarget
throw
timer
timerSet
top
trackGroup
transformComplete
transformDesired
transformFade
transformStart
transformationConsumeDescriptionValid
transformationSlotActive
truckOS003
uninterruptible
unknown
up
variableIntSet
variableIntTemporarySet
variableNameSet
vehicle
wait
wallExit
wallJump
wallJumpUp
wallRun
wallSlide
whipFistAnimated
whipFistExtend
whipFistFreezeSegments
whipFistMakeTaut
whipFistPullParentToTarget
whipFistResetTarget
whipFistRetract
whipFistSegmentFixToJoint
worldConeDamage
worldDamage
""".splitlines()

def _name_map(st):
 m=dict(st["names"])
 for s in ("store","bank","node","conditions","tracks","animation","input","or","not",
           "charge","consumptionName","playbackState","sequence","opportunity","onEvent",
           "execute","reference","Pressed","Down","Up","Released","Attack","Special",
           "Action","Jump","Target","Alex","AlexShotBody","AlexParasite","AlexBlades","AlexWhipFist",
           "AlexShield","AlexSpines","AlexMuscleMass","AlexHammerFist","AlexClaws","AlexArmour",
           "AlexChameleonicSkin","Disguise","Consume","==","!=",">=","<","initial","mid","final","disabled",
           "windup","hold","release","velocity","targetDistance","health","motionState",
           "supportingSurface","axisDirection","false","Locomotion","Physics","<=",">",
           "sound","spawn","hit","cameraShakeRequest","noPushback","moveCharged","move",
           "autoTarget","threatBall","chargeClear","freezeScale","dialogue","ikSurfaceFixup",
           "transformationDisallowed","DpadDown","variableNameCompare","sequencerBranch","Throw",
           "supportingLimbIsActive","grabSlotObjectTemplate","supportingLimbTestSurfaceConditionGroup",
           "pushbackVelocity","crowdPosition","unlockable","random","grabbedConditionGroup",
           "grabbedBySlot","DpadRight","transformationDesiredSenses","variableIntCompare","pckeyboard",
           "lua","RightBumper","transformationSlotTransformDesired","MovementAxis","grabSlot","className",
           "supportingLimbTestSurfaceNormalAngle","outOfCombatTimer","grabSlotConditionGroup","pushbackDirection",
           "grabbableClass","Movement","transformationAllowed","grabSlotClassName","targetAngle","timerCompare",
           "grabSlotGrabbableClass","shieldProtecting","grabSlotMass","crowdMinDistance","targeting","DpadUp",
           "X","Y","Z","XZ","conditionGroup","surface","air","common","pushFromObstacle",
           "locoInput","reactionEvent","autoTargetConditionGroup","whipFistExtendBlockedOrNot",
           "supportingLimbTestSurfaceDistance","Ground","Climb","Run","Holding","heliBoundaryStatus","Heli_Warn","Heli_Kill"): 
  m[H(s.encode())]=s
 for s in ALEX_DLL_NAMES:m.setdefault(H(s.encode()),s)
 return m

def _node_at(st,block,off):
 return next((n for n in _walk(st["parsed"][block]) if n.off==off),None)

def _hname(names,h):return names.get(h,f"0x{h:016x}")
def _owner_name(blob,n,names):
 return _hname(names,struct.unpack_from("<Q",blob,n.voff)[0]) if n.size>=8 else None

def _condition_token(blob,n,names):
 v=n.voff; kind=_hname(names,n.key); out={"kind":kind,"offset":n.off,"size":n.size}
 if kind=="input" and n.size==40:
  out.update(button=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             state=_hname(names,struct.unpack_from("<Q",blob,v+8)[0]),
             comparison=_hname(names,struct.unpack_from("<Q",blob,v+16)[0]),
             hold_seconds=struct.unpack_from("<f",blob,v+24)[0])
 elif kind=="consumptionName" and n.size>=8:
  out["name_hash"]=f"0x{struct.unpack_from('<Q',blob,v)[0]:016x}"
 elif kind=="playbackState" and n.size==24:
  out.update(comparison=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             state=_hname(names,struct.unpack_from("<Q",blob,v+8)[0]),
             target_hash=f"0x{struct.unpack_from('<Q',blob,v+16)[0]:016x}")
 elif kind=="charge" and n.size==20:
  out.update(comparison=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             threshold=struct.unpack_from("<f",blob,v+8)[0],
             button=_hname(names,struct.unpack_from("<Q",blob,v+12)[0]))
 elif kind=="sequencerBranch" and n.size>=20:
  comparison=_hname(names,struct.unpack_from("<Q",blob,v)[0]);path_len=struct.unpack_from("<I",blob,v+8)[0]
  out.update(comparison=comparison,branch_length=path_len)
  if path_len and v+12+path_len+8<=v+n.size:
   raw=blob[v+12:v+12+path_len];out['branch']=raw.rstrip(b'\0').decode('ascii','replace')
   out.update(branch_tail_i32=struct.unpack_from('<i',blob,v+12+path_len)[0],
              branch_flag_u32=struct.unpack_from('<I',blob,v+16+path_len)[0])
  elif path_len==0:
   out.update(local_branch_i32_at_12=struct.unpack_from('<i',blob,v+12)[0],
              branch_flag_u32=struct.unpack_from('<I',blob,v+16)[0])
 elif kind=="velocity" and n.size==28:
  out.update(source=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             component=_hname(names,struct.unpack_from("<Q",blob,v+8)[0]),
             comparison=_hname(names,struct.unpack_from("<Q",blob,v+16)[0]),
             threshold=struct.unpack_from("<f",blob,v+24)[0])
 elif kind=="targetDistance" and n.size==20:
  out.update(component=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             comparison=_hname(names,struct.unpack_from("<Q",blob,v+8)[0]),
             threshold=struct.unpack_from("<f",blob,v+16)[0])
 elif kind in ("supportingSurfaceDistance","sequencerTime") and n.size==12:
  out.update(comparison=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             threshold=struct.unpack_from("<f",blob,v+8)[0])
 elif kind=="health" and n.size==16:
  out.update(comparison=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             threshold=struct.unpack_from("<f",blob,v+8)[0],flag=struct.unpack_from("<I",blob,v+12)[0])
 elif kind=="heliBoundaryStatus" and n.size==8:
  status=struct.unpack_from("<Q",blob,v)[0]
  out.update(status=_hname(names,status),status_hash=f"0x{status:016x}")
 elif kind=="motionState" and n.size==20:
  out.update(state=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             state2=_hname(names,struct.unpack_from("<Q",blob,v+8)[0]) if struct.unpack_from("<Q",blob,v+8)[0] else None,
             flag=struct.unpack_from("<I",blob,v+16)[0])
 elif kind in ("supportingSurface","grabbed") and n.size==4:
  out["required"]=bool(struct.unpack_from("<I",blob,v)[0])
 elif kind=="supportingLimbIsActive" and n.size==12:
  limb=struct.unpack_from("<Q",blob,v)[0]
  out.update(limb=_hname(names,limb) if limb else None,
             limb_hash=f"0x{limb:016x}",required=bool(struct.unpack_from("<I",blob,v+8)[0]))
 elif kind=="grabSlot" and n.size==12:
  slot=struct.unpack_from("<Q",blob,v)[0]
  out.update(slot=_hname(names,slot),slot_hash=f"0x{slot:016x}",required=bool(struct.unpack_from("<I",blob,v+8)[0]))
 elif kind=="grabSlotGrabbableClass" and n.size==24:
  slot,middle,target=struct.unpack_from("<QQQ",blob,v)
  out.update(slot=_hname(names,slot),slot_hash=f"0x{slot:016x}",
             class_u64_at_8=middle,grabbable_class=_hname(names,target),grabbable_class_hash=f"0x{target:016x}")
 elif kind=="axisDirection" and n.size==16:
  out.update(source=_hname(names,struct.unpack_from("<Q",blob,v)[0]),
             angle_deg=struct.unpack_from("<f",blob,v+8)[0],tolerance_deg=struct.unpack_from("<f",blob,v+12)[0])
 elif kind=="random" and n.size==4:
  out["weight"]=struct.unpack_from("<f",blob,v)[0]
 elif kind=="unlockable" and n.size==8:
  out["unlockable_hash"]=f"0x{struct.unpack_from('<Q',blob,v)[0]:016x}"
 elif kind=="variableIntCompare" and n.size==20:
  out.update(variable_hash=f"0x{struct.unpack_from('<Q',blob,v)[0]:016x}",
             comparison=_hname(names,struct.unpack_from("<Q",blob,v+8)[0]),value=struct.unpack_from("<i",blob,v+16)[0])
 elif kind=="variableNameCompare" and n.size==24:
  out.update(variable_hash=f"0x{struct.unpack_from('<Q',blob,v)[0]:016x}",
             comparison=_hname(names,struct.unpack_from("<Q",blob,v+8)[0]),
             value_hash=f"0x{struct.unpack_from('<Q',blob,v+16)[0]:016x}")
 elif kind=="targeting" and n.size==8:
  out["required"]=bool(struct.unpack_from("<I",blob,v)[0])
 elif kind not in ("or","not","false"):
  out["raw_hex"]=blob[v:v+n.size].hex()
 if n.children:out["children"]=[_condition_token(blob,c,names) for c in n.children]
 return out

def _track(blob,n,names,st):
 v=n.voff;kind=_hname(names,n.key);out={"kind":kind,"offset":n.off,"size":n.size}
 if kind=="animation" and n.size>=40:
  ah=struct.unpack_from("<Q",blob,v+12)[0];meta=st["frames_of"].get(ah);f0,f1=struct.unpack_from("<ff",blob,v+32)
  out.update(time_begin=struct.unpack_from("<f",blob,v+4)[0],time_end=struct.unpack_from("<f",blob,v+8)[0],
             animation=meta[0] if meta else f"0x{ah:016x}",animation_hash=f"0x{ah:016x}",
             total_frames=meta[1] if meta else None,fps=meta[2] if meta else None,start_frame=f0,end_frame=f1)
  if n.size>=108:
   type_hash=struct.unpack_from("<Q",blob,v+40)[0];partition_hash=struct.unpack_from("<Q",blob,v+60)[0]
   animation_types={0xa43298b89a5bf3c6:'From Animation',0x1b729cb6529f2e87:'Hold End Frame',0x25f64ae2e13c4a85:'Cyclic',0x8b3e16d80559e678:'Not Cyclic'}
   partitions={0x2e7c5ad2600aeb45:'Legacy',0xd907d85ac26f2f87:'From Puppet Phase'}
   out.update(animation_type=animation_types.get(type_hash,f"0x{type_hash:016x}"),
              animation_type_hash=f"0x{type_hash:016x}",priority=struct.unpack_from("<I",blob,v+48)[0],
              reverse=bool(struct.unpack_from("<I",blob,v+52)[0]),u32_at_value_56=struct.unpack_from("<I",blob,v+56)[0],
              partition=partitions.get(partition_hash,f"0x{partition_hash:016x}"),partition_hash=f"0x{partition_hash:016x}",blend_in_time=struct.unpack_from("<f",blob,v+100)[0],
              blend_out_time=struct.unpack_from("<f",blob,v+104)[0],
              field_evidence='native AnimationAction metadata registration names type/startFrame/endFrame/reverse/partition/priority/blendInTime/blendOutTime; setter offsets and serialized correlations')
 elif kind=="sound" and n.size>=16:
  event_hash=struct.unpack_from("<Q",blob,v+8)[0]
  out.update(time_begin=struct.unpack_from("<f",blob,v+4)[0],
             event=_hname(names,event_hash),event_hash=f"0x{event_hash:016x}")
  if n.size>=72:
   verb_hash=struct.unpack_from("<Q",blob,v+16)[0]
   mode_hash=struct.unpack_from("<Q",blob,v+64)[0]
   out.update(verb=_hname(names,verb_hash),volume=struct.unpack_from("<f",blob,v+24)[0],
              mode=_hname(names,mode_hash))
 elif kind=="hit" and n.size>=8:
  out["time_begin"]=struct.unpack_from("<f",blob,v+4)[0]
 elif kind=="cameraShakeRequest" and n.size>=16:
  preset_hash=struct.unpack_from("<Q",blob,v+8)[0]
  out.update(time_begin=struct.unpack_from("<f",blob,v+4)[0],
             preset=_hname(names,preset_hash),preset_hash=f"0x{preset_hash:016x}")
 elif kind=="sequence" and n.size==20:
  # DLL metadata registration 0x10a8c9b0 names the four serialized
  # fields after the record header exactly: timeBegin, branch, priority,
  # conditions. Do not relabel priority as a combo/sequence id.
  header,time_bits,branch,priority,conditions=struct.unpack_from("<IIIII",blob,v)
  out.update(raw_u32=[header,time_bits,branch,priority,conditions],
             record_header=header,time_begin=struct.unpack_from("<f",blob,v+4)[0],
             branch=branch,priority=priority,conditions_ref=conditions)
 elif kind=="opportunity" and n.size>=28:
  # Opportunity registration 0x10a8cf80 supplies these exact names.
  # branch is a bounded serialized string, so priority/conditions follow it
  # rather than occupying fixed +20/+24 offsets.
  header=struct.unpack_from("<I",blob,v)[0]
  tb,te,th=struct.unpack_from("<fff",blob,v+4)
  branch_len=struct.unpack_from("<I",blob,v+16)[0]
  tail=v+20+branch_len
  if branch_len<=n.size-28 and tail+8<=v+n.size:
   branch_raw=blob[v+20:tail]
   try:branch=branch_raw.rstrip(b"\0").decode("ascii","strict")
   except UnicodeDecodeError:branch=None
   priority=struct.unpack_from("<i",blob,tail)[0]
   conditions=struct.unpack_from("<I",blob,tail+4)[0]
   out.update(record_header=header,time_begin=tb,time_end=te,time_hook=th,
              branch=branch,branch_raw_hex=branch_raw.hex(),priority=priority,
              conditions_ref=conditions)
  else:
   out.update(record_header=header,time_begin=tb,time_end=te,time_hook=th,
              raw_hex=blob[v:v+n.size].hex(),decode_error="invalid branch length")
 elif kind in ("execute","spawn") and n.size>=16:
  out.update(time_begin=struct.unpack_from("<f",blob,v+4)[0],
             time_end=struct.unpack_from("<f",blob,v+8)[0])
  ln=struct.unpack_from("<I",blob,v+12)[0]
  if 0<ln<=n.size-16:
   try:out["path"]=blob[v+16:v+16+ln].decode("ascii","strict")
   except UnicodeDecodeError:pass
 elif kind=="onEvent" and n.size>=36:
  # DLL registration 0x10A81700 names these fields exactly. branch is a
  # bounded variable-length string; priority/conditions follow it.
  header=struct.unpack_from("<I",blob,v)[0]
  tb,te,th=struct.unpack_from("<fff",blob,v+4)
  event_mask,initial_test,branch_len=struct.unpack_from("<III",blob,v+16)
  branch_end=v+28+branch_len
  tail=(branch_end+3)&~3
  if branch_len<=n.size-36 and tail+8<=v+n.size:
   branch_raw=blob[v+28:branch_end]
   try:branch=branch_raw.rstrip(b"\0").decode("ascii","strict")
   except UnicodeDecodeError:branch=None
   priority=struct.unpack_from("<i",blob,tail)[0]
   conditions=struct.unpack_from("<I",blob,tail+4)[0]
   out.update(record_header=header,time_begin=tb,time_end=te,time_hook=th,
              event_mask=event_mask,event_names=list(decode_event_mask(event_mask)),
              unknown_event_bits=unknown_event_bits(event_mask),initial_test=bool(initial_test),
              initial_test_raw=initial_test,branch=branch,
              branch_raw_hex=branch_raw.hex(),priority=priority,
              conditions_ref=conditions)
  else:
   out.update(record_header=header,time_begin=tb,time_end=te,time_hook=th,
              event_mask=event_mask,initial_test_raw=initial_test,
              raw_hex=blob[v:v+n.size].hex(),decode_error="invalid branch length")
 elif kind=="locoCrowd" and n.size>=204:
  def anim_slot(off):
   h=struct.unpack_from('<Q',blob,v+off)[0];m=st['frames_of'].get(h)
   return {'animation':m[0] if m else f'0x{h:016x}','animation_hash':f'0x{h:016x}'}
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],
             time_end=struct.unpack_from('<f',blob,v+8)[0],float_at_12=struct.unpack_from('<f',blob,v+12)[0],
             float_at_16=struct.unpack_from('<f',blob,v+16)[0],float_at_20=struct.unpack_from('<f',blob,v+20)[0],
             float_at_24=struct.unpack_from('<f',blob,v+24)[0],animation_slots={
              'idle':[anim_slot(x) for x in (52,60,68)],'walk':[anim_slot(x) for x in (76,84,92)],
              'run':[anim_slot(x) for x in (100,108,116)]},
             blend_samples=list(struct.unpack_from('<5f',blob,v+172)),
             evidence='decoded LocoCrowdAction serialized animation hashes; idle/walk/run family labels proven by exact source clip names; direction-slot semantics unresolved')
 else:
  out["raw_hex"]=blob[v:v+n.size].hex()
 # Every recovered concrete track serializer derives from the same three-word
 # base prefix. For kinds without a dedicated decoder, preserve that prefix as
 # inferred timing rather than dropping the co-owned record.
 if kind=='transformStart' and n.size>=20:
  target=struct.unpack_from('<Q',blob,v+8)[0]
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],
             transformation=_hname(names,target),transformation_hash=f'0x{target:016x}',flag=struct.unpack_from('<I',blob,v+16)[0],
             timing_evidence='decoded transformStart layout')
 elif kind=='charge' and n.size>=32:
  channel=struct.unpack_from('<Q',blob,v+12)[0]
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],time_end=struct.unpack_from('<f',blob,v+8)[0],
             charge_channel=_hname(names,channel),charge_channel_hash=f'0x{channel:016x}',raw_u32_20=struct.unpack_from('<I',blob,v+20)[0],
             target=struct.unpack_from('<f',blob,v+24)[0],reset=bool(blob[v+28]),
             timing_evidence='decoded ChargeAction record; rate branch recovered from 0x100A13B0/0x100A1540')
 elif kind=='chargeClear' and n.size>=16:
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],time_end=struct.unpack_from('<f',blob,v+8)[0],
             clear_on_start=bool(struct.unpack_from('<I',blob,v+12)[0]),
             timing_evidence='decoded ChargeClearAction start-versus-teardown callback flag')
 elif kind=='motionState' and n.size>=24:
  state=struct.unpack_from('<Q',blob,v+8)[0]
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],
             state=_hname(names,state),state_hash=f'0x{state:016x}',raw_u32_16=struct.unpack_from('<I',blob,v+16)[0],message_value=struct.unpack_from('<I',blob,v+20)[0],
             timing_evidence='decoded MotionStateAction name/message fields; downstream host interpretation of message value unresolved')
 elif kind=='variableNameSet' and n.size>=28:
  variable,value=struct.unpack_from('<QQ',blob,v+8)
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],
             variable_hash=f'0x{variable:016x}',value=_hname(names,value),value_hash=f'0x{value:016x}',notify=bool(struct.unpack_from('<I',blob,v+24)[0]),
             timing_evidence='decoded VariableNameSetAction destination/value/notify message')
 elif kind=='variableIntSet' and n.size>=32:
  variable=struct.unpack_from('<Q',blob,v+8)[0];operation=struct.unpack_from('<i',blob,v+20)[0]
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],
             variable_hash=f'0x{variable:016x}',operation=operation,operand=struct.unpack_from('<i',blob,v+24)[0],notify=bool(blob[v+28]),
             timing_evidence='decoded VariableIntSetAction descriptor fields and integer operator dispatch')
 elif kind=='variableIntTemporarySet' and n.size>=56:
  variable,context=struct.unpack_from('<QQ',blob,v+12);start_symbol,end_symbol=struct.unpack_from('<QQ',blob,v+32)
  op_codes={0x3d:0,0x2b3d:1,0x2d3d:2,0x2a3d:3,0x2f3d:4,0x253d:5,0x2b2b:6,0x2d2d:7}
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],time_end=struct.unpack_from('<f',blob,v+8)[0],
             variable_hash=f'0x{variable:016x}',context_hash=f'0x{context:016x}',context_lookup=bool(struct.unpack_from('<I',blob,v+28)[0]),
             start_operation_symbol=start_symbol,start_operation=op_codes.get(start_symbol),start_operand=struct.unpack_from('<i',blob,v+48)[0],
             teardown_operation_symbol=end_symbol,teardown_operation=op_codes.get(end_symbol),teardown_operand=struct.unpack_from('<i',blob,v+52)[0],
             timing_evidence='decoded VariableIntTemporarySetTrack layout matched to constructor fields and start/teardown callbacks')
 elif kind=='transformDesired' and n.size>=24:
  target=struct.unpack_from('<Q',blob,v+8)[0]
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],
             transformation=_hname(names,target),transformation_hash=f'0x{target:016x}',raw_u64_tail=f'0x{struct.unpack_from("<Q",blob,v+16)[0]:016x}',
             timing_evidence='decoded time/target; runtime callback copies target hash range only; +16 serialized tail unresolved')
 elif kind=='transformFade' and n.size>=20:
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],time_end=struct.unpack_from('<f',blob,v+8)[0],
             fade_from=struct.unpack_from('<f',blob,v+12)[0],fade_to=struct.unpack_from('<f',blob,v+16)[0],
             timing_evidence='decoded TransformFadeAction start/update/teardown fields and linear callback')
 elif kind=='transformComplete' and n.size>=8:
  out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=struct.unpack_from('<f',blob,v+4)[0],
             timing_evidence='decoded transformComplete layout')
 if 'time_begin' not in out and n.size>=12:
  tb,te=struct.unpack_from('<ff',blob,v+4)
  if -1.0001<=tb<10000 and -1.0001<=te<10000:
   out.update(record_header=struct.unpack_from('<I',blob,v)[0],time_begin=tb,time_end=te,
              timing_evidence='inferred common track base prefix; kind-specific callback unresolved')
 if n.children:
  out["condition_groups"]=[[_condition_token(blob,x,names) for x in c.children]
                           for c in n.children if c.key==K_COND]
 return out

def _animation_source_runs(tracks):
 """Losslessly group adjacent, exactly-contiguous records of one source clip.

 This is not a playback schedule. It only prevents first-record truncation and
 records which source ranges form an unbroken interval.
 """
 runs=[]
 for t in (x for x in tracks if x.get("kind")=="animation"):
  if runs and runs[-1]["animation"]==t["animation"] and runs[-1]["end_frame"]==t["start_frame"]:
   runs[-1]["end_frame"]=t["end_frame"];runs[-1]["record_offsets"].append(t["offset"])
  else:runs.append({"animation":t["animation"],"start_frame":t["start_frame"],"end_frame":t["end_frame"],"record_offsets":[t["offset"]]})
 return runs

def list_owner_roots(game_root:str,block:str)->list[dict[str,Any]]:
 st=ft._state(game_root)
 if block not in st['blocks']:return []
 blob=st['blocks'][block];names=_name_map(st);out=[]
 def visit(n,inside=False):
  structural=n.key in STRUCT
  if structural and not inside:
   out.append({'offset':n.off,'kind':STRUCT[n.key],'name':_owner_name(blob,n,names)})
  for c in n.children:visit(c,inside or structural)
 for r in st['parsed'][block]:visit(r)
 return out


def resolve_owner_path(game_root:str,path:str):
 """Resolve //block/owner paths, including named reference aliases."""
 def resolve(current_path,seen):
  if current_path in seen:return None
  seen=seen|{current_path}
  parts=[x for x in current_path.split('/') if x]
  if not parts:return None
  block=parts[0];roots=list_owner_roots(game_root,block)
  if len(parts)==1:
   selectable=[r for r in roots if r.get('kind')!='store']
   if len(selectable)!=1:return None
   return block,build_owner_ir(game_root,block,selectable[0]['offset'])['root']
  def matches_name(owner,name):return owner.get('name') in (name,f'0x{H(name.encode()):016x}')
  matches=[r for r in roots if matches_name(r,parts[1])]
  if not matches:return None
  owner=build_owner_ir(game_root,block,matches[0]['offset'])['root']
  for name in parts[2:]:
   children=[c for c in owner.get('child_owners',[]) if matches_name(c,name)]
   if children:owner=children[0];continue
   target_hash=f'0x{H(name.encode()):016x}'
   refs=[r for r in owner.get('other_direct_children',[])
         if r.get('kind')=='reference' and r.get('target_owner_hash')==target_hash]
   if len(refs)!=1:return None
   ref=refs[0];targets=ref.get('target_owner_offsets',[])
   if len(targets)==1:owner=build_owner_ir(game_root,block,targets[0])['root'];continue
   branch=ref.get('branch')
   if not branch:return None
   external=resolve(branch,seen)
   if external is None:return None
   block,owner=external
  return block,owner
 return resolve(path,set())

def build_owner_ir(game_root:str,block:str,owner_offset:int)->dict[str,Any]:
 st=ft._state(game_root)
 if block not in st["blocks"]:return {"found":False,"reason":"block not found"}
 root=_node_at(st,block,owner_offset)
 if root is None or root.key not in STRUCT:return {"found":False,"reason":"structural owner not found"}
 blob=st["blocks"][block];names=_name_map(st)
 owner_offsets_by_hash={};reference_aliases_by_hash={}
 for candidate in _walk(st["parsed"][block]):
  if candidate.key in STRUCT and candidate.size>=8:
   h=struct.unpack_from("<Q",blob,candidate.voff)[0]
   owner_offsets_by_hash.setdefault(h,[]).append(candidate.off)
  elif _hname(names,candidate.key)=="reference" and candidate.size>24:
   h=struct.unpack_from("<Q",blob,candidate.voff)[0]
   path_len=struct.unpack_from("<I",blob,candidate.voff+16)[0];start=candidate.voff+20;end=start+path_len
   if path_len>0 and end<=candidate.voff+candidate.size and blob[start:start+2]==b'//':
    reference_aliases_by_hash.setdefault(h,[]).append({
     'offset':candidate.off,'branch':blob[start:end].rstrip(b'\0').decode('ascii','replace')})
 def other(c):
  kind=_hname(names,c.key);out={"kind":kind,"offset":c.off,"size":c.size}
  if kind=="reference" and c.size>=24:
   target=struct.unpack_from("<Q",blob,c.voff)[0]
   tail=blob[c.voff+8:c.voff+c.size]
   out.update(target_owner_hash=f"0x{target:016x}",
              target_owner_offsets=owner_offsets_by_hash.get(target,[]),
              target_reference_aliases=reference_aliases_by_hash.get(target,[]),
              raw_u32_tail=list(struct.unpack_from(f"<{len(tail)//4}I",tail)),
              condition_groups=[[_condition_token(blob,x,names) for x in group.children]
                                for group in c.children if group.key==K_COND])
   # Extended references encode an exact external branch after three fixed
   # words: zero/context, priority/category, then NUL-inclusive byte length.
   if c.size>24:
    path_len=struct.unpack_from("<I",blob,c.voff+16)[0]
    path_start=c.voff+20;path_end=path_start+path_len
    if path_len>0 and path_end<=c.voff+c.size and blob[path_start:path_start+2]==b'//':
     raw=blob[path_start:path_end]
     out.update(branch=raw.rstrip(b"\0").decode("ascii","replace"),
                branch_length=path_len,
                branch_u32_at_12=struct.unpack_from("<I",blob,c.voff+12)[0],
                branch_tail_i32=struct.unpack_from("<i",blob,c.voff+c.size-4)[0])
  else:out["raw_hex"]=blob[c.voff:c.voff+c.size].hex()
  return out
 def owner(n):
  direct_conditions=[];tracks=[];children=[];unowned=[]
  for c in n.children:
   if c.key==K_COND:direct_conditions.append([_condition_token(blob,x,names) for x in c.children])
   elif c.key==K_TRACKS:tracks.extend(_track(blob,x,names,st) for x in c.children)
   elif c.key in STRUCT:children.append(owner(c))
   else:unowned.append(other(c))
  return {"kind":STRUCT[n.key],"name":_owner_name(blob,n,names),"offset":n.off,"size":n.size,
          "condition_groups":direct_conditions,"tracks":tracks,
          "animation_source_runs":_animation_source_runs(tracks),"child_owners":children,
          "other_direct_children":unowned}
 return {"found":True,"schema":"prototype-state-owner-ir/v1","block":block,"root":owner(root),
         "execution_claim":"none: ownership and source order only; no arbitration or scheduling inferred"}
