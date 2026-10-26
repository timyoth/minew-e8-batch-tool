"""BeaconPlus per-slot advertisement modes; 0=connectable, 3=non-connectable."""

def decode_modes(reply):
    if len(reply)!=15 or reply[:3]!=bytes.fromhex('010d16'):
        raise ValueError('Unexpected connectability response')
    pairs=reply[3:]
    if pairs[::2]!=bytes(range(6)) or any(x not in (0,3) for x in pairs[1::2]):
        raise ValueError('Unsupported slot modes')
    return list(pairs[1::2])

def encode_modes(modes):
    if len(modes)!=6 or any(type(x)!=int or x not in (0,3) for x in modes):
        raise ValueError('Expected six supported advertisement modes')
    return b''.join(bytes([i,x]) for i,x in enumerate(modes))

async def read_modes(c):
    return decode_modes(await c.minew_command(0x16,b'\x00'))

async def write_modes(c,modes):
    reply=await c.minew_command(0x15,encode_modes(modes))
    if reply!=bytes.fromhex('01021500'):
        raise RuntimeError('Connection mode write rejected')
    if await read_modes(c)!=modes:
        raise RuntimeError('Connection mode readback mismatch')

def preserve_access(slots,modes):
    """Choose a surviving active slot if current connection slots are disabled."""
    encode_modes(modes)
    active=[s for s in slots if s['frame_hex']!='ffff' and 100<=s['interval_ms']<=5000]
    if not active:raise ValueError('At least one active slot must remain for configuration access')
    result=list(modes)
    if not any(result[s['slot']]==0 for s in active):
        result[min(active,key=lambda s:s['interval_ms'])['slot']]=0
    return result
