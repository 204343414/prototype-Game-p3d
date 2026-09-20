"""Evidence-bounded fight onEvent lifecycle model.

Implements only behavior directly visible in prototypeenginef.dll
0x10A82460, 0x10A825B0, 0x10A82690 and 0x10A82790. Candidate/branch
resolution is injected; event-name production remains unresolved.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Generic,Optional,TypeVar
T=TypeVar('T')
HOOK_THRESHOLD=0.0 # exact float at 0x10D93F78

@dataclass(frozen=True)
class OnEventConfig:
 time_begin:float;time_end:float;time_hook:float;event_mask:int
 initial_test:bool;branch:str;priority:int
 condition_groups:object=();track_offset:int=-1
 @property
 def duration(self)->float:return self.time_end-self.time_begin
 @property
 def hook_offset(self)->float:
  d=self.time_hook-self.time_begin
  return 0.0 if d<0.0 and self.time_hook>=0.0 else d

@dataclass
class OnEventWindow(Generic[T]):
 config:OnEventConfig
 elapsed:float=0.0
 candidate:Optional[T]=None
 alive:bool=True
 activated:bool=False
 initial_tested:bool=False

 def receive(self,event_bits:int,resolved:Optional[T])->Optional[T]:
  """Mirror the mask test, single-candidate cache, and positive-hook dispatch."""
  if not self.alive or self.candidate is not None:return None
  if not (self.config.event_mask & event_bits):return None
  # 0x10A825DF sets the latch before branch/condition/candidate resolution.
  self.initial_tested=True
  if resolved is None:return None
  self.candidate=resolved
  if self.config.hook_offset>=HOOK_THRESHOLD and self.elapsed>=self.config.hook_offset:
   self.activated=True;self.alive=False
   return resolved
  return None

 def tick(self,dt:float)->Optional[T]:
  if dt<0:raise ValueError('dt must be nonnegative')
  if not self.alive:return None
  self.elapsed+=dt
  hook=self.config.hook_offset
  if self.candidate is not None:
   if hook>=HOOK_THRESHOLD and self.elapsed>=hook:
    self.activated=True;self.alive=False
    return self.candidate
   # A cached negative-hook candidate remains alive; its dispatch belongs to
   # teardown(). (A zero-hook candidate dispatches immediately.)
   return None
  if self.config.duration>=0.0 and self.elapsed>self.config.duration:
   self.alive=False
  return None

 def phase_tick(self,phase:int,dt:float,resolved:Optional[T]=None)->Optional[T]:
  """Mirror the high-category initial test used by 0x10A7F7B0/0x10A82690.

  ``phase`` 0..10 maps to event bits 11..21. The caller supplies the candidate
  resolved for that category/current context; resolution semantics stay outside
  this evidence-bounded window.
  """
  if not 0<=phase<=10:raise ValueError('phase must be in 0..10')
  if self.config.initial_test and not self.initial_tested:
   fired=self.receive(1<<(11+phase),resolved)
   if fired is not None:return fired
  return self.tick(dt)

 def teardown(self)->Optional[T]:
  """Mirror 0x10A82790: only a strictly negative hook dispatches on teardown."""
  self.alive=False
  if self.candidate is not None and self.config.hook_offset<0.0 and not self.activated:
   self.activated=True
   return self.candidate
  return None

# Compatibility name retained for the first positive-hook regression clients.
PositiveHookOnEvent=OnEventWindow
