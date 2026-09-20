"""Headless ownership-preserving FIG fight executor.

Decoded track/event times and conditions remain source facts. Two mechanics are
explicit in trace evidence as INFERRED: active direct-child cursor progression,
and animation-frame-derived node expiry. No rendering/physics shortcuts select
moves.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any,Callable,Optional
from active_event_scope_runtime import ActiveEventScope
from on_event_window_runtime import OnEventConfig
from structural_candidate_runtime import select_scope,select_child_scope
from track_schedule_runtime import OwnerTrackSchedule,TrackEvent
from arbitration_runtime import owner_condition_truth
from condition_runtime import Blackboard,Truth

def _trace_json(value):
 if isinstance(value,Truth):return value.name
 if isinstance(value,dict):return {k:_trace_json(v) for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [_trace_json(v) for v in value]
 return value

@dataclass
class ScopeFrame:
 owner:dict[str,Any]
 cursor:Optional[int]=None

@dataclass
class RetainedOwner:
 owner:dict[str,Any]
 schedule:OwnerTrackSchedule
 events:ActiveEventScope
 opportunities:list[dict[str,Any]]
 scope_offset:int

@dataclass(frozen=True)
class Candidate:
 leaf_offset:int
 direct_offset:int
 selection_status:str
 scope_index:int=0
 branch_owner:Optional[dict[str,Any]]=None
 branch_resolve:Optional[Callable[[int],dict[str,Any]]]=None
 branch_path:str=''
 source_branch_path:str=''

class FightExecutor:
 def __init__(self,root:dict[str,Any],resolve:Callable[[int],dict[str,Any]],bb:Blackboard,branch_resolver=None,opportunity_start_mode:int=1,external_branch_handler=None):
  if opportunity_start_mode not in (1,2):raise ValueError('opportunity start mode must be 1 or 2')
  self.root=root;self.resolve=resolve;self.bb=bb;self.branch_resolver=branch_resolver;self.external_branch_handler=external_branch_handler;self.opportunity_phase=opportunity_start_mode-1;self.scopes:list[ScopeFrame]=[]
  self.active:Optional[dict[str,Any]]=None;self.schedule=None;self.events=None
  self.node_time=0.0;self.node_expiry:Optional[float]=None;self.trace:list[dict[str,Any]]=[]
  self.opportunities:list[dict[str,Any]]=[];self.pending_event:Optional[tuple[int,object]]=None
  self.retained:list[RetainedOwner]=[]

 def start(self)->Optional[int]:
  s=select_scope(self.root,self.bb,self.resolve,resolve_branch=self.branch_resolver)
  if s.selected_offset is None:self.trace.append({'type':'blocked','status':s.status});return None
  if s.external_branch:
   candidate=Candidate(s.selected_offset,s.selected_offset,s.status,-1,
    s.external_root,s.selected_resolver,s.external_branch)
   if self.external_branch_handler is not None:
    handled=bool(self.external_branch_handler(self,candidate,'decoded initial structural selection'))
    self.trace.append({'type':'externalBranchRouted' if handled else 'externalBranchDeferred',
     'branch':s.external_branch,'source_branch':s.external_branch,'candidate':s.selected_offset,'cause':'decoded initial structural selection',
     'evidence':'decoded cross-package branch; integrated MotionTree ownership coordinator' if handled else 'decoded cross-package branch; coordinator has no proven ownership route'})
    return s.selected_offset if handled else None
   self.root=s.external_root;self.resolve=s.selected_resolver or self.resolve
  return self.start_selected(s.selected_offset,'decoded initial structural selection',s.status)

 def _structural_path(self,owner,offset):
  if owner.get('offset')==offset:return [owner]
  for child in owner.get('child_owners',[]):
   tail=self._structural_path(child,offset)
   if tail:return [owner]+tail
  return None

 def _set_selected_scopes(self,offset):
  leaf=self.resolve(offset);path=self._structural_path(self.root,offset)
  if not path:self.scopes=[ScopeFrame(leaf)];return leaf
  self.scopes=[ScopeFrame(owner,path[i+1]['offset'] if i+1<len(path) else None)
               for i,owner in enumerate(path)]
  return leaf

 def start_selected(self,offset:int,cause:str='decoded preselected branch',status:str='preselected') -> Optional[int]:
  """Enter a preselected candidate while retaining its decoded structural ancestry."""
  leaf=self._set_selected_scopes(offset)
  self._enter(leaf,cause,status)
  self._fire_due_opportunities(-1e-12,0.0)
  self._fire_immediate_sequence_delegate()
  return self.active['offset'] if self.active else leaf['offset']

 def _configs(self,owner):
  return [OnEventConfig(t['time_begin'],t['time_end'],t['time_hook'],t['event_mask'],
          t['initial_test'],t['branch'],t['priority'],t.get('condition_groups',()),t.get('offset',-1))
          for t in owner.get('tracks',[]) if t['kind']=='onEvent']

 def _enter(self,leaf,cause,status,preserve_previous=False):
  if self.events is not None and not preserve_previous:
   for a in self.events.teardown():self.trace.append({'type':'teardownActivation','candidate':getattr(a.candidate,'leaf_offset',a.candidate)})
  if self.active is not None and self.schedule is not None and not preserve_previous:
   self._teardown_schedule(self.active,self.schedule,self.node_time,'owner transition')
  self.active=leaf;self.node_time=0.0;self.schedule=OwnerTrackSchedule(leaf,self.bb)
  self.events=ActiveEventScope.from_configs(self._configs(leaf))
  self.opportunities=[dict(t,fired=False,registration_phase=self.opportunity_phase) for t in leaf.get('tracks',[]) if t['kind']=='opportunity']
  ends=[e.time for e in self.schedule.events if e.kind=='animation' and e.phase=='end']
  tracks=leaf.get('tracks',[]);persistent=any(t['kind']=='charge' for t in tracks)
  hit_only_terminal=not leaf.get('child_owners') and bool(tracks) and all(t['kind']=='hit' for t in tracks)
  self.node_expiry=0.0 if hit_only_terminal else (None if persistent or not ends else max(ends))
  expiry_evidence=('inferred immediate completion after decoded terminal HitAction dispatch' if hit_only_terminal else ('inferred from decoded animation frame bounds/fps' if self.node_expiry is not None else None))
  self.trace.append({'type':'enter','offset':leaf['offset'],'name':leaf.get('name'),
    'cause':cause,'selection_status':status,'expiry':self.node_expiry,'expiry_evidence':expiry_evidence})
  self._record_tracks(self.schedule.start());self._record_condition_decisions(self.schedule,leaf['offset'])

 def _record_condition_decisions(self,schedule,node):
  fresh=schedule.condition_decisions[schedule.reported_decisions:];schedule.reported_decisions=len(schedule.condition_decisions)
  for d in fresh:
   if d['truth']!='TRUE':self.trace.append({'type':'blockedTrack','node':node,**_trace_json(d),
    'evidence':'decoded track conditions; FALSE/UNKNOWN cannot activate'})

 def _record_tracks(self,events:list[TrackEvent],node_offset=None):
  node=self.active['offset'] if node_offset is None else node_offset
  for e in events:
   self.trace.append({'type':'track','node':node,'time':e.time,'kind':e.kind,'track_offset':e.offset,'phase':e.phase,'evidence':e.evidence,'track':e.track})
   self._apply_track_side_effect(e.track,e.phase,node)

 def _apply_track_side_effect(self,track,phase,node):
  kind=track.get('kind')
  if kind=='charge' and phase=='start' and track.get('reset'):
   channel=track.get('charge_channel','Attack');before=self.bb.charge_seconds.get(channel)
   self.bb.charge_seconds[channel]=0.0
   self.trace.append({'type':'chargeMutation','node':node,'track_offset':track.get('offset'),'channel':channel,
    'before':before,'after':0.0,'phase':phase,'evidence':'decoded ChargeAction reset flag'})
  if kind=='chargeClear' and ((phase=='start' and track.get('clear_on_start')) or (phase in ('end','teardown') and not track.get('clear_on_start'))):
   before=dict(self.bb.charge_seconds)
   for channel in list(self.bb.charge_seconds):self.bb.charge_seconds[channel]=0.0
   self.trace.append({'type':'chargeClear','node':node,'track_offset':track.get('offset'),'before':before,
    'after':dict(self.bb.charge_seconds),'phase':phase,'evidence':'decoded ChargeClearAction callback phase flag'})
  if kind=='variableIntSet' and phase in ('start','fire'):self._apply_variable_int_set(track,node,phase)
  if kind=='variableIntTemporarySet':
   if track.get('context_hash')!='0x0000000000000000' or track.get('context_lookup'):
    if phase in ('start','end','teardown'):self.trace.append({'type':'blockedVariableIntTemporarySet','node':node,'track_offset':track.get('offset'),'phase':phase,
     'evidence':'nonlocal context lookup is decoded but its receiver provider is unavailable'})
   elif phase=='start':
    self._apply_variable_int_set({**track,'operation':track.get('start_operation'),'operand':track.get('start_operand')},node,phase,'decoded VariableIntTemporarySetAction start expression')
   elif phase in ('end','teardown'):
    self._apply_variable_int_set({**track,'operation':track.get('teardown_operation'),'operand':track.get('teardown_operand')},node,phase,'decoded VariableIntTemporarySetAction teardown expression')
  if kind=='variableNameSet' and phase in ('start','fire'):
   key=track.get('variable_hash');before=self.bb.name_variables.get(key);self.bb.name_variables[key]=track.get('value')
   self.trace.append({'type':'variableNameMutation','node':node,'track_offset':track.get('offset'),'variable_hash':key,
    'before':before,'after':track.get('value'),'evidence':'decoded VariableNameSetAction message'})
  if kind=='motionState' and phase in ('start','fire'):
   before=self.bb.motion_state;self.bb.motion_state=track.get('state')
   self.trace.append({'type':'blackboardMutation','node':node,'track_offset':track.get('offset'),'fact':'motionState',
    'before':before,'after':self.bb.motion_state,'phase':phase,'evidence':'decoded MotionStateAction message'})
  if kind in ('transformationAllowed','transformationDisallowed'):
   if phase in ('start','fire'):value=kind=='transformationAllowed'
   elif phase in ('end','teardown'):value=kind=='transformationDisallowed'
   else:return
   before=self.bb.named_facts.get('transformationAllowed');self.bb.named_facts['transformationAllowed']=value
   self.trace.append({'type':'blackboardMutation','node':node,'track_offset':track.get('offset'),'fact':'transformationAllowed',
    'before':before,'after':value,'phase':phase,'evidence':'decoded permission action start/teardown message'})

 def _teardown_schedule(self,owner,schedule,at,cause):
  starts={e.offset:e for e in schedule.events if e.phase=='start'}
  for offset in list(schedule.active_offsets):
   e=starts[offset]
   self.trace.append({'type':'track','node':owner['offset'],'time':at,'kind':e.kind,'track_offset':e.offset,
    'phase':'teardown','evidence':cause,'track':e.track})
   self._apply_track_side_effect(e.track,'teardown',owner['offset'])
  schedule.active_offsets.clear()

 def _apply_variable_int_set(self,track,node,phase='start',evidence='decoded VariableIntSetAction operator dispatch'):
  key=track.get('variable_hash');op=track.get('operation');operand=track.get('operand');before=self.bb.int_variables.get(key)
  after=None
  if op==0:after=operand
  elif before is not None and op==1:after=before+operand
  elif before is not None and op==2:after=before-operand
  elif before is not None and op==3:after=before*operand
  elif before is not None and op==4:after=0 if operand==0 else int(before/operand)
  elif before is not None and op==5:after=0 if operand==0 else before%operand
  elif before is not None and op==6:after=(before+1) if operand==0 else (before+1)%operand
  elif before is not None and op==7:after=(before+operand-1) if operand==0 else (before+operand-1)%operand
  if after is not None:self.bb.int_variables[key]=after
  self.trace.append({'type':'variableIntMutation','node':node,'track_offset':track.get('offset'),'variable_hash':key,
   'operation':op,'operand':operand,'before':before,'after':after,'phase':phase,
   'evidence':evidence if after is not None else 'blocked: current value or operator unresolved'})

 def _contains(self,owner,leaf_offset):
  if owner['offset']==leaf_offset:return True
  return any(self._contains(c,leaf_offset) for c in owner.get('child_owners',[]))

 def _candidate(self)->Optional[Candidate]:
  for index in range(len(self.scopes)-1,-1,-1):
   fr=self.scopes[index]
   s=select_child_scope(fr.owner,self.bb,self.resolve,after_child_offset=fr.cursor,
                        resolve_branch=self.branch_resolver)
   if s.selected_offset is None:continue
   if s.external_branch:
    return Candidate(s.selected_offset,s.selected_offset,s.status,-1,
     s.external_root,s.selected_resolver,s.external_branch)
   leaf=self.resolve(s.selected_offset);direct=None
   for child in fr.owner.get('child_owners',[]):
    if self._contains(child,leaf['offset']):direct=child['offset'];break
   if direct is None:direct=leaf['offset']
   return Candidate(leaf['offset'],direct,s.status,index)
  return None

 def _track_transition_candidate(self,track,source):
  truth,condition_trace=owner_condition_truth(track,self.bb)
  if truth is not Truth.TRUE:
   self.trace.append({'type':f'blocked{source}','track_offset':track.get('offset'),'truth':truth.name,'conditions':_trace_json(condition_trace),
    'evidence':f'decoded {source}-local conditions; FALSE/UNKNOWN cannot resolve a candidate'})
   return None
  branch=track.get('branch','')
  if not branch:return self._candidate()
  resolved=self.branch_resolver(branch) if self.branch_resolver else None
  if resolved is None:
   self.trace.append({'type':f'blocked{source}','track_offset':track.get('offset'),'truth':'UNKNOWN','branch':branch,
    'evidence':'decoded external branch path could not be resolved'})
   return None
  owner,branch_resolve=resolved;s=select_scope(owner,self.bb,branch_resolve,resolve_branch=self.branch_resolver)
  if s.selected_offset is None:
   truth='UNKNOWN' if s.status=='blocked-by-unknown' else 'FALSE'
   self.trace.append({'type':f'blocked{source}','track_offset':track.get('offset'),'truth':truth,'branch':branch,
    'selection_status':s.status,'evidence':'decoded external branch resolved; no condition-eligible candidate selected'})
   return None
  if s.external_branch:
   return Candidate(s.selected_offset,s.selected_offset,s.status,-1,
    s.external_root,s.selected_resolver,s.external_branch,branch)
  return Candidate(s.selected_offset,s.selected_offset,s.status,-1,owner,branch_resolve,branch,branch)

 def _activate(self,c:Candidate,cause):
  if c.branch_owner is not None:
   if self.external_branch_handler is not None:
    handled=bool(self.external_branch_handler(self,c,cause))
    self.trace.append({'type':'externalBranchRouted' if handled else 'externalBranchDeferred',
     'branch':c.branch_path,'source_branch':c.source_branch_path or c.branch_path,'candidate':c.leaf_offset,'cause':cause,
     'evidence':'decoded cross-package branch; integrated MotionTree ownership coordinator' if handled else 'decoded cross-package branch; coordinator has no proven ownership route'})
    return
   for scope_offset in {r.scope_offset for r in self.retained}:self._drop_retained_scope(scope_offset,'external branch transition')
   self.scopes=[];self.resolve=c.branch_resolve or self.resolve;self.root=c.branch_owner or self.root
   leaf=self._set_selected_scopes(c.leaf_offset)
   self._enter(leaf,f'branch {c.branch_path}',c.selection_status)
   self._fire_due_opportunities(-1e-12,0.0);self._fire_immediate_sequence_delegate();return
  target_scope=self.scopes[c.scope_index]
  preserved=False
  if self.active is not None and self.schedule is not None and self.active['offset']==target_scope.owner['offset']:
   if not any(r.owner['offset']==self.active['offset'] for r in self.retained):
    self.retained.append(RetainedOwner(self.active,self.schedule,self.events,self.opportunities,target_scope.owner['offset']))
    preserved=True
    self.trace.append({'type':'retainOwner','offset':self.active['offset'],
      'evidence':'nested owner remains scheduled/event-active while its selected child is active'})
  removed_scopes=self.scopes[c.scope_index+1:]
  self.scopes=self.scopes[:c.scope_index+1]
  for fr in removed_scopes:self._drop_retained_scope(fr.owner['offset'],'outward scope transition')
  self.scopes[-1].cursor=c.direct_offset
  leaf=self.resolve(c.leaf_offset)
  if leaf.get('child_owners') or any(x.get('kind')=='reference' for x in leaf.get('other_direct_children',[])):
   self.scopes.append(ScopeFrame(leaf))
  self._enter(leaf,cause,c.selection_status,preserve_previous=preserved)
  self._fire_due_opportunities(-1e-12,0.0)
  self._fire_immediate_sequence_delegate()
  self._redeliver_pending()

 def _fire_immediate_sequence_delegate(self):
  if not self.active:return
  has_animation=any(t['kind']=='animation' for t in self.active.get('tracks',[]))
  immediate=[t for t in self.active.get('tracks',[]) if t['kind']=='sequence' and t.get('time_begin')==0 and not t.get('branch')]
  has_children=bool(self.active.get('child_owners')) or any(x.get('kind')=='reference' for x in self.active.get('other_direct_children',[]))
  if not has_animation and immediate and has_children:
   candidates=[(t.get('priority',0),t.get('offset',0),self._track_transition_candidate(t,'Sequence')) for t in immediate]
   candidates=[x for x in candidates if x[2] is not None];c=min(candidates,key=lambda x:(x[0],x[1]))[2] if candidates else None
   self.trace.append({'type':'sequenceDelegate','node':self.active['offset'],'candidate':c.leaf_offset if c else None,
    'evidence':'inferred immediate delegation for animation-less sequence wrapper; local conditions/branch decoded'})
   if c is not None:self._activate(c,'animation-less sequence wrapper')

 def dispatch_opportunity_phase(self,phase:int,dt:float=0.0,allow_external:bool=True):
  """Drive OpportunityAction from an explicit host phase (0 or 1).

  Alex's decoded PrototypeTemplate binds //prototype to MotionTreeBehaviour,
  whose recovered mode 2 registers phase 1/Threat. The integrated controller
  may withhold cross-package branches until shared MotionTree branch ownership
  replaces its current split locomotion/fight executors.
  """
  if phase not in (0,1):raise ValueError('opportunity phase must be 0 or 1')
  if dt<0:raise ValueError('opportunity dt must be nonnegative')
  if not self.active:return False
  event_name='Patsy' if phase==0 else 'Threat';event_mask=0x800<<phase
  registrations=[(r.owner['offset'],r.opportunities) for r in list(self.retained)]+[(self.active['offset'],self.opportunities)]
  for registration_owner,opportunities in registrations:
   for t in list(opportunities):
    if t.get('fired') or t.get('registration_phase')!=phase:continue
    elapsed=t.get('phase_elapsed',0.0);begin=t.get('time_begin',0.0)
    if t.get('branch') and not allow_external:
     elapsed+=dt;t['phase_elapsed']=elapsed
     if not t.get('_phase_deferred'):
      t['_phase_deferred']=True
      self.trace.append({'type':'opportunityDeferred','track_offset':t.get('offset'),'time':elapsed,
       'registration_owner':registration_owner,'branch':t.get('branch'),'phase':phase,'event':event_name,'event_mask':event_mask,
       'evidence':'decoded MotionTree phase callback; external branch withheld because split simulator scopes cannot preserve native shared branch ownership'})
     continue
    cached=t.get('_phase_candidate')
    if cached is None and (begin<0 or elapsed+1e-7>=begin):
     cached=self._track_transition_candidate(t,'Opportunity')
     if cached is not None:t['_phase_candidate']=cached
    elapsed+=dt;t['phase_elapsed']=elapsed
    hook=t.get('time_hook',0.0)
    self.trace.append({'type':'opportunityPhase','track_offset':t.get('offset'),'registration_owner':registration_owner,
     'phase':phase,'event':event_name,'event_mask':event_mask,'elapsed':elapsed,
     'cached_candidate':cached.leaf_offset if cached else None,
     'evidence':'decoded OpportunityAction host-phase callback and default 0x800<<phase registration'})
    if cached is not None and hook>=0 and elapsed+1e-7>=hook:
     t['fired']=True;self._activate(cached,f'opportunity host phase {phase} track {t["offset"]}');return True
  return False

 def _fire_due_opportunities(self,old:float,new:float):
  # Legacy branchless approximation remains for split-scope immediate control
  # changes; controller-driven phase callbacks are authoritative during ticks.
  for t in list(self.opportunities):
   begin=t.get('time_begin',0.0);hook=t.get('time_hook',begin)
   trigger=max(begin,hook) if hook>=0 else begin
   if not t.get('fired') and not t.get('auto_deferred') and old<trigger<=new:
    if t.get('branch'):
     if t.get('registration_phase')==1:continue
     t['auto_deferred']=True
     self.trace.append({'type':'opportunityDeferred','track_offset':t['offset'],'time':trigger,'branch':t.get('branch'),
      'evidence':'decoded external branch; automatic activation withheld because the Alex owning host pipeline remains unresolved'})
     continue
    t['fired']=True;c=self._track_transition_candidate(t,'Opportunity')
    self.trace.append({'type':'opportunity','track_offset':t['offset'],'time':trigger,
      'candidate':c.leaf_offset if c else None,'evidence':'legacy branchless simulator probe; exact host-phase API is available but the Alex owner pipeline remains unresolved'})
    if c is not None:self._activate(c,f'opportunity {t["offset"]}');return

 def _apply_activations(self,acts,cause):
  if not acts:return False
  valid=[]
  for a in acts:
   c=a.candidate
   if c.scope_index>=0 and c.scope_index<len(self.scopes) and self.scopes[c.scope_index].cursor==c.direct_offset:
    self.trace.append({'type':'suppressedActivation','candidate':c.leaf_offset,
      'cause':'receiver current-candidate rejection','evidence':'decoded receiver equality gate'})
   else:valid.append(a)
  if not valid:return False
  unique={(a.candidate.leaf_offset,a.candidate.scope_index,a.candidate.branch_path):a for a in valid}
  chosen=min(unique.values(),key=lambda a:a.priority)
  if len(unique)>1:self.trace.append({'type':'activationArbitration','candidates':[a.candidate.leaf_offset for a in unique.values()],
    'chosen':chosen.candidate.leaf_offset,'evidence':'inferred lower-priority transition arbitration; lower-numeric override path is decoded'})
  self._activate(chosen.candidate,cause)
  return True

 def _redeliver_pending(self):
  if self.pending_event is None or not self.active:return
  bits,context=self.pending_event
  self.dispatch(bits,context)

 def dispatch(self,event_bits:int,context:object=None):
  if not self.active:return []
  self.pending_event=(event_bits,context)
  active_scopes=[r.events for r in self.retained]+[self.events]
  before=[(w.candidate,w.initial_tested) for scope in active_scopes for w in scope.tracks]
  def resolver(config,bits,ctx):
   truth,condition_trace=owner_condition_truth({'condition_groups':config.condition_groups},self.bb)
   if truth is not Truth.TRUE:
    self.trace.append({'type':'blockedOnEvent','track_offset':config.track_offset,'truth':truth.name,'conditions':_trace_json(condition_trace),
     'evidence':'decoded onEvent-local conditions; FALSE/UNKNOWN cannot cache a candidate'})
    return None
   if config.branch:
    resolved=self.branch_resolver(config.branch) if self.branch_resolver else None
    if resolved is None:
     self.trace.append({'type':'blockedOnEvent','track_offset':config.track_offset,'truth':'UNKNOWN','branch':config.branch,
      'evidence':'decoded external branch path could not be resolved'})
     return None
    owner,branch_resolve=resolved;s=select_scope(owner,self.bb,branch_resolve,resolve_branch=self.branch_resolver)
    if s.selected_offset is None:
     truth='UNKNOWN' if s.status=='blocked-by-unknown' else 'FALSE'
     self.trace.append({'type':'blockedOnEvent','track_offset':config.track_offset,'truth':truth,'branch':config.branch,
      'selection_status':s.status,'evidence':'decoded external branch resolved; no condition-eligible candidate selected'})
     return None
    if s.external_branch:
     return Candidate(s.selected_offset,s.selected_offset,s.status,-1,s.external_root,s.selected_resolver,s.external_branch,config.branch)
    return Candidate(s.selected_offset,s.selected_offset,s.status,-1,owner,branch_resolve,config.branch,config.branch)
   return self._candidate()
  acts=[]
  for scope in active_scopes:acts.extend(scope.dispatch(event_bits,context,resolver))
  after=[(w.candidate,w.initial_tested) for scope in active_scopes for w in scope.tracks]
  accepted=any(a!=b for a,b in zip(before,after))
  states=set(context.get('states',[])) if isinstance(context,dict) else set()
  forward_terminal=bool(states&{'Up','Released'} and not context.get('_forwarded',False)) if isinstance(context,dict) else False
  if accepted:
   if forward_terminal:
    forwarded=dict(context);forwarded['_forwarded']=True;self.pending_event=(event_bits,forwarded)
   else:self.pending_event=None
  self._apply_activations(acts,f'event bits {event_bits}')
  return acts

 def active_tracks(self,kind:Optional[str]=None):
  """Return currently active decoded tracks without inferring a visual blend."""
  sources=[(r.owner,r.schedule) for r in self.retained]
  if self.active is not None and self.schedule is not None:sources.append((self.active,self.schedule))
  rows=[]
  for owner,schedule in sources:
   tracks={t.get('offset'):t for t in owner.get('tracks',[])}
   for offset in sorted(schedule.active_offsets):
    track=tracks.get(offset)
    if track is None or (kind is not None and track.get('kind')!=kind):continue
    rows.append({'owner_offset':owner['offset'],'owner_time':schedule.time,'track':track})
  return rows

 def set_input(self,button:str,states:set[str],seconds:float=0.0):
  self.bb.input_states[button]=set(states);self.bb.input_seconds[button]=seconds
  return self.dispatch(1,{'button':button,'states':sorted(states)})

 def interrupt(self,cause='external interruption'):
  if not self.active:return []
  teardown_acts=self.events.teardown() if self.events is not None else []
  for a in teardown_acts:self.trace.append({'type':'teardownActivation','candidate':a.candidate.leaf_offset,
    'priority':a.priority,'evidence':'decoded negative-hook teardown dispatch; suppressed by external interruption'})
  self._teardown_schedule(self.active,self.schedule,self.node_time,'inferred callback/removal on external owner teardown')
  for scope_offset in {r.scope_offset for r in self.retained}:self._drop_retained_scope(scope_offset,cause)
  off=self.active['offset'];self.trace.append({'type':'interruption','offset':off,'cause':cause})
  self.active=None;self.schedule=None;self.events=None;self.scopes=[];self.pending_event=None
  return teardown_acts

 def _drop_retained_scope(self,scope_offset,cause):
  keep=[]
  for r in self.retained:
   if r.scope_offset!=scope_offset:keep.append(r);continue
   for a in r.events.teardown():self.trace.append({'type':'teardownActivation','candidate':a.candidate.leaf_offset,
     'priority':a.priority,'evidence':'decoded retained-owner negative-hook teardown dispatch'})
   self._teardown_schedule(r.owner,r.schedule,r.schedule.time,'owner-scope teardown')
   self.trace.append({'type':'releaseRetainedOwner','offset':r.owner['offset'],'cause':cause})
  self.retained=keep

 def _finish_node(self):
  # SequenceAction resolves its own local conditions/branch on teardown. A
  # failed sequence must not fall through into that same owner's children.
  sequences=[t for t in self.active.get('tracks',[]) if t['kind']=='sequence']
  if sequences:
   candidates=[]
   for t in sequences:
    c=self._track_transition_candidate(t,'Sequence')
    if c is not None:candidates.append((t.get('priority',0),t.get('offset',0),c))
   if candidates:
    _,off,c=min(candidates,key=lambda x:(x[0],x[1]));self._activate(c,f'sequence teardown {off}');return
   if self.scopes and self.scopes[-1].owner['offset']==self.active['offset']:
    fr=self.scopes.pop();self._drop_retained_scope(fr.owner['offset'],'sequence produced no candidate')
  # Otherwise advance outward scopes after their active direct child.
  while self.scopes:
   c=self._candidate()
   if c is not None:self._activate(c,'owner expiry progression');return
   fr=self.scopes.pop();self._drop_retained_scope(fr.owner['offset'],'scope exhausted')
  self._teardown_schedule(self.active,self.schedule,self.node_time,'owner completion')
  self.trace.append({'type':'complete','offset':self.active['offset']});self.active=None;self.schedule=None;self.events=None

 def _next_boundary(self,limit):
  bounds=[limit]
  if self.node_expiry is not None:bounds.append(max(0.0,self.node_expiry-self.node_time))
  # Split on track boundaries so active ChargeAction intervals accrue only
  # while their decoded owner-local track is active.
  if self.schedule is not None:
   bounds.extend(max(0.0,e.time-self.node_time) for e in self.schedule.events if e.time>self.node_time+1e-12)
  
  for t in self.opportunities:
   if not t.get('fired') and not t.get('auto_deferred'):
    begin=t.get('time_begin',0.0);hook=t.get('time_hook',begin);trigger=max(begin,hook) if hook>=0 else begin
    bounds.append(max(0.0,trigger-self.node_time))
  for w in self.events.tracks:
   if w.alive and w.candidate is not None and w.config.hook_offset>=0:
    bounds.append(max(0.0,w.config.hook_offset-w.elapsed))
  return min(bounds)

 def _advance_charge(self,schedule,dt,node):
  saturated=[]
  if dt<=0:return saturated
  by_offset={t.get('offset'):t for t in schedule.owner.get('tracks',[])}
  # 0x100A13B0 computes one rate when the action starts; 0x100A1540 then
  # emits rate*dt. Keep that rate per source track rather than recomputing it.
  rates=getattr(schedule,'charge_rates',None)
  if rates is None:rates={};schedule.charge_rates=rates
  for offset in tuple(schedule.active_offsets):
   track=by_offset.get(offset,{})
   if track.get('kind')!='charge':continue
   channel=track.get('charge_channel','Attack');target=track.get('target',1.0)
   if target is None:continue
   before=self.bb.charge_seconds.get(channel,0.0)
   if offset not in rates:
    duration=track.get('time_end',-1.0)-track.get('time_begin',0.0)
    # Exact callback branch: nonpositive duration keeps target as the rate;
    # otherwise sub-unit targets approach from the clamped starting value,
    # while targets >= 1 use target/duration directly.
    rates[offset]=float(target) if duration<=0 else ((float(target)-max(0.0,min(1.0,before)))/duration if target<1.0 else float(target)/duration)
   after=max(0.0,min(1.0,before+rates[offset]*dt))
   self.bb.charge_seconds[channel]=after
   if after!=before:self.trace.append({'type':'chargeMutation','node':node,'track_offset':offset,'channel':channel,
    'before':before,'after':after,'rate':rates[offset],'phase':'advance',
    'evidence':'recovered ChargeAction start/update formula; source interval used for descriptor duration'})
   if target>=1.0 and before<float(target)<=after:saturated.append({'track_offset':offset,'channel':channel,'target':float(target)})
  return saturated

 def _advance_retained(self,dt):
  for r in list(self.retained):
   self._advance_charge(r.schedule,dt,r.owner['offset'])
   self._record_tracks(r.schedule.tick(dt),r.owner['offset']);self._record_condition_decisions(r.schedule,r.owner['offset'])
   acts=r.events.advance(dt)
   if acts and self._apply_activations(acts,'retained-owner buffered onEvent hook'):
    return True
  return False

 def _advance_step(self,dt):
  old_offset=self.active['offset']
  if self._advance_retained(dt):return []
  if self.active is None or self.active['offset']!=old_offset:return []
  old_time=self.node_time
  saturated=self._advance_charge(self.schedule,dt,self.active['offset'])
  self.node_time+=dt;self._record_tracks(self.schedule.advance_to(self.node_time));self._record_condition_decisions(self.schedule,self.active['offset'])
  self._fire_due_opportunities(old_time,self.node_time)
  if self.active is None or self.active['offset']!=old_offset:return []
  acts=self.events.advance(dt);self._apply_activations(acts,'buffered onEvent hook')
  if self.active is None or self.active['offset']!=old_offset:return acts
  if saturated:
   candidate=self._candidate()
   if candidate is not None:
    self.trace.append({'type':'chargeTargetReached','node':old_offset,'tracks':saturated,'candidate':candidate.leaf_offset,
     'evidence':'decoded ChargeAction target reached; existing structural candidate re-evaluated without fabricating input release'})
    self._activate(candidate,'ChargeAction target reached');return acts
  if self.node_expiry is not None and self.node_time+1e-7>=self.node_expiry:self._finish_node()
  return acts

 def tick(self,dt:float,advance_inputs=True):
  if dt<0:raise ValueError('dt must be nonnegative')
  if not self.active:return []
  if advance_inputs:
   for button,states in self.bb.input_states.items():
    if 'Down' in states:
     self.bb.input_seconds[button]=self.bb.input_seconds.get(button,0.0)+dt
  remaining=dt;all_acts=[]
  # 0x10A7FC20 stores start overshoot; split at transition boundaries so the
  # remainder advances the newly activated owner instead of being discarded.
  for _ in range(64):
   if not self.active or remaining<-1e-9:break
   step=self._next_boundary(max(0.0,remaining))
   before=(self.active['offset'],self.node_time)
   acts=self._advance_step(step);all_acts.extend(acts);remaining-=step
   after=(self.active['offset'],self.node_time) if self.active else None
   if remaining<=1e-9:break
   if step<=1e-12 and after==before:
    # No transition consumed an exact-zero boundary; advance the remainder.
    self._advance_step(remaining);remaining=0;break
  return all_acts
