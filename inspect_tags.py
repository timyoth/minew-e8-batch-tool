"""Read-only BLE discovery and standard Device Information inspection."""
import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from bleak import BleakClient, BleakScanner

INFO = {'2a24':'model','2a25':'serial','2a26':'firmware','2a27':'hardware','2a28':'software','2a29':'manufacturer'}
def compact(value):
    return value.replace(':','').replace('-','').lower()
def mac(value):
    value = compact(value)
    if not re.fullmatch('[0-9a-f]{12}', value):
        raise argparse.ArgumentTypeError('Use a full 12-digit MAC address.')
    return ':'.join(value[i:i+2] for i in range(0,12,2)).upper()
def save(data, prefix):
    folder=Path(__file__).resolve().parent/'results'
    folder.mkdir(exist_ok=True)
    path=folder/(prefix+'_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'.json')
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    print('Saved:',path)
async def run(args):
    if args.command == 'scan':
        found=await BleakScanner.discover(timeout=args.seconds,return_adv=True)
        rows=[]
        for device,adv in found.values():
            rows.append({'address':device.address,'name':adv.local_name or device.name,'rssi':adv.rssi,'service_uuids':adv.service_uuids,'manufacturer_data':{str(k):v.hex() for k,v in adv.manufacturer_data.items()},'service_data':{k:v.hex() for k,v in adv.service_data.items()}})
        rows.sort(key=lambda r:r['rssi'],reverse=True)
        for r in rows: print(r['address'],r['rssi'],r['name'])
        print(f'{len(rows)} matching devices; {len(found)} total devices detected.')
        save({'captured_utc':datetime.now(timezone.utc).isoformat(),'devices':rows},'scan')
        return
    device=await BleakScanner.find_device_by_address(args.address,timeout=20)
    if device is None: raise RuntimeError('Tag not found. Check proximity, Bluetooth and connectable mode.')
    result={'address':device.address,'captured_utc':datetime.now(timezone.utc).isoformat(),'device_information':{},'services':[]}
    try:
        async with BleakClient(device,timeout=25,winrt={'use_cached_services':False}) as client:
            for service in client.services:
                entry={'uuid':service.uuid,'description':service.description,'characteristics':[]}
                result['services'].append(entry)
                for char in service.characteristics:
                    item={'uuid':char.uuid,'handle':char.handle,'properties':list(char.properties),'descriptors':[{'uuid':d.uuid,'handle':d.handle} for d in char.descriptors]}
                    entry['characteristics'].append(item)
                    # Only read standard Device Information strings. No vendor reads or writes.
                    key=INFO.get(char.uuid[4:8]) if char.uuid.startswith('0000') and char.uuid.endswith('-0000-1000-8000-00805f9b34fb') else None
                    if service.uuid=='0000180a-0000-1000-8000-00805f9b34fb' and key and 'read' in char.properties:
                        try:
                            raw=await asyncio.wait_for(client.read_gatt_char(char,use_cached=False),timeout=8)
                            result['device_information'][key]=raw.decode('utf-8',errors='replace').rstrip('\0')
                        except Exception as exc: item['read_error']=str(exc)
            print(json.dumps(result['device_information'],indent=2))
            for service in result['services']:
                print('Service:',service['uuid'])
                for char in service['characteristics']: print(' ',char['uuid'],','.join(char['properties']))
    except Exception as exc:
        result['error']=str(exc)
        raise
    finally: save(result,'inspect_'+compact(args.address))
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    scan=sub.add_parser('scan',help='Scan nearby BLE devices; no connections.')
    scan.add_argument('--seconds',type=float,default=15)
    inspect=sub.add_parser('inspect',help='List GATT services and read standard identity strings only.')
    inspect.add_argument('address',type=mac)
    args=parser.parse_args()
    if args.command=='scan' and not 1 <= args.seconds <= 60: parser.error('--seconds must be 1-60')
    try: asyncio.run(run(args))
    except KeyboardInterrupt: return 130
    except Exception as exc:
        print(f'Bluetooth inspection failed: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1
    return 0
if __name__=='__main__': sys.exit(main())
