import copy,json,tempfile,unittest
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import patch
import batch_configure as batch
import profiles
ROOT=Path(__file__).resolve().parent
BASE=json.loads((ROOT/'tests_data/baseline.json').read_text())
PROFILE=json.loads((ROOT/'profile.json').read_text(encoding='utf-8-sig'))
ADDR='C3:00:00:00:00:40'

class ProfileTests(unittest.TestCase):
 def test_enables_second_slot_and_preserves_identity(self):
  before=copy.deepcopy(BASE); before['slots'][1].update(frame_hex='ffff',interval_ms=0,tx_dbm=0)
  target=profiles.target_snapshot(before,PROFILE)
  self.assertEqual(target['slots'][1]['frame_hex'],'a103')
  self.assertEqual(target['slots'][1]['interval_ms'],100)
  self.assertEqual(target['slots'][1]['tx_dbm'],-4)
  self.assertEqual(target['slots'][0]['frame_hex'],before['slots'][0]['frame_hex'])
  self.assertEqual(target['slots'][2:],before['slots'][2:])
 def test_invalid_profiles_fail_before_writes(self):
  for rule in [{'slot':0},{'slot':7},{'slot':True},{'slot':2,'type':'acc'},{'slot':1,'interval_ms':1},{'slot':1,'tx_dbm':3},{'slot':1,'typo':2}]:
   with self.subTest(rule=rule),self.assertRaises(ValueError): profiles.validate_profile({'version':1,'slots':[rule]})
  with self.assertRaises(ValueError): profiles.validate_profile({'version':1,'slots':[{'slot':1},{'slot':1}]})
 def test_refuses_overwriting_other_frame(self):
  p={'version':1,'slots':[{'slot':1,'type':'acc','interval_ms':100,'tx_dbm':0}]}
  with self.assertRaises(ValueError): profiles.target_snapshot(BASE,p)
 def test_profile_hash_tracks_settings_not_name(self):
  a=copy.deepcopy(PROFILE); a['name']='different'; a['slots'].reverse()
  self.assertEqual(profiles.profile_hash(a),profiles.profile_hash(PROFILE))
  a['slots'][0]['tx_dbm']=4
  self.assertNotEqual(profiles.profile_hash(a),profiles.profile_hash(PROFILE))
 def test_inventory_explicit_selection_duplicates(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'tags.csv'; p.write_text('mac,enabled\nC30000000040,yes\nnot-a-mac,no\n')
   self.assertEqual(batch.inventory(p),[ADDR])
   p.write_text('mac,enabled\nC30000000040,yes\nC3:00:00:00:00:40,yes\n')
   with self.assertRaises(ValueError): batch.inventory(p)
 def test_state_profile_mismatch_and_lock(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'state.json'
   with batch.Store(path,PROFILE) as store:
    store.put(ADDR,{'status':'verified'})
    with self.assertRaises(RuntimeError):
     with batch.Store(path,PROFILE): pass
   p=copy.deepcopy(PROFILE);p['slots'][1]['tx_dbm']=4
   with self.assertRaises(ValueError):
    with batch.Store(path,p):pass

class TransactionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.device=copy.deepcopy(BASE)
  self.device['slots'][1].update(frame_hex='a103',interval_ms=1000,tx_dbm=0)
  self.original=copy.deepcopy(self.device);self.fail_write=False;self.fail_restore=False;self.calls=0
  @asynccontextmanager
  async def fake_session(*args):yield object()
  async def fake_snapshot(c):return copy.deepcopy(self.device)
  async def fake_write(c,current,target):
   self.calls+=1
   if self.fail_write:
    self.fail_write=False;self.device['slots'][1]['interval_ms']=100
    raise OSError('simulated disconnect after partial frame write')
   if self.fail_restore:raise OSError('simulated device unavailable')
   self.device=copy.deepcopy(target)
  self.patches=[patch.object(batch,'session',fake_session),patch.object(batch,'snapshot',fake_snapshot),patch.object(batch,'write_snapshot',fake_write)]
  for p in self.patches:p.start()
  self.store=batch.Store(Path(self.temp.name)/'state.json',PROFILE).__enter__()
 async def asyncTearDown(self):
  self.store.__exit__(None,None,None)
  for p in self.patches:p.stop()
  self.temp.cleanup()
 async def test_apply_verify_resume_and_rollback(self):
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'applied_verified')
  self.assertEqual(self.device,profiles.target_snapshot(self.original,PROFILE))
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'skipped_verified')
  self.assertEqual(self.calls,1)
  self.assertEqual(await batch.rollback(self.store,ADDR,b'password'),'rolled_back')
  self.assertEqual(self.device,self.original)
 async def test_empty_acc_activation_and_rollback_preserve_access(self):
  self.device['slots'][1].update(frame_hex='ffff',interval_ms=0,tx_dbm=0)
  self.device['connection_modes']=[0,3,3,3,3,3]
  original=copy.deepcopy(self.device)
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'applied_verified')
  self.assertEqual(await batch.rollback(self.store,ADDR,b'password'),'rolled_back')
  self.assertEqual(self.device,original)
 async def test_zero_baseline_refused_without_mutation(self):
  self.device['slots'][1]['interval_ms']=0
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'failed')
  self.assertEqual(self.calls,0)
 async def test_partial_write_rolls_back(self):
  self.fail_write=True
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'failed')
  self.assertEqual(self.device,self.original)
  self.assertTrue(self.store.data['tags'][ADDR]['rollback_verified'])
 async def test_failure_to_restore_blocks_progress(self):
  self.fail_write=True;self.fail_restore=True
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'needs_recovery')
  self.fail_restore=False
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'applied_verified')
 async def test_interrupted_complete_write_is_verified_on_resume(self):
  target=profiles.target_snapshot(self.original,PROFILE)
  self.store.put(ADDR,{'status':'applying','original':self.original,'before':self.original,'target':target})
  self.device=copy.deepcopy(target)
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'skipped_verified')
  self.assertEqual(self.calls,0)
 async def test_interrupted_partial_write_recovers_then_applies(self):
  target=profiles.target_snapshot(self.original,PROFILE)
  self.store.put(ADDR,{'status':'applying','original':self.original,'before':self.original,'target':target})
  self.device['slots'][1]['interval_ms']=100
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE),'applied_verified')
  self.assertEqual(self.device,target)
 async def test_recheck_detects_changed_tag(self):
  await batch.provision(self.store,ADDR,b'password',PROFILE)
  self.device['slots'][1]['tx_dbm']=0
  self.assertEqual(await batch.provision(self.store,ADDR,b'password',PROFILE,True),'applied_verified')
 async def test_interrupted_rollback_uses_original_target(self):
  target=profiles.target_snapshot(self.original,PROFILE)
  record={'status':'restoring','original':self.original,'before':target,'target':target,'restore_target':self.original}
  self.device=copy.deepcopy(target);self.store.put(ADDR,record)
  await batch.reconcile(self.store,ADDR,b'password',record)
  self.assertEqual(self.device,self.original)
  self.assertEqual(record['status'],'rolled_back')

class EncodingTests(unittest.IsolatedAsyncioTestCase):
 async def test_acc_writes_sdk_header_and_big_endian_interval(self):
  before=copy.deepcopy(BASE);before['slots'][1].update(frame_hex='ffff',interval_ms=0,tx_dbm=0)
  target=profiles.target_snapshot(before,PROFILE);writes=[]
  class Client:
   async def write_gatt_char(self,u,data,response):writes.append((u,data,response))
  async def select(c,s):self.assertEqual(s,1)
  async def read(c,u):return bytes.fromhex('a103')
  with patch.object(profiles,'select',select),patch.object(profiles,'read',read):
   await profiles.write_snapshot(Client(),before,target)
  self.assertIn((profiles.uuid('0a'),bytes.fromhex('0000a103'),True),writes)
  self.assertIn((profiles.uuid('03'),bytes.fromhex('0064'),True),writes)
  self.assertIn((profiles.uuid('04'),bytes.fromhex('fc'),True),writes)
class SafetyTests(unittest.IsolatedAsyncioTestCase):
 async def test_disable_preserves_interval(self):
  target=profiles.target_snapshot(BASE,{'version':1,'slots':[{'slot':2,'type':'disabled'}]})
  self.assertEqual(target['slots'][1]['interval_ms'],0)
  self.assertEqual(target['slots'][1]['frame_hex'],'ffff')
  self.assertEqual(target['connection_modes'][0],0)
 async def test_disable_without_access_rejected(self):
  target=profiles.target_snapshot(BASE,{'version':1,'slots':[{'slot':2,'type':'disabled'}]})
  target['connection_modes']=[3]*6
  with self.assertRaisesRegex(ValueError,'no active connectable slot'):profiles.validate_transition(BASE,target)
 async def test_zero_write_rejected_before_any_write(self):
  target=copy.deepcopy(BASE);target['slots'][0]['tx_dbm']=4;target['slots'][1]['interval_ms']=0
  from unittest.mock import AsyncMock
  writer=AsyncMock();selector=AsyncMock()
  with patch.object(profiles,'write',writer),patch.object(profiles,'select',selector):
   with self.assertRaises(ValueError):await profiles.write_snapshot(object(),BASE,target)
  writer.assert_not_called();selector.assert_not_called()
class BatchStopTests(unittest.IsolatedAsyncioTestCase):
 async def test_failed_restored_write_stops_without_retry_or_next_tag(self):
  from types import SimpleNamespace
  from unittest.mock import AsyncMock
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);inv=root/'tags.csv';inv.write_text('mac,enabled\nC30000000040,yes\nC3000000004E,yes\n')
   profile=root/'profile.json';profile.write_text(json.dumps(PROFILE))
   args=SimpleNamespace(command='apply',inventory=str(inv),profile=str(profile),state=str(root/'state.json'),retries=2,recheck=False)
   async def fail(store,address,*args):
    store.put(address,{'status':'failed','rollback_verified':True,'error':'injected verification failure'})
    return 'failed'
   mocked=AsyncMock(side_effect=fail)
   with patch.object(batch,'provision',mocked):self.assertEqual(await batch.run(args,b'password'),1)
   self.assertEqual(mocked.await_count,1)
   self.assertEqual(mocked.await_args.args[1],ADDR)
class ConnectionWriteTests(unittest.IsolatedAsyncioTestCase):
 async def test_bridge_precedes_disable_and_no_power_write_after_disable(self):
  target=profiles.target_snapshot(BASE,{'version':1,'slots':[{'slot':2,'type':'disabled'}]})
  target['slots'][1]['tx_dbm']=0
  events=[]
  async def modes(c,v):events.append(('modes',v))
  async def select(c,v):events.append(('select',v))
  async def write(c,u,v):events.append((u[-30:],v))
  async def read(c,u):return bytes.fromhex('ffff')
  with patch.object(profiles,'write_modes',modes),patch.object(profiles,'select',select),patch.object(profiles,'write',write),patch.object(profiles,'read',read):
   await profiles.write_snapshot(object(),BASE,target)
  self.assertEqual(events[0][0],'modes')
  data=[v for k,v in events if k not in ('modes','select')]
  self.assertEqual(data,[b'\x00',bytes.fromhex('0000ffff'),b'\x00\x00'])
class ReadRetryTests(unittest.IsolatedAsyncioTestCase):
 async def test_read_retries_transient_discovery_only(self):
  from unittest.mock import AsyncMock
  attempts=[]
  @asynccontextmanager
  async def flaky(*args):
   attempts.append(1)
   if len(attempts)<3:raise RuntimeError('No connectable advertisement found for this tag')
   yield object()
  with patch.object(batch,'session',flaky),patch.object(batch,'snapshot',AsyncMock(return_value=BASE)),patch.object(batch.asyncio,'sleep',AsyncMock()):
   self.assertEqual(await batch.get_snapshot(ADDR,b'password'),BASE)
  self.assertEqual(len(attempts),3)
 async def test_identity_errors_are_not_retried(self):
  attempts=[]
  @asynccontextmanager
  async def wrong(*args):
   attempts.append(1);raise RuntimeError('Challenge identity mismatch');yield
  with patch.object(batch,'session',wrong):
   with self.assertRaisesRegex(RuntimeError,'identity mismatch'):await batch.get_snapshot(ADDR,b'password')
  self.assertEqual(len(attempts),1)
if __name__=='__main__':unittest.main()




