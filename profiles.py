"""Validated slot profile operations. User-facing slots are numbered 1..6."""
import copy,hashlib,json
from configure_tag import read,write,select,uuid
from connectivity import preserve_access,write_modes,encode_modes

def frame_type(value):
    if value=='ffff': return 'disabled'
    if value=='a103': return 'acc'
    if value.startswith('a0ff'): return 'ibeacon'
    return 'other'

def validate_profile(profile):
    if not isinstance(profile,dict) or set(profile)-{'version','name','slots'}: raise ValueError('Unknown profile fields')
    if type(profile.get('version'))!=int or profile.get('version')!=1: raise ValueError('Profile version must be 1')
    slots=profile.get('slots')
    if not isinstance(slots,list) or not slots: raise ValueError('Profile needs at least one slot')
    seen=set()
    for rule in slots:
        if not isinstance(rule,dict) or set(rule)-{'slot','type','require_frame','interval_ms','tx_dbm'}: raise ValueError('Unknown slot fields')
        n=rule.get('slot')
        if type(n)!=int or n not in range(1,7) or n in seen: raise ValueError('Slots must be unique integers 1..6')
        seen.add(n)
        kind=rule.get('type','preserve')
        if kind not in ('preserve','acc','disabled'): raise ValueError('type must be preserve, acc or disabled')
        if rule.get('require_frame') not in (None,'ibeacon','acc','disabled','other'): raise ValueError('Unknown required frame')
        interval=rule.get('interval_ms')
        if interval is not None and (type(interval)!=int or not 100<=interval<=5000): raise ValueError('interval_ms must be an integer from 100 to 5000')
        power=rule.get('tx_dbm')
        if power is not None and (type(power)!=int or power not in [-40,-20,-16,-12,-8,-4,0,4]): raise ValueError('Unsupported TX power')
        if kind=='acc' and (interval is None or power is None): raise ValueError('ACC rules need interval_ms and tx_dbm')
        if kind=='disabled' and (interval is not None or power is not None): raise ValueError('Disabled slots must omit interval and TX power')
    return profile

def profile_hash(profile):
    validate_profile(profile)
    effective={'version':1,'slots':sorted(profile['slots'],key=lambda x:x['slot'])}
    return hashlib.sha256(json.dumps(effective,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def target_snapshot(before,profile):
    validate_profile(profile)
    target=copy.deepcopy(before)
    caps=bytes.fromhex(before['capabilities_hex'])
    if caps[1]!=6 or caps[3]&3!=3: raise ValueError('Device lacks expected per-slot capabilities')
    supported={int.from_bytes(bytes([v]),'big',signed=True) for v in caps[6:]}
    for rule in profile['slots']:
        s=target['slots'][rule['slot']-1]
        actual=frame_type(s['frame_hex']); kind=rule.get('type','preserve')
        if rule.get('require_frame') and actual!=rule['require_frame']: raise ValueError(f"Slot {rule['slot']} must already be {rule['require_frame']}; found {actual}")
        if kind in ('acc','disabled'):
            if actual not in ('acc','disabled'): raise ValueError(f"Slot {rule['slot']}: replacing {actual} is not supported; identifiers would need a separate profile")
            s['frame_hex']='a103' if kind=='acc' else 'ffff'
            if kind=='disabled':s['interval_ms']=0
        if kind=='preserve' and actual=='disabled' and ('interval_ms' in rule or 'tx_dbm' in rule): raise ValueError('Select ACC explicitly to enable an empty slot')
        if 'interval_ms' in rule: s['interval_ms']=rule['interval_ms']
        if 'tx_dbm' in rule:
            if rule['tx_dbm'] not in supported: raise ValueError('Requested TX power not in device capabilities')
            s['tx_dbm']=rule['tx_dbm']
    if 'connection_modes' not in before:raise ValueError('Fresh snapshot with connection modes required')
    target['connection_modes']=preserve_access(target['slots'],before['connection_modes'])
    return target

def differences(before,after):
    out=[]
    for a,b in zip(before['slots'],after['slots']):
        changed={k:{'from':a[k],'to':b[k]} for k in b if a[k]!=b[k]}
        if changed: out.append({'slot':b['slot']+1,'changes':changed})
    if before.get('connection_modes')!=after.get('connection_modes'):
        out.append({'connection_modes':{'from':before.get('connection_modes'),'to':after.get('connection_modes')}})
    return out

def validate_transition(current,target):
    for state in (current,target):
        if 'connection_modes' not in state:raise ValueError('Legacy snapshot lacks connection modes; take a fresh baseline')
        encode_modes(state['connection_modes'])
    if preserve_access(target['slots'],target['connection_modes'])!=target['connection_modes']:
        raise ValueError('Target would leave no active connectable slot')
    for old,new in zip(current['slots'],target['slots']):
        if old==new:continue
        if old['frame_hex']!=new['frame_hex'] and (old['frame_hex'] not in ('a103','ffff') or new['frame_hex'] not in ('a103','ffff')):
            raise ValueError('Unsupported frame replacement')
        if not 100<=new['interval_ms']<=5000 and not (new['frame_hex']=='ffff' and new['interval_ms']==0):
            raise ValueError('Zero interval is permitted only for a disabled slot')
        if old['frame_hex']=='ffff' and new['frame_hex']=='ffff' and any(old[k]!=new[k] for k in ('tx_dbm','measured_power_hex')):
            raise ValueError('Do not write power or calibration on a disabled slot')

async def write_snapshot(c,current,target):
    validate_transition(current,target)
    # Enable every connection path needed by either snapshot before any slot change.
    bridge=[0 if a==0 or b==0 else 3 for a,b in zip(current['connection_modes'],target['connection_modes'])]
    if bridge!=current['connection_modes']:await write_modes(c,bridge)
    for old,new in zip(current['slots'],target['slots']):
        if old==new:continue
        await select(c,new['slot'])
        changed_frame=old['frame_hex']!=new['frame_hex']
        if changed_frame and new['frame_hex']=='ffff':
            # Restore saved power/calibration while the frame is still active.
            for field,suffix in [('tx_dbm','04'),('measured_power_hex','05')]:
                if old[field]!=new[field]:
                    data=new[field].to_bytes(1,'big',signed=True) if field=='tx_dbm' else bytes.fromhex(new[field])
                    await write(c,uuid(suffix),data)
        if changed_frame:
            await write(c,uuid('0a'),b'\x00\x00'+bytes.fromhex(new['frame_hex']))
        for field,suffix in [('interval_ms','03'),('tx_dbm','04'),('measured_power_hex','05')]:
            if new['frame_hex']=='ffff' and field!='interval_ms':continue
            if not changed_frame and old[field]==new[field]:continue
            if field=='interval_ms':data=new[field].to_bytes(2,'big')
            elif field=='tx_dbm':data=new[field].to_bytes(1,'big',signed=True)
            else:data=bytes.fromhex(new[field])
            await write(c,uuid(suffix),data)
        if (await read(c,uuid('0a'))).hex()!=new['frame_hex']:
            raise RuntimeError(f"Slot {new['slot']+1}: frame readback mismatch")
    if bridge!=target['connection_modes']:await write_modes(c,target['connection_modes'])
