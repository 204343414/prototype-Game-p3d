#!/usr/bin/env python3
"""Export validated, already-decodable Manhattan Cell core geometry to glTF.
This is deliberately not labelled a complete map exporter: unresolved placed-model,
effect, collision and other records remain in unsupported-records.json.
"""
import argparse,base64,json,struct,urllib.parse,urllib.request
from pathlib import Path
from export_entity_gltf import Builder,dxt_png,safe,write_glb

def get(url,params):return json.load(urllib.request.urlopen(url+'?'+urllib.parse.urlencode(params),timeout=600))
def main():
 p=argparse.ArgumentParser();p.add_argument('--server',default='http://127.0.0.1:8421');p.add_argument('--archive',required=True);p.add_argument('--shared',default='');p.add_argument('--cells',required=True,help='comma/range list, or all');p.add_argument('--output',required=True);p.add_argument('--name',default='manhattan-cells');a=p.parse_args()
 if a.cells=='all':cells=list(range(260))
 else:
  cells=[]
  for x in a.cells.split(','):
   q=x.split('-',1);cells.extend(range(int(q[0]),int(q[-1])+1))
 out=Path(a.output);out.mkdir(parents=True,exist_ok=True);(out/'textures').mkdir(exist_ok=True);b=Builder(a.name);tex={};mats={};reports=[];loaded=[]
 for cell in cells:
  d=get(a.server+'/api/rcf_cell_preview',{'path':a.archive,'cell':cell,'materials':1,'shared_path':a.shared})
  reports.append({'cell':cell,'status':d.get('status'),'geometryReport':d.get('report'),'materialReport':d.get('materials')})
  if d.get('error'):raise RuntimeError(f'Cell {cell}: {d["error"]}')
  if d.get('status')!='ready':continue
  for t in d.get('textures',[]):
   if t['key'] in tex:continue
   mip=t['mips'][0];fn=t['key']+'.png';(out/'textures'/fn).write_bytes(dxt_png(t['format'],mip['width'],mip['height'],base64.b64decode(mip['data'])));b.g['images'].append({'name':t['key'],'uri':'textures/'+fn});b.g['textures'].append({'source':len(b.g['images'])-1,'name':t['key']});tex[t['key']]=len(b.g['textures'])-1
  cell_node={'name':f'Cell {cell}','children':[],'extras':{'sourceCell':cell,'sourceArchive':a.archive}};b.g['nodes'].append(cell_node);ci=len(b.g['nodes'])-1;b.g['scenes'][0]['nodes'].append(ci)
  for m in d['meshes']:
   pb=base64.b64decode(m['p']);v=struct.unpack('<%df'%(len(pb)//4),pb);attrs={'POSITION':b.acc(pb,5126,'VEC3',len(v)//3,34962,[min(v[i::3]) for i in range(3)],[max(v[i::3]) for i in range(3)])};ub=base64.b64decode(m.get('uv') or '')
   if ub:attrs['TEXCOORD_0']=b.acc(ub,5126,'VEC2',len(ub)//8,34962)
   ib=base64.b64decode(m['i']);ia=b.acc(ib,5123,'SCALAR',len(ib)//2,34963);key=m.get('texture')
   if key not in mats:
    mat={'name':key or 'unresolved material','pbrMetallicRoughness':{'metallicFactor':0,'roughnessFactor':0.9},'doubleSided':True,'extras':{'sourceTextureKey':key}}
    if key in tex:mat['pbrMetallicRoughness']['baseColorTexture']={'index':tex[key]}
    b.g['materials'].append(mat);mats[key]=len(b.g['materials'])-1
   prim={'attributes':attrs,'indices':ia,'material':mats[key],'mode':4};b.g['meshes'].append({'name':m['geometry'],'primitives':[prim],'extras':{'sourceEntry':m['entry'],'sourceGroup':m['group'],'shaderTemplate':m.get('shader_template'),'materialClass':m.get('material_class'),'textureSource':m.get('texture_source'),'textureParameter':m.get('texture_parameter')}});b.g['nodes'].append({'name':f'{m["geometry"]} group {m["group"]}','mesh':len(b.g['meshes'])-1});cell_node['children'].append(len(b.g['nodes'])-1)
  loaded.append(cell)
 b.g['asset']['extras']={'scope':'validated merged Manhattan Cell core triangles and resolved color textures only','completeMap':False,'reason':'placed models, effects, collision and other source record classes are not yet decoded'};b.g['buffers'][0]['byteLength']=len(b.bin);base=safe(a.name);b.g['buffers'][0]['uri']=base+'.bin';(out/(base+'.bin')).write_bytes(b.bin);(out/(base+'.gltf')).write_text(json.dumps(b.g,ensure_ascii=False,indent=2));write_glb(b.g,b.bin,out/(base+'.glb'),out);(out/'unsupported-records.json').write_text(json.dumps({'completeMap':False,'requestedCells':cells,'loadedCells':loaded,'knownUnsupported':['placed model references and transforms','effects','collision and navigation','non-core geometry groups rejected by strict decoder'],'cellReports':reports},ensure_ascii=False,indent=2));print(out/(base+'.gltf'))
if __name__=='__main__':main()
