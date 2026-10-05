# SPDX-License-Identifier: Apache-2.0
"""Run against any implementation of this HTTP profile: python -m conformance.test_contract.
NA_CONFIG selects private {base,admin,reader,outsider,wrong_audience} credentials.
NA_RESULT selects the public evidence file. Every JSON response is schema checked.
"""
import concurrent.futures,hashlib,json,os,pathlib,time,unittest
from .client import Client,TRACE,SCHEMAS,sha
from jsonschema import Draft202012Validator
class Contract(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads(pathlib.Path(os.environ['NA_CONFIG']).read_text())
  self.c=Client(self.cfg['base'],self.cfg['admin']);self.t=self._testMethodName
  self.c.create(self.t);self.b='/v0/tenants/'+self.t
 def get(self,suffix,**kw):return self.c.request('GET',self.b+suffix,**kw)
 def mutate(self,method,suffix,**kw):return self.c.request(method,self.b+suffix,**kw)
 def parallel(self,*calls):
  with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:return [f.result() for f in [pool.submit(c) for c in calls]]
 def test_01_single_put_metadata_list_usage(self):
  data=b'hello\x00attachment';r=self.c.put(self.t,'alpha',data,headers={'Content-Type':'text/plain'})
  self.assertEqual((r['revision'],r['sha256'],r['byte_length']),(1,sha(data),len(data)))
  self.assertEqual(self.c.read(self.t,'alpha'),data);self.assertEqual(self.get('/resources/alpha'),r)
  self.assertEqual(self.get('/resources')['items'],[r]);self.assertEqual(self.get('/usage'),dict(ready_count=1,ready_bytes=len(data),open_uploads=0))
 def test_02_multipart_tiny(self):
  s=self.c.begin(self.t,'tiny',b'tiny');u=s['upload_id'];self.get('/resources/tiny',status=404)
  self.c.part(self.t,u,1,b'tiny');r=self.c.complete(self.t,u,[(1,b'tiny')]);self.assertEqual(r['state'],'READY')
  self.assertEqual(self.c.read(self.t,'tiny'),b'tiny');self.assertEqual(self.get('/uploads/'+u)['state'],'COMPLETED')
 def test_03_multipart_parallel_reordered_large(self):
  a=b'a'*(9*1024*1024);b=b'b'*(9*1024*1024);s=self.c.begin(self.t,'large',a+b);u=s['upload_id']
  self.parallel(lambda:self.c.part(self.t,u,2,b),lambda:self.c.part(self.t,u,1,a))
  self.c.complete(self.t,u,[(1,a),(2,b)]);self.assertEqual(sha(self.c.read(self.t,'large')),sha(a+b))
 def test_04_idempotency_conflict_and_route_scope(self):
  a=self.c.put(self.t,'r',b'one',key='same');b=self.c.put(self.t,'r',b'one',key='same');self.assertEqual(a,b)
  self.c.put(self.t,'r',b'two',key='same',status=409);self.c.put(self.t,'s',b'one',key='same',status=409)
  self.mutate('DELETE','/resources/r',key='same',status=409)
  self.assertEqual(self.get('/operations/same')['result'],a);self.get('/operations/unknown',status=404)
 def test_05_history_never_resurrects(self):
  r=self.c.put(self.t,'r',key='original');deleted=self.mutate('DELETE','/resources/r');self.assertEqual(deleted['revision'],2)
  self.assertEqual(self.c.put(self.t,'r',key='original'),r);self.c.read(self.t,'r',status=404)
  self.c.put(self.t,'r',status=409);self.assertEqual(self.get('/usage')['ready_count'],0)
  self.assertEqual(self.mutate('DELETE','/resources/r'),deleted)
 def test_06_part_immutability_and_invalid_manifest(self):
  u=self.c.begin(self.t,'r',b'ab')['upload_id'];self.c.part(self.t,u,2,b'b');self.c.part(self.t,u,1,b'a')
  self.c.part(self.t,u,1,b'a');self.c.part(self.t,u,1,b'z',status=409)
  for parts in [[(2,b'b'),(1,b'a')],[(1,b'a')],[(1,b'x'),(2,b'b')],[(1,b'a'),(1,b'a')]]:self.c.complete(self.t,u,parts,status=400)
  self.c.complete(self.t,u,[(1,b'a'),(2,b'b')]);self.assertEqual(self.c.read(self.t,'r'),b'ab')
 def test_07_size_hash_and_session_isolation(self):
  u=self.c.begin(self.t,'r',b'ab')['upload_id'];v=self.c.begin(self.t,'s',b'ab')['upload_id']
  self.c.part(self.t,u,1,b'abc',status=400);self.c.part(self.t,u,1,b'a',headers={'X-Content-SHA256':'0'*64},status=400)
  self.c.part(self.t,u,1,b'zz');self.c.complete(self.t,u,[(1,b'zz')],status=400)
  self.c.complete(self.t,v,[(1,b'zz')],status=400);self.c.part(self.t,'absent',1,b'a',status=404)
  self.c.read(self.t,'r',status=404)
 def test_08_empty_resource_and_upload(self):
  self.c.put(self.t,'zero',b'');self.assertEqual(self.c.read(self.t,'zero'),b'')
  u=self.c.begin(self.t,'zero-multi',b'')['upload_id'];self.c.complete(self.t,u,[])
  self.assertEqual(self.c.read(self.t,'zero-multi'),b'')
 def test_09_authentication_context(self):
  self.c.put(self.t,'secret');path=self.b+'/resources/secret/content'
  for token in ['',self.cfg['wrong_audience'],'unknown']:Client(self.cfg['base'],token).request('GET',path,status=401)
  self.c.request('GET',path,headers={'Authorization':None},status=401)
  self.c.request('GET',path,headers={'Authorization':self.cfg['admin']},status=401)
  Client(self.cfg['base'],self.cfg['outsider']).request('GET',path,status=403)
  self.c.request('GET',path,headers={'Authorization':None,'X-User-Id':'administrator','X-Workspace-Id':self.t},status=401)
 def test_10_action_authorization_and_replay(self):
  self.c.put(self.t,'r',key='write');reader=Client(self.cfg['base'],self.cfg['reader'])
  self.assertEqual(reader.read(self.t,'r'),b'attachment');reader.put(self.t,'r',key='write',status=403)
  reader.request('POST',self.b+'/freeze',status=403);reader.request('GET',self.b+'/export',status=403)
 def test_11_epoch_enforcement(self):
  self.c.put(self.t,'r',key='write');self.c.put(self.t,'r',key='write',epoch=2,status=409)
  self.c.read(self.t,'r',epoch=2,status=409);self.c.read(self.t,'r',headers={'X-Tenant-Epoch':None},status=400)
  self.mutate('PUT','',body={'epoch':1},status=409)
 def test_12_versions_and_capabilities(self):
  cap=self.c.request('GET','/v0/capabilities');self.assertIn('multipart',cap['features'])
  self.get('/usage',headers={'X-Contract-Version':'2.0'},status=422)
  self.get('/usage',headers={'X-Required-Capabilities':'future-magic'},status=422)
  self.get('/usage',headers={'X-Required-Capabilities':'multipart,idempotency'})
 def test_13_strict_requests_extensible_responses(self):
  begin=dict(resource_id='x',media_type='text/plain',byte_length=0,sha256=sha(b''),future=True)
  self.mutate('POST','/uploads',body=begin,status=400)
  self.mutate('POST','/uploads',data=b'{"resource_id":"a","resource_id":"b"}',headers={'Content-Type':'application/json'},status=400)
  self.get('/resources?future=1',status=400);self.get('/resources?limit=1&limit=2',status=400)
  cap=self.c.request('GET','/v0/capabilities');cap['future_optional']='supported by a later server'
  Draft202012Validator({'$defs':SCHEMAS['$defs'],'$ref':'#/$defs/Capabilities'}).validate(cap)
 def test_14_limits_and_invalid_identifiers(self):
  u=self.c.begin(self.t,'r',b'a')['upload_id']
  for n in [0,257]:self.c.part(self.t,u,n,b'a',status=400)
  self.c.put(self.t,'bad.id',status=400);self.c.put(self.t,'too-big',b'x'*(16*1024*1024+1),status=413)
  self.mutate('POST','/uploads',body=dict(resource_id='oversize',media_type='text/plain',byte_length=67108865,sha256='0'*64),status=400)
  self.c.put(self.t,'missing-key',headers={'Idempotency-Key':None},status=400)
  self.get('/resources?limit=101',status=400)
 def test_15_abort_and_expiry(self):
  u=self.c.begin(self.t,'aborted',b'ab')['upload_id'];self.c.part(self.t,u,1,b'a')
  self.mutate('POST','/uploads/'+u+'/abort');self.c.part(self.t,u,2,b'b',status=409)
  self.assertEqual(self.get('/uploads/'+u)['state'],'ABORTED');self.c.read(self.t,'aborted',status=404)
  v=self.c.begin(self.t,'expired',b'a',ttl_seconds=1)['upload_id'];self.c.part(self.t,v,1,b'a');time.sleep(1.2)
  self.assertEqual(self.get('/uploads/'+v)['state'],'EXPIRED');self.c.complete(self.t,v,[(1,b'a')],status=409)
 def test_16_concurrent_resource_creation(self):
  out=self.parallel(lambda:self.c.put(self.t,'r',b'a',status=(200,409)),lambda:self.c.put(self.t,'r',b'b',status=(200,409)))
  self.assertEqual(sum(x.get('state')=='READY' for x in out),1);self.assertIn(self.c.read(self.t,'r'),[b'a',b'b'])
  self.assertEqual(self.get('/usage')['ready_count'],1)
 def test_17_concurrent_same_idempotency_key(self):
  out=self.parallel(*[lambda:self.c.put(self.t,'r',b'concurrent',key='same') for _ in range(6)])
  self.assertTrue(all(x==out[0] for x in out));self.assertEqual(self.get('/usage')['ready_count'],1)
 def test_18_complete_delete_race(self):
  for i in range(4):
   r='r'+str(i);u=self.c.begin(self.t,r,b'a')['upload_id'];self.c.part(self.t,u,1,b'a')
   self.parallel(lambda:self.c.complete(self.t,u,[(1,b'a')],status=(200,409)),lambda:self.mutate('DELETE','/resources/'+r))
   self.c.read(self.t,r,status=404);self.c.complete(self.t,u,[(1,b'a')],status=409)
 def test_19_complete_close_race(self):
  u=self.c.begin(self.t,'r',b'a')['upload_id'];self.c.part(self.t,u,1,b'a')
  self.parallel(lambda:self.c.complete(self.t,u,[(1,b'a')],status=(200,409)),lambda:self.mutate('POST','/close'))
  self.c.read(self.t,'r',status=409);self.c.complete(self.t,u,[(1,b'a')],status=409)
  self.assertEqual(self.get('')['state'],'CLOSED')
 def test_20_abort_part_race(self):
  u=self.c.begin(self.t,'r',b'a')['upload_id']
  self.parallel(lambda:self.c.part(self.t,u,1,b'a',status=(200,409)),lambda:self.mutate('POST','/uploads/'+u+'/abort'))
  self.assertEqual(self.get('/uploads/'+u)['state'],'ABORTED');self.c.read(self.t,'r',status=404)
 def test_21_freeze_export_verify(self):
  self.c.put(self.t,'ready',b'document');self.c.put(self.t,'deleted',b'old');self.mutate('DELETE','/resources/deleted')
  u=self.c.begin(self.t,'pending',b'part-rest',ttl_seconds=1)['upload_id'];self.c.part(self.t,u,1,b'part-')
  self.mutate('POST','/freeze');time.sleep(1.2);a=self.get('/export');self.assertEqual(a,self.get('/export'))
  self.assertEqual(self.get('/uploads/'+u)['state'],'OPEN');self.c.put(self.t,'blocked',status=409)
  self.assertEqual({x['state'] for x in a['resources']},{'READY','DELETED','UPLOADING'})
  for b in a['blobs']:
   content=self.get('/export/blobs/'+b['sha256']);self.assertEqual((sha(content),len(content)),(b['sha256'],b['byte_length']))
  self.get('/export/blobs/'+'0'*64,status=404)
 def test_22_freeze_write_race(self):
  self.parallel(lambda:self.c.put(self.t,'r',status=(200,409)),lambda:self.mutate('POST','/freeze'))
  export=self.get('/export');self.assertEqual(export,self.get('/export'));self.c.put(self.t,'new',status=409)
 def test_23_pagination_usage(self):
  for n in ['c','a','b']:self.c.put(self.t,n,n.encode())
  page=self.get('/resources?limit=2');self.assertEqual([x['resource_id'] for x in page['items']],['a','b'])
  tail=self.get('/resources?limit=2&after='+page['next_after']);self.assertEqual([x['resource_id'] for x in tail['items']],['c']);self.assertIsNone(tail['next_after'])
  self.mutate('DELETE','/resources/b');self.assertEqual(self.get('/usage')['ready_bytes'],2)
 def test_24_markdown_repository_neutrality(self):
  # Application-owned mapping: paths and link syntax never enter the service API.
  attachment=b'optics notes from a Markdown repository';mapping={'papers/diagram.txt':'asset-7'}
  self.c.put(self.t,mapping['papers/diagram.txt'],attachment)
  markdown='See [diagram](papers/diagram.txt).';self.assertIn('papers/diagram.txt',markdown)
  self.assertEqual(self.c.read(self.t,mapping['papers/diagram.txt']),attachment)
  self.assertEqual(set(self.get('/resources/asset-7'))-{'tenant_id','resource_id','state','revision','media_type','byte_length','sha256','modified_at'},set())
 def test_25_complete_replay_after_delete(self):
  u=self.c.begin(self.t,'r',b'a')['upload_id'];self.c.part(self.t,u,1,b'a');r=self.c.complete(self.t,u,[(1,b'a')],key='complete')
  self.mutate('DELETE','/resources/r');self.assertEqual(self.c.complete(self.t,u,[(1,b'a')],key='complete'),r)
  self.c.read(self.t,'r',status=404)
 def test_26_media_and_request_shape(self):
  self.c.put(self.t,'json',b'{"message":"bytes"}',headers={'Content-Type':'application/json'})
  self.assertEqual(self.c.read(self.t,'json'),b'{"message":"bytes"}')
  self.c.put(self.t,'bad',headers={'Content-Type':'broken'},status=415)
  self.mutate('POST','/uploads',data=b'{}',status=415)
  self.mutate('POST','/freeze',body={},status=400)
 def test_27_exportable_fingerprint_vector(self):
  self.c.put(self.t,'vector',b'abc',key='vector',headers={'Content-Type':'text/plain'})
  frame={'method':'PUT','path':self.b+'/resources/vector','body':sha(b'abc'),'media_type':'text/plain','sha256':sha(b'abc')}
  expected=sha(json.dumps(frame,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
  self.assertEqual(self.get('/operations/vector')['request_fingerprint'],expected)
  # JSON whitespace/property order/integral number syntax are normalized.
  raw=b'{"sha256":"'+sha(b'').encode()+b'", "byte_length":0.0,"media_type":"text/plain","resource_id":"empty"}'
  first=self.mutate('POST','/uploads',data=raw,headers={'Content-Type':'application/json','X-Content-SHA256':None},key='json-key')
  second=self.mutate('POST','/uploads',body=dict(resource_id='empty',media_type='text/plain',byte_length=0,sha256=sha(b'')),key='json-key')
  self.assertEqual(first,second)
 def test_28_concurrent_complete(self):
  u=self.c.begin(self.t,'r',b'complete')['upload_id'];self.c.part(self.t,u,1,b'complete')
  out=self.parallel(*[lambda:self.c.complete(self.t,u,[(1,b'complete')],key='same') for _ in range(4)])
  self.assertTrue(all(x==out[0] for x in out));self.c.complete(self.t,u,[(1,b'complete')],key='different',status=409)
  self.assertEqual(self.get('/usage'),dict(ready_count=1,ready_bytes=8,open_uploads=0))
 def test_29_foreign_session_and_invalid_unicode(self):
  other=self.t+'-other';self.c.create(other);u=self.c.begin(other,'r',b'a')['upload_id']
  self.c.part(self.t,u,1,b'a',status=404);self.c.complete(self.t,u,[(1,b'a')],status=404)
  raw=b'{"resource_id":"bad","media_type":"text/\\ud800","byte_length":0,"sha256":"'+sha(b'').encode()+b'"}'
  self.mutate('POST','/uploads',data=raw,headers={'Content-Type':'application/json'},status=400)
  self.assertEqual(self.get('/usage')['open_uploads'],0)

class EvidenceResult(unittest.TextTestResult):
 def __init__(self,*args,**kw):super().__init__(*args,**kw);self.records=[]
 def addSuccess(self,test):super().addSuccess(test);self.records.append({'case':str(test),'status':'PASS'})
 def addFailure(self,test,err):super().addFailure(test,err);self.records.append({'case':str(test),'status':'FAIL','error':self._exc_info_to_string(err,test)})
 def addError(self,test,err):super().addError(test,err);self.records.append({'case':str(test),'status':'ERROR','error':self._exc_info_to_string(err,test)})
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contract))
 pathlib.Path(os.environ['NA_RESULT']).write_text(json.dumps(dict(success=result.wasSuccessful(),cases=result.records,requests=TRACE),indent=2)+'\n',encoding='utf-8')
 raise SystemExit(0 if result.wasSuccessful() else 1)
