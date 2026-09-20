#!/usr/bin/env python3
import argparse,csv,json,os,wave
from collections import defaultdict
from pathlib import Path

def bucket():return {'objects':0,'duration_seconds':0.0,'output_bytes':0}
def add(d,k,r,size):
 x=d[k];x['objects']+=1;x['duration_seconds']+=r.get('duration_seconds',0);x['output_bytes']+=size
def main():
 p=argparse.ArgumentParser();p.add_argument('root');a=p.parse_args();root=Path(a.root);rows=[json.loads(x) for x in (root/'manifest.jsonl').read_text().splitlines() if x.strip()];groups={x:defaultdict(bucket) for x in ('class','bank','path_prefix','channels','sample_rate')};total=bucket();invalid=[]
 for r in rows:
  out=root/r.get('output','');size=out.stat().st_size if out.is_file() else 0;src=r.get('source_entry','').replace('\\','/');parts=src.split('/');low=src.lower()
  if '/english/audiofile/' in low:cls='dialogue_or_voice_loose_audiofile'
  elif 'patch' in low:cls='patch_member'
  elif len(parts)<=2:cls='sound_effect_bank'
  else:cls='other_embedded_audiofile'
  bank=parts[1] if len(parts)>1 else '(unknown)';prefix='/'.join(parts[:min(5,len(parts))]);
  for typ,key in [('class',cls),('bank',bank),('path_prefix',prefix),('channels',str(r.get('channels'))),('sample_rate',str(r.get('rate')))]:add(groups[typ],key,r,size)
  add(defaultdict(lambda:total),'x',r,size)
  if r.get('status')!='ok' or not size:invalid.append({'record':r,'exists':out.is_file(),'bytes':size})
 summary={'overall':total,'manifest_records':len(rows),'missing_or_failed':invalid,'groups':{typ:dict(sorted(vals.items())) for typ,vals in groups.items()}}
 (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
 with (root/'summary.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['group_type','group','objects','duration_seconds','output_bytes'])
  for typ,vals in groups.items():
   for key,x in sorted(vals.items()):w.writerow([typ,key,x['objects'],f"{x['duration_seconds']:.6f}",x['output_bytes']])
 print(json.dumps(summary['overall'],indent=2));print('missing_or_failed',len(invalid))
if __name__=='__main__':main()
