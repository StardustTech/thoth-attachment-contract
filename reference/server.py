# SPDX-License-Identifier: GPL-3.0-only
"""Single-node persistent HTTP reference. Independent consumers use the Apache contract.
Run with --help. Private fault files are enabled only by --test-controls.
"""
import argparse,contextlib,datetime,hashlib,hmac,json,os,pathlib,re,sqlite3,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from jsonschema import Draft202012Validator,FormatChecker,ValidationError

ROOT=pathlib.Path(__file__).resolve().parents[1]
SCHEMAS=json.loads((ROOT/'spec/schemas.json').read_text(encoding='utf-8'))
API=json.loads((ROOT/'spec/openapi.json').read_text(encoding='utf-8'))
FEATURES=['single-put','multipart','immutable-resources','idempotency','freeze-export','tenant-close','server-measured-content']
MAX_PART=16*1024*1024;MAX_RESOURCE=64*1024*1024
ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$');SHA=re.compile(r'^[a-f0-9]{64}$')
def normalized(x):
 if isinstance(x,dict):return {k:normalized(v) for k,v in x.items()}
 if isinstance(x,list):return [normalized(v) for v in x]
 if isinstance(x,float) and x.is_integer():return int(x)
 return x
def canonical(x):return json.dumps(normalized(x),sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(b):return hashlib.sha256(b).hexdigest()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def at(ts):return datetime.datetime.fromtimestamp(ts,datetime.timezone.utc).isoformat()
def timestamp(s):return datetime.datetime.fromisoformat(s).timestamp()
class Failure(Exception):
 def __init__(self,status,code,message,retryable=False):self.status=status;self.body=dict(code=code,message=message,retryable=retryable)
def fail(status,code,message,retryable=False):raise Failure(status,code,message,retryable)
def validate(name,data):
 try:Draft202012Validator({'$defs':SCHEMAS['$defs'],'$ref':'#/$defs/'+name},format_checker=FormatChecker()).validate(data)
 except ValidationError:fail(400,'invalid','Payload does not match '+name)
def unique_object(pairs):
 result={}
 for key,value in pairs:
  if key in result:fail(400,'invalid','Duplicate JSON property')
  result[key]=value
 return result
def parse_json(data):
 try:
  value=json.loads(data,object_pairs_hook=unique_object,parse_constant=lambda _:fail(400,'invalid','Nonfinite JSON number'))
  canonical(value).encode('utf-8') # Reject isolated UTF-16 surrogate escapes.
  return value
 except (ValueError,UnicodeError):fail(400,'invalid','Invalid JSON')

class Store:
 def __init__(self,root,control=None):
  self.root=pathlib.Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
  self.blobs=self.root/'blobs';self.blobs.mkdir(exist_ok=True)
  self.tmp=self.root/'tmp';self.tmp.mkdir(exist_ok=True)
  self.control=pathlib.Path(control).resolve() if control else None
  self.lock=threading.RLock();self.stopping=threading.Event()
  with self.transaction() as db:
   db.executescript('''CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resources(tenant TEXT,id TEXT,data TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS uploads(tenant TEXT,id TEXT,data TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS parts(tenant TEXT,upload TEXT,number INTEGER,sha256 TEXT,byte_length INTEGER,PRIMARY KEY(tenant,upload,number));
CREATE TABLE IF NOT EXISTS operations(tenant TEXT,id TEXT,fingerprint TEXT,status INTEGER,result TEXT,PRIMARY KEY(tenant,id));''')
  self.maintain()
 @contextlib.contextmanager
 def transaction(self):
  with self.lock:
   db=sqlite3.connect(self.root/'catalog.sqlite',timeout=10,isolation_level=None)
   db.row_factory=sqlite3.Row
   try:
    db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
    yield db
    if db.in_transaction:db.commit()
   except BaseException:
    if db.in_transaction:db.rollback()
    raise
   finally:db.close()
 def fault(self,point,operation=None):
  if not self.control or not self.control.exists():return
  try:data=json.loads(self.control.read_text(encoding='utf-8'))
  except (OSError,ValueError):return
  if data.get('point')!=point or (data.get('operation_id') and data['operation_id']!=operation):return
  if data.get('action')=='unavailable':raise OSError('Injected byte provider unavailable')
  self.control.unlink();os._exit(91 if point=='after_bytes' else 92)
 @staticmethod
 def fetch(db,table,tenant,id=None):
  if table=='tenants':row=db.execute('SELECT data FROM tenants WHERE id=?',(tenant,)).fetchone()
  else:row=db.execute('SELECT data FROM '+table+' WHERE tenant=? AND id=?',(tenant,id)).fetchone()
  return json.loads(row['data']) if row else None
 @staticmethod
 def put(db,table,tenant,id,data):
  if table=='tenants':db.execute('INSERT OR REPLACE INTO tenants VALUES(?,?)',(tenant,canonical(data)))
  else:db.execute('INSERT OR REPLACE INTO '+table+' VALUES(?,?,?)',(tenant,id,canonical(data)))
 def blob_path(self,tenant,sha):return self.blobs/tenant/sha
 def write_blob(self,tenant,content,operation):
  sha=digest(content);dest=self.blob_path(tenant,sha);dest.parent.mkdir(exist_ok=True)
  if not dest.exists():
   temp=self.tmp/uuid.uuid4().hex
   with temp.open('xb') as f:f.write(content);f.flush();os.fsync(f.fileno())
   os.replace(temp,dest)
   if os.name!='nt':
    fd=os.open(dest.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
  else:self.read_blob(tenant,sha,len(content))
  self.fault('after_bytes',operation)
  return sha
 def read_blob(self,tenant,sha,size):
  try:data=self.blob_path(tenant,sha).read_bytes()
  except OSError:fail(503,'integrity_error','Committed bytes are unavailable',True)
  if len(data)!=size or digest(data)!=sha:fail(503,'integrity_error','Committed byte integrity check failed',True)
  return data
 def resource(self,db,t,r):
  data=self.fetch(db,'resources',t,r)
  if not data:fail(404,'missing','Resource not found')
  return data
 def session(self,db,t,u):
  data=self.fetch(db,'uploads',t,u)
  if not data:fail(404,'missing','Upload not found')
  return data
 def parts(self,db,t,u):return [dict(x) for x in db.execute('SELECT number,sha256,byte_length FROM parts WHERE tenant=? AND upload=? ORDER BY number',(t,u))]
 def tombstone(self,db,t,r):
  data=self.resource(db,t,r)
  if data['state']!='DELETED':data.update(state='DELETED',revision=data['revision']+1,modified_at=now());self.put(db,'resources',t,r,data)
  for row in db.execute('SELECT id,data FROM uploads WHERE tenant=?',(t,)).fetchall():
   s=json.loads(row['data'])
   if s['resource_id']==r and s['state']=='OPEN':s['state']='ABORTED';self.put(db,'uploads',t,row['id'],s)
  return data
 def expire(self,db):
  for row in db.execute('SELECT tenant,id,data FROM uploads').fetchall():
   s=json.loads(row['data']);tenant=self.fetch(db,'tenants',row['tenant'])
   if s['state']=='OPEN' and tenant['state']=='ACTIVE' and timestamp(s['expires_at'])<=time.time():
    self.tombstone(db,row['tenant'],s['resource_id']);s['state']='EXPIRED';self.put(db,'uploads',row['tenant'],row['id'],s)
 def reachable(self,db,t):
  out={}
  for row in db.execute('SELECT data FROM resources WHERE tenant=?',(t,)):
   r=json.loads(row['data'])
   if r['state']=='READY':out[r['sha256']]=r['byte_length']
  for row in db.execute('SELECT id,data FROM uploads WHERE tenant=?',(t,)):
   if json.loads(row['data'])['state']=='OPEN':
    for p in self.parts(db,t,row['id']):out[p['sha256']]=p['byte_length']
  return out
 def maintain(self):
  try:
   with self.transaction() as db:
    self.expire(db);self.fault('cleanup')
    for row in db.execute('SELECT id FROM tenants').fetchall():
     t=row['id'];live=self.reachable(db,t);folder=self.blobs/t
     if folder.exists():
      for p in folder.iterdir():
       if p.is_file() and p.name not in live:p.unlink()
    for p in self.tmp.iterdir():
     if p.is_file():p.unlink()
  except (OSError,sqlite3.Error):pass # Durable tombstones/sessions drive the next retry.
 def maintenance_loop(self):
  while not self.stopping.wait(0.3):self.maintain()
 def execute(self,operation,method,path,params,query,headers,body,json_body,permission):
  t=params.get('tenant');rid=params.get('resource');uid=params.get('upload')
  opkey=headers.get('Idempotency-Key');mutating=method in ('PUT','POST','DELETE')
  if operation=='getCapabilities':return dict(contract_version='0.2.0',features=FEATURES,max_resource_bytes=MAX_RESOURCE,max_part_bytes=MAX_PART,max_parts=256,authorization_profile='trusted-service-bearer')
  try:epoch=int(headers.get('X-Tenant-Epoch',''))
  except ValueError:fail(400,'invalid','X-Tenant-Epoch required')
  if epoch<1:fail(400,'invalid','Invalid epoch')
  if mutating and (not opkey or not ID.fullmatch(opkey)):fail(400,'invalid','Idempotency-Key required')
  fp=digest(canonical(dict(method=method,path=path,body=canonical(json_body) if json_body is not None else digest(body),media_type=headers.get('Content-Type',''),sha256=headers.get('X-Content-SHA256',''))).encode())
  with self.transaction() as db:
   self.expire(db)
   tenant=self.fetch(db,'tenants',t)
   if not tenant and operation!='createTenant':fail(404,'missing','Tenant not found')
   if tenant and tenant['epoch']!=epoch:fail(409,'conflict','Stale tenant epoch')
   previous=db.execute('SELECT * FROM operations WHERE tenant=? AND id=?',(t,opkey)).fetchone() if mutating else None
   if previous:
    if previous['fingerprint']!=fp:fail(409,'conflict','Idempotency key input conflict')
    return json.loads(previous['result'])
   if tenant and tenant['state']=='CLOSED' and operation not in ('getTenant','getOperation','closeTenant'):fail(409,'conflict','Tenant is closed')
   if tenant and tenant['state']=='FROZEN' and mutating and operation not in ('freezeTenant','closeTenant'):fail(409,'conflict','Tenant is frozen')
   result=self.dispatch(db,operation,t,rid,uid,params,query,headers,body,json_body,tenant,epoch,opkey)
   if mutating:
    self.fault('before_commit',opkey)
    db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(t,opkey,fp,200,canonical(result)))
    db.commit();self.fault('after_commit',opkey)
   return result
 def dispatch(self,db,op,t,rid,uid,params,q,h,body,j,tenant,epoch,opkey):
  if op=='createTenant':
   if tenant:fail(409,'conflict','Tenant already provisioned')
   if j['epoch']!=epoch:fail(409,'conflict','Epoch mismatch')
   result=dict(tenant_id=t,epoch=epoch,state='ACTIVE');self.put(db,'tenants',t,None,result);return result
  if op=='getTenant':return tenant
  if op in ('freezeTenant','closeTenant'):
   if op=='freezeTenant' and tenant['state']=='CLOSED':fail(409,'conflict','Closed tenant cannot freeze')
   tenant['state']='FROZEN' if op=='freezeTenant' else 'CLOSED'
   self.put(db,'tenants',t,None,tenant)
   if op=='closeTenant':
    for row in db.execute('SELECT id FROM resources WHERE tenant=?',(t,)).fetchall():self.tombstone(db,t,row['id'])
   return tenant
  if op=='getOperation':
   row=db.execute('SELECT * FROM operations WHERE tenant=? AND id=?',(t,params['operation'])).fetchone()
   if not row:fail(404,'missing','No committed operation')
   return dict(operation_id=row['id'],state='SUCCEEDED',status=row['status'],result=json.loads(row['result']),request_fingerprint=row['fingerprint'])
  if op in ('listResources','getUsage'):
   resources=[json.loads(r['data']) for r in db.execute('SELECT data FROM resources WHERE tenant=? ORDER BY id',(t,))];ready=[r for r in resources if r['state']=='READY']
   if op=='getUsage':return dict(ready_count=len(ready),ready_bytes=sum(r['byte_length'] for r in ready),open_uploads=sum(json.loads(s['data'])['state']=='OPEN' for s in db.execute('SELECT data FROM uploads WHERE tenant=?',(t,))))
   try:limit=int(q.get('limit',['100'])[0])
   except ValueError:fail(400,'invalid','Invalid limit')
   if not 1<=limit<=100:fail(400,'invalid','Invalid limit')
   after=q.get('after',[''])[0]
   if after and not ID.fullmatch(after):fail(400,'invalid','Invalid cursor')
   items=[r for r in ready if r['resource_id']>after];page=items[:limit]
   return dict(items=page,next_after=page[-1]['resource_id'] if len(items)>limit else None)
  if op in ('putResource','beginUpload'):
   r=rid if op=='putResource' else j['resource_id']
   if self.fetch(db,'resources',t,r):fail(409,'conflict','Resource ID is reserved')
   media=h.get('Content-Type','application/octet-stream') if op=='putResource' else j['media_type']
   length=len(body) if op=='putResource' else j['byte_length'];sha=digest(body) if op=='putResource' else j['sha256']
   result=dict(tenant_id=t,resource_id=r,state='READY' if op=='putResource' else 'UPLOADING',revision=1 if op=='putResource' else 0,media_type=media,byte_length=length,sha256=sha,modified_at=now())
   if op=='putResource':self.write_blob(t,body,opkey)
   self.put(db,'resources',t,r,result)
   if op=='putResource':return result
   s=dict(tenant_id=t,upload_id=uuid.uuid4().hex,resource_id=r,state='OPEN',expires_at=at(time.time()+j.get('ttl_seconds',3600)),part_count=0)
   self.put(db,'uploads',t,s['upload_id'],s);return s
  if op in ('getResource','readResource','deleteResource'):
   r=self.resource(db,t,rid)
   if op=='deleteResource':return self.tombstone(db,t,rid)
   if r['state']!='READY':fail(404,'missing','Resource is not visible')
   if op=='getResource':return r
   return self.read_blob(t,r['sha256'],r['byte_length']),r['media_type']
  if op in ('getUpload','putPart','completeUpload','abortUpload'):
   s=self.session(db,t,uid)
   if op=='getUpload':return s
   if s['state']!='OPEN':
    if op=='abortUpload' and s['state'] in ('ABORTED','EXPIRED'):return s
    fail(409,'conflict','Upload is not open')
   if op=='abortUpload':self.tombstone(db,t,s['resource_id']);s['state']='ABORTED';self.put(db,'uploads',t,uid,s);return s
   r=self.resource(db,t,s['resource_id'])
   if r['state']!='UPLOADING':fail(409,'conflict','Resource is not uploading')
   if op=='putPart':
    n=int(params['number']);sha=digest(body);existing=db.execute('SELECT sha256,byte_length FROM parts WHERE tenant=? AND upload=? AND number=?',(t,uid,n)).fetchone()
    if existing and existing['sha256']!=sha:fail(409,'conflict','Part already contains different bytes')
    total=db.execute('SELECT coalesce(sum(byte_length),0) FROM parts WHERE tenant=? AND upload=?',(t,uid)).fetchone()[0]
    if total-(existing['byte_length'] if existing else 0)+len(body)>(r['byte_length'] if r['byte_length'] is not None else MAX_RESOURCE):fail(400,'invalid','Parts exceed resource length limit')
    self.write_blob(t,body,opkey);db.execute('INSERT OR IGNORE INTO parts VALUES(?,?,?,?,?)',(t,uid,n,sha,len(body)))
    s['part_count']=len(self.parts(db,t,uid));self.put(db,'uploads',t,uid,s)
    return dict(number=n,sha256=sha,byte_length=len(body))
   parts=self.parts(db,t,uid)
   expected=[dict(number=p['number'],sha256=p['sha256']) for p in parts]
   if j['parts']!=expected or [p['number'] for p in parts]!=list(range(1,len(parts)+1)):fail(400,'invalid','Complete manifest mismatch or nonconsecutive parts')
   data=b''.join(self.read_blob(t,p['sha256'],p['byte_length']) for p in parts)
   if (r['byte_length'] is not None and len(data)!=r['byte_length']) or (r['sha256'] is not None and digest(data)!=r['sha256']):fail(400,'invalid','Complete content length or digest mismatch')
   self.write_blob(t,data,opkey);r.update(state='READY',revision=1,modified_at=now(),byte_length=len(data),sha256=digest(data));self.put(db,'resources',t,r['resource_id'],r)
   s['state']='COMPLETED';self.put(db,'uploads',t,uid,s);return r
  if op in ('exportTenant','readExportBlob'):
   if tenant['state']!='FROZEN':fail(409,'conflict','Freeze tenant before export')
   live=self.reachable(db,t)
   if op=='readExportBlob':
    sha=params['digest']
    if sha not in live:fail(404,'missing','Blob absent from export')
    return self.read_blob(t,sha,live[sha]),'application/octet-stream'
   return dict(contract_version='0.2.0',tenant=tenant,
     resources=[json.loads(r['data']) for r in db.execute('SELECT data FROM resources WHERE tenant=? ORDER BY id',(t,))],
     uploads=[dict(session=json.loads(s['data']),parts=self.parts(db,t,s['id'])) for s in db.execute('SELECT id,data FROM uploads WHERE tenant=? ORDER BY id',(t,))],
     operations=[dict(operation_id=o['id'],state='SUCCEEDED',status=o['status'],result=json.loads(o['result']),request_fingerprint=o['fingerprint']) for o in db.execute('SELECT * FROM operations WHERE tenant=? ORDER BY id',(t,))],
     blobs=[dict(sha256=sha,byte_length=size) for sha,size in sorted(live.items())])
  fail(404,'missing','Unknown operation')

class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def setup(self):super().setup();self.connection.settimeout(15)
 def send(self,status,data,media='application/json'):
  raw=data if isinstance(data,bytes) else canonical(data).encode()
  self.send_response(status);self.send_header('Content-Type',media);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','private, no-store');self.send_header('X-Contract-Version','0.2.0');self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.close_connection=True
 def handle_request(self):
  try:
   split=urlsplit(self.path);path=split.path;query=parse_qs(split.query,keep_blank_values=True)
   selected=None
   for template,methods in API['paths'].items():
    pattern=re.sub(r'\{(\w+)\}',r'(?P<\1>[^/]+)',template)
    match=re.fullmatch(pattern,path)
    if match and self.command.lower() in methods:selected=methods[self.command.lower()];params=match.groupdict();break
   if not selected:fail(404,'missing','Unknown operation')
   permission=selected['x-permission'];tenant=params.get('tenant')
   # Reload a private policy on every request: no end-user identity is accepted here.
   try:policy=json.loads(self.server.policy.read_text(encoding='utf-8'))
   except (ValueError,OSError):fail(503,'unavailable','Service policy unavailable',True)
   auth=self.headers.get_all('Authorization',[])
   if len(auth)!=1 or not auth[0].startswith('Bearer '):fail(401,'unauthenticated','Bearer credential required')
   token=auth[0][7:]
   principal=next((v for k,v in policy.items() if hmac.compare_digest(k.encode(),token.encode())),None)
   if not principal or principal.get('audience')!='neutral-attachment-v0':fail(401,'unauthenticated','Valid service credential required')
   if (tenant and tenant not in principal.get('tenants',[])) or not ({permission,'admin'} & set(principal.get('actions',[]))):fail(403,'denied','Service operation denied')
   if self.headers.get('X-Contract-Version','0.2.0')!='0.2.0':fail(422,'unsupported','Contract version unsupported')
   required=set(filter(None,(x.strip() for x in self.headers.get('X-Required-Capabilities','').split(','))))
   if not required.issubset(FEATURES):fail(422,'unsupported','Required capability unsupported')
   for key,value in params.items():
    if key=='number':
     if not value.isdigit() or not 1<=int(value)<=256:fail(400,'invalid','Invalid part number')
    elif not (SHA if key=='digest' else ID).fullmatch(value):fail(400,'invalid','Invalid identifier')
   allowed={'after','limit'} if selected['operationId']=='listResources' else set()
   if not set(query)<=allowed or any(len(v)!=1 for v in query.values()):fail(400,'invalid','Unknown or duplicate query parameter')
   lengths=self.headers.get_all('Content-Length',[])
   if self.headers.get('Transfer-Encoding') or len(lengths)>1:fail(400,'invalid','Ambiguous transfer framing')
   if self.command in ('PUT','POST','DELETE') and not lengths:fail(411,'invalid','Content-Length required')
   try:length=int(lengths[0]) if lengths else 0
   except ValueError:fail(400,'invalid','Invalid Content-Length')
   if length<0:fail(400,'invalid','Invalid Content-Length')
   req=selected.get('requestBody',{}).get('content',{})
   binary='application/octet-stream' in req or '*/*' in req
   if length>(MAX_PART if binary else 65536):
    # Drain bounded local payloads before closing so a pipelined body cannot
    # discard the structured error through a TCP reset. Never buffer the body.
    if length<=MAX_RESOURCE:
     remaining=length
     while remaining:
      chunk=self.rfile.read(min(65536,remaining))
      if not chunk:break
      remaining-=len(chunk)
    fail(413,'invalid','Request exceeds declared limit')
   body=self.rfile.read(length)
   if len(body)!=length:fail(400,'invalid','Truncated body')
   j=None
   if 'application/json' in req:
    if self.headers.get('Content-Type','').split(';')[0]!='application/json':fail(415,'invalid','JSON media type required')
    j=parse_json(body);name=req['application/json']['schema']['$ref'].split('/')[-1];validate(name,j)
   elif binary:
    sha=self.headers.get('X-Content-SHA256','')
    if not SHA.fullmatch(sha) or digest(body)!=sha:fail(400,'invalid','Byte digest mismatch')
    if selected['operationId']=='putResource' and not re.fullmatch(r'[^\r\n]+/[^\r\n]+',self.headers.get('Content-Type','')):fail(415,'invalid','Media type required')
    if selected['operationId']=='putPart' and self.headers.get('Content-Type')!='application/octet-stream':fail(415,'invalid','Binary part media type required')
   elif body:fail(400,'invalid','This operation has no request body')
   result=self.server.store.execute(selected['operationId'],self.command,path,params,query,self.headers,body,j,permission)
   if isinstance(result,tuple):self.send(200,*result)
   else:self.send(200,result)
  except Failure as e:self.send(e.status,e.body)
  except (sqlite3.Error,OSError):
   try:self.send(503,dict(code='unavailable',message='Storage or transport unavailable; retry same operation key',retryable=True))
   except OSError:pass
  except Exception:
   self.send(500,dict(code='unavailable',message='Internal reference error',retryable=True));raise
 do_GET=handle_request;do_PUT=handle_request;do_POST=handle_request;do_DELETE=handle_request

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',required=True);p.add_argument('--policy',required=True);p.add_argument('--host',default='127.0.0.1',help='Use a container-private interface only for integration');p.add_argument('--port',type=int,default=18120);p.add_argument('--test-controls',help='Private fault-control file; test instances only');a=p.parse_args()
 store=Store(a.data,a.test_controls);server=ThreadingHTTPServer((a.host,a.port),Handler);server.daemon_threads=True;server.store=store;server.policy=pathlib.Path(a.policy)
 worker=threading.Thread(target=store.maintenance_loop,daemon=True);worker.start();print(canonical(dict(status='READY',port=server.server_port)),flush=True)
 try:server.serve_forever(poll_interval=0.1)
 finally:store.stopping.set();server.server_close();worker.join(2)
if __name__=='__main__':main()
