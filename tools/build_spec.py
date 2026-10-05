# SPDX-License-Identifier: Apache-2.0
"""Build the normative JSON artifacts from maintained contract definitions."""
import json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
ID={'type':'string','pattern':'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'}
HASH={'type':'string','pattern':'^[a-f0-9]{64}$'}
TIME={'type':'string','format':'date-time'}
def ref(n):return {'$ref':'#/$defs/'+n}
def obj(props,required=None):return {'type':'object','properties':props,'required':list(props) if required is None else required,'additionalProperties':False}
defs={
 'TenantCreate':obj({'epoch':{'type':'integer','minimum':1}}),
 'Tenant':obj({'tenant_id':ID,'epoch':{'type':'integer','minimum':1},'state':{'enum':['ACTIVE','FROZEN','CLOSED']}}),
 'Begin':obj({'resource_id':ID,'media_type':{'type':'string','minLength':1,'maxLength':255,'pattern':'^[^\\r\\n]+/[^\\r\\n]+$'},'byte_length':{'type':'integer','minimum':0,'maximum':67108864},'sha256':HASH,'ttl_seconds':{'type':'integer','minimum':1,'maximum':86400}},['resource_id','media_type','byte_length','sha256']),
 'PartRef':obj({'number':{'type':'integer','minimum':1,'maximum':256},'sha256':HASH}),
 'Complete':obj({'parts':{'type':'array','minItems':0,'maxItems':256,'items':ref('PartRef')}}),
 'Resource':obj({'tenant_id':ID,'resource_id':ID,'state':{'enum':['UPLOADING','READY','DELETED']},'revision':{'type':'integer','minimum':0},'media_type':{'type':'string'},'byte_length':{'type':'integer','minimum':0},'sha256':HASH,'modified_at':TIME}),
 'Session':obj({'tenant_id':ID,'upload_id':ID,'resource_id':ID,'state':{'enum':['OPEN','COMPLETED','ABORTED','EXPIRED']},'expires_at':TIME,'part_count':{'type':'integer','minimum':0}}),
 'Part':obj({'number':{'type':'integer','minimum':1},'sha256':HASH,'byte_length':{'type':'integer','minimum':0}}),
 'ResourceList':obj({'items':{'type':'array','items':ref('Resource')},'next_after':{'type':['string','null']}}),
 'Usage':obj({'ready_count':{'type':'integer','minimum':0},'ready_bytes':{'type':'integer','minimum':0},'open_uploads':{'type':'integer','minimum':0}}),
 'Operation':obj({'operation_id':ID,'state':{'const':'SUCCEEDED'},'status':{'const':200},'result':{'oneOf':[ref('Tenant'),ref('Resource'),ref('Session'),ref('Part')]},'request_fingerprint':HASH}),
 'Error':obj({'code':{'enum':['unauthenticated','denied','missing','invalid','conflict','unsupported','unavailable','integrity_error']},'message':{'type':'string'},'retryable':{'type':'boolean'}}),
 'Capabilities':obj({'contract_version':{'const':'0.2.0'},'features':{'type':'array','items':{'type':'string'}},'max_resource_bytes':{'type':'integer'},'max_part_bytes':{'type':'integer'},'max_parts':{'type':'integer'},'authorization_profile':{'const':'trusted-service-bearer'}}),
 'ExportBlob':obj({'sha256':HASH,'byte_length':{'type':'integer','minimum':0}}),
 'ExportSession':obj({'session':ref('Session'),'parts':{'type':'array','items':ref('Part')}}),
}
defs['Export']=obj({'contract_version':{'const':'0.2.0'},'tenant':ref('Tenant'),'resources':{'type':'array','items':ref('Resource')},'uploads':{'type':'array','items':ref('ExportSession')},'operations':{'type':'array','items':ref('Operation')},'blobs':{'type':'array','items':ref('ExportBlob')}})
for name in ['Begin','Resource']:
 for field in ['byte_length','sha256']:
  defs[name]['properties'][field]={'anyOf':[defs[name]['properties'][field],{'type':'null'}]}
defs['Resource']['allOf']=[{'if':{'properties':{'state':{'const':'READY'}}},'then':{'properties':{'byte_length':{'type':'integer','minimum':0},'sha256':HASH}}}]
for name in set(defs)-{'TenantCreate','Begin','PartRef','Complete'}:defs[name]['additionalProperties']=True
schemas={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'https://neutral-attachment.invalid/schema/0.2.0','title':'Neutral Attachment v0','x-spdx-license-identifier':'Apache-2.0','$defs':defs}
paths={}
def param(name,loc,schema,required=True):return {'name':name,'in':loc,'required':required,'schema':schema}
def add(path,method,op,response,request=None,binary=False,admin=False,extra=None):
 ps=[param(n,'path',{'type':'integer','minimum':1,'maximum':256} if n=='number' else HASH if n=='digest' else ID) for n in ['tenant','resource','upload','number','operation','digest'] if '{'+n+'}' in path]
 ps += [param('X-Contract-Version','header',{'type':'string','default':'0.2.0'},False),param('X-Required-Capabilities','header',{'type':'string'},False)]
 if '{tenant}' in path:ps.append(param('X-Tenant-Epoch','header',{'type':'integer','minimum':1}))
 if method in ['put','post','delete']:ps += [param('Idempotency-Key','header',ID),param('Content-Length','header',{'type':'integer','minimum':0})]
 if binary:ps.append(param('X-Content-SHA256','header',HASH))
 ps+=extra or []
 content={'*/*' if op=='readResource' else 'application/octet-stream':{}} if response=='Bytes' else {'application/json':{'schema':{'$ref':'#/components/schemas/'+response}}}
 d={'operationId':op,'summary':op,'x-permission':'admin' if admin else 'read' if method=='get' else 'write','parameters':ps,
    'responses':{'200':{'description':'Committed result or authorized read. See spec/semantics.md.','content':content},'default':{'description':'Structured error; mutations have no committed success except unknown-outcome unavailable.','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}}}}
 if request:d['requestBody']={'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/'+request}}}}
 if binary:d['requestBody']={'required':True,'content':{'*/*' if op=='putResource' else 'application/octet-stream':{'schema':{'description':'Raw bytes; Content-Type is the resource media type for single Put.'}}}}
 paths.setdefault(path,{})[method]=d
base='/v0/tenants/{tenant}'
add('/v0/capabilities','get','getCapabilities','Capabilities')
add(base,'put','createTenant','Tenant','TenantCreate',admin=True);add(base,'get','getTenant','Tenant')
for action in ['freeze','close']:add(base+'/'+action,'post',action+'Tenant','Tenant',admin=True)
add(base+'/resources','get','listResources','ResourceList',extra=[param('after','query',ID,False),param('limit','query',{'type':'integer','minimum':1,'maximum':100},False)])
add(base+'/usage','get','getUsage','Usage')
r=base+'/resources/{resource}'
add(r,'put','putResource','Resource',binary=True);add(r,'get','getResource','Resource');add(r,'delete','deleteResource','Resource');add(r+'/content','get','readResource','Bytes')
add(base+'/uploads','post','beginUpload','Session','Begin')
u=base+'/uploads/{upload}'
add(u,'get','getUpload','Session');add(u+'/parts/{number}','put','putPart','Part',binary=True);add(u+'/complete','post','completeUpload','Resource','Complete');add(u+'/abort','post','abortUpload','Session')
add(base+'/operations/{operation}','get','getOperation','Operation')
add(base+'/export','get','exportTenant','Export',admin=True);add(base+'/export/blobs/{digest}','get','readExportBlob','Bytes',admin=True)
def oas_refs(value):
 if isinstance(value,dict):return {k:(v.replace('#/$defs/','#/components/schemas/') if k=='$ref' else oas_refs(v)) for k,v in value.items()}
 if isinstance(value,list):return [oas_refs(v) for v in value]
 return value
api={'openapi':'3.1.1','info':{'title':'Neutral Attachment Resource Lifecycle','version':'0.2.0','license':{'name':'Apache License 2.0','identifier':'Apache-2.0'},'description':'Normative semantics: spec/semantics.md. Independent implementations use HTTP and schema assets; no implementation SDK dependency.'},'jsonSchemaDialect':'https://json-schema.org/draft/2020-12/schema','security':[{'serviceBearer':[]}],'paths':paths,'components':{'securitySchemes':{'serviceBearer':{'type':'http','scheme':'bearer','description':'Trusted service credential; binding to tenant/action/audience is required. End-user authorization remains at the adapter.'}},'schemas':oas_refs(defs)}}
for name,data in [('schemas.json',schemas),('openapi.json',api)]:
 (ROOT/'spec'/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')
print('schemas',len(defs),'operations',sum(map(len,paths.values())))
