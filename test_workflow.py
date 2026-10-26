import asyncio,copy,json,tempfile,unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import batch_configure as b
import batch_workflow as w
import test_batch as fixtures
ADDR,PROFILE,BASE=fixtures.ADDR,fixtures.PROFILE,fixtures.BASE

class DelayedVerificationTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.TransactionTests.asyncSetUp
 asyncTearDown=fixtures.TransactionTests.asyncTearDown
 async def test_reconnect_timeout_does_not_rollback_or_rewrite(self):
  with patch.object(b,'get_snapshot',AsyncMock(side_effect=TimeoutError('radio busy'))):
   self.assertEqual(await b.provision(self.store,ADDR,b'password',PROFILE),'pending_verify')
  self.assertEqual(self.calls,1)
  self.assertEqual(self.store.data['tags'][ADDR]['immediate'],self.device)
  self.assertEqual(await b.provision(self.store,ADDR,b'password',PROFILE),'skipped_verified')
  self.assertEqual(self.calls,1)
 async def test_repeated_pending_timeout_stays_pending(self):
  target=b.target_snapshot(self.original,PROFILE)
  self.store.put(ADDR,{'status':'pending_verify','before':self.original,'original':self.original,'target':target})
  with patch.object(b,'get_snapshot',AsyncMock(side_effect=TimeoutError())):
   self.assertEqual(await b.provision(self.store,ADDR,b'password',PROFILE),'pending_verify')
  self.assertEqual(self.calls,0)
 async def test_immediate_mismatch_restores_baseline(self):
  real=b.snapshot;calls=0
  async def inconsistent(c):
   nonlocal calls
   calls+=1
   result=await real(c)
   if calls==2:result['slots'][0]['tx_dbm']=-4
   return result
  with patch.object(b,'snapshot',inconsistent):
   self.assertEqual(await b.provision(self.store,ADDR,b'password',PROFILE),'failed')
  self.assertEqual(self.device,self.original)
  self.assertTrue(self.store.data['tags'][ADDR]['rollback_verified'])
 async def test_cancelled_verification_keeps_durable_pending(self):
  with patch.object(b,'get_snapshot',AsyncMock(side_effect=asyncio.CancelledError())):
   with self.assertRaises(asyncio.CancelledError):await b.provision(self.store,ADDR,b'password',PROFILE)
  self.assertEqual(self.store.data['tags'][ADDR]['status'],'pending_verify')
  self.assertEqual(self.calls,1)
 async def test_confirmed_reconnect_mismatch_rolls_back(self):
  original=b.get_snapshot;calls=0
  async def mismatch(*args):
   nonlocal calls
   calls+=1
   value=await original(*args)
   if calls==1:value['slots'][0]['tx_dbm']=-4
   return value
  with patch.object(b,'get_snapshot',mismatch):
   self.assertEqual(await b.provision(self.store,ADDR,b'password',PROFILE),'failed')
  self.assertEqual(self.device,self.original)

class QueueTests(unittest.IsolatedAsyncioTestCase):
 async def test_continue_then_retry_pending_without_rechecking_success(self):
  with tempfile.TemporaryDirectory() as td:
   args=SimpleNamespace(state=str(Path(td)/'state.json'),retries=1,retry_delay=30,failure_limit=3,recheck=False)
   addresses=[ADDR,'C3:00:00:00:00:21'];calls=[]
   async def provision(store,a,*args):
    calls.append(a);status='pending_verify' if len(calls)==1 else 'verified'
    store.put(a,{'status':status});return status
   with patch.object(b,'provision',provision),patch.object(w.asyncio,'sleep',AsyncMock()) as sleep:
    self.assertEqual(await w.queued_apply(args,b'password',PROFILE,addresses),0)
   self.assertEqual(calls,[addresses[0],addresses[1],addresses[0]])
   sleep.assert_awaited_once_with(30)
 async def test_circuit_pause_reports_unattempted_and_nonzero_exit(self):
  with tempfile.TemporaryDirectory() as td:
   args=SimpleNamespace(state=str(Path(td)/'state.json'),retries=2,retry_delay=30,failure_limit=2,recheck=False)
   addresses=[ADDR,'C3:00:00:00:00:21','C3:00:00:00:00:24']
   async def fail(store,a,*args):
    store.put(a,{'status':'failed','retryable':True,'error':'TimeoutError:'});return 'failed'
   with patch.object(b,'provision',AsyncMock(side_effect=fail)) as call:
    self.assertEqual(await w.queued_apply(args,b'password',PROFILE,addresses),1)
   self.assertEqual(call.await_count,2)
   report=json.loads(Path(args.state).with_suffix('.progress.json').read_text())
   self.assertTrue(report['paused_for_connections']);self.assertEqual(report['counts']['not_attempted'],1)
 async def test_validation_failure_is_not_automatically_retried(self):
  with tempfile.TemporaryDirectory() as td:
   args=SimpleNamespace(state=str(Path(td)/'state.json'),retries=2,retry_delay=30,failure_limit=3,recheck=False)
   async def fail(store,a,*args):
    store.put(a,{'status':'failed','retryable':False});return 'failed'
   with patch.object(b,'provision',AsyncMock(side_effect=fail)) as call:
    self.assertEqual(await w.queued_apply(args,b'password',PROFILE,[ADDR]),1)
   self.assertEqual(call.await_count,1)
 async def test_recovery_timeout_never_calls_provision(self):
  with tempfile.TemporaryDirectory() as td:
   args=SimpleNamespace(state=str(Path(td)/'state.json'),address=ADDR,startup_seconds=0.001)
   with b.Store(args.state,PROFILE) as s:s.put(ADDR,{'status':'failed'})
   class Scanner:
    def __init__(self,**kwargs):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
   with patch.object(w,'BleakScanner',Scanner),patch.object(b,'provision',AsyncMock()) as p:
    self.assertEqual(await w.recover(args,b'password',PROFILE,[ADDR]),1)
   p.assert_not_called()
 async def test_recovery_targets_only_selected_mac(self):
  with tempfile.TemporaryDirectory() as td:
   args=SimpleNamespace(state=str(Path(td)/'state.json'),address=ADDR,startup_seconds=1)
   with b.Store(args.state,PROFILE) as s:s.put(ADDR,{'status':'failed'})
   class Scanner:
    def __init__(self,detection_callback):self.cb=detection_callback
    async def __aenter__(self):
     self.cb(SimpleNamespace(address=ADDR),SimpleNamespace(platform_data=(None,[SimpleNamespace(is_connectable=True),None])));return self
    async def __aexit__(self,*args):pass
   async def ok(store,a,*args):store.put(a,{'status':'verified'});return 'applied_verified'
   with patch.object(w,'BleakScanner',Scanner),patch.object(b,'provision',AsyncMock(side_effect=ok)) as p:
    self.assertEqual(await w.recover(args,b'password',PROFILE,[ADDR,'C3:00:00:00:00:21']),0)
   self.assertEqual(p.await_count,1);self.assertEqual(p.await_args.args[1],ADDR)
class RetryLimitsTests(unittest.IsolatedAsyncioTestCase):
 async def test_exhausted_pending_queue_is_not_success(self):
  with tempfile.TemporaryDirectory() as td:
   args=SimpleNamespace(state=str(Path(td)/'s.json'),retries=1,retry_delay=30,failure_limit=3,recheck=False)
   async def pending(store,a,*args):store.put(a,{'status':'pending_verify'});return 'pending_verify'
   with patch.object(b,'provision',AsyncMock(side_effect=pending)) as call,patch.object(w.asyncio,'sleep',AsyncMock()):
    self.assertEqual(await w.queued_apply(args,b'password',PROFILE,[ADDR]),1)
   self.assertEqual(call.await_count,2)
   self.assertEqual(json.loads(Path(args.state).read_text())['tags'][ADDR]['status'],'pending_verify')
 async def test_discovery_windows_increase_on_read_retries(self):
  windows=[]
  @asynccontextmanager
  async def unavailable(address,password,seconds):
   windows.append(seconds)
   raise TimeoutError()
   yield
  with patch.object(b,'session',unavailable),patch.object(b.asyncio,'sleep',AsyncMock()):
   with self.assertRaises(TimeoutError):await b.get_snapshot(ADDR,b'password')
  self.assertEqual(windows,[15,30,60])

class RadioLockTests(unittest.TestCase):
 def test_second_job_is_blocked_and_lock_is_released(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'radio.lock'
   with b.radio_lock(path):
    with self.assertRaisesRegex(RuntimeError,'Another Bluetooth'): 
     with b.radio_lock(path):pass
   with b.radio_lock(path):pass
if __name__=='__main__':unittest.main()

