"""Integrated headless Alex controls over assembly + FIG executors.

Mouse/buttons and WASD mutate the blackboard and ask decoded state trees to
select/transition. They never choose animation clips directly. Rendering,
kinematic speed, and instantaneous power switching are labeled approximations.
"""
from __future__ import annotations
import json,math
from pathlib import Path
from alex_assembly_runtime import AlexAssembly
from actor_state_runtime import build_owner_ir,list_owner_roots,resolve_owner_path
from condition_runtime import Blackboard
from fight_executor_runtime import FightExecutor
from event_mask_runtime import EVENT_BITS

POWER_BLOCK={'Alex':'prototype_bare','AlexParasite':'prototype_bare','AlexShotBody':'prototype_bare',
 'AlexBlades':'prototype_blades','AlexWhipFist':'prototype_whipfist','AlexShield':'prototype_shield',
 'AlexSpines':'prototype_devastator','AlexMuscleMass':'prototype_musclemass',
 'AlexHammerFist':'prototype_hammerfist','AlexClaws':'prototype_claws','AlexArmour':'prototype_bare',
 'AlexChameleonicSkin':'prototype_bare','Disguise':'prototype_bare','Consume':'prototype_consume'}

class AlexController:
 def __init__(self,game_root,manifest_path):
  self.game_root=game_root;self.assembly=AlexAssembly(manifest_path);self.cache={};self.path_cache={}
  profile_path=Path(manifest_path).with_name('alex-unlockables.json')
  if profile_path.exists():
   profile=json.loads(profile_path.read_text());unlocks=set(profile.get('hashes',[]));self.unlockable_profile=profile.get('profile')
  else:unlocks=None;self.unlockable_profile=None
  self.bb=Blackboard(consumption_name='Alex',motion_state='Locomotion',supporting_surface=True,supporting_surface_distance=0.0,
   active_supporting_limbs={'Ground'},default_playback_state='Locomotion',
   input_states={k:{'Up'} for k in ('Attack','Special','Action','Jump','Run','MovementAxis')},
   input_seconds={k:0.0 for k in ('Attack','Special','Action','Jump','Run','MovementAxis')},
   charge_seconds={'Jump':0.0},unlockables=unlocks,grab_slot_classes={'Holding':set()},named_facts={
    'grabbed':False,'grabbedConditionGroup':False,'grabbedBySlot':False,'shieldProtecting':False,
    'pushbackVelocity':False,'transformationAllowed':True})
  self.keys=set();self.fight=None;self.locomotion=None;self.locomotion_driver=None;self.time=0.0;self.executor_serial=0;self.processed_transform_tracks=set()
  self.airborne_seconds=None;self.approximate_landing_seconds=1.1;self.last_landing=None

 def resolve(self,block,off):
  k=(block,off)
  if k not in self.cache:self.cache[k]=build_owner_ir(self.game_root,block,off)['root']
  return self.cache[k]
 def _resolver(self,block):return lambda off:self.resolve(block,off)
 def _surface_root(self,block):
  roots=list_owner_roots(self.game_root,block)
  stance='surface' if self.bb.supporting_surface is not False else 'air'
  named=[x for x in roots if x['name']==stance]
  if not named and stance=='surface':named=[x for x in roots if x['name']!='air' and x['kind']!='store']
  return (named or roots)[0]['offset'] if roots else None
 def switch_power(self,name):
  if self.fight and self.fight.active:self.fight.interrupt('transformation switch');self._apply_transform_callbacks(self.fight)
  self.fight=None;event=self.assembly.switch(name);self.bb.consumption_name=name;return event
 def _branch_resolver(self,path):
  if path not in self.path_cache:self.path_cache[path]=resolve_owner_path(self.game_root,path)
  found=self.path_cache[path]
  if found is None:return None
  block,owner=found;return owner,self._resolver(block)
 def _new_executor(self,block,root):
  # PrototypeTemplate binds //prototype through engine::MotionTreeBehaviour.
  # Its recovered generic-update mode is 2, registering Opportunity as phase 1.
  self.executor_serial+=1;x=FightExecutor(self.resolve(block,root),self._resolver(block),self.bb,self._branch_resolver,
   opportunity_start_mode=2,external_branch_handler=self._route_motion_branch);x.executor_id=self.executor_serial;return x
 def _route_motion_branch(self,source,candidate,cause):
  """Route proven movement-package branches without destroying fight ownership.

  PrototypeTemplate owns one MotionTree. This coordinator is the incremental
  bridge from the simulator's split locomotion/fight scopes toward that native
  ownership model.
  """
  path=candidate.branch_path or ''
  family=path.split('/')[2] if path.startswith('//') and len(path.split('/'))>2 else ''
  movement_families={'prototype','prototype_air','prototype_wall','prototype_jump','prototype_sprint','human'}
  fight_families={'prototype_bare','prototype_firearm','prototype_blades','prototype_claws',
   'prototype_whipfist','prototype_hammerfist','prototype_devastator','prototype_consume'}
  if family in movement_families:layer='locomotion'
  elif family in fight_families:layer='fight'
  else:return False
  resolver=candidate.branch_resolve
  if resolver is None or candidate.branch_owner is None:return False
  previous=getattr(self,layer)
  if previous is not None and previous.active:
   previous.interrupt(f'motion branch {path}');self._apply_transform_callbacks(previous)
  self.executor_serial+=1
  routed=FightExecutor(candidate.branch_owner,resolver,self.bb,self._branch_resolver,
   opportunity_start_mode=2,external_branch_handler=self._route_motion_branch)
  routed.executor_id=self.executor_serial
  selected=routed.start_selected(candidate.leaf_offset,f'motion branch {path}',candidate.selection_status)
  if selected is None:return False
  setattr(self,layer,routed)
  routed.trace.append({'type':'motionLayerRoute','layer':layer,'branch':path,
   'source_branch':candidate.source_branch_path or path,'requested_candidate':candidate.leaf_offset,'selected':selected,
   'source_executor_id':getattr(source,'executor_id',None),
   'evidence':'decoded Alex single-MotionTree ownership; split-scope coordinator routes package to its proven layer'})
  return True
 def _replace_fight(self,block,root,cause):
  if self.fight and self.fight.active:self.fight.interrupt(cause);self._apply_transform_callbacks(self.fight)
  self.fight=self._new_executor(block,root);return self.fight
 def _replace_locomotion(self,block,root,cause):
  if self.locomotion and self.locomotion.active:self.locomotion.interrupt(cause);self._apply_transform_callbacks(self.locomotion)
  self.locomotion=self._new_executor(block,root);return self.locomotion
 def _start_executor(self,executor):
  selected=executor.start();self._apply_transform_callbacks(executor);return selected
 def _dispatch_world_event(self,bits,context):
  if not bits:return
  for executor in (self.fight,self.locomotion):
   if executor and executor.active:
    executor.dispatch(bits,context);self._apply_transform_callbacks(executor)
 def _attack_block(self,button):
  name=self.assembly.active_name
  holding=(self.bb.grab_slot_classes or {}).get('Holding',set())
  if 'firearm' in holding or '0xf582c83b817039e2' in holding:return 'prototype_firearm'
  if name=='AlexSpines' and sum(bool(self.bb.input_states.get(x,set())&{'Pressed','Down'}) for x in ('Attack','Special','Action'))>=2:
   return 'prototype_devastator'
  if name in ('AlexBlades','AlexWhipFist','AlexHammerFist','AlexClaws','Consume'):return POWER_BLOCK[name]
  return 'prototype_bare'

 def attack_down(self,button='Attack'):
  self.bb.input_states[button]={'Pressed','Down'};self.bb.input_seconds[button]=0
  block=self._attack_block(button)
  if self.fight and self.fight.active and not (self.assembly.active_name=='AlexSpines' and block=='prototype_devastator'):
   self.fight.set_input(button,{'Pressed','Down'},0);self._apply_transform_callbacks(self.fight);return self.fight.active['offset']
  root=self._surface_root(block)
  if root is None:return None
  self._replace_fight(block,root,'new attack root');selected=self._start_executor(self.fight)
  # The same Pressed edge already selected this root. Host active-track dispatch
  # does not re-inject that edge into tracks created by the selection itself.
  return selected
 def attack_up(self,button='Attack'):
  self.bb.input_states[button]={'Up','Released'};self.bb.input_seconds[button]=0
  if not self.fight or not self.fight.active:return []
  result=self.fight.set_input(button,{'Up','Released'},0);self._apply_transform_callbacks(self.fight);return result

 def action_e(self,down=True):
  self.bb.input_states['Action']={'Pressed','Down'} if down else {'Up','Released'};self.bb.input_seconds['Action']=0
  if down and self.assembly.active_name=='AlexSpines' and any(self.bb.input_states.get(x,set())&{'Pressed','Down'} for x in ('Attack','Special')):
   block='prototype_devastator';root=self._surface_root(block);self._replace_fight(block,root,'Devastator chord root')
   return self._start_executor(self.fight)
  if down and self.assembly.active_name=='AlexWhipFist':
   block='prototype_whipfist';root=self._surface_root(block);self._replace_fight(block,root,'Whip Action root')
   return self._start_executor(self.fight)
  if not down and self.fight and self.fight.active:
   self.fight.set_input('Action',{'Up','Released'},0);self._apply_transform_callbacks(self.fight)
  # Input first enters the currently active MotionTree owner. Rebuilding the
  # base root here discarded native sprint/locomotion OnEvent transitions.
  if down:
   before=(self.locomotion,id(self.locomotion),self.locomotion.active['offset'] if self.locomotion and self.locomotion.active else None)
   self._dispatch_world_event(EVENT_BITS['Input'],{'button':'Action','states':['Pressed','Down']})
   after=(self.locomotion,id(self.locomotion),self.locomotion.active['offset'] if self.locomotion and self.locomotion.active else None)
   if after[2] is not None and after[1:]!=before[1:]:return after[2]
  # With no active owner handling the event, evaluate the decoded base root.
  block='prototype';self._replace_locomotion(block,152,'new Action evaluation')
  return self._start_executor(self.locomotion)

 def jump_down(self):
  self._refresh_locomotion_driver(False);self.airborne_seconds=None;self.bb.input_states['Jump']={'Pressed','Down'};self.bb.input_seconds['Jump']=0.0;self.bb.charge_seconds['Jump']=0.0
  before=(id(self.locomotion),self.locomotion.active['offset'] if self.locomotion and self.locomotion.active else None)
  self._dispatch_world_event(EVENT_BITS['Input'],{'button':'Jump','states':['Pressed','Down']})
  after=(id(self.locomotion),self.locomotion.active['offset'] if self.locomotion and self.locomotion.active else None)
  if after[1] is not None and after!=before:return after[1]
  self._replace_locomotion('prototype',152,'Jump input evaluation')
  return self._start_executor(self.locomotion)
 def jump_up(self):
  self.bb.input_states['Jump']={'Up','Released'};self.bb.input_seconds['Jump']=0.0
  if self.locomotion and self.locomotion.active:
   result=self.locomotion.set_input('Jump',{'Up','Released'},0.0);self._apply_transform_callbacks(self.locomotion)
  else:result=[]
  # Host physics is outside FIG. The simulator marks takeoff at release so
  # subsequent decoded air/surface predicates receive an explicit context.
  self.bb.supporting_surface=False;self.bb.supporting_surface_distance=None;self.bb.active_supporting_limbs=set();self.bb.motion_state='Physics';self.airborne_seconds=0.0
  self._dispatch_world_event(EVENT_BITS['SupportingSurface']|EVENT_BITS['MotionState'],
   {'source':'jump_release_physics_approximation','supporting_surface':False})
  return result

 def _refresh_locomotion_driver(self,moving):
  if self.locomotion_driver and self.locomotion_driver.active:self.locomotion_driver.interrupt('locomotion driver reevaluation')
  self.locomotion_driver=None
  if not moving or self.bb.supporting_surface is False:return
  # prototype @192664 is the decoded default LocoCrowd partition nested under
  # the co-owned locomotion bank. It is kept separate until generic parallel-
  # bank ownership is recovered; it emits a LocoCrowd track, never a clip chosen by input.
  self.locomotion_driver=self._new_executor('prototype',192664)
  self.locomotion_driver.start_selected(192664,'decoded co-owned LocoCrowd partition','selected decoded default locomotion driver')
 def set_move_keys(self,keys):
  self.keys={k.upper() for k in keys};x=(1 if 'D' in self.keys else 0)-(1 if 'A' in self.keys else 0);z=(1 if 'W' in self.keys else 0)-(1 if 'S' in self.keys else 0)
  moving=bool(x or z);running=bool(self.keys&{'SHIFT','SHIFTLEFT','SHIFTRIGHT','RUN'});speed=(9.0 if running else 4.0) if moving else 0.0
  self.bb.input_states['MovementAxis']={'Down'} if moving else {'Up'};self.bb.input_seconds['MovementAxis']=0
  self.bb.input_states['Run']={'Down'} if running else {'Up'};self.bb.input_seconds['Run']=0
  self.bb.velocities[('Locomotion','XZ')]=speed;self.bb.velocities[('Locomotion','Z')]=z*speed
  if moving:self.bb.axis_degrees['Movement']=math.degrees(math.atan2(x,z))
  self._dispatch_world_event(EVENT_BITS['Movement'],{'keys':sorted(self.keys),'moving':moving})
  self._replace_locomotion('prototype',152,'movement reevaluation');selected=self._start_executor(self.locomotion);self._refresh_locomotion_driver(moving);return selected

 def set_world_context(self,*,targeting=None,supporting_surface=None,supporting_surface_distance=None,supporting_limbs=None,motion_state=None,
                       named_facts=None,int_variables=None,name_variables=None,unlockables=None,grab_slot_classes=None,
                       velocities=None,target_distances=None,axis_degrees=None,health=None,sequencer_time=None,sequencer_branch=None,
                       playback_states=None,default_playback_state=None,heli_boundary_status=None):
  """Inject host/world facts, then dispatch their decoded event categories."""
  bits=0
  if targeting is not None:self.bb.targeting=bool(targeting);bits|=EVENT_BITS['Target']
  if supporting_surface is not None:
   self.bb.supporting_surface=bool(supporting_surface)
   if supporting_limbs is None:self.bb.active_supporting_limbs={'Ground'} if self.bb.supporting_surface else set()
   if supporting_surface_distance is None:self.bb.supporting_surface_distance=0.0 if self.bb.supporting_surface else None
   bits|=EVENT_BITS['SupportingSurface']
  if supporting_surface_distance is not None:
   self.bb.supporting_surface_distance=float(supporting_surface_distance);bits|=EVENT_BITS['SupportingSurface']
  if supporting_limbs is not None:
   self.bb.active_supporting_limbs={str(x) for x in supporting_limbs};bits|=EVENT_BITS['SupportingSurface']
  if motion_state is not None:self.bb.motion_state=str(motion_state);bits|=EVENT_BITS['MotionState']
  if named_facts:
   self.bb.named_facts.update({str(k):bool(v) for k,v in named_facts.items()});bits|=EVENT_BITS['Variable']
  if int_variables:
   self.bb.int_variables.update({str(k):int(v) for k,v in int_variables.items()});bits|=EVENT_BITS['Variable']
  if name_variables:
   self.bb.name_variables.update({str(k):str(v) for k,v in name_variables.items()});bits|=EVENT_BITS['Variable']
  if unlockables is not None:self.bb.unlockables={str(x) for x in unlockables};bits|=EVENT_BITS['Variable']
  if grab_slot_classes is not None:
   self.bb.grab_slot_classes={str(slot):{str(x) for x in classes} for slot,classes in grab_slot_classes.items()};bits|=EVENT_BITS['Attachment']
  if velocities:
   for key,value in velocities.items():
    parts=key if isinstance(key,(list,tuple)) else str(key).split('/',1)
    if len(parts)!=2:raise ValueError('velocity keys must be source/component')
    self.bb.velocities[(str(parts[0]),str(parts[1]))]=float(value)
   bits|=EVENT_BITS['Movement']
  if target_distances:self.bb.target_distances.update({str(k):float(v) for k,v in target_distances.items()});bits|=EVENT_BITS['Target']
  if axis_degrees:self.bb.axis_degrees.update({str(k):float(v) for k,v in axis_degrees.items()});bits|=EVENT_BITS['Movement']
  if health is not None:self.bb.health=float(health);bits|=EVENT_BITS['Health']
  if sequencer_time is not None:self.bb.sequencer_time=float(sequencer_time);bits|=EVENT_BITS['Playback']
  if sequencer_branch is not None:self.bb.sequencer_branch=sequencer_branch;bits|=EVENT_BITS['Playback']
  if playback_states:self.bb.playback_states.update({str(k):str(v) for k,v in playback_states.items()});bits|=EVENT_BITS['Playback']
  if default_playback_state is not None:self.bb.default_playback_state=str(default_playback_state);bits|=EVENT_BITS['Playback']
  if heli_boundary_status is not None:self.bb.heli_boundary_status=str(heli_boundary_status);bits|=EVENT_BITS['AIEvent']
  self._dispatch_world_event(bits,{'source':'world_context','event_names':[name for name,bit in EVENT_BITS.items() if bits&bit]})
  return self.snapshot()

 def _apply_transform_callbacks(self,executor):
  if executor is None:return
  for i,event in enumerate(executor.trace):
   key=(executor.executor_id,i)
   if key in self.processed_transform_tracks:continue
   if event.get('type')=='track' and event.get('kind') in ('transformDesired','transformStart','transformComplete','transformFade') and (event.get('phase') in ('start','fire') or (event.get('kind')=='transformFade' and event.get('phase') in ('end','teardown'))):
    self.assembly.apply_transform_track(event['track'],event['phase']);self.bb.consumption_name=self.assembly.active_name
   self.processed_transform_tracks.add(key)
 def tick(self,dt):
  self.time+=dt;self.assembly.advance(dt)
  if self.airborne_seconds is not None:
   self.airborne_seconds+=dt
   if self.airborne_seconds>=self.approximate_landing_seconds:
    elapsed=self.airborne_seconds;self.airborne_seconds=None;self.last_landing={'time':self.time,'airborne_seconds':elapsed,'evidence':'simulator ballistic landing approximation'};self.bb.supporting_surface=True;self.bb.supporting_surface_distance=0.0;self.bb.active_supporting_limbs={'Ground'};self.bb.motion_state='Locomotion'
    self._dispatch_world_event(EVENT_BITS['SupportingSurface']|EVENT_BITS['MotionState'],{'source':'simulator_ballistic_landing_approximation','airborne_seconds':elapsed,'supporting_surface':True})
    for executor in (self.fight,self.locomotion):
     if executor is not None:executor.trace.append({'type':'hostPhysics','event':'land','airborne_seconds':elapsed,'evidence':'simulator ballistic landing approximation; FIG receives decoded SupportingSurface/MotionState events'})
    self._refresh_locomotion_driver(bool(self.keys&{'W','A','S','D'}))
  input_advanced=False
  # MotionTreeBehaviour dispatches phase 1 once over the registrations that
  # existed at callback entry, then performs its mode-2 generic update.
  fight_at_phase=self.fight;locomotion_at_phase=self.locomotion;driver_at_phase=self.locomotion_driver
  if fight_at_phase and fight_at_phase.active:fight_at_phase.dispatch_opportunity_phase(1,dt)
  if locomotion_at_phase and locomotion_at_phase.active:locomotion_at_phase.dispatch_opportunity_phase(1,dt)
  if driver_at_phase and driver_at_phase.active:driver_at_phase.dispatch_opportunity_phase(1,dt)
  current_fight=self.fight;current_locomotion=self.locomotion;current_driver=self.locomotion_driver
  if current_fight and current_fight.active:current_fight.tick(dt,True);input_advanced=True
  if current_locomotion and current_locomotion.active:current_locomotion.tick(dt,not input_advanced)
  if current_driver and current_driver.active:current_driver.tick(dt,False)
  self._apply_transform_callbacks(self.fight);self._apply_transform_callbacks(self.locomotion)
  for button,states in list(self.bb.input_states.items()):
   if 'Pressed' in states:self.bb.input_states[button]=set(states)-{'Pressed'}
   elif 'Released' in states:self.bb.input_states[button]=set(states)-{'Released'}
 def snapshot(self):
  active_animation_tracks=[]
  for layer,executor in (('locomotion',self.locomotion),('fight',self.fight)):
   if executor is None or not hasattr(executor,'active_tracks'):continue
   for row in executor.active_tracks('animation'):
    active_animation_tracks.append({'layer':layer,'executor_id':executor.executor_id,**row})
  active_locomotion_tracks=[]
  if self.locomotion_driver:
   for row in self.locomotion_driver.active_tracks():
    if row['track'].get('kind') in ('locoCrowd','locoStrafe'):active_locomotion_tracks.append({'layer':'locomotionDriver','executor_id':self.locomotion_driver.executor_id,**row})
  return {'time':self.time,'assembly':self.assembly.assembled_components(),
   'host_physics':{'airborne_seconds':self.airborne_seconds,'last_landing':self.last_landing,'landing_after_seconds':self.approximate_landing_seconds,'evidence':'simulator approximation; decoded FIG receives SupportingSurface and MotionState events'},
   'active_animation_tracks':active_animation_tracks,'active_locomotion_tracks':active_locomotion_tracks,
   'blackboard':{'input_states':{k:sorted(v) for k,v in self.bb.input_states.items()},
    'input_seconds':dict(self.bb.input_seconds),'charge_seconds':dict(self.bb.charge_seconds),
    'consumption_name':self.bb.consumption_name,'velocities':{f'{a}/{b}':v for (a,b),v in self.bb.velocities.items()},
    'targeting':self.bb.targeting,'target_distances':dict(self.bb.target_distances),'supporting_surface':self.bb.supporting_surface,'supporting_surface_distance':self.bb.supporting_surface_distance,
    'active_supporting_limbs':None if self.bb.active_supporting_limbs is None else sorted(self.bb.active_supporting_limbs),'motion_state':self.bb.motion_state,
    'airborne_seconds':self.airborne_seconds,'approximate_landing_seconds':self.approximate_landing_seconds,
    'axis_degrees':dict(self.bb.axis_degrees),'health':self.bb.health,'sequencer_time':self.bb.sequencer_time,'sequencer_branch':self.bb.sequencer_branch,'heli_boundary_status':self.bb.heli_boundary_status,'playback_states':dict(self.bb.playback_states),'default_playback_state':self.bb.default_playback_state,
    'grab_slot_classes':None if self.bb.grab_slot_classes is None else {k:sorted(v) for k,v in self.bb.grab_slot_classes.items()},
    'unlockable_profile':self.unlockable_profile,'unlockable_count':None if self.bb.unlockables is None else len(self.bb.unlockables),
    'named_facts':dict(self.bb.named_facts),'int_variables':dict(self.bb.int_variables),'name_variables':dict(self.bb.name_variables)},  
   'fight':None if not self.fight else {'executor_id':self.fight.executor_id,'active_offset':self.fight.active['offset'] if self.fight.active else None,
    'retained_owner_offsets':[r.owner['offset'] for r in self.fight.retained],'trace':self.fight.trace},
   'locomotion':None if not self.locomotion else {'executor_id':self.locomotion.executor_id,'active_offset':self.locomotion.active['offset'] if self.locomotion.active else None,
    'retained_owner_offsets':[r.owner['offset'] for r in self.locomotion.retained],'trace':self.locomotion.trace},
   'claim_boundary':{'controls':'decoded condition/state-tree selection','component_fields':'decoded TOD manifest',
    'active-child cursor/opportunity/expiry':'inferred, explicitly traced','kinematics':'simulator approximation'}}
