

SBOX = [
0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16]
INV_SBOX = [0] * 256
for _i, _v in enumerate(SBOX):
    INV_SBOX[_v] = _i
RCON = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36]

def _xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1b) & 0xff
    return a

def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        a = _xtime(a)
    return p & 0xff

def _expand_key(key):
    assert len(key) == 16, 'AES-128 needs a 16-byte key'
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(11)]

def _add_round_key(s, rk):
    return [s[i] ^ rk[i] for i in range(16)]

def _sub_bytes(s):
    return [SBOX[b] for b in s]

def _inv_sub_bytes(s):
    return [INV_SBOX[b] for b in s]

def _shift_rows(s):
    o = list(s)
    for r in range(1, 4):
        row = [s[r + 4 * c] for c in range(4)]
        row = row[r:] + row[:r]
        for c in range(4):
            o[r + 4 * c] = row[c]
    return o

def _inv_shift_rows(s):
    o = list(s)
    for r in range(1, 4):
        row = [s[r + 4 * c] for c in range(4)]
        row = row[-r:] + row[:-r]
        for c in range(4):
            o[r + 4 * c] = row[c]
    return o

def _mix_columns(s):
    o = list(s)
    for c in range(4):
        a = s[4 * c:4 * c + 4]
        o[4 * c + 0] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
        o[4 * c + 1] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
        o[4 * c + 2] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
        o[4 * c + 3] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
    return o

def _inv_mix_columns(s):
    o = list(s)
    for c in range(4):
        a = s[4 * c:4 * c + 4]
        o[4 * c + 0] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
        o[4 * c + 1] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
        o[4 * c + 2] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
        o[4 * c + 3] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)
    return o

def _encrypt_block(block, rks):
    s = _add_round_key(list(block), rks[0])
    for rnd in range(1, 10):
        s = _sub_bytes(s)
        s = _shift_rows(s)
        s = _mix_columns(s)
        s = _add_round_key(s, rks[rnd])
    s = _sub_bytes(s)
    s = _shift_rows(s)
    return bytes(_add_round_key(s, rks[10]))

def _decrypt_block(block, rks):
    s = _add_round_key(list(block), rks[10])
    for rnd in range(9, 0, -1):
        s = _inv_shift_rows(s)
        s = _inv_sub_bytes(s)
        s = _add_round_key(s, rks[rnd])
        s = _inv_mix_columns(s)
    s = _inv_shift_rows(s)
    s = _inv_sub_bytes(s)
    return bytes(_add_round_key(s, rks[0]))

def _pkcs7_pad(data):
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n

def _pkcs7_unpad(data):
    if not data:
        return b''
    n = data[-1]
    if n < 1 or n > 16 or data[-n:] != bytes([n]) * n:
        return None
    return data[:-n]

def cbc_encrypt(key, iv, plaintext, pad=True):
    rks = _expand_key(key)
    data = _pkcs7_pad(plaintext) if pad else plaintext
    out = bytearray()
    prev = bytes(iv)
    for i in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = _encrypt_block(blk, rks)
        out += prev
    return bytes(out)

def cbc_decrypt(key, iv, ciphertext, unpad=True):
    rks = _expand_key(key)
    out = bytearray()
    prev = bytes(iv)
    for i in range(0, len(ciphertext), 16):
        blk = ciphertext[i:i + 16]
        dec = _decrypt_block(blk, rks)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = blk
    if unpad:
        return _pkcs7_unpad(bytes(out))
    return bytes(out)

if __name__ == '__main__':
    import hashlib, binascii, sys
    ok = True

    k = bytes(range(16))
    pt = bytes.fromhex('00112233445566778899aabbccddeeff')
    ct = _encrypt_block(pt, _expand_key(k))
    exp = bytes.fromhex('69c4e0d86a7b0430d8cdb78070b4c55a')
    print('AES-128 KAT      :', 'ok' if ct == exp else 'FAIL ' + binascii.hexlify(ct).decode())
    ok &= ct == exp
    back = _decrypt_block(exp, _expand_key(k))
    print('AES-128 KAT dec  :', 'ok' if back == pt else 'FAIL')
    ok &= back == pt

    PASS = binascii.unhexlify('7b0630b24c4395559642a06348662b0f')
    SALT = binascii.unhexlify('434d825242b9225b5b8027965dde5070')
    KEY = hashlib.pbkdf2_hmac('sha1', PASS, SALT, 1001, 32)[:16]
    exp_key = binascii.unhexlify('d4a1de88456bf86bfbad3bb9ddaf4bca')
    print('PBKDF2 == in-game key:', 'ok' if KEY == exp_key else 'FAIL')
    ok &= KEY == exp_key

    IV = binascii.unhexlify('e3acda84a734c94ad7b9f785a10a8176')
    B64 = 'doEKoYX3uddKyTSnhNqs4w8rZkhjoEKWVZVDTLIwBnulcN3/LVagnRlY3742j4ARvSd547B5RxwyAn4VnJaf57Q9zkN7jyysxgFzc6afW4RQtitV+KlNIODhV/hxraZ3Al2e/AmJeiODXH6b4JFYj6KYGwgxNvhE1e7LRwQ4y+E2HGNdfR/YgHWLbtB+ggLTcFDeXZYngFtbIrlCUoJNQw=='
    blob = binascii.a2b_base64(B64)
    got = cbc_decrypt(KEY, IV, blob[32:144])

    want = b'{"AppId":"788589","ViewerId":"XXXXXX","Name":"321","OnetimeToken":"localtoken0000000000000000000000"}'
    print('real ciphertext  :', 'ok' if got == want else 'FAIL ' + repr(got))
    ok &= got == want

    rt = cbc_decrypt(KEY, IV, cbc_encrypt(KEY, IV, want))
    print('round trip       :', 'ok' if rt == want else 'FAIL')
    ok &= rt == want

    print('RESULT           :', 'ALL OK' if ok else 'FAILURES')
    sys.exit(0 if ok else 1)
