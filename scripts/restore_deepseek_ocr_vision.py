"""Restore the unchanged embedded vision tensors skipped by llama-quantize."""
import io,json,struct
from pathlib import Path
SIZES={0:1,1:1,2:2,3:2,4:4,5:4,6:4,7:1,10:8,11:8,12:8}
def u32(f):return struct.unpack('<I',f.read(4))[0]
def u64(f):return struct.unpack('<Q',f.read(8))[0]
def string(f):return f.read(u64(f)).decode('utf-8')
def packstring(value):
 data=value.encode();return struct.pack('<Q',len(data))+data
def skip(f,t):
 if t in SIZES:f.seek(SIZES[t],1)
 elif t==8:f.seek(u64(f),1)
 elif t==9:
  subtype,count=u32(f),u64(f)
  if subtype in SIZES:f.seek(SIZES[subtype]*count,1)
  else:
   for _ in range(count):skip(f,subtype)
 else:raise ValueError(t)
def read(path):
 with open(path,'rb') as f:
  assert f.read(4)==b'GGUF';version=u32(f);count,kv=u64(f),u64(f);assert version==3
  records=[];alignment=32
  for _ in range(kv):
   start=f.tell();name=string(f);kind=u32(f);value=f.tell();skip(f,kind);end=f.tell()
   f.seek(value)
   if name=='general.alignment':alignment=u32(f)
   f.seek(start);data=f.read(end-start)
   if name=='general.architecture':data=packstring(name)+struct.pack('<I',8)+packstring('deepseekocr')
   records.append(data)
  tensors=[]
  for _ in range(count):
   name=string(f);dims=[u64(f) for _ in range(u32(f))];kind=u32(f);offset=u64(f)
   tensors.append({'name':name,'dims':dims,'kind':kind,'offset':offset,'path':path})
  data_offset=(f.tell()+alignment-1)//alignment*alignment
  ordered=sorted(tensors,key=lambda item:item['offset'])
  for i,tensor in enumerate(ordered):
   end=ordered[i+1]['offset'] if i+1<len(ordered) else Path(path).stat().st_size-data_offset
   tensor['size']=end-tensor['offset'];tensor['absolute']=data_offset+tensor['offset']
  return records,tensors,alignment
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', help='Original Ollama DeepSeek OCR GGUF')
parser.add_argument('quantized_text', help='GGUF produced by llama-quantize')
parser.add_argument('output', help='New complete GGUF; must not already exist')
args=parser.parse_args()
source,quant,out=args.source,args.quantized_text,args.output
if Path(out).exists():
    parser.error('Output already exists; choose a new filename')
_,original,_=read(source);records,text,alignment=read(quant)
vision=[t for t in original if t['name'].startswith(('s.','v.','mm.'))]
assert vision
original_text={t['name']:t['dims'] for t in original if t not in vision}
assert original_text=={t['name']:t['dims'] for t in text},'Text tensor shapes/names differ'
tensors=text+vision;assert len({t['name'] for t in tensors})==len(tensors)
header=io.BytesIO();header.write(b'GGUF'+struct.pack('<IQQ',3,len(tensors),len(records)))
for record in records:header.write(record)
offset=0
for tensor in tensors:
 offset=(offset+alignment-1)//alignment*alignment;tensor['destination']=offset
 header.write(packstring(tensor['name'])+struct.pack('<I',len(tensor['dims'])))
 for dim in tensor['dims']:header.write(struct.pack('<Q',dim))
 header.write(struct.pack('<IQ',tensor['kind'],offset));offset+=tensor['size']
with open(out,'wb') as f:
 f.write(header.getvalue());f.write(b'\0'*((-f.tell())%alignment));start=f.tell()
 for tensor in tensors:
  f.write(b'\0'*(start+tensor['destination']-f.tell()))
  with open(tensor['path'],'rb') as src:
   src.seek(tensor['absolute']);left=tensor['size']
   while left:
    block=src.read(min(8*1024*1024,left));assert block
    f.write(block);left-=len(block)
_,merged,_=read(out)
assert len(merged)==len(original)
assert {t['name']:t['dims'] for t in merged}=={t['name']:t['dims'] for t in original}
print(json.dumps({'output':out,'text_tensors':len(text),'unchanged_vision_tensors':len(vision),'bytes':Path(out).stat().st_size}))
