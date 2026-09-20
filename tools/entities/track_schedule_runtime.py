"""Lossless per-owner track-time scheduler.

This module schedules decoded co-owned records; it does NOT select owners or
invent transition targets. Serialized times are FACT. Animation segment end
is DERIVED from source frame bounds/fps. Unknown records remain in the owner IR
and are reported as unscheduled rather than discarded.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any,Iterable
from arbitration_runtime import owner_condition_truth
from condition_runtime import Truth

ONE_SHOT={'sound','hit','cameraShakeRequest'}
RANGED={'animation','spawn','execute'}

@dataclass(frozen=True)
class TrackEvent:
 time:float
 phase:str
 kind:str
 offset:int
 track:dict[str,Any]
 evidence:str

class OwnerTrackSchedule:
 def __init__(self,owner:dict[str,Any],bb=None):
  self.owner=owner;self.bb=bb;self.time=-1e-12;self.events:list[TrackEvent]=[];self.unscheduled=[]
  self.active_offsets=set();self.condition_decisions=[];self.reported_decisions=0
  for t in owner.get('tracks',[]):self._compile(t)
  self.events.sort(key=lambda e:(e.time,e.offset,0 if e.phase=='start' else 1))

 def _add(self,time,phase,t,evidence='decoded'):
  if time>=0:self.events.append(TrackEvent(float(time),phase,t['kind'],t['offset'],t,evidence))

 def _compile(self,t):
  k=t.get('kind')
  if k in ONE_SHOT and 'time_begin' in t:self._add(t['time_begin'],'fire',t);return
  if k in RANGED and 'time_begin' in t:
   self._add(t['time_begin'],'start',t)
   if k=='animation':
    f0=t.get('start_frame');f1=t.get('end_frame');fps=t.get('fps');total=t.get('total_frames')
    if f1 is not None and f1<0:f1=total
    if None not in (f0,f1,fps) and fps>0 and f1>=f0:
     self._add(t['time_begin']+(f1-f0)/fps,'end',t,'derived frame bounds/fps')
   elif t.get('time_end',-1)>=0:self._add(t['time_end'],'end',t)
   return
  # Sequence/opportunity/onEvent are handled by their dedicated runtimes.
  if k in ('sequence','opportunity','onEvent'):return
  if 'time_begin' in t:
   evidence=t.get('timing_evidence','decoded')
   self._add(t['time_begin'],'start',t,evidence)
   if t.get('time_end',-1)>=0:self._add(t['time_end'],'end',t,evidence)
   return
  self.unscheduled.append(t)

 def advance_to(self,new_time:float)->list[TrackEvent]:
  if new_time<self.time:raise ValueError('schedule time cannot move backwards')
  due=[e for e in self.events if self.time<e.time<=new_time];out=[]
  for e in due:
   if e.phase in ('start','fire'):
    truth,trace=(Truth.TRUE,[]) if self.bb is None else owner_condition_truth(e.track,self.bb)
    self.condition_decisions.append({'track_offset':e.offset,'phase':e.phase,'time':e.time,'truth':truth.name,'trace':trace})
    if truth is not Truth.TRUE:continue
    out.append(e)
    if e.phase=='start':self.active_offsets.add(e.offset)
   elif e.phase=='end':
    if e.offset in self.active_offsets:
     out.append(e);self.active_offsets.remove(e.offset)
   else:out.append(e)
  self.time=float(new_time);return out

 def tick(self,dt:float)->list[TrackEvent]:
  if dt<0:raise ValueError('dt must be nonnegative')
  return self.advance_to(self.time+dt)

 def start(self)->list[TrackEvent]:return self.advance_to(0.0)
