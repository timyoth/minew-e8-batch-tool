"""Opt-in delayed batch retries and single-tag startup recovery."""
import asyncio
import json
from collections import Counter
from pathlib import Path
from bleak import BleakScanner
from configure_tag import current_advertisement_events
import batch_configure as b

UNRESOLVED=('applying','pending_verify','needs_recovery','restoring')

def retryable(record):
    return record.get('status')=='pending_verify' or (record.get('status')=='failed' and record.get('retryable',False))

def report(store,addresses,paused=False):
    rows=[]
    for address in addresses:
        r=store.data['tags'].get(address,{})
        rows.append({'mac':address,'status':r.get('status','not_attempted'),
                     'error':r.get('error',''),'attempts':r.get('attempts',0),
                     'rollback_verified':r.get('rollback_verified',False)})
    counts=dict(Counter(r['status'] for r in rows))
    result={'updated_utc':b.now(),'paused_for_connections':paused,'counts':counts,
            'total':len(addresses),'tags':rows}
    path=store.path.with_suffix('.progress.json')
    b.atomic_json(path,result)
    store.export()
    print('Tray progress: '+json.dumps(counts),flush=True)
    return result

async def queued_apply(args,password,profile,addresses):
    with b.Store(args.state,profile) as store:
        missing=[a for a,r in store.data['tags'].items() if r.get('status') in UNRESOLVED and a not in addresses]
        if missing:raise ValueError('Include unfinished tags in the inventory: '+', '.join(missing))
        pending=list(addresses); consecutive=0; paused=False
        try:
            for pass_number in range(args.retries+1):
                if not pending:break
                if pass_number:
                    delay=args.retry_delay*pass_number
                    print(f'Retry pass {pass_number}: waiting {delay}s; {len(pending)} tags queued.',flush=True)
                    await asyncio.sleep(delay)
                next_pass=[]
                for address in pending:
                    print(f'Pass {pass_number+1}: {address}',flush=True)
                    result=await b.provision(store,address,password,profile,
                                             args.recheck if pass_number==0 else False)
                    r=store.data['tags'][address]
                    print(f'{address}: {result}',flush=True)
                    if r.get('error'):print('  '+r['error'],flush=True)
                    if retryable(r):next_pass.append(address)
                    connection_failure=retryable(r) or (result=='needs_recovery' and (
                        'No connectable advertisement' in r.get('error','') or
                        r.get('error','').startswith(('TimeoutError:','OSError:'))))
                    consecutive=consecutive+1 if connection_failure else 0
                    report(store,addresses)
                    if consecutive>=args.failure_limit:
                        paused=True
                        print('Paused after consecutive connection failures. Check adapter, app and tag placement; resume the same command.',flush=True)
                        break
                if paused:break
                pending=next_pass
        finally:
            final=report(store,addresses,paused)
        return 0 if final['counts'].get('verified',0)==len(addresses) else 1

async def recover(args,password,profile,addresses):
    address=args.address
    if address not in addresses:raise ValueError('Recovery MAC must be enabled in the inventory')
    # Lock the transaction ledger before waiting; never allow simultaneous writes to it.
    with b.Store(args.state,profile) as store:
        if address not in store.data['tags']:raise ValueError('Recovery requires an existing tag record; use apply first')
        found=asyncio.Event()
        def callback(device,advertisement):
            if device.address.upper()==address and any(e is not None and getattr(e,'is_connectable',False) for e in current_advertisement_events(advertisement)):
                found.set()
        print(f'RECOVERY: {address}. Keep the phone app closed. Only this tag will be processed.',flush=True)
        async with BleakScanner(detection_callback=callback):
            print(f'SCANNER READY: insert the battery in {address} NOW. Listening for {args.startup_seconds}s.',flush=True)
            try:await asyncio.wait_for(found.wait(),args.startup_seconds)
            except asyncio.TimeoutError:
                print('No connectable advertisement detected; no settings changed.',flush=True)
                return 1
        print('Tag detected. Reading current settings before any changes.',flush=True)
        result=await b.provision(store,address,password,profile)
        print(f'{address}: {result}',flush=True)
        report(store,addresses)
        return 0 if store.data['tags'][address]['status']=='verified' else 1
