#!/usr/bin/env python3
"""Extract every RADP AudioFile object from Prototype's 00audio.rcf to WAV.
Preserves archive provenance and writes a resumable JSONL manifest.
"""
from __future__ import annotations
import argparse, json, os, re, struct, sys, wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rcf_unpack'))
import rcf_extract
MARK=b'AudioFile\x00\x02\x00\x00\x00'
STEP=[7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767]
INDEX=[-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8]
def safe(s):
 s=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',s).strip(' .');return s or '_'
def parse_audiofiles(data):
 pos=0
 while True:
  at=data.find(MARK,pos)
  if at<0:return
  try:
   p=at+len(MARK);ln=struct.unpack_from('<I',data,p)[0];p+=4
   if not 0<ln<512 or p+ln+1>len(data):raise ValueError('name')
   name=data[p:p+ln].decode('utf-8','replace');p+=ln+1
   ln2=struct.unpack_from('<I',data,p)[0];p+=4
   if ln2>4096 or p+ln2+1>len(data):raise ValueError('secondary')
   secondary=data[p:p+ln2].decode('utf-8','replace');p+=ln2+1
   reserved=struct.unpack_from('<I',data,p)[0];p+=4
   ln3=struct.unpack_from('<I',data,p)[0];p+=4
   if not 0<ln3<64 or p+ln3+1>len(data):raise ValueError('codec')
   codec=data[p:p+ln3].decode('ascii','replace');p+=ln3+1
   if codec!='radp' or data[p:p+4]!=b'RADP':
    yield {'name':name,'secondary':secondary,'codec':codec,'reserved':reserved,'error':'unsupported codec or missing RADP'};pos=at+len(MARK);continue
   channels,rate,unknown,data_size=struct.unpack_from('<IIII',data,p+4);start=p+20;end=start+data_size
   if not 1<=channels<=32 or not 1000<=rate<=384000 or end>len(data) or data_size%20:
    raise ValueError(f'RADP header channels={channels} rate={rate} size={data_size}')
   yield {'name':name,'secondary':secondary,'codec':codec,'reserved':reserved,'channels':channels,'rate':rate,'unknown':unknown,'payload':memoryview(data)[start:end]}
   pos=end
  except (ValueError,struct.error,UnicodeDecodeError) as e:
   yield {'name':f'object_{at:x}','codec':'unknown','error':str(e)};pos=at+len(MARK)
def decode_block(block):
 idx=struct.unpack_from('<h',block,0)[0];hist=struct.unpack_from('<h',block,2)[0];idx=max(0,min(88,idx));out=[]
 for i in range(32):
  b=block[4+i//2];n=(b>>(4 if i&1 else 0))&15;step=STEP[idx];delta=step>>3
  if n&1:delta+=step>>2
  if n&2:delta+=step>>1
  if n&4:delta+=step
  if n&8:delta=-delta
  hist=max(-32768,min(32767,hist+delta));idx=max(0,min(88,idx+INDEX[n]));out.append(hist)
 return out
def write_wav(path,payload,channels,rate):
 path.parent.mkdir(parents=True,exist_ok=True);blocks=len(payload)//20
 if blocks%channels:raise ValueError('RADP block count is not divisible by channels')
 with wave.open(str(path),'wb') as w:
  w.setnchannels(channels);w.setsampwidth(2);w.setframerate(rate)
  for base in range(0,blocks,channels):
   decoded=[decode_block(payload[(base+c)*20:(base+c+1)*20]) for c in range(channels)]
   pcm=[]
   for frame in range(32):
    for c in range(channels):pcm.append(decoded[c][frame])
   w.writeframesraw(struct.pack('<%dh'%len(pcm),*pcm))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('rcf');ap.add_argument('output');ap.add_argument('--scan-only',action='store_true');a=ap.parse_args()
 out=Path(a.output);out.mkdir(parents=True,exist_ok=True);manifest=out/'manifest.jsonl';cem=rcf_extract.CementFile.load(a.rcf)
 entries={e.name_hash:e for e in cem.entries};done=set()
 if manifest.exists():
  for line in manifest.read_text(errors='ignore').splitlines():
   try:
    r=json.loads(line)
    if r.get('status')=='ok':done.add((r['source_entry'],r['audio_name']))
   except:pass
 total=ok=errors=0
 mode='a' if manifest.exists() else 'w'
 with open(a.rcf,'rb') as fh,manifest.open(mode,encoding='utf-8') as mf:
  for mi,meta in enumerate(cem.metadatas,1):
   entry=entries.get(rcf_extract.hash_file_name(meta.name))
   if entry is None:continue
   fh.seek(entry.offset);data=fh.read(entry.size)
   objects=list(parse_audiofiles(data))
   for oi,obj in enumerate(objects):
    total+=1;name=obj['name'];key=(meta.name,name)
    relparts=[safe(x) for x in meta.name.replace('audio\\','',1).split('\\')]
    if relparts[-1].lower().endswith('.p3d'):relparts[-1]=relparts[-1][:-4]
    rel=Path(*relparts)/(safe(name)+'.wav') if len(objects)>1 else Path(*relparts).with_name(relparts[-1]+'__'+safe(name)+'.wav')
    rec={k:v for k,v in obj.items() if k!='payload'};rec.update(source_entry=meta.name,audio_name=name,output=str(rel))
    if obj.get('error'):
     rec['status']='error';errors+=1
    elif key in done and (out/rel).exists():
     continue
    elif a.scan_only:
     rec['status']='scanned';rec['duration_seconds']=(len(obj['payload'])//20*32)/(obj['rate']*obj['channels'])
    else:
     try:
      write_wav(out/rel,obj['payload'],obj['channels'],obj['rate']);rec['status']='ok';rec['duration_seconds']=(len(obj['payload'])//20*32)/(obj['rate']*obj['channels']);ok+=1
     except Exception as e:rec['status']='error';rec['error']=str(e);errors+=1
    mf.write(json.dumps(rec,ensure_ascii=False)+'\n');mf.flush()
   if mi%100==0:print(json.dumps({'entries':mi,'of':len(cem.metadatas),'objects':total,'written':ok,'errors':errors}),flush=True)
 print(json.dumps({'entries':len(cem.metadatas),'objects':total,'written':ok,'errors':errors,'scan_only':a.scan_only}),flush=True)
if __name__=='__main__':main()
