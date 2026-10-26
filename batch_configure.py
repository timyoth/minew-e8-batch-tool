"""Batch provisioning for Minew Beacon Plus-BL 2.3.17 on Windows."""
import argparse,asyncio,csv,getpass,json,os,sys,time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from bleak import BleakScanner
from configure_tag import session,snapshot
from inspect_tags import mac
from profiles import validate_profile,profile_hash,target_snapshot,write_snapshot,differences,validate_transition
ROOT=Path(__file__).resolve().parent

def now(): return datetime.now(timezone.utc).isoformat()
def atomic_json(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(value,f,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(temp,path)

def inventory(path):
    addresses=[]; seen=set()
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        rows=csv.DictReader(f)
        if not {'mac','enabled'} <= set(rows.fieldnames or []): raise ValueError('Inventory requires mac and enabled columns')
        for line,row in enumerate(rows,2):
            flag=(row.get('enabled') or '').strip().lower()
            if flag not in ('yes','true','1','no','false','0',''): raise ValueError(f'Line {line}: enabled must be yes/no')
            if flag in ('no','false','0',''): continue
            address=mac((row.get('mac') or '').strip())
            if address in seen: raise ValueError(f'Duplicate enabled MAC: {address}')
            seen.add(address); addresses.append(address)
    if not addresses: raise ValueError('No enabled tags. Mark intended inventory rows enabled=yes.')
    return addresses

def validate_paths(args):
    """Reject output/input collisions before opening Bluetooth or a ledger."""
    if args.command in ('scan','doctor'):
        return
    inputs={Path(args.inventory).resolve(),Path(args.profile).resolve()}
    if args.command=='plan':
        output=Path(args.output).resolve()
        outputs=[output,output.with_name(output.name+'.tmp')]
    else:
        state=Path(args.state).resolve()
        if state.suffix.lower()!='.json':
            raise ValueError('--state must end in .json; CSV is reserved for the status export')
        outputs=[state,state.with_suffix('.csv'),state.with_suffix('.progress.json'),
                 state.with_suffix(state.suffix+'.lock'),state.with_name(state.name+'.tmp')]
    if any(path in inputs for path in outputs):
        raise ValueError('Output files must not overwrite the inventory or profile; choose a different output/state filename')
    if args.command=='plan' and Path(args.output).exists():
        raise ValueError('Plan output already exists; choose another --output path')

def doctor():
    """Local readiness checks only: no scan, connection or tag writes."""
    from configure_tag import load_bootstrap_key
    from importlib.metadata import version
    print('Python:',sys.version.split()[0])
    print('Platform:',sys.platform)
    for package in ('bleak','cryptography'):
        print(f'{package}: {version(package)}')
    if sys.platform!='win32':
        raise RuntimeError('This tool requires Windows')
    load_bootstrap_key()
    print('Bootstrap file: valid length (16 bytes); contents not displayed')
    print('Local prerequisites passed. Bluetooth availability, password and tag compatibility still require a live connection.')
    return 0

class Store:
    """Durable per-tag transactions with an OS-released single-process lock."""
    def __init__(self,path,profile):
        self.path=Path(path); self.profile=profile; self.data=None; self.lock=None
    def __enter__(self):
        import msvcrt
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=self.path.with_suffix(self.path.suffix+'.lock').open('a+b')
        self.lock.seek(0,2)
        if self.lock.tell()==0: self.lock.write(b'0'); self.lock.flush()
        self.lock.seek(0)
        try: msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            self.lock.close(); raise RuntimeError('Another process is using this state file')
        try:
            h=profile_hash(self.profile)
            if self.path.exists():
                self.data=json.loads(self.path.read_text(encoding='utf-8'))
                if self.data.get('version')!=1 or self.data.get('profile_hash')!=h: raise ValueError('State belongs to another settings profile. Use a different --state path.')
            else: self.data={'version':1,'profile_hash':h,'profile':self.profile,'created_utc':now(),'tags':{}}
            return self
        except BaseException:
            self.__exit__(None,None,None); raise
    def __exit__(self,*args):
        import msvcrt
        if self.lock and not self.lock.closed:
            self.lock.seek(0); msvcrt.locking(self.lock.fileno(),msvcrt.LK_UNLCK,1); self.lock.close()
    def put(self,address,record):
        record['updated_utc']=now(); self.data['tags'][address]=record
        atomic_json(self.path,self.data)
    def export(self):
        p=self.path.with_suffix('.csv')
        with p.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=['mac','status','attempts','updated_utc','error']); w.writeheader()
            for address,r in self.data['tags'].items():
                w.writerow({'mac':address,**{k:r.get(k,'') for k in ['status','attempts','updated_utc','error']}})
        return p

def transient_error(exc):
    return isinstance(exc,(TimeoutError,OSError)) or str(exc)=='No connectable advertisement found for this tag'

@contextmanager
def radio_lock(path=None):
    """Prevent CLI jobs from this installation from sharing the Bluetooth adapter."""
    import msvcrt
    path=Path(path) if path is not None else ROOT/'results'/'.bluetooth.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0,2)
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('Another Bluetooth CLI job from this installation is running')
        try:yield
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

async def get_snapshot(address,password):
    for attempt in range(3):
        try:
            async with session(address,password,(15,30,60)[attempt]) as c:return await snapshot(c)
        except Exception as exc:
            transient=transient_error(exc)
            if not transient or attempt==2:raise
            await asyncio.sleep(2)


async def restore(address,password,target):
    for attempt in range(3):
        try:
            async with session(address,password) as c:
                current=await snapshot(c)
                if current!=target: await write_snapshot(c,current,target)
            actual=await get_snapshot(address,password)
            if actual!=target: raise RuntimeError('Restoration did not match the saved six-slot snapshot')
            return actual
        except Exception:
            if attempt==2: raise
            await asyncio.sleep(1)

async def reconcile(store,address,password,record):
    """Recover a process interrupted after its durable pre-write checkpoint."""
    current=await get_snapshot(address,password)
    if 'restore_target' in record:
        target=record['restore_target']; await restore(address,password,target)
        record.update(status='rolled_back',error=''); store.put(address,record); return
    if current==record['target']:
        record.update(status='verified',after=current,error=''); store.put(address,record); return
    if current!=record['before']: await restore(address,password,record['before'])
    record.update(status='failed',error='Interrupted attempt restored; ready to retry')
    store.put(address,record)

async def provision(store,address,password,profile,recheck=False):
    record=store.data['tags'].get(address,{})
    if record.get('status') in ('applying','pending_verify','needs_recovery','restoring'):
        try: await reconcile(store,address,password,record)
        except Exception as exc:
            record.update(status='pending_verify' if record.get('status')=='pending_verify' and transient_error(exc) else 'needs_recovery',error=f'{type(exc).__name__}: {exc}'); store.put(address,record)
            return record['status']
    if record.get('status')=='verified' and not recheck: return 'skipped_verified'
    record.pop('restore_target',None)
    record['attempts']=record.get('attempts',0)+1
    attempted=False
    try:
        async with session(address,password) as c:
            before=await snapshot(c)
            target=target_snapshot(before,profile)
            record.setdefault('original',before)
            record.update(before=before,target=target,changes=differences(before,target),error='',retryable=False)
            if before==target:
                record.update(status='verified',after=before); store.put(address,record)
                return 'already_matches'
            validate_transition(before,target)
            validate_transition(target,before)  # Rollback must also avoid unsafe writes.
            record.pop('rollback_verified',None)
            record['status']='applying'; store.put(address,record)
            attempted=True
            await write_snapshot(c,before,target)
            immediate=await snapshot(c)
            if immediate!=target:raise RuntimeError('Immediate six-slot readback mismatch')
            record.update(status='pending_verify',immediate=immediate,written_utc=now())
            store.put(address,record)
        after=await get_snapshot(address,password)
        if after!=target: raise RuntimeError('Verification mismatch after reconnect; checking all six slots')
        record.update(status='verified',after=after); store.put(address,record)
        return 'applied_verified'
    except BaseException as exc:
        record['error']=f'{type(exc).__name__}: {exc}'
        if isinstance(exc,(asyncio.CancelledError,KeyboardInterrupt)):
            store.put(address,record)
            raise
        if attempted and record.get('status')=='pending_verify' and transient_error(exc):
            store.put(address,record)
            return 'pending_verify'
        if attempted:
            try:
                await restore(address,password,record['before'])
                record.update(status='failed',rollback_verified=True)
            except BaseException as recovery:
                record.update(status='needs_recovery',recovery_error=f'{type(recovery).__name__}: {recovery}')
        else: record.update(status='failed',retryable=transient_error(exc))
        store.put(address,record)
        if isinstance(exc,(asyncio.CancelledError,KeyboardInterrupt)): raise
        return record['status']

async def rollback(store,address,password):
    record=store.data['tags'].get(address)
    if not record or 'original' not in record: return 'no_saved_baseline'
    try:
        # Checkpoint recovery remains possible if rollback itself is interrupted.
        record.update(status='restoring',restore_target=record['original'],error=''); store.put(address,record)
        await restore(address,password,record['original'])
        record.update(status='rolled_back',error=''); store.put(address,record)
        return 'rolled_back'
    except Exception as exc:
        record['error']=f'{type(exc).__name__}: {exc}'
        # Keep restoring status so the next invocation resumes this rollback.
        store.put(address,record); return 'needs_recovery'

async def scan(args):
    found=await BleakScanner.discover(timeout=args.seconds,return_adv=True)
    output=Path(args.output)
    if output.exists(): raise ValueError('Inventory file already exists; choose another --output path')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['mac','enabled','name','rssi']); w.writeheader()
        for d,a in sorted(found.values(),key=lambda pair:pair[1].rssi,reverse=True):
            w.writerow({'mac':d.address,'enabled':'no','name':a.local_name or d.name or '', 'rssi':a.rssi})
    print(f'Inventory: {output.resolve()}')
    print('Mark only your intended tags enabled=yes before applying. Discovery alone does not establish ownership/model.')

async def run(args,password=None):
    validate_paths(args)
    if args.command=='scan': return await scan(args)
    profile=validate_profile(json.loads(Path(args.profile).read_text(encoding='utf-8-sig')))
    addresses=inventory(args.inventory)
    if args.command=='recover':
        from batch_workflow import recover
        return await recover(args,password,profile,addresses)
    if args.command=='apply' and getattr(args,'auto_retry',False):
        from batch_workflow import queued_apply
        return await queued_apply(args,password,profile,addresses)
    results=[]; plan=[]
    if args.command=='plan':
        for i,address in enumerate(addresses,1):
            try:
                before=await get_snapshot(address,password); target=target_snapshot(before,profile)
                validate_transition(before,target)
                validate_transition(target,before)
                changes=differences(before,target)
                plan.append({'mac':address,'status':'changes_needed' if changes else 'already_matches','changes':changes,'before':before,'target':target})
                print(f'[{i}/{len(addresses)}] {address}: '+json.dumps(changes),flush=True)
            except Exception as exc:
                plan.append({'mac':address,'status':'failed','error':f'{type(exc).__name__}: {exc}'})
                print(f'[{i}/{len(addresses)}] {address}: {type(exc).__name__}: {exc}',flush=True)
        atomic_json(args.output,{'profile':profile,'profile_hash':profile_hash(profile),'tags':plan})
        return 1 if any(r['status']=='failed' for r in plan) else 0
    with Store(args.state,profile) as store:
        # Never leave an unresolved changed tag hidden by a different inventory.
        unresolved=[a for a,r in store.data['tags'].items() if r.get('status') in ('applying','pending_verify','needs_recovery','restoring') and a not in addresses]
        if unresolved: raise ValueError('Include these unfinished tags in the inventory first: '+', '.join(unresolved))
        addresses.sort(key=lambda a: store.data['tags'].get(a,{}).get('status') not in ('applying','pending_verify','needs_recovery','restoring'))
        try:
            for i,address in enumerate(addresses,1):
                start=time.monotonic(); status='failed'
                print(f'[{i}/{len(addresses)}] {address}: starting {args.command}',flush=True)
                for attempt in range(args.retries+1):
                    status=await rollback(store,address,password) if args.command=='rollback' else await provision(store,address,password,profile,args.recheck)
                    if status!='failed' or store.data['tags'].get(address,{}).get('rollback_verified'): break
                    if attempt<args.retries:
                        print(f'{address}: retry {attempt+1}/{args.retries}',flush=True)
                        await asyncio.sleep(1)
                results.append({'mac':address,'status':status})
                print(f'[{i}/{len(addresses)}] {address}: {status} ({time.monotonic()-start:.1f}s)',flush=True)
                if status in ('failed','needs_recovery'): print('  '+store.data['tags'][address].get('error',''),flush=True)
                store.export()
                if status in ('failed','pending_verify','needs_recovery','no_saved_baseline'):
                    print('Stopped after failure: inspect this tag before continuing.',flush=True); break
        finally:
            print('Progress:',store.path.resolve()); print('CSV:',store.export().resolve())
    counts=dict(Counter(r['status'] for r in results)); print('Summary:',json.dumps(counts))
    return 1 if any(r['status'] in ('failed','pending_verify','needs_recovery','no_saved_baseline') for r in results) else 0

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor',help='Check local prerequisites without contacting tags')
    p=sub.add_parser('scan',help='Export a candidate MAC inventory')
    p.add_argument('--output',default='inventory.csv'); p.add_argument('--seconds',type=int,choices=range(1,61),default=15)
    for command in ['plan','apply','rollback','recover']:
        p=sub.add_parser(command)
        p.add_argument('--inventory',required=True); p.add_argument('--profile',default=str(ROOT/'profile.json'))
        p.add_argument('--default-password',action='store_true')
        if command=='plan': p.add_argument('--output',default='plan.json')
        else:
            p.add_argument('--state',default='batch_state.json'); p.add_argument('--retries',type=int,choices=range(0,6),default=2)
            p.add_argument('--recheck',action='store_true',help='Re-read tags already verified in this state file')
        if command=='apply':
            p.add_argument('--auto-retry',action='store_true',help='Continue past tag failures and run delayed retry passes')
            p.add_argument('--retry-delay',type=int,choices=range(10,601),metavar='SECONDS',default=30,help='Base delay between retry passes, in seconds')
            p.add_argument('--failure-limit',type=int,choices=range(1,21),default=3,help='Pause after this many consecutive transient connection failures')
        if command=='recover':
            p.add_argument('--address',required=True,type=mac,help='One MAC from the inventory; other tags are not processed')
            p.add_argument('--startup-seconds',type=int,choices=range(30,601),metavar='SECONDS',default=300)
    args=parser.parse_args(); password=None
    try:
        if args.command=='doctor':return doctor()
        validate_paths(args)
        if args.command!='scan':
            from configure_tag import load_bootstrap_key
            load_bootstrap_key()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr); return 1
    if args.command!='scan':
        password='minew123' if args.default_password else getpass.getpass('Tag password (not saved): ')
        if len(password)!=8 or not password.isascii() or not password.isalnum(): parser.error('Password must be eight ASCII letters/digits')
        password=password.encode('ascii')
    try:
        with radio_lock():return asyncio.run(run(args,password)) or 0
    except KeyboardInterrupt: print('Interrupted. Re-run with the same state file to recover/resume.',file=sys.stderr); return 130
    except Exception as exc: print(f'{type(exc).__name__}: {exc}',file=sys.stderr); return 1
if __name__=='__main__': sys.exit(main())
