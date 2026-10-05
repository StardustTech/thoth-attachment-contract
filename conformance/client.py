# SPDX-License-Identifier: Apache-2.0
"""Transport-only independent consumer. Imports no implementation or SDK."""
import hashlib,json,pathlib,re,threading,time,urllib.request,urllib.error,uuid
from jsonschema import Draft202012Validator,FormatChecker
ROOT=pathlib.Path(__file__).resolve().parents[1]
API=json.loads((ROOT/'spec/openapi.json').read_text(encoding='utf-8'))
SCHEMAS=json.loads((ROOT/'spec/schemas.json').read_text(encoding='utf-8'))
TRACE=[];LOCK=threading.Lock()
def sha(data):return hashlib.sha256(data).hexdigest()
class Client:
 def __init__(self,base,token):self.base=base;self.token=token
 def request(self,method,path,body=None,data=None,key=None,epoch=1,headers=None,status=200):
  h={'X-Tenant-Epoch':str(epoch),'Authorization':'Bearer '+self.token}
  if body is not None:data=json.dumps(body,separators=(',',':')).encode();h['Content-Type']='application/json'
  elif data is not None:h.update({'Content-Type':'application/octet-stream','X-Content-SHA256':sha(data)})
  if method in ('POST','PUT','DELETE'):h['Idempotency-Key']=key or uuid.uuid4().hex;data=b'' if data is None else data
  h.update(headers or {});h={k:v for k,v in h.items() if v is not None}
  request=urllib.request.Request(self.base+path,data=data,headers=h,method=method)
  started=time.monotonic()
  try:
   try:response=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=30)
   except urllib.error.HTTPError as e:response=e
   with response:raw=response.read();actual=response.status;rh=dict(response.headers)
  except Exception as e:
   with LOCK:TRACE.append(dict(method=method,path=path,status='TRANSPORT_LOSS',error=type(e).__name__))
   raise
  with LOCK:TRACE.append(dict(method=method,path=path,status=actual,response_sha256=sha(raw),elapsed_ms=round((time.monotonic()-started)*1000,2)))
  allowed=(status,) if isinstance(status,int) else status
  assert actual in allowed,(method,path,actual,raw[:250])
  assert response.headers['X-Contract-Version']=='0.1.0'
  assert response.headers['Cache-Control']=='private, no-store'
  operation=None
  for template,methods in API['paths'].items():
   if re.fullmatch(re.sub(r'\{\w+\}',r'[^/]+',template),path.split('?')[0]):operation=methods.get(method.lower());break
  if actual>=400:name='Error'
  elif operation:
   content=operation['responses']['200']['content']
   if 'application/json' not in content:return raw
   name=content['application/json']['schema']['$ref'].split('/')[-1]
  else:raise AssertionError('Successful route absent from contract')
  value=json.loads(raw)
  Draft202012Validator({'$defs':SCHEMAS['$defs'],'$ref':'#/$defs/'+name},format_checker=FormatChecker()).validate(value)
  return value
 def create(self,t):return self.request('PUT','/v0/tenants/'+t,body={'epoch':1})
 def put(self,t,r,data=b'attachment',**kw):return self.request('PUT',f'/v0/tenants/{t}/resources/{r}',data=data,**kw)
 def read(self,t,r,**kw):return self.request('GET',f'/v0/tenants/{t}/resources/{r}/content',**kw)
 def begin(self,t,r,data,**kw):return self.request('POST',f'/v0/tenants/{t}/uploads',body=dict(resource_id=r,media_type='text/plain',byte_length=len(data),sha256=sha(data),**kw))
 def part(self,t,u,n,data,**kw):return self.request('PUT',f'/v0/tenants/{t}/uploads/{u}/parts/{n}',data=data,**kw)
 def complete(self,t,u,parts,**kw):return self.request('POST',f'/v0/tenants/{t}/uploads/{u}/complete',body={'parts':[{'number':n,'sha256':sha(b)} for n,b in parts]},**kw)
