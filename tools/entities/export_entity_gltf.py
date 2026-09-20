#!/usr/bin/env python3
"""Export decoded Prototype entity API data as glTF 2.0 + BIN + PNG textures."""
from __future__ import annotations
import argparse,base64,copy,json,math,re,struct,urllib.parse,urllib.request,zlib
from pathlib import Path

def safe(s):return re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('._') or 'asset'
def png_chunk(tag,p):
 c=tag+p;return struct.pack('>I',len(p))+c+struct.pack('>I',zlib.crc32(c)&0xffffffff)
def dxt_png(fmt,w,h,data):
 rows=[bytearray(w*4) for _ in range(h)];p=0
 def rgb(c):return ((c>>11&31)*255//31,(c>>5&63)*255//63,(c&31)*255//31)
 for by in range(0,h,4):
  for bx in range(0,w,4):
   if fmt=='DXT5':a0,a1=data[p],data[p+1];ab=int.from_bytes(data[p+2:p+8],'little');c0,c1=struct.unpack_from('<HH',data,p+8);bits=struct.unpack_from('<I',data,p+12)[0];p+=16
   elif fmt=='DXT3':ab=int.from_bytes(data[p:p+8],'little');c0,c1=struct.unpack_from('<HH',data,p+8);bits=struct.unpack_from('<I',data,p+12)[0];p+=16;a0=a1=0
   elif fmt=='DXT1':c0,c1=struct.unpack_from('<HH',data,p);bits=struct.unpack_from('<I',data,p+4)[0];p+=8;ab=0;a0=a1=0
   else:raise ValueError('unsupported '+fmt)
   x0=rgb(c0);x1=rgb(c1)
   if fmt=='DXT1' and c0<=c1:pal=[x0,x1,tuple((x0[i]+x1[i])//2 for i in range(3)),(0,0,0)]
   else:pal=[x0,x1,tuple((2*x0[i]+x1[i])//3 for i in range(3)),tuple((x0[i]+2*x1[i])//3 for i in range(3))]
   for y in range(4):
    for x in range(4):
     ci=(bits>>(2*(y*4+x)))&3
     if fmt=='DXT5':
      ai=(ab>>(3*(y*4+x)))&7
      if a0>a1:alpha=[a0,a1,(6*a0+a1)//7,(5*a0+2*a1)//7,(4*a0+3*a1)//7,(3*a0+4*a1)//7,(2*a0+5*a1)//7,(a0+6*a1)//7][ai]
      else:alpha=[a0,a1,(4*a0+a1)//5,(3*a0+2*a1)//5,(2*a0+3*a1)//5,(a0+4*a1)//5,0,255][ai]
     elif fmt=='DXT3':alpha=((ab>>(4*(y*4+x)))&15)*17
     else:alpha=0 if c0<=c1 and ci==3 else 255
     xx,yy=bx+x,by+y
     if xx<w and yy<h:rows[yy][xx*4:xx*4+4]=bytes((*pal[ci],alpha))
 raw=b''.join(b'\0'+r for r in rows)
 return b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+png_chunk(b'IDAT',zlib.compress(raw))+png_chunk(b'IEND',b'')
def mm(a,b):return [sum(a[r+k*4]*b[k+c*4] for k in range(4)) for c in range(4) for r in range(4)]
def decompose_matrix(m):
 t=[m[12],m[13],m[14]];s=[math.sqrt(sum(m[r+c*4]**2 for r in range(3))) for c in range(3)]
 det=(m[0]*(m[5]*m[10]-m[9]*m[6])-m[4]*(m[1]*m[10]-m[9]*m[2])+m[8]*(m[1]*m[6]-m[5]*m[2]))
 if det<0:s[0]=-s[0]
 r=[0.0]*16
 for c in range(3):
  if abs(s[c])<1e-12:raise ValueError('zero-scale joint matrix')
  for row in range(3):r[row+c*4]=m[row+c*4]/s[c]
 r[15]=1.0;tr=r[0]+r[5]+r[10]
 if tr>0:qv=math.sqrt(tr+1)*2;q=[(r[6]-r[9])/qv,(r[8]-r[2])/qv,(r[1]-r[4])/qv,.25*qv]
 elif r[0]>r[5] and r[0]>r[10]:qv=math.sqrt(1+r[0]-r[5]-r[10])*2;q=[.25*qv,(r[4]+r[1])/qv,(r[8]+r[2])/qv,(r[6]-r[9])/qv]
 elif r[5]>r[10]:qv=math.sqrt(1+r[5]-r[0]-r[10])*2;q=[(r[4]+r[1])/qv,.25*qv,(r[9]+r[6])/qv,(r[8]-r[2])/qv]
 else:qv=math.sqrt(1+r[10]-r[0]-r[5])*2;q=[(r[8]+r[2])/qv,(r[9]+r[6])/qv,.25*qv,(r[1]-r[4])/qv]
 ql=math.sqrt(sum(x*x for x in q));return t,[x/ql for x in q],s
def inv(a):
 z=[[a[r+c*4] for c in range(4)]+[1.0 if r==c else 0.0 for c in range(4)] for r in range(4)]
 for c in range(4):
  p=max(range(c,4),key=lambda r:abs(z[r][c]));z[c],z[p]=z[p],z[c];q=z[c][c]
  if abs(q)<1e-12:raise ValueError('singular bind matrix')
  z[c]=[v/q for v in z[c]]
  for r in range(4):
   if r!=c:q=z[r][c];z[r]=[z[r][k]-q*z[c][k] for k in range(8)]
 return [z[r][4+c] for c in range(4) for r in range(4)]
def write_glb(gltf, binary, path, resource_dir):
 g=copy.deepcopy(gltf);blob=bytearray(binary)
 for image in g.get('images',[]):
  uri=image.pop('uri',None)
  if not uri:continue
  while len(blob)%4:blob.append(0)
  payload=(resource_dir/uri).read_bytes();g['bufferViews'].append({'buffer':0,'byteOffset':len(blob),'byteLength':len(payload)});image['bufferView']=len(g['bufferViews'])-1;image['mimeType']='image/png';blob.extend(payload)
 while len(blob)%4:blob.append(0)
 g['buffers'][0].pop('uri',None);g['buffers'][0]['byteLength']=len(blob);j=json.dumps(g,ensure_ascii=False,separators=(',',':')).encode();j+=b' '*((-len(j))%4);total=12+8+len(j)+8+len(blob);path.write_bytes(struct.pack('<4sII',b'glTF',2,total)+struct.pack('<I4s',len(j),b'JSON')+j+struct.pack('<I4s',len(blob),b'BIN\0')+blob)

class Builder:
 def __init__(self,name):
  self.bin=bytearray();self.g={'asset':{'version':'2.0','generator':'Prototype source decoder glTF exporter'},'scene':0,'scenes':[{'name':name,'nodes':[]}],'nodes':[],'meshes':[],'buffers':[{'uri':safe(name)+'.bin','byteLength':0}],'bufferViews':[],'accessors':[],'materials':[],'images':[],'textures':[],'skins':[],'animations':[]}
 def add(self,data,target=None):
  while len(self.bin)%4:self.bin.append(0)
  off=len(self.bin);self.bin.extend(data);v={'buffer':0,'byteOffset':off,'byteLength':len(data)}
  if target:v['target']=target
  self.g['bufferViews'].append(v);return len(self.g['bufferViews'])-1
 def acc(self,data,ctype,typ,count,target=None,minv=None,maxv=None,normalized=False):
  v=self.add(data,target);a={'bufferView':v,'componentType':ctype,'count':count,'type':typ}
  if minv is not None:a['min']=minv
  if maxv is not None:a['max']=maxv
  if normalized:a['normalized']=True
  self.g['accessors'].append(a);return len(self.g['accessors'])-1
def export(data,out,name):
 out.mkdir(parents=True,exist_ok=True);b=Builder(name);tex_by_key={};img_dir=out/'textures';img_dir.mkdir(exist_ok=True)
 for t in data.get('textures',[]):
  mip=t['mipmaps'][0];raw=base64.b64decode(mip['data']);fn=safe(Path(t['name']).stem)+'.png';(img_dir/fn).write_bytes(dxt_png(t['format'],mip['width'],mip['height'],raw));b.g['images'].append({'name':t['name'],'uri':'textures/'+fn});b.g['textures'].append({'source':len(b.g['images'])-1,'name':t['name']});tex_by_key[t['key']]=len(b.g['textures'])-1
 mat_by_sig={}
 def material(m):
  keys=m.get('material_texture_keys') or {'color':m.get('texture_key')};sig=tuple(sorted(keys.items()))
  if sig in mat_by_sig:return mat_by_sig[sig]
  pbr={'metallicFactor':0.0,'roughnessFactor':0.9};base=keys.get('color');normal=keys.get('normal');spec=keys.get('specular')
  if base in tex_by_key:pbr['baseColorTexture']={'index':tex_by_key[base]}
  x={'name':m.get('shader_name','material'),'pbrMetallicRoughness':pbr,'doubleSided':True,'extras':{'sourceShader':m.get('shader_name'),'sourceTextureChannels':keys}}
  if normal in tex_by_key:x['normalTexture']={'index':tex_by_key[normal]}
  if spec in tex_by_key:x['extras']['sourceSpecularTexture']=tex_by_key[spec]
  b.g['materials'].append(x);mat_by_sig[sig]=len(b.g['materials'])-1;return mat_by_sig[sig]
 joint_nodes={};joint_bind_trs={};skin_by_name={}
 for s in data.get('skeletons',[]):
  ids=[];bind_trs=[]
  for j in s['joints']:
   t,q,scale=decompose_matrix(j['matrix']);node={'name':j['name'],'translation':t,'rotation':q,'scale':scale,'extras':{'sourceJointMatrix':j['matrix']}};b.g['nodes'].append(node);ids.append(len(b.g['nodes'])-1);bind_trs.append((t,q,scale))
  roots=[]
  for i,j in enumerate(s['joints']):
   p=j['parent']
   if 0<=p<len(ids) and p!=i:b.g['nodes'][ids[p]].setdefault('children',[]).append(ids[i])
   else:roots.append(ids[i])
  glob=[]
  for i,j in enumerate(s['joints']):glob.append(mm(glob[j['parent']],j['matrix']) if 0<=j['parent']<i else j['matrix'])
  ibm=b''.join(struct.pack('<16f',*inv(x)) for x in glob);ia=b.acc(ibm,5126,'MAT4',len(glob));b.g['skins'].append({'name':s['name'],'joints':ids,'skeleton':roots[0] if roots else ids[0],'inverseBindMatrices':ia});skin_by_name[s['name']]=len(b.g['skins'])-1;joint_nodes[s['name']]={j['name']:ids[i] for i,j in enumerate(s['joints'])};joint_bind_trs[s['name']]={j['name']:bind_trs[i] for i,j in enumerate(s['joints'])};b.g['scenes'][0]['nodes'].extend(roots)
 for m in data.get('meshes',[]):
  pb=base64.b64decode(m['positions']);vals=struct.unpack('<%df'%(len(pb)//4),pb);mins=[min(vals[i::3]) for i in range(3)];maxs=[max(vals[i::3]) for i in range(3)];attrs={'POSITION':b.acc(pb,5126,'VEC3',len(vals)//3,34962,mins,maxs)}
  ub=base64.b64decode(m.get('uv') or '');
  if ub:attrs['TEXCOORD_0']=b.acc(ub,5126,'VEC2',len(ub)//8,34962)
  jb=base64.b64decode(m.get('skin_indices') or '');wb=base64.b64decode(m.get('skin_weights') or '')
  if jb and wb:attrs['JOINTS_0']=b.acc(jb,5123,'VEC4',len(jb)//8,34962);attrs['WEIGHTS_0']=b.acc(wb,5126,'VEC4',len(wb)//16,34962)
  ib=base64.b64decode(m['indices']);ia=b.acc(ib,5123,'SCALAR',len(ib)//2,34963)
  prim={'attributes':attrs,'indices':ia,'material':material(m),'mode':4};b.g['meshes'].append({'name':m['geometry_name'],'primitives':[prim],'extras':{'sourceSkin':m.get('skin_name'),'sourceShader':m.get('shader_name')}});node={'name':m['geometry_name'],'mesh':len(b.g['meshes'])-1}
  if jb and m.get('skeleton_name') in skin_by_name:node['skin']=skin_by_name[m['skeleton_name']]
  b.g['nodes'].append(node);b.g['scenes'][0]['nodes'].append(len(b.g['nodes'])-1)
 # glTF has no standard "active animation" field.  Put an explicit one-frame
 # source-bind utility clip first so DCC importers expose an unambiguous A/bind
 # pose without altering any source clip.
 if joint_nodes:
  sam=[];chs=[];time_acc=b.acc(struct.pack('<f',0.0),5126,'SCALAR',1,minv=[0.0],maxv=[0.0])
  for skel_name,nodes in joint_nodes.items():
   for joint_name,node_id in nodes.items():
    t,q,scale=joint_bind_trs[skel_name][joint_name]
    for value,path,ncomp in ((t,'translation',3),(q,'rotation',4),(scale,'scale',3)):
     out_acc=b.acc(struct.pack('<%df'%ncomp,*value),5126,'VEC%d'%ncomp,1);sam.append({'input':time_acc,'output':out_acc,'interpolation':'STEP'});chs.append({'sampler':len(sam)-1,'target':{'node':node_id,'path':path}})
  b.g['animations'].append({'name':'000_A_POSE_BIND','samplers':sam,'channels':chs,'extras':{'generatedUtilityClip':True,'source':'exact decomposed source joint bind matrices'}});b.g['asset']['extras']={'defaultPoseAnimation':'000_A_POSE_BIND','defaultPoseIsSourceBind':True}
 omitted_animations=[]
 for a in data.get('animations',[]):
  sam=[];chs=[];group_names={g['name'] for g in a.get('groups',[])};overlaps={name:len(group_names & set(nodes)) for name,nodes in joint_nodes.items()};best=max(overlaps.values(),default=0);eligible={name:nodes for name,nodes in joint_nodes.items() if best and overlaps[name]>=max(1,math.ceil(best*.8))}
  for g in a.get('groups',[]):
   targets=[nodes[g['name']] for nodes in eligible.values() if g['name'] in nodes]
   for kind,path,ncomp in [('pos','translation',3),('rot','rotation',4),('scale','scale',3)]:
    tr=g.get(kind)
    if not tr or not targets:continue
    times=tr['times'];flat=[v for row in tr['values'] for v in row];ti=b.acc(struct.pack('<%df'%len(times),*times),5126,'SCALAR',len(times),minv=[min(times)],maxv=[max(times)]);vo=b.acc(struct.pack('<%df'%len(flat),*flat),5126,'VEC%d'%ncomp,len(tr['values']))
    for target in targets:sam.append({'input':ti,'output':vo,'interpolation':'LINEAR'});chs.append({'sampler':len(sam)-1,'target':{'node':target,'path':path}})
  if chs:b.g['animations'].append({'name':a['name'],'samplers':sam,'channels':chs,'extras':{'sourceCyclic':a.get('cyclic',False),'sourceFrames':a.get('frames'),'sourceFPS':a.get('fps')}})
  else:omitted_animations.append({'name':a.get('name'),'reason':'no decoded skeleton has matching animation group joints'})
 b.g['buffers'][0]['byteLength']=len(b.bin);base=safe(name);b.g['buffers'][0]['uri']=base+'.bin';(out/(base+'.bin')).write_bytes(b.bin);(out/(base+'.gltf')).write_text(json.dumps(b.g,ensure_ascii=False,indent=2));write_glb(b.g,b.bin,out/(base+'.glb'),out); report={'sourceEntry':data.get('_source_entry'),'meshes':len(b.g['meshes']),'skins':len(b.g['skins']),'animations':len(b.g['animations']),'sourceAnimations':sum(1 for a in b.g['animations'] if not a.get('extras',{}).get('generatedUtilityClip')),'generatedBindPoseAnimations':sum(1 for a in b.g['animations'] if a.get('extras',{}).get('generatedUtilityClip')),'textures':len(b.g['textures']),'sourceDiagnostics':data.get('diagnostics',[]),'omittedAnimations':omitted_animations};(out/'export-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(out/'unsupported-records.json').write_text(json.dumps({'sourceEntry':data.get('_source_entry'),'decoderDiagnostics':data.get('diagnostics',[]),'omittedAnimations':omitted_animations,'knownUnmappedSemantics':['shader parameters other than color/normal/specular are preserved only in source bytes','source-specific rendering template behavior is not translated into generic glTF PBR','non-render/gameplay chunks are outside an entity render asset']},ensure_ascii=False,indent=2));print(out/(base+'.gltf'))
def main():
 p=argparse.ArgumentParser();p.add_argument('--server',default='http://127.0.0.1:8421');p.add_argument('--entry',required=True);p.add_argument('--output',required=True);p.add_argument('--name');a=p.parse_args();u=a.server+'/api/entity_mesh?'+urllib.parse.urlencode({'entry':a.entry});d=json.load(urllib.request.urlopen(u));d['_source_entry']=a.entry;export(d,Path(a.output),a.name or Path(a.entry.replace('\\','/')).stem)
if __name__=='__main__':main()
