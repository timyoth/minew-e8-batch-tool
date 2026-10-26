"""Single-tag Minew BeaconPlus configuration, validated on firmware 2.3.17."""
import argparse,asyncio,binascii,copy,getpass,json,secrets,sys
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from pathlib import Path
from bleak import BleakClient,BleakScanner
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from inspect_tags import mac
ROOT=Path(__file__).resolve().parent
AUTH='7f280002-8204-f393-e0a9-e50e24dcca9e'
def uuid(s): return 'a3c875'+s+'-8ed3-4bdf-8a39-a01bebede295'
def aes(data,key,decrypt=False):
    if len(data)!=16 or len(key)!=16: raise ValueError('Expected 16-byte protocol blocks')
    c=Cipher(algorithms.AES(key),modes.ECB())
    ctx=c.decryptor() if decrypt else c.encryptor()
    return ctx.update(data)+ctx.finalize()
async def read(c,u): return bytes(await asyncio.wait_for(c.read_gatt_char(u,use_cached=False),5))
async def write(c,u,data):
    return await asyncio.wait_for(c.write_gatt_char(u,data,response=True),5)
def current_advertisement_events(advertisement):
    """WinRT updates AdvertisementData.platform_data, not BLEDevice.details."""
    data=getattr(advertisement,'platform_data',())
    if len(data)<2:return ()
    return tuple(event for event in data[1] if event is not None)

async def find_connectable(address,timeout):
    observed=[]
    def connectable(device,advertisement):
        if device.address.upper()!=address.upper():return False
        for event in current_advertisement_events(advertisement):
            if getattr(event,'is_connectable',False):
                observed[:]=[event]
                return True
        return False
    device=await BleakScanner.find_device_by_filter(connectable,timeout=timeout)
    if device is None or not observed:raise RuntimeError('No connectable advertisement found for this tag')
    return device,observed[0]

@asynccontextmanager
async def session(address,password,discovery_timeout=15):
    key=(ROOT/'sdk_bootstrap_key.bin').read_bytes()
    await asyncio.sleep(1)  # Give Windows time to release the previous BLE session.
    d,event=await find_connectable(address,discovery_timeout)
    address_type='random' if int(event.bluetooth_address_type)==1 else 'public'
    async with BleakClient(d,timeout=20,winrt={'use_cached_services':False,'address_type':address_type}) as c:
        challenge=aes(await read(c,AUTH),key,True)
        identity=bytes.fromhex(address.replace(':',''))
        if challenge[:6]!=identity: raise RuntimeError('Challenge identity mismatch')
        nonce=secrets.token_bytes(8); session_key=challenge[6:14]+nonce
        payload=identity+nonce
        payload+=binascii.crc_hqx(payload,0xffff).to_bytes(2,'big')
        q=asyncio.Queue()
        await c.start_notify(AUTH,lambda _,data:q.put_nowait(bytes(data)))
        await write(c,AUTH,aes(payload,key))
        cmd=bytes([0,9,0x13])+password+secrets.token_bytes(5)
        await write(c,AUTH,aes(cmd,session_key))
        response=aes(await asyncio.wait_for(q.get(),5),session_key,True)
        if response[:4]!=bytes.fromhex('01021300'): raise RuntimeError('Authentication rejected or unexpected response')
        model=(await read(c,'00002a24-0000-1000-8000-00805f9b34fb')).decode().strip('\x00\r\n ')
        firmware=(await read(c,'00002a26-0000-1000-8000-00805f9b34fb')).decode().strip('\x00\r\n ')
        if (model,firmware)!=('Beacon Plus-BL','2.3.17'): raise RuntimeError(f'Unvalidated device: {model}, {firmware}')
        command_lock=asyncio.Lock()
        async def command(code,data):
            if len(data)>13: raise ValueError('Command payload too long')
            async with command_lock:
                while not q.empty(): q.get_nowait()
                plain=bytes([0,len(data)+1,code])+data
                await write(c,AUTH,aes(plain+secrets.token_bytes(16-len(plain)),session_key))
                answer=aes(await asyncio.wait_for(q.get(),5),session_key,True)
                if answer[0]!=1 or answer[2]!=code or not 1<=answer[1]<=14:
                    raise RuntimeError('Unexpected control reply')
                return answer[:answer[1]+2]
        c.minew_command=command
        yield c
async def select(c,slot):
    await write(c,uuid('02'),bytes([slot]))
    if await read(c,uuid('02'))!=bytes([slot]): raise RuntimeError('Slot selection failed')
async def snapshot(c):
    caps=await read(c,uuid('01'))
    if len(caps)<7 or caps[1]!=6: raise RuntimeError('Unexpected capabilities')
    result={'capabilities_hex':caps.hex(),'slots':[]}
    for slot in range(caps[1]):
        await select(c,slot)
        interval=await read(c,uuid('03')); tx=await read(c,uuid('04')); measured=await read(c,uuid('05')); frame=await read(c,uuid('0a'))
        if len(interval)!=2 or len(tx)!=1 or len(measured)!=1: raise RuntimeError('Unexpected field sizes')
        result['slots'].append({'slot':slot,'interval_ms':int.from_bytes(interval,'big'),'tx_dbm':int.from_bytes(tx,'big',signed=True),'measured_power_hex':measured.hex(),'frame_hex':frame.hex()})
    from connectivity import read_modes
    result['connection_modes']=await read_modes(c)
    return result
async def change(c,slot,power,caps):
    raw=bytes.fromhex(caps)
    supported=[int.from_bytes(bytes([v]),'big',signed=True) for v in raw[6:]]
    if not raw[3]&2: raise RuntimeError('Per-slot TX power not supported')
    if power not in supported: raise ValueError('Power not supported by this device')
    await select(c,slot)
    await write(c,uuid('04'),power.to_bytes(1,'big',signed=True))
    if int.from_bytes(await read(c,uuid('04')),'big',signed=True)!=power: raise RuntimeError('Immediate readback mismatch')
def save(report):
    (ROOT/'results').mkdir(exist_ok=True)
    name='configuration_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'.json'
    path=ROOT/'results'/name
    path.write_text(json.dumps(report,indent=2),encoding='utf-8'); print('Report:',path,flush=True)
async def run(args,password):
    report={'address':args.address,'action':args.action,'started_utc':datetime.now(timezone.utc).isoformat(),'requested_slot':args.slot,'requested_dbm':args.dbm}
    attempted=False; before=None
    try:
        async with session(args.address,password) as c:
            before=await snapshot(c); report['before']=before
            print(json.dumps(before,indent=2),flush=True)
            if args.action=='read': return
            slot=before['slots'][args.slot]
            if slot['frame_hex']=='ffff': raise ValueError('Refusing to configure a disabled slot')
            if slot['tx_dbm']==args.dbm: report['status']='already_matches'; return
            # Persist the full baseline before the first configuration write.
            save(report)
            attempted=True
            await change(c,args.slot,args.dbm,before['capabilities_hex'])
        async with session(args.address,password) as c: after=await snapshot(c)
        expected=copy.deepcopy(before); expected['slots'][args.slot]['tx_dbm']=args.dbm
        report['after_reconnect']=after
        if after!=expected: raise RuntimeError('Reconnect verification failed or unrelated slot fields changed')
        report['status']='change_verified'; print('Change verified after reconnect.',flush=True)
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        if attempted and (args.action=='test-power' or 'error' in report):
            try:
                async with session(args.address,password) as c:
                    await change(c,args.slot,before['slots'][args.slot]['tx_dbm'],before['capabilities_hex'])
                async with session(args.address,password) as c: restored=await snapshot(c)
                report['after_restore']=restored
                if restored!=before: raise RuntimeError('Restoration verification mismatch')
                report['restoration']='verified'; print('Original settings restored and verified after reconnect.',flush=True)
            except Exception as exc:
                report['restoration']='FAILED: '+type(exc).__name__+': '+str(exc)
                save(report)
                raise RuntimeError('Restoration was not verified; inspect this tag before continuing.') from exc
        save(report)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['read','test-power','set-power'])
    parser.add_argument('address',type=mac)
    parser.add_argument('--slot',type=int,choices=range(6))
    parser.add_argument('--dbm',type=int,choices=[-40,-20,-16,-12,-8,-4,0,4])
    parser.add_argument('--default-password',action='store_true',help='Use the confirmed factory password; otherwise prompt securely.')
    args=parser.parse_args()
    if args.action!='read' and (args.slot is None or args.dbm is None): parser.error('--slot and --dbm are required for power changes')
    password='minew123' if args.default_password else getpass.getpass('Tag password: ')
    if len(password)!=8 or not password.isascii() or not password.isalnum(): parser.error('Password must be eight ASCII letters/digits')
    try: asyncio.run(run(args,password.encode('ascii')))
    except Exception as exc: print(type(exc).__name__+': '+str(exc),file=sys.stderr); return 1
    return 0
if __name__=='__main__': sys.exit(main())
