"""Tri-state FIG condition evaluator for the player simulator.

Unknown condition kinds propagate UNKNOWN and can never silently enable a
bank. Empty marker nodes `or` and `not` are interpreted as infix OR and prefix
NOT; adjacency is implicit AND. This grammar is isolated behind tests so it can
be replaced if DLL code evidence contradicts it.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from enum import Enum
import math
from typing import Any
import fig_timeline as ft

class Truth(Enum):
 FALSE=0; TRUE=1; UNKNOWN=2
 def __bool__(self):raise TypeError('Truth is tri-state; compare explicitly')
def tnot(x):return Truth.UNKNOWN if x is Truth.UNKNOWN else (Truth.FALSE if x is Truth.TRUE else Truth.TRUE)
def tand(a,b):
 if a is Truth.FALSE or b is Truth.FALSE:return Truth.FALSE
 if a is Truth.UNKNOWN or b is Truth.UNKNOWN:return Truth.UNKNOWN
 return Truth.TRUE
def tor(a,b):
 if a is Truth.TRUE or b is Truth.TRUE:return Truth.TRUE
 if a is Truth.UNKNOWN or b is Truth.UNKNOWN:return Truth.UNKNOWN
 return Truth.FALSE

def cmp(op,a,b):
 try:
  if op=='==':return a==b
  if op=='!=':return a!=b
  if op=='>=':return a>=b
  if op=='<=':return a<=b
  if op=='>':return a>b
  if op=='<':return a<b
 except TypeError:return None
 return None
@dataclass
class Blackboard:
 input_states:dict[str,set[str]]=field(default_factory=dict)
 input_seconds:dict[str,float]=field(default_factory=dict)
 charge_seconds:dict[str,float]=field(default_factory=dict)
 consumption_name:str|None=None
 playback_states:dict[str,str]=field(default_factory=dict)
 default_playback_state:str|None=None
 velocities:dict[tuple[str,str],float]=field(default_factory=dict)
 target_distances:dict[str,float]=field(default_factory=dict)
 health:float|None=None
 motion_state:str|None=None
 supporting_surface:bool|None=None
 supporting_surface_distance:float|None=None
 sequencer_time:float|None=None
 sequencer_branch:str|int|None=None
 active_supporting_limbs:set[str]|None=None
 grab_slot_classes:dict[str,set[str]]|None=None
 axis_degrees:dict[str,float]=field(default_factory=dict)
 unlockables:set[str]|None=None
 int_variables:dict[str,int]=field(default_factory=dict)
 name_variables:dict[str,str]=field(default_factory=dict)
 targeting:bool|None=None
 heli_boundary_status:str|None=None
 named_facts:dict[str,bool]=field(default_factory=dict)

def predicate(tok:dict[str,Any],bb:Blackboard)->Truth:
 k=tok['kind']
 if k in ('conditionGroup','conditions'):
  return evaluate_tokens(tok.get('children',[]),bb)['truth']
 if k=='false':return Truth.FALSE
 if k=='input':
  active=tok.get('state') in bb.input_states.get(tok.get('button'),set())
  if not active:return Truth.FALSE
  seconds=bb.input_seconds.get(tok.get('button'))
  if seconds is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),seconds,tok.get('hold_seconds',0.0))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='random':return Truth.TRUE
 if k=='unlockable':
  if bb.unlockables is None:return Truth.UNKNOWN
  return Truth.TRUE if tok.get('unlockable_hash') in bb.unlockables else Truth.FALSE
 if k=='variableIntCompare':
  val=bb.int_variables.get(tok.get('variable_hash'))
  if val is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),val,tok.get('value'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='sequencerBranch':
  if bb.sequencer_branch is None:return Truth.UNKNOWN
  expected=tok.get('branch',tok.get('local_branch_i32_at_12'))
  got=cmp(tok.get('comparison'),bb.sequencer_branch,expected)
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='variableNameCompare':
  val=bb.name_variables.get(tok.get('variable_hash'))
  if val is None:return Truth.UNKNOWN
  actual=f"0x{ft.hash64(val.encode()):016x}"
  got=cmp(tok.get('comparison'),actual,tok.get('value_hash'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='targeting' and bb.targeting is not None:
  return Truth.TRUE if bb.targeting==tok.get('required') else Truth.FALSE
 if k=='charge':
  val=bb.charge_seconds.get(tok.get('button'))
  if val is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),val,tok.get('threshold'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='consumptionName':
  if bb.consumption_name is None:return Truth.UNKNOWN
  got=ft.hash64(bb.consumption_name.encode())==int(tok['name_hash'],16)
  return Truth.TRUE if got else Truth.FALSE
 if k=='playbackState':
  target=tok.get('target_hash','0x0000000000000000')
  actual=bb.default_playback_state if int(target,16)==0 else bb.playback_states.get(target)
  if actual is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),actual,tok.get('state'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='velocity':
  val=bb.velocities.get((tok.get('source'),tok.get('component')))
  if val is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),val,tok.get('threshold'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='targetDistance':
  val=bb.target_distances.get(tok.get('component'))
  if val is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),val,tok.get('threshold'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='health':
  if bb.health is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),bb.health,tok.get('threshold'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='heliBoundaryStatus':
  if bb.heli_boundary_status is None:return Truth.UNKNOWN
  actual=bb.heli_boundary_status
  got=actual==tok.get('status') or actual==tok.get('status_hash')
  return Truth.TRUE if got else Truth.FALSE
 if k=='motionState':
  if bb.motion_state is None:return Truth.UNKNOWN
  return Truth.TRUE if bb.motion_state in (tok.get('state'),tok.get('state2')) else Truth.FALSE
 if k=='supportingSurface':
  if bb.supporting_surface is None:return Truth.UNKNOWN
  return Truth.TRUE if bb.supporting_surface==tok.get('required') else Truth.FALSE
 if k in ('supportingSurfaceDistance','sequencerTime'):
  value=bb.supporting_surface_distance if k=='supportingSurfaceDistance' else bb.sequencer_time
  if value is None:return Truth.UNKNOWN
  got=cmp(tok.get('comparison'),value,tok.get('threshold'))
  return Truth.UNKNOWN if got is None else (Truth.TRUE if got else Truth.FALSE)
 if k=='supportingLimbIsActive':
  if bb.active_supporting_limbs is None:return Truth.UNKNOWN
  limb=tok.get('limb');limb_hash=tok.get('limb_hash')
  active=bool(bb.active_supporting_limbs) if limb is None else (limb in bb.active_supporting_limbs or limb_hash in bb.active_supporting_limbs)
  return Truth.TRUE if active==tok.get('required',True) else Truth.FALSE
 if k=='grabSlot':
  if bb.grab_slot_classes is None:return Truth.UNKNOWN
  classes=bb.grab_slot_classes.get(tok.get('slot'),bb.grab_slot_classes.get(tok.get('slot_hash'),set()))
  return Truth.TRUE if bool(classes)==tok.get('required',True) else Truth.FALSE
 if k=='grabSlotGrabbableClass':
  if bb.grab_slot_classes is None:return Truth.UNKNOWN
  classes=bb.grab_slot_classes.get(tok.get('slot'),bb.grab_slot_classes.get(tok.get('slot_hash'),set()))
  target=tok.get('grabbable_class');target_hash=tok.get('grabbable_class_hash')
  return Truth.TRUE if target in classes or target_hash in classes else Truth.FALSE
 if k=='axisDirection':
  actual=bb.axis_degrees.get(tok.get('source'))
  if actual is None:return Truth.UNKNOWN
  delta=abs((actual-tok.get('angle_deg')+180.0)%360.0-180.0)
  return Truth.TRUE if delta<=tok.get('tolerance_deg') else Truth.FALSE
 if k in bb.named_facts:
  actual=bool(bb.named_facts[k]);required=tok.get('required')
  got=actual if required is None else actual==required
  return Truth.TRUE if got else Truth.FALSE
 return Truth.UNKNOWN

def evaluate_tokens(tokens:list[dict[str,Any]],bb:Blackboard)->dict[str,Any]:
 """Evaluate marker stream with NOT > implicit AND > OR and a trace."""
 if not tokens:return {'truth':Truth.TRUE,'trace':[],'grammar':'NOT > implicit AND > OR'}
 groups=[];cur=[];neg=False;trace=[];syntax=[]
 for tok in tokens:
  k=tok['kind']
  if k=='or':
   if neg:syntax.append('dangling not before or');neg=False
   groups.append(cur);cur=[];continue
  if k=='not':neg=not neg;continue
  val=predicate(tok,bb);raw=val
  if neg:val=tnot(val);neg=False
  cur.append(val);trace.append({'offset':tok.get('offset'),'kind':k,'raw':raw.name,'after_not':val.name})
 groups.append(cur)
 if neg:syntax.append('dangling not at end')
 if any(not g for g in groups):syntax.append('empty OR term')
 vals=[]
 for g in groups:
  x=Truth.TRUE
  for y in g:x=tand(x,y)
  vals.append(x)
 result=Truth.FALSE
 for x in vals:result=tor(result,x)
 # Malformed marker streams are not allowed to select a state.
 if syntax:result=Truth.UNKNOWN
 return {'truth':result,'trace':trace,'syntax':syntax,'grammar':'NOT > implicit AND > OR'}
