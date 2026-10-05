# SPDX-License-Identifier: Apache-2.0
"""Black-box durability assertions. A deployment controller provides fault/lifecycle
operations; this module imports only the permissive HTTP client, never server code.
Controller methods are test orchestration, not a mandatory consumer SDK.
"""
import time
from .client import sha
def run(c,ctl):
 records=[];t='recovery-main';b='/v0/tenants/'+t;c.create(t)
 def record(name):records.append(dict(case=name,status='PASS'))
 def missing_operation(k):c.request('GET',b+'/operations/'+k,status=404)
 def lost(action,expected):
  try:action()
  except (OSError,EOFError):pass
  else:raise AssertionError('Expected lost response after process exit')
  ctl.wait_exit(expected);ctl.start()
 # Existing durable part state must survive an ordinary service restart.
 u=c.begin(t,'pending',b'prefix-suffix')['upload_id'];c.part(t,u,1,b'prefix-')
 ctl.stop();ctl.start();assert c.request('GET',b+'/uploads/'+u)['part_count']==1
 c.part(t,u,2,b'suffix');c.complete(t,u,[(1,b'prefix-'),(2,b'suffix')]);assert c.read(t,'pending')==b'prefix-suffix'
 record('R01_restart_preserves_parts_directory_and_operations')
 # Byte installation succeeded; SQL transaction has not committed. Real os._exit.
 u=c.begin(t,'crash-bytes',b'left-right')['upload_id'];c.part(t,u,1,b'left-');c.part(t,u,2,b'right')
 ctl.arm('after_bytes','complete-crash')
 lost(lambda:c.complete(t,u,[(1,b'left-'),(2,b'right')],key='complete-crash'),91)
 missing_operation('complete-crash');c.read(t,'crash-bytes',status=404)
 assert c.request('GET',b+'/uploads/'+u)['state']=='OPEN'
 assert not ctl.has_blob(t,sha(b'left-right'))
 c.complete(t,u,[(1,b'left-'),(2,b'right')],key='complete-crash');assert c.read(t,'crash-bytes')==b'left-right'
 record('R02_process_exit_after_bytes_before_catalog_complete_retry')
 # SQL commit succeeded; no HTTP response was sent. Independent read + key replay.
 ctl.arm('after_commit','commit-crash')
 lost(lambda:c.put(t,'commit-crash',b'committed content',key='commit-crash'),92)
 assert c.read(t,'commit-crash')==b'committed content'
 committed=c.request('GET',b+'/operations/commit-crash')['result']
 assert c.put(t,'commit-crash',b'committed content',key='commit-crash')==committed
 record('R03_process_exit_after_catalog_before_response_replay')
 # Recoverable catalog error after a real byte write.
 ctl.arm('before_commit','catalog-fail','unavailable')
 error=c.put(t,'catalog-fail',b'orphan after catalog failure',key='catalog-fail',status=503);assert error['retryable']
 ctl.clear_fault();missing_operation('catalog-fail');c.read(t,'catalog-fail',status=404)
 ctl.stop();ctl.start();assert not ctl.has_blob(t,sha(b'orphan after catalog failure'))
 c.put(t,'catalog-fail',b'orphan after catalog failure',key='catalog-fail');assert c.read(t,'catalog-fail')==b'orphan after catalog failure'
 record('R04_catalog_failure_reconciles_orphan_and_retries')
 # Durable tombstone while physical cleanup is unavailable, then restart/reclaim.
 c.put(t,'garbage',b'delete-unique');c.put(t,'shared-a',b'shared');c.put(t,'shared-b',b'shared')
 ctl.arm('cleanup',None,'unavailable');c.request('DELETE',b+'/resources/garbage');c.request('DELETE',b+'/resources/shared-a')
 c.read(t,'garbage',status=404);assert ctl.has_blob(t,sha(b'delete-unique'))
 ctl.stop();ctl.clear_fault();ctl.start();c.read(t,'garbage',status=404)
 assert not ctl.has_blob(t,sha(b'delete-unique'));assert c.read(t,'shared-b')==b'shared'
 record('R05_delete_cleanup_failure_restart_and_shared_byte_retention')
 # Current service credential policy applies to reads and operation replay.
 ctl.revoke(True)
 try:
  c.read(t,'commit-crash',status=401);c.put(t,'commit-crash',b'committed content',key='commit-crash',status=401)
 finally:ctl.revoke(False)
 record('R06_policy_revocation_precedes_operation_replay')
 # Detect physical integrity failure through an independent HTTP read.
 c.put(t,'integrity',b'full-integrity');ctl.corrupt(t,sha(b'full-integrity'))
 try:
  assert c.read(t,'integrity',status=503)['code']=='integrity_error'
  assert c.put(t,'integrity-new',b'full-integrity',status=503)['code']=='integrity_error'
  c.request('GET',b+'/resources/integrity-new',status=404)
 finally:ctl.repair()
 assert c.read(t,'integrity')==b'full-integrity'
 record('R07_corrupt_bytes_fail_closed_without_false_commit')
 # Coordinated whole-instance restore into a fresh directory, including pending op.
 u=c.begin(t,'backup-pending',b'one-two')['upload_id'];c.part(t,u,1,b'one-')
 before=c.request('GET',b+'/usage');ctl.backup_restore()
 assert c.request('GET',b+'/usage')==before;assert c.read(t,'commit-crash')==b'committed content';c.read(t,'garbage',status=404)
 assert c.put(t,'commit-crash',b'committed content',key='commit-crash')==committed
 assert c.request('GET',b+'/uploads/'+u)['part_count']==1
 c.part(t,u,2,b'two');c.complete(t,u,[(1,b'one-'),(2,b'two')]);assert c.read(t,'backup-pending')==b'one-two'
 record('R08_coordinated_restore_new_instance_with_pending_upload')
 # Every multipart mutation can lose its response independently after commit.
 begin=dict(resource_id='response-loss',media_type='text/plain',byte_length=5,sha256=sha(b'parts'))
 ctl.arm('after_commit','lost-begin')
 lost(lambda:c.request('POST',b+'/uploads',body=begin,key='lost-begin'),92)
 saved=c.request('GET',b+'/operations/lost-begin')['result']
 assert c.request('POST',b+'/uploads',body=begin,key='lost-begin')==saved;u=saved['upload_id']
 ctl.arm('after_commit','lost-part');lost(lambda:c.part(t,u,1,b'parts',key='lost-part'),92)
 saved=c.request('GET',b+'/operations/lost-part')['result'];assert c.part(t,u,1,b'parts',key='lost-part')==saved
 ctl.arm('after_commit','lost-complete');lost(lambda:c.complete(t,u,[(1,b'parts')],key='lost-complete'),92)
 saved=c.request('GET',b+'/operations/lost-complete')['result'];assert c.complete(t,u,[(1,b'parts')],key='lost-complete')==saved
 assert c.read(t,'response-loss')==b'parts'
 record('R10_begin_part_complete_response_loss_restart_replay')
 # Verify actual reclamation of aborted and expired parts after service restart.
 u=c.begin(t,'abort-reclaim',b'abandoned')['upload_id'];c.part(t,u,1,b'abandoned');c.request('POST',b+'/uploads/'+u+'/abort')
 v=c.begin(t,'expire-reclaim',b'expire-me',ttl_seconds=1)['upload_id'];c.part(t,v,1,b'expire-me');time.sleep(1.2)
 ctl.stop();ctl.start();assert c.request('GET',b+'/uploads/'+v)['state']=='EXPIRED'
 assert not ctl.has_blob(t,sha(b'abandoned'));assert not ctl.has_blob(t,sha(b'expire-me'))
 record('R11_abort_expiry_restart_reclaims_parts')
 # Freeze + verified export forms the source half of an eventual epoch handoff.
 c.request('POST',b+'/freeze');export=c.request('GET',b+'/export')
 for descriptor in export['blobs']:
  data=c.request('GET',b+'/export/blobs/'+descriptor['sha256'])
  assert (sha(data),len(data))==(descriptor['sha256'],descriptor['byte_length'])
 assert c.request('GET',b+'/export')==export
 c.put(t,'post-freeze',status=409);c.request('GET',b+'/usage',epoch=2,status=409)
 record('R09_frozen_export_all_blob_hashes_and_stale_epoch_fencing')
 return records
