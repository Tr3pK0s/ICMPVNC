"""
ICMPVNC Server Germanna v1.0 ⛏
Screen sharing + mouse/keyboard input over ICMP

Usage: -i interface --mode -k password
  sudo -E python3 server.py -i eth0 --xdp -k password
  sudo -E python3 server.py -i wlan0 --raw -k password
"""
import socket, struct, os, sys, time, mmap, ctypes, ctypes.util, re
import select, fcntl, argparse, signal, atexit, zlib, hashlib, hmac
import subprocess, tempfile, getpass, array, threading


import itertools as _itertools

_VERBOSE = False
_QUIET = False

def _iscolor():
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

class _C:
    _on = True
    RST = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    BLK = '\033[1;90m'
    RED = '\033[1;31m'
    GOLD = '\033[33m'
    GREEN = '\033[1;32m'
    YELLOW = '\033[33m'
    BRED = '\033[1;31m'
    WHITE = '\033[1;37m'
    DIMW = '\033[2;37m'
    MAG = '\033[35m'

    @classmethod
    def off(cls):
        cls._on = False
        for k in list(vars(cls)):
            if k.startswith('_') or callable(getattr(cls, k)): continue
            setattr(cls, k, '')

def _init_colors():
    if not _iscolor(): _C.off()

_BANNER = r"""
{BLK}    ██  ██████ ██   ██ ██████ {RED} ██    ██ ███  ██  ██████{RST}
{BLK}    ██ ██      ███ ███ ██   ██{RED} ██    ██ ████ ██ ██{RST}
{BLK}    ██ ██      ██ █ ██ ██████ {RED} ██    ██ ██ ████ ██{RST}
{BLK}    ██ ██      ██   ██ ██     {RED}  ██  ██  ██  ███ ██{RST}
{BLK}    ██  ██████ ██   ██ ██     {RED}   ████   ██   ██  ██████{RST}
{GOLD}                    1714-1717⛏{RST}
{DIMW}   Screen Sharing + mouse/keyboard input Over ICMP{RST}
"""

def _print_banner():
    if _QUIET: return
    b = _BANNER.format(BLK=_C.BLK, RED=_C.RED, GOLD=_C.GOLD,
                       RST=_C.RST, DIMW=_C.DIMW)
    print(b)

def _tunnel_box(title, rows, width=48):
    if _QUIET: return
    inner = width - 4
    t = f" {title} "
    pad = inner - len(t)
    top = f"  {_C.DIMW}▓░{_C.GOLD}{t}{_C.DIMW}{'░' * pad}▓{_C.RST}"
    print(top)
    for label, value in rows:
        line = f"  {label:<12}{value}"
        vis_len = len(f"  {label:<12}{value}")
        rpad = max(0, inner - vis_len + 2)
        print(f"  {_C.DIMW}░  {_C.WHITE}{label:<12}{_C.GOLD}{value}{' ' * rpad}{_C.DIMW}░{_C.RST}")
    bottom = f"  {_C.DIMW}▓{'░' * (width - 2)}▓{_C.RST}"
    print(bottom)

_SPARK_CHARS = '▁▂▃▄▅▆▇█'
_pps_history = []

def _sparkline(current_pps):
    _pps_history.append(current_pps)
    if len(_pps_history) > 8: _pps_history.pop(0)
    if not _pps_history: return ''
    mx = max(_pps_history) or 1
    return ''.join(_SPARK_CHARS[min(int(v / mx * 7), 7)] for v in _pps_history)

_spinner_frames = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
_spinner_cycle = _itertools.cycle(_spinner_frames)

def _spin():
    return next(_spinner_cycle)

def _term_w():
    try: return os.get_terminal_size().columns
    except: return 120

def _status_write(text):
    w = _term_w()
    vis = re.sub(r'\033\[[0-9;]*m', '', text)
    if len(vis) > w - 1:
        out = []; vlen = 0; i = 0
        while i < len(text):
            if text[i] == '\033':
                j = text.find('m', i)
                if j < 0: break
                out.append(text[i:j+1]); i = j + 1
            else:
                if vlen >= w - 2: break
                out.append(text[i]); vlen += 1; i += 1
        text = ''.join(out) + _C.RST
    sys.stdout.write(f"\r\033[K{text}")
    sys.stdout.flush()

def _event(icon, msg, color=None):
    ts = time.strftime('%H:%M:%S')
    c = color or _C.RST
    sys.stdout.write(f"\r\033[K  {_C.DIMW}[{ts}]{_C.RST} {c}{icon}{_C.RST} {msg}\n")
    sys.stdout.flush()

def _status_server(rx, tx, el, client, frame_id, chunks, pps_rx, pps_tx):
    if _QUIET: return
    spark = _sparkline(pps_rx)
    c = client or 'waiting'
    mins, secs = divmod(int(el), 60)
    t = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
    _status_write(
        f"  {_C.GOLD}⛏{_C.RST} "
        f"{_C.DIMW}RX{_C.RST} {_C.WHITE}{rx:,}{_C.RST} "
        f"{_C.DIMW}│ TX{_C.RST} {_C.WHITE}{tx:,}{_C.RST} "
        f"{_C.DIMW}│{_C.RST} {_C.WHITE}{pps_rx:.0f}{_C.DIMW}/{_C.RST}{_C.WHITE}{pps_tx:.0f}{_C.RST}{_C.DIMW} pps{_C.RST} "
        f"{_C.DIMW}│{_C.RST} {_C.GOLD}{c}{_C.RST} "
        f"{_C.DIMW}│ F:{_C.RST}{_C.WHITE}{frame_id}{_C.RST} "
        f"{_C.DIMW}C:{_C.RST}{_C.WHITE}{chunks}{_C.RST} "
        f"{_C.DIMW}│{_C.RST} {_C.GOLD}{spark}{_C.RST} "
        f"{_C.DIMW}│{_C.RST} {t}"
    )

def _waiting_server():
    if _QUIET: return
    _status_write(f"  {_C.GOLD}⛏{_C.RST} {_C.DIMW}Waiting for client {_C.GOLD}{_spin()}{_C.RST}")

def _summary_box(rows, width=48):
    if _QUIET: return
    print()
    _tunnel_box("Session Summary", rows, width)
    print(f"  {_C.GREEN}✓{_C.RST} Cleanup complete. Goodbye.")
    print()

def _vlog(msg):
    if _VERBOSE and not _QUIET:
        print(f"  {_C.DIMW}[v] {msg}{_C.RST}")



MAGIC        = b'\xCA\xFE'
SESSION_ID   = 0x1111
HANDSHAKE_ID = 0xBEEF
CMD_TEST       = 0x00
CMD_DISCONNECT = 0x05
CMD_FRAME_REQ  = 0x10
CMD_FRAME_HDR  = 0x11
CMD_FRAME_DATA = 0x12
CMD_KEY_CHUNK  = 0x13
CMD_FILE_REQ   = 0x30
CMD_FILE_DATA  = 0x31
CMD_FILE_UP_HDR  = 0x32
CMD_FILE_UP_DATA = 0x33
CMD_INPUT_KEY    = 0x40
CMD_INPUT_MOUSE  = 0x41
MAX_PAYLOAD  = 1400
ENCRYPT_OH   = 28          


ETH_IP       = b'\x08\x00'
ETH_IP6      = b'\x86\xDD'
IPPROTO_ICMP   = 1
IPPROTO_ICMPV6 = 58
FRAME_SIZE   = 2048
NUM_FRAMES   = 4096
RING_SIZE    = 4096
BATCH_SIZE   = 256
MASK64       = 0xFFFFFFFFFFFFFFFF
P25519       = 2**255 - 19

DISPATCH_V4 = {
    8:   (0,   'Echo',            True,  0),
    13:  (14,  'Timestamp',       True,  12),
    17:  (18,  'Address Mask',    True,  4),
    15:  (16,  'Information',     True,  0),
    10:  (9,   'Router Advert',   False, 0),
    253: (254, 'Experimental',    True,  0),
    37:  (38,  'Domain Name',     True,  0),
    35:  (36,  'Mobile Reg',      True,  0),
    30:  (0,   'Traceroute',      False, 0),
    40:  (40,  'Photuris',        False, 0),
    42:  (43,  'Extended Echo',   True,  0),
}
DISPATCH_V6 = {
    128: (129, 'Echo',            True,  0),
    133: (134, 'Router Advert',   False, 0),
    135: (136, 'Neighbor Advert', False, 0),
}

_cleanup_fns = []; _cleanup_ran = False
def register_cleanup(fn): _cleanup_fns.append(fn)
def run_cleanup():
    global _cleanup_ran
    if _cleanup_ran: return
    _cleanup_ran = True
    for fn in reversed(_cleanup_fns):
        try: fn()
        except: pass
atexit.register(run_cleanup)
def _sig(s, f):
    _event("⏻", "Shutting down...", _C.MAG); run_cleanup(); sys.exit(0)
signal.signal(signal.SIGINT, _sig); signal.signal(signal.SIGTERM, _sig)

def cksum(d):
    if len(d) % 2: d += b'\x00'
    s = sum(struct.unpack('!%dH' % (len(d)//2), d))
    s = (s >> 16) + (s & 0xFFFF)
    return ~(s + (s >> 16)) & 0xFFFF

def ms_now():
    t = time.time(); return int((t % 86400) * 1000) & 0xFFFFFFFF

def get_iface_info(iface):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mac = fcntl.ioctl(s.fileno(), 0x8927, struct.pack('256s', iface.encode()[:15]))[18:24]
    try: ip4 = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', iface.encode()[:15]))[20:24])
    except: ip4 = None
    ip6 = None
    try:
        with open('/proc/net/if_inet6') as f:
            for line in f:
                p = line.split()
                if len(p) >= 6 and p[5] == iface:
                    a = p[0]; ip6 = ':'.join(a[i:i+4] for i in range(0, 32, 4))
                    if a.startswith('fe80'): break
    except: pass
    s.close(); return ip4, mac, ip6

def validate_interface(iface):
    """Check interface exists. If not, list available and exit cleanly."""
    sysnet = '/sys/class/net'
    if not os.path.isdir(os.path.join(sysnet, iface)):
        avail = sorted(d for d in os.listdir(sysnet) if d != 'lo') if os.path.isdir(sysnet) else []
        _event("✗", f"Interface '{iface}' not found.", _C.BRED)
        if avail:
            print(f"  {_C.DIMW}Available: {', '.join(avail)}{_C.RST}")
        else:
            print(f"  {_C.DIMW}No network interfaces found.{_C.RST}")
        sys.exit(1)
    try:
        with open(f'{sysnet}/{iface}/operstate') as f:
            state = f.read().strip()
        if state == 'down':
            _event("✗", f"Interface '{iface}' is DOWN.", _C.BRED)
            print(f"  {_C.DIMW}Bring it up with: sudo ip link set {iface} up{_C.RST}")
            sys.exit(1)
    except (FileNotFoundError, PermissionError):
        pass

def is_wireless(iface):
    """Check if interface is wireless."""
    return (os.path.isdir(f'/sys/class/net/{iface}/wireless') or
            os.path.isdir(f'/sys/class/net/{iface}/phy80211'))

def warn_wireless_xdp(iface):
    """Warn user about XDP on wireless and prompt for confirmation."""
    print()
    _event("⚠", f"'{iface}' appears to be a wireless adapter.", _C.YELLOW)
    print(f"  {_C.DIMW}Most wireless drivers do NOT support native XDP.")
    print(f"  XDP will fall back to generic (SKB) mode with degraded performance.")
    print(f"  Raw mode (--raw) is recommended for wireless interfaces.{_C.RST}\n")
    try:
        ans = input(f"  {_C.WHITE}Continue with XDP on wireless? [y/N]: {_C.RST}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(); sys.exit(0)
    if ans != 'y':
        print(f"  {_C.DIMW}Aborted. Re-run with --raw instead.{_C.RST}")
        sys.exit(0)


import random as _random

_BIT_REV = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))

def obf_bitrev(data):
    return data.translate(_BIT_REV)

def obf_sub_init(seed):
    rng = _random.Random(seed)
    fwd = list(range(256)); rng.shuffle(fwd)
    inv = [0]*256
    for i,v in enumerate(fwd): inv[v] = i
    return bytes(fwd), bytes(inv)

def obf_sub_enc(data, table):
    return data.translate(table) if isinstance(data, bytes) else bytes(data).translate(table)

def obf_sub_dec(data, inv_table):
    return data.translate(inv_table) if isinstance(data, bytes) else bytes(data).translate(inv_table)

def x25519_clamp(k):
    k = bytearray(k); k[0] &= 248; k[31] &= 127; k[31] |= 64
    return bytes(k)

def x25519(k_bytes, u_bytes):
    p = P25519; a24 = 121665
    k = int.from_bytes(k_bytes,'little')
    u = int.from_bytes(u_bytes,'little') & ((1<<255)-1)
    x_1=u; x_2=1; z_2=0; x_3=u; z_3=1; swap=0
    for t in range(254,-1,-1):
        k_t=(k>>t)&1; swap^=k_t
        if swap: x_2,x_3=x_3,x_2; z_2,z_3=z_3,z_2
        swap=k_t
        A=(x_2+z_2)%p; AA=(A*A)%p; B=(x_2-z_2)%p; BB=(B*B)%p; E=(AA-BB)%p
        C=(x_3+z_3)%p; D=(x_3-z_3)%p; DA=(D*A)%p; CB=(C*B)%p
        x_3=pow(DA+CB,2,p); z_3=(x_1*pow(DA-CB,2,p))%p
        x_2=(AA*BB)%p; z_2=(E*((AA+(a24*E)%p)%p))%p
    if swap: x_2,x_3=x_3,x_2; z_2,z_3=z_3,z_2
    return ((x_2*pow(z_2,p-2,p))%p).to_bytes(32,'little')

def x25519_keypair():
    priv = x25519_clamp(os.urandom(32))
    return priv, x25519(priv, (9).to_bytes(32,'little'))

class Speck128_256:
    ROUNDS = 2
    def __init__(self, kb):
        M = MASK64; w = [int.from_bytes(kb[i*8:(i+1)*8],'big') for i in range(4)]
        l = [w[2],w[1],w[0]]; k = [w[3]]
        for i in range(self.ROUNDS-1):
            li = ((k[i]+(((l[i]>>8)|(l[i]<<56))&M))&M)^i; l.append(li)
            k.append((((k[i]<<3)|(k[i]>>61))&M)^li)
        self.rk = k

class PrimeARX:
    PRIMES = [2,3,5,7,11,13,17,19,23,29,31,37,41,43,47,53]
    ROUNDS = 2
    def __init__(self, kb):
        M = MASK64
        k_lo = int.from_bytes(kb[:16],'big'); k_hi = int.from_bytes(kb[16:],'big')
        rk=[]; state=k_lo
        for i in range(self.ROUNDS):
            p=self.PRIMES[i]
            state=(((state<<(p&63))|(state>>(64-(p&63))))&M)^((k_hi+i)&M)
            state=(state+p*0x9E3779B97F4A7C15)&M; rk.append(state)
        self._rounds=[]
        for i in range(self.ROUNDS):
            r1=self.PRIMES[i]&63; r2=self.PRIMES[(i+8)%16]&63
            self._rounds.append((r1,64-r1,r2,64-r2,rk[i]))

def speck_ctr(speck, nonce, data):
    if not data: return b''
    n=len(data); out=bytearray(n); M=MASK64; rk=speck.rk
    n_hi=int.from_bytes(nonce[:8],'big'); n_lo=int.from_bytes(nonce[8:12],'big')<<32
    for blk in range(0,n,16):
        x=n_hi; y=n_lo|(blk>>4&0xFFFFFFFF)
        for k in rk:
            x=((((x>>8)|(x<<56))&M)+y)&M^k; y=(((y<<3)|(y>>61))&M)^x
        ks=x.to_bytes(8,'big')+y.to_bytes(8,'big')
        end=min(blk+16,n); cl=end-blk
        di=int.from_bytes(data[blk:end],'big'); ki=int.from_bytes(ks[:cl],'big')
        out[blk:end]=(di^ki).to_bytes(cl,'big')
    return bytes(out)

def arx_ctr(arx, nonce, data):
    if not data: return b''
    n=len(data); out=bytearray(n); M=MASK64; rounds=arx._rounds
    n_hi=int.from_bytes(nonce[:8],'big'); n_lo=int.from_bytes(nonce[8:12],'big')<<32
    for blk in range(0,n,16):
        x=n_hi; y=n_lo|(blk>>4&0xFFFFFFFF)
        for r1,r1i,r2,r2i,rk in rounds:
            x=((x>>r1)|(x<<r1i))&M; x=((x+y)&M)^rk; y=(((y<<r2)|(y>>r2i))&M)^x
        ks=x.to_bytes(8,'big')+y.to_bytes(8,'big')
        end=min(blk+16,n); cl=end-blk
        di=int.from_bytes(data[blk:end],'big'); ki=int.from_bytes(ks[:cl],'big')
        out[blk:end]=(di^ki).to_bytes(cl,'big')
    return bytes(out)

def sha256_ctr(key, nonce, data):
    if not data: return b''
    n=len(data); out=bytearray(n); prefix=key+nonce
    for i in range(0,n,32):
        ks=hashlib.sha256(prefix+struct.pack('!I',i//32)).digest()
        end=min(i+32,n); cl=end-i
        di=int.from_bytes(data[i:end],'big'); ki=int.from_bytes(ks[:cl],'big')
        out[i:end]=(di^ki).to_bytes(cl,'big')
    return bytes(out)

class CryptoV3:
    NONCE_SZ=12; MAC_SZ=16; RAW_OH=28

    def __init__(self, psk):
        self.psk = psk.encode('utf-8') if isinstance(psk,str) else psk
        self.arx=None; self.speck=None
        self.sha_key=None; self.mac_key=None
        self.sub_fwd=None; self.sub_inv=None
        self.tx_ctr=0; self.rx_high=-1; self._seen=set()
        self.dh_private=None; self.dh_public=None

    def generate_dh(self):
        self.dh_private, self.dh_public = x25519_keypair()
        return self.dh_public

    def derive(self, client_nonce, server_nonce, client_pub=None, server_pub=None):
        if client_pub and server_pub and self.dh_private:
            other = server_pub if client_pub == self.dh_public else client_pub
            shared = x25519(self.dh_private, other)
            self.dh_private = None
            ikm = self.psk + shared
        else:
            ikm = self.psk
        salt = client_nonce + server_nonce
        prk = hmac.new(salt, ikm, hashlib.sha256).digest()
        t=b''; okm=b''
        for i in range(1,7):
            t=hmac.new(prk,t+b'icmpvnc-v3'+bytes([i]),hashlib.sha256).digest()
            okm+=t
        self.arx = PrimeARX(okm[0:32]); self.speck = Speck128_256(okm[32:64])
        self.sha_key = okm[64:96]; self.mac_key = okm[96:128]
        self.sub_fwd, self.sub_inv = obf_sub_init(int.from_bytes(okm[129:137],'big'))
        self.tx_ctr=0; self.rx_high=-1; self._seen=set()

    def encrypt(self, data, light=False):
        nonce = struct.pack('!I',0)+struct.pack('!Q',self.tx_ctr); self.tx_ctr+=1
        if light:
            ct = sha256_ctr(self.sha_key, nonce, data)
        else:
            ct = obf_bitrev(data)
            ct = obf_sub_enc(ct, self.sub_fwd)
            ct = arx_ctr(self.arx, nonce, ct)
            ct = speck_ctr(self.speck, nonce, ct)
            ct = sha256_ctr(self.sha_key, nonce, ct)
        tag = hmac.new(self.mac_key, nonce+ct, hashlib.sha256).digest()[:16]
        return nonce+tag+ct

    def decrypt(self, data, light=False):
        raw = data
        if len(raw) < self.RAW_OH: return None
        nonce=raw[:12]; tag=raw[12:28]; ct=raw[28:]
        exp = hmac.new(self.mac_key, nonce+ct, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, exp): return None
        ctr = struct.unpack('!Q', nonce[4:])[0]
        if ctr in self._seen: return None
        if self.rx_high >= 0 and ctr <= self.rx_high - 2048: return None
        self._seen.add(ctr)
        if ctr > self.rx_high:
            self.rx_high = ctr
            self._seen = {c for c in self._seen if c > ctr - 2048}
        if light:
            return sha256_ctr(self.sha_key, nonce, ct)
        ct = sha256_ctr(self.sha_key, nonce, ct)
        ct = speck_ctr(self.speck, nonce, ct)
        ct = arx_ctr(self.arx, nonce, ct)
        ct = obf_sub_dec(ct, self.sub_inv)
        return obf_bitrev(ct)

    @staticmethod
    def max_plaintext(max_payload, proto_overhead):
        avail = max_payload - proto_overhead
        return max(0, avail - CryptoV3.RAW_OH)

_C_SRC = r"""
#include <stddef.h>
void bgra_to_rgb(const unsigned char *s, unsigned char *d,
                 int w, int h, int stride, int sc) {
    int ow = w / sc, oh = h / sc, di = 0;
    for (int y = 0; y < oh; y++) {
        const unsigned char *r = s + (y * sc) * stride;
        for (int x = 0; x < ow; x++) {
            const unsigned char *p = r + (x * sc) * 4;
            d[di++] = p[2]; d[di++] = p[1]; d[di++] = p[0];
        }
    }
}
void xor_bytes(const unsigned char *a, const unsigned char *b,
               unsigned char *o, size_t n) {
    for (size_t i = 0; i < n; i++) o[i] = a[i] ^ b[i];
}
"""
_native = None

def _compile_native():
    global _native
    try:
        td = tempfile.mkdtemp(prefix='icmpvnc_')
        cf = os.path.join(td, 'helper.c')
        sf = os.path.join(td, 'helper.so')
        with open(cf, 'w') as f: f.write(_C_SRC)
        r = subprocess.run(['gcc', '-shared', '-O2', '-fPIC', '-o', sf, cf],
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            _native = ctypes.CDLL(sf)
            _native.bgra_to_rgb.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            _native.bgra_to_rgb.restype = None
            _native.xor_bytes.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_size_t]
            _native.xor_bytes.restype = None
            _vlog("Native helper compiled (gcc)")
        else:
            _vlog("gcc compile failed, using pure Python fallback")
    except Exception as e:
        _vlog(f"Native compile failed ({e}), using pure Python fallback")

def _xor_bytes(a, b):
    """XOR two byte strings, returns bytes."""
    if _native and len(a) == len(b):
        n = len(a)
        ba = (ctypes.c_char * n).from_buffer_copy(a)
        bb = (ctypes.c_char * n).from_buffer_copy(b)
        out = (ctypes.c_char * n)()
        _native.xor_bytes(ba, bb, out, n)
        return bytes(out)
    ai = int.from_bytes(a, 'little')
    bi = int.from_bytes(b, 'little')
    return (ai ^ bi).to_bytes(len(a), 'little')

class XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ('x', ctypes.c_int), ('y', ctypes.c_int),
        ('width', ctypes.c_int), ('height', ctypes.c_int),
        ('border_width', ctypes.c_int), ('depth', ctypes.c_int),
        ('visual', ctypes.c_void_p), ('root', ctypes.c_ulong),
        ('_class', ctypes.c_int),
        ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int),
        ('backing_store', ctypes.c_int),
        ('backing_planes', ctypes.c_ulong), ('backing_pixel', ctypes.c_ulong),
        ('save_under', ctypes.c_int), ('colormap', ctypes.c_ulong),
        ('map_installed', ctypes.c_int), ('map_state', ctypes.c_int),
        ('all_event_masks', ctypes.c_long), ('your_event_masks', ctypes.c_long),
        ('do_not_propagate_mask', ctypes.c_long),
        ('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p),
    ]

class XShmSegmentInfo(ctypes.Structure):
    _fields_ = [
        ('shmseg', ctypes.c_ulong), ('shmid', ctypes.c_int),
        ('shmaddr', ctypes.c_void_p), ('readOnly', ctypes.c_int),
    ]

class XImage(ctypes.Structure):
    _fields_ = [
        ('width', ctypes.c_int), ('height', ctypes.c_int),
        ('xoffset', ctypes.c_int), ('format', ctypes.c_int),
        ('data', ctypes.c_void_p),
        ('byte_order', ctypes.c_int), ('bitmap_unit', ctypes.c_int),
        ('bitmap_bit_order', ctypes.c_int), ('bitmap_pad', ctypes.c_int),
        ('depth', ctypes.c_int), ('bytes_per_line', ctypes.c_int),
        ('bits_per_pixel', ctypes.c_int),
        ('red_mask', ctypes.c_ulong), ('green_mask', ctypes.c_ulong),
        ('blue_mask', ctypes.c_ulong),
    ]

class XFixesCursorImage(ctypes.Structure):
    _fields_ = [
        ('x', ctypes.c_short), ('y', ctypes.c_short),
        ('width', ctypes.c_ushort), ('height', ctypes.c_ushort),
        ('xhot', ctypes.c_ushort), ('yhot', ctypes.c_ushort),
        ('cursor_serial', ctypes.c_ulong),
        ('pixels', ctypes.POINTER(ctypes.c_ulong)),
        ('name', ctypes.c_ulong),
    ]

IPC_PRIVATE = 0; IPC_CREAT = 0o1000; IPC_RMID = 0
ZPixmap = 2; AllPlanes = (1 << 64) - 1

class ScreenCapture:
    """X11 screen capture using SHM (fast) or XGetImage (fallback)."""
    def __init__(self, scale=1):
        self.scale = max(1, min(6, scale))
        self.autoscale_floor = self.scale
        self.delta_ratios = []
        self.last_scale_time = 0
        self._autoscale_pending = False
        self._zero_row = None; self._zero_row_w = 0
        self.use_shm = False
        self.display = None; self.root = 0
        self.scr_w = 0; self.scr_h = 0; self.depth = 0; self.visual = None
        self.ximage_ptr = None; self.shm_info = None; self.shmid = -1
        self._shm_method = None; self._memfd = -1; self._shm_sz = 0
        self.prev_rgb = None; self.frame_id = 0
        self.x11 = None; self.xext = None
        self._load_x11()
        self._sudo_uid = int(os.environ.get('SUDO_UID', 0))
        dropped = False
        if self._sudo_uid and os.geteuid() == 0:
            try:
                os.seteuid(self._sudo_uid)
                dropped = True
                _vlog(f"Dropped to UID {self._sudo_uid} for X11/SHM setup")
            except OSError as e:
                _vlog(f"Could not seteuid({self._sudo_uid}): {e}")
                self._sudo_uid = 0
        self._open_display()
        self._try_shm()
        if dropped:
            os.seteuid(0)
            _vlog("Restored root (UID 0) for network operations")
        register_cleanup(self.close)
        self.cursor_visible = True
        self.xfixes = None
        try:
            xf = ctypes.CDLL('libXfixes.so.3')
            ev = ctypes.c_int(); err = ctypes.c_int()
            xf.XFixesQueryExtension.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
            xf.XFixesQueryExtension(self.display, ctypes.byref(ev), ctypes.byref(err))
            xf.XFixesGetCursorImage.argtypes = [ctypes.c_void_p]
            xf.XFixesGetCursorImage.restype = ctypes.POINTER(XFixesCursorImage)
            self.x11.XFree.argtypes = [ctypes.c_void_p]
            self.xfixes = xf
            _vlog("XFixes: cursor compositing enabled")
        except Exception:
            pass

    def _load_x11(self):
        self.x11 = ctypes.CDLL('libX11.so.6')
        x = self.x11
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]; x.XOpenDisplay.restype = ctypes.c_void_p
        x.XDefaultScreen.argtypes = [ctypes.c_void_p]; x.XDefaultScreen.restype = ctypes.c_int
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]; x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XWindowAttributes)]
        x.XGetWindowAttributes.restype = ctypes.c_int
        x.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        x.XGetImage.restype = ctypes.POINTER(XImage)
        x.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]; x.XDestroyImage.restype = ctypes.c_int
        self._xerror_fn_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        x.XSetErrorHandler.argtypes = [self._xerror_fn_type]; x.XSetErrorHandler.restype = ctypes.c_void_p
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]; x.XSync.restype = ctypes.c_int
        self._xerror_occurred = False
        def _xerror_handler(display, event):
            self._xerror_occurred = True
            return 0
        self._xerror_cb = self._xerror_fn_type(_xerror_handler)
        try:
            self.xext = ctypes.CDLL('libXext.so.6')
            xe = self.xext
            xe.XShmQueryExtension.argtypes = [ctypes.c_void_p]; xe.XShmQueryExtension.restype = ctypes.c_int
            xe.XShmCreateImage.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XShmSegmentInfo),
                ctypes.c_uint, ctypes.c_uint]
            xe.XShmCreateImage.restype = ctypes.POINTER(XImage)
            xe.XShmAttach.argtypes = [ctypes.c_void_p, ctypes.POINTER(XShmSegmentInfo)]
            xe.XShmAttach.restype = ctypes.c_int
            xe.XShmGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XImage),
                ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            xe.XShmGetImage.restype = ctypes.c_int
            xe.XShmDetach.argtypes = [ctypes.c_void_p, ctypes.POINTER(XShmSegmentInfo)]
            xe.XShmDetach.restype = ctypes.c_int
        except:
            self.xext = None

    def _open_display(self):
        if 'DISPLAY' not in os.environ:
            os.environ['DISPLAY'] = ':0'
            _vlog("DISPLAY not set, defaulting to :0")
        if 'XAUTHORITY' not in os.environ:
            for user_dir in ['/home', '/root']:
                try:
                    for u in os.listdir(user_dir):
                        xa = os.path.join(user_dir, u, '.Xauthority')
                        if os.path.isfile(xa):
                            os.environ['XAUTHORITY'] = xa
                            _vlog(f"XAUTHORITY not set, using {xa}")
                            break
                except: pass
                if 'XAUTHORITY' in os.environ: break
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            self.display = self.x11.XOpenDisplay(b':0')
        if not self.display:
            raise RuntimeError("Cannot open X11 display (try: sudo -E or export DISPLAY=:0)")
        self.x11.XSetErrorHandler(self._xerror_cb)
        self.root = self.x11.XDefaultRootWindow(self.display)
        attrs = XWindowAttributes()
        self.x11.XGetWindowAttributes(self.display, self.root, ctypes.byref(attrs))
        self.scr_w = attrs.width; self.scr_h = attrs.height
        self.depth = attrs.depth; self.visual = attrs.visual
        _vlog(f"X11 display: {self.scr_w}x{self.scr_h} depth={self.depth}")

    def _try_shm(self):
        if not self.xext: return
        if not self.xext.XShmQueryExtension(self.display):
            _vlog("X11 SHM extension not available, using XGetImage"); return
        try:
            env = os.environ.copy()
            if 'DISPLAY' not in env: env['DISPLAY'] = ':0'
            r = subprocess.run(['xhost', '+local:root'], capture_output=True, timeout=3, env=env)
            if r.returncode == 0:
                _vlog("xhost: granted local root access to X display")
            else:
                _vlog("xhost: could not grant (non-fatal)")
        except FileNotFoundError:
            _vlog("xhost: not found (non-fatal)")
        except Exception:
            pass
        self.shm_info = XShmSegmentInfo()
        self.ximage_ptr = self.xext.XShmCreateImage(
            self.display, self.visual, self.depth, ZPixmap, None,
            ctypes.byref(self.shm_info), self.scr_w, self.scr_h)
        if not self.ximage_ptr:
            _vlog("XShmCreateImage failed, using XGetImage"); self.shm_info = None; return
        ximg = self.ximage_ptr.contents
        sz = ximg.bytes_per_line * ximg.height
        libc_shm = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        libc_shm.shmget.restype = ctypes.c_int
        libc_shm.shmat.restype = ctypes.c_void_p
        libc_shm.shmdt.restype = ctypes.c_int
        libc_shm.shmctl.restype = ctypes.c_int
        if self._try_shm_xcb_fd(libc_shm, sz):
            return
        if self._try_shm_memfd(libc_shm, sz):
            return
        if self._try_shm_sysv(libc_shm, sz):
            return
        _vlog("All SHM methods failed, using XGetImage (slower)")
        self.shm_info = None; self.ximage_ptr = None

    def _try_shm_xcb_fd(self, libc_shm, sz):
        """Try xcb_shm_attach_fd — uses libxcb-shm which has fd-passing even when libXext doesn't."""
        try:
            xcb = ctypes.CDLL('libxcb.so.1')
            xcb_shm = ctypes.CDLL('libxcb-shm.so.0')
            x11xcb = ctypes.CDLL('libX11-xcb.so.1')
        except OSError as e:
            _vlog(f"XCB-SHM libs not available: {e}"); return False
        try:
            xcb_shm.xcb_shm_attach_fd_checked
        except AttributeError:
            _vlog("xcb_shm_attach_fd not in libxcb-shm"); return False
        try:
            class xcb_void_cookie_t(ctypes.Structure):
                _fields_ = [('sequence', ctypes.c_uint)]
            x11xcb.XGetXCBConnection.argtypes = [ctypes.c_void_p]
            x11xcb.XGetXCBConnection.restype = ctypes.c_void_p
            xcb.xcb_generate_id.argtypes = [ctypes.c_void_p]
            xcb.xcb_generate_id.restype = ctypes.c_uint32
            xcb.xcb_flush.argtypes = [ctypes.c_void_p]
            xcb.xcb_flush.restype = ctypes.c_int
            xcb.xcb_request_check.argtypes = [ctypes.c_void_p, xcb_void_cookie_t]
            xcb.xcb_request_check.restype = ctypes.c_void_p
            xcb_shm.xcb_shm_attach_fd_checked.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint8]
            xcb_shm.xcb_shm_attach_fd_checked.restype = xcb_void_cookie_t
        except Exception as e:
            _vlog(f"XCB binding setup failed: {e}"); return False
        try:
            libc_shm.memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
            libc_shm.memfd_create.restype = ctypes.c_int
            libc_shm.ftruncate.argtypes = [ctypes.c_int, ctypes.c_long]
            libc_shm.ftruncate.restype = ctypes.c_int
            libc_shm.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_long]
            libc_shm.mmap.restype = ctypes.c_void_p
            libc_shm.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            libc_shm.munmap.restype = ctypes.c_int
        except Exception:
            return False
        PROT_RW = 0x1 | 0x2; MAP_SHARED = 0x01
        memfd = libc_shm.memfd_create(b"icmpvnc_shm", 0)
        if memfd < 0: return False
        if libc_shm.ftruncate(memfd, sz) != 0:
            os.close(memfd); return False
        addr = libc_shm.mmap(None, sz, PROT_RW, MAP_SHARED, memfd, 0)
        if addr == ctypes.c_void_p(-1).value:
            os.close(memfd); return False
        self.x11.XFlush(self.display)
        xcb_conn = x11xcb.XGetXCBConnection(self.display)
        if not xcb_conn:
            libc_shm.munmap(addr, sz); os.close(memfd); return False
        shmseg = xcb.xcb_generate_id(xcb_conn)
        memfd_for_xcb = os.dup(memfd)
        cookie = xcb_shm.xcb_shm_attach_fd_checked(xcb_conn, shmseg, memfd_for_xcb, 0)
        xcb.xcb_flush(xcb_conn)
        err = xcb.xcb_request_check(xcb_conn, cookie)
        if err:
            libc_shm.munmap(addr, sz); os.close(memfd)
            _vlog("xcb_shm_attach_fd: X server rejected"); return False
        self.shm_info.shmseg = shmseg
        self.shm_info.shmaddr = addr; self.shm_info.readOnly = 0
        ctypes.cast(ctypes.byref(self.ximage_ptr.contents, XImage.data.offset),
                    ctypes.POINTER(ctypes.c_void_p)).contents.value = addr
        self.x11.XSync(self.display, 0)
        self.use_shm = True; self._libc_shm = libc_shm
        self._shm_method = 'xcb_fd'; self._memfd = memfd; self._shm_sz = sz
        self._xcb = xcb; self._xcb_shm = xcb_shm; self._xcb_conn = xcb_conn
        self._xcb_shmseg = shmseg
        _vlog(f"SHM: xcb_shm_attach_fd (memfd) — {sz:,} bytes")
        return True

    def _try_shm_memfd(self, libc_shm, sz):
        """Try XShmAttachFd with memfd_create — no UID restrictions."""
        try:
            self.xext.XShmAttachFd.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(XShmSegmentInfo),
                ctypes.c_int, ctypes.c_int]
            self.xext.XShmAttachFd.restype = ctypes.c_int
        except AttributeError:
            _vlog("XShmAttachFd not in libXext (need libXext ≥1.3.3)")
            return False
        try:
            libc_shm.memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
            libc_shm.memfd_create.restype = ctypes.c_int
            libc_shm.ftruncate.argtypes = [ctypes.c_int, ctypes.c_long]
            libc_shm.ftruncate.restype = ctypes.c_int
            libc_shm.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_long]
            libc_shm.mmap.restype = ctypes.c_void_p
            libc_shm.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            libc_shm.munmap.restype = ctypes.c_int
        except Exception:
            _vlog("memfd/mmap not available")
            return False
        PROT_RW = 0x1 | 0x2; MAP_SHARED = 0x01
        memfd = libc_shm.memfd_create(b"icmpvnc_shm", 0)
        if memfd < 0:
            _vlog("memfd_create failed"); return False
        if libc_shm.ftruncate(memfd, sz) != 0:
            os.close(memfd); _vlog("ftruncate(memfd) failed"); return False
        addr = libc_shm.mmap(None, sz, PROT_RW, MAP_SHARED, memfd, 0)
        if addr == ctypes.c_void_p(-1).value:
            os.close(memfd); _vlog("mmap(memfd) failed"); return False
        self.shm_info.shmaddr = addr; self.shm_info.readOnly = 0
        ctypes.cast(ctypes.byref(self.ximage_ptr.contents, XImage.data.offset),
                    ctypes.POINTER(ctypes.c_void_p)).contents.value = addr
        self._xerror_occurred = False
        self.xext.XShmAttachFd(self.display, ctypes.byref(self.shm_info), memfd, 0)
        self.x11.XSync(self.display, 0)
        if self._xerror_occurred:
            _vlog("XShmAttachFd: X server rejected fd-based attach")
            libc_shm.munmap(addr, sz); os.close(memfd)
            return False
        self.use_shm = True; self._libc_shm = libc_shm
        self._shm_method = 'memfd'; self._memfd = memfd; self._shm_sz = sz
        _vlog(f"SHM: memfd + XShmAttachFd — {sz:,} bytes (no UID restrictions)")
        return True

    def _try_shm_sysv(self, libc_shm, sz):
        """Try XShmAttach with SysV shared memory (fallback)."""
        self.shmid = libc_shm.shmget(IPC_PRIVATE, sz, IPC_CREAT | 0o777)
        if self.shmid < 0:
            _vlog("shmget failed"); return False
        addr = libc_shm.shmat(self.shmid, None, 0)
        if addr == ctypes.c_void_p(-1).value:
            libc_shm.shmctl(self.shmid, IPC_RMID, None)
            _vlog("shmat failed"); return False
        self.shm_info.shmaddr = addr; self.shm_info.readOnly = 0
        ctypes.cast(ctypes.byref(self.ximage_ptr.contents, XImage.data.offset),
                    ctypes.POINTER(ctypes.c_void_p)).contents.value = addr
        self._xerror_occurred = False
        self.xext.XShmAttach(self.display, ctypes.byref(self.shm_info))
        self.x11.XSync(self.display, 0)
        if self._xerror_occurred:
            _vlog("XShmAttach failed (BadAccess — UID mismatch)")
            libc_shm.shmdt(addr)
            libc_shm.shmctl(self.shmid, IPC_RMID, None); self.shmid = -1
            return False
        self.use_shm = True; self._libc_shm = libc_shm
        self._shm_method = 'sysv'; self._shm_sz = sz
        _vlog(f"SHM: SysV IPC + XShmAttach — {sz:,} bytes")
        return True

    def _composite_cursor(self, rgb_ba, w, h):
        ci = self.xfixes.XFixesGetCursorImage(self.display)
        if not ci: return
        c = ci.contents
        sc = self.scale
        cw = c.width; ch = c.height
        cx = c.x // sc - c.xhot // sc; cy = c.y // sc - c.yhot // sc
        for row in range(0, ch, sc):
            py = cy + row // sc
            if py < 0 or py >= h: continue
            for col in range(0, cw, sc):
                px = cx + col // sc
                if px < 0 or px >= w: continue
                argb = int(c.pixels[row * cw + col]) & 0xFFFFFFFF
                a = (argb >> 24) & 0xFF
                if a == 0: continue
                r = (argb >> 16) & 0xFF; g = (argb >> 8) & 0xFF; b = argb & 0xFF
                i = (py * w + px) * 3
                if a >= 255:
                    rgb_ba[i] = r; rgb_ba[i+1] = g; rgb_ba[i+2] = b
                else:
                    ia = 255 - a
                    rgb_ba[i] = (r * a + rgb_ba[i] * ia) // 255
                    rgb_ba[i+1] = (g * a + rgb_ba[i+1] * ia) // 255
                    rgb_ba[i+2] = (b * a + rgb_ba[i+2] * ia) // 255
        self.x11.XFree(ci)

    def grab(self, force_keyframe=False):
        raw_bgra, bpl = self._grab_pixels()
        rgb = self._bgra_to_rgb(raw_bgra, self.scr_w, self.scr_h, bpl, self.scale)
        out_w = self.scr_w // self.scale; out_h = self.scr_h // self.scale
        if self.xfixes and self.cursor_visible:
            rgb = bytearray(rgb)
            self._composite_cursor(rgb, out_w, out_h)
        first_row = 0; row_count = out_h
        scale_changed = False

        if force_keyframe:
            compressed = zlib.compress(rgb, 6)
            mode = 1
            self._autoscale_pending = False
        elif self.prev_rgb is None:
            if self._autoscale_pending:
                compressed = zlib.compress(rgb, 3)
                mode = 2
                self._autoscale_pending = False
            else:
                compressed = zlib.compress(rgb, 6)
                mode = 1
        else:
            delta = _xor_bytes(rgb, self.prev_rgb)

            row_sz = out_w * 3
            if self._zero_row_w != out_w:
                self._zero_row = b'\x00' * row_sz; self._zero_row_w = out_w
            zr = self._zero_row; fr = -1; lr = -1
            for r in range(out_h):
                if delta[r*row_sz:(r+1)*row_sz] != zr:
                    fr = r; break
            if fr >= 0:
                for r in range(out_h - 1, fr - 1, -1):
                    if delta[r*row_sz:(r+1)*row_sz] != zr:
                        lr = r; break

            if fr < 0:
                first_row = 0; row_count = 1
                strip = delta[:row_sz]
            else:
                first_row = fr; row_count = lr - fr + 1
                strip = delta[fr*row_sz:(lr+1)*row_sz]

            dirty_frac = row_count / out_h
            if dirty_frac > 0.6: level = 4
            elif dirty_frac > 0.3: level = 2
            else: level = 1
            compressed = zlib.compress(strip, level)

            delta_ratio = len(compressed) / len(rgb) if len(rgb) > 0 else 0
            mode = 0

            if len(compressed) > len(rgb) * 0.5:
                kf_level = 3 if len(self.delta_ratios) >= 3 and sum(self.delta_ratios[-3:])/3 > 0.30 else 6
                key_compressed = zlib.compress(rgb, kf_level)
                if len(key_compressed) <= len(compressed) or len(compressed) > len(rgb) * 0.7:
                    compressed = key_compressed; mode = 2
                    first_row = 0; row_count = out_h

            self.delta_ratios.append(delta_ratio)
            if len(self.delta_ratios) > 20: self.delta_ratios.pop(0)
            now = time.time()
            if now - self.last_scale_time > 2.0:
                if len(self.delta_ratios) >= 2:
                    avg2 = sum(self.delta_ratios[-2:]) / 2
                    if avg2 > 0.70 and self.scale < 5:
                        self.scale = min(6, self.scale + 2); scale_changed = True
                    elif avg2 > 0.35 and self.scale < 6:
                        self.scale += 1; scale_changed = True
                if not scale_changed and len(self.delta_ratios) >= 15:
                    avg15 = sum(self.delta_ratios[-15:]) / 15
                    if avg15 < 0.08 and self.scale > self.autoscale_floor:
                        self.scale -= 1; scale_changed = True
                if scale_changed:
                    self.last_scale_time = now
                    self.delta_ratios.clear()
                    self._autoscale_pending = True

        if scale_changed:
            self.prev_rgb = None
        else:
            self.prev_rgb = rgb
        fid = self.frame_id; self.frame_id += 1
        return compressed, mode, out_w, out_h, fid, first_row, row_count

    def _grab_pixels(self):
        """Returns (raw_bgra_bytes, bytes_per_line)."""
        if self.use_shm:
            self.xext.XShmGetImage(self.display, self.root, self.ximage_ptr,
                                   0, 0, AllPlanes)
            ximg = self.ximage_ptr.contents
            bpl = ximg.bytes_per_line; sz = bpl * ximg.height
            raw = ctypes.string_at(ximg.data, sz)
            return raw, bpl
        else:
            ximg_p = self.x11.XGetImage(self.display, self.root, 0, 0,
                                         self.scr_w, self.scr_h, AllPlanes, ZPixmap)
            if not ximg_p: raise RuntimeError("XGetImage failed")
            ximg = ximg_p.contents
            bpl = ximg.bytes_per_line; sz = bpl * ximg.height
            raw = ctypes.string_at(ximg.data, sz)
            self.x11.XDestroyImage(ximg_p)
            return raw, bpl

    def _bgra_to_rgb(self, bgra, w, h, stride, scale):
        out_w = w // scale; out_h = h // scale; out_sz = out_w * out_h * 3
        if _native:
            src = (ctypes.c_char * len(bgra)).from_buffer_copy(bgra)
            dst = (ctypes.c_char * out_sz)()
            _native.bgra_to_rgb(src, dst, w, h, stride, scale)
            return bytes(dst)
        out = bytearray(out_sz); di = 0
        for y in range(0, out_h * scale, scale):
            ro = y * stride
            for x in range(0, out_w * scale, scale):
                si = ro + x * 4
                out[di] = bgra[si+2]; out[di+1] = bgra[si+1]; out[di+2] = bgra[si]
                di += 3
        return bytes(out)

    def close(self):
        if self.use_shm and self.xext and self.display:
            try: self.xext.XShmDetach(self.display, ctypes.byref(self.shm_info))
            except: pass
            if self._shm_method in ('memfd', 'xcb_fd'):
                try: self._libc_shm.munmap(self.shm_info.shmaddr, self._shm_sz)
                except: pass
                try: os.close(self._memfd)
                except: pass
            else:
                try: self._libc_shm.shmdt(self.shm_info.shmaddr)
                except: pass
                try: self._libc_shm.shmctl(self.shmid, IPC_RMID, None)
                except: pass
        if self.display:
            try: self.x11.XCloseDisplay(self.display)
            except: pass
            self.display = None

class FrameBuffer:
    def __init__(self):
        self.frame_id = -1; self.is_key = True
        self.width = 0; self.height = 0
        self.first_row = 0; self.row_count = 0
        self.compressed = b''; self.chunks = []

    def update(self, frame_id, mode, w, h, compressed, chunk_sz, first_row=0, row_count=0):
        self.frame_id = frame_id; self.is_key = mode
        self.width = w; self.height = h
        self.first_row = first_row; self.row_count = row_count if row_count else h
        self.compressed = compressed
        self.chunks = [compressed[i:i+chunk_sz] for i in range(0, len(compressed), chunk_sz)]

    def header_bytes(self):
        return struct.pack('!IBHHHIHH', self.frame_id, self.is_key,
                           self.width, self.height, len(self.chunks), len(self.compressed),
                           self.first_row, self.row_count)

    def get_chunk(self, idx):
        if 0 <= idx < len(self.chunks): return self.chunks[idx]
        return b''

_KEYSYM_TO_LINUX = {
    0x61:30,0x62:48,0x63:46,0x64:32,0x65:18,0x66:33,0x67:34,0x68:35,
    0x69:23,0x6a:36,0x6b:37,0x6c:38,0x6d:50,0x6e:49,0x6f:24,0x70:25,
    0x71:16,0x72:19,0x73:31,0x74:20,0x75:22,0x76:47,0x77:17,0x78:45,
    0x79:21,0x7a:44,
    0x41:30,0x42:48,0x43:46,0x44:32,0x45:18,0x46:33,0x47:34,0x48:35,
    0x49:23,0x4a:36,0x4b:37,0x4c:38,0x4d:50,0x4e:49,0x4f:24,0x50:25,
    0x51:16,0x52:19,0x53:31,0x54:20,0x55:22,0x56:47,0x57:17,0x58:45,
    0x59:21,0x5a:44,
    0x30:11,0x31:2,0x32:3,0x33:4,0x34:5,0x35:6,0x36:7,0x37:8,0x38:9,0x39:10,
    0x20:57,0x2d:12,0x3d:13,0x5b:26,0x5d:27,0x5c:43,0x3b:39,0x27:40,
    0x60:41,0x2c:51,0x2e:52,0x2f:53,0x09:15,
    0x21:2,0x40:3,0x23:4,0x24:5,0x25:6,0x5e:7,0x26:8,0x2a:9,0x28:10,0x29:11,
    0x5f:12,0x2b:13,0x7e:41,0x7b:26,0x7d:27,0x7c:43,0x3a:39,0x22:40,
    0x3c:51,0x3e:52,0x3f:53,
    0xffbe:59,0xffbf:60,0xffc0:61,0xffc1:62,0xffc2:63,0xffc3:64,
    0xffc4:65,0xffc5:66,0xffc6:67,0xffc7:68,0xffc8:69,0xffc9:70,
    0xffe1:42,0xffe2:54,0xffe3:29,0xffe4:97,0xffe9:56,0xffea:100,
    0xffeb:125,0xffec:126,0xffe5:58,
    0xff0d:28,0xff08:14,0xff09:15,0xff1b:1,0xffff:111,0xff63:110,
    0xff50:102,0xff57:107,0xff55:104,0xff56:109,
    0xff51:105,0xff52:103,0xff53:106,0xff54:108,
    0xff13:119,0xff14:70,0xff61:99,0xff7f:69,
    0xffb0:82,0xffb1:79,0xffb2:80,0xffb3:81,0xffb4:75,0xffb5:76,
    0xffb6:77,0xffb7:71,0xffb8:72,0xffb9:73,
    0xff8d:96,0xffab:78,0xffad:74,0xffaa:55,0xffaf:98,0xffae:83,
}

class UInputBackend:
    """Input injection via /dev/uinput — works on X11, Wayland, TTY."""
    EV_SYN=0; EV_KEY=1; EV_REL=2; EV_ABS=3; SYN_REPORT=0
    ABS_X=0; ABS_Y=1; REL_WHEEL=8
    BTN_LEFT=0x110; BTN_RIGHT=0x111; BTN_MIDDLE=0x112
    UI_SET_EVBIT  = 0x40045564
    UI_SET_KEYBIT = 0x40045565
    UI_SET_RELBIT = 0x40045566
    UI_SET_ABSBIT = 0x40045567
    UI_DEV_SETUP  = 0x405c5503
    UI_ABS_SETUP  = 0x40185504
    UI_DEV_CREATE = 0x5501
    UI_DEV_DESTROY= 0x5502

    def __init__(self, scr_w=1920, scr_h=1080):
        self.fd = -1
        try:
            fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            raise RuntimeError(f"Cannot open /dev/uinput: {e}")
        self.fd = fd
        ioctl = fcntl.ioctl
        ioctl(fd, self.UI_SET_EVBIT, self.EV_KEY)
        ioctl(fd, self.UI_SET_EVBIT, self.EV_REL)
        ioctl(fd, self.UI_SET_EVBIT, self.EV_ABS)
        ioctl(fd, self.UI_SET_EVBIT, self.EV_SYN)
        for k in range(1, 249): ioctl(fd, self.UI_SET_KEYBIT, k)
        for b in (self.BTN_LEFT, self.BTN_MIDDLE, self.BTN_RIGHT):
            ioctl(fd, self.UI_SET_KEYBIT, b)
        ioctl(fd, self.UI_SET_RELBIT, self.REL_WHEEL)
        ioctl(fd, self.UI_SET_ABSBIT, self.ABS_X)
        ioctl(fd, self.UI_SET_ABSBIT, self.ABS_Y)
        abs_x = struct.pack('HHiiiiii', self.ABS_X, 0, 0, 0, scr_w, 0, 0, 0)
        abs_y = struct.pack('HHiiiiii', self.ABS_Y, 0, 0, 0, scr_h, 0, 0, 0)
        fcntl.ioctl(fd, self.UI_ABS_SETUP, abs_x)
        fcntl.ioctl(fd, self.UI_ABS_SETUP, abs_y)
        name = b'icmpvnc-virtual-input'
        setup = struct.pack('HHHH80sI', 0x03, 0x1234, 0x5678, 1,
                            name + b'\x00'*(80-len(name)), 0)
        fcntl.ioctl(fd, self.UI_DEV_SETUP, setup)
        fcntl.ioctl(fd, self.UI_DEV_CREATE)
        time.sleep(0.3)
        register_cleanup(self.close)
        _vlog("Input: uinput device created")

    def _write(self, etype, code, value):
        t = time.time(); sec = int(t); usec = int((t - sec) * 1e6)
        os.write(self.fd, struct.pack('QQHHi', sec, usec, etype, code, value))

    def _syn(self):
        self._write(self.EV_SYN, self.SYN_REPORT, 0)

    def inject_key(self, keysym, press):
        kc = _KEYSYM_TO_LINUX.get(keysym)
        if kc:
            self._write(self.EV_KEY, kc, 1 if press else 0)
            self._syn()

    def inject_motion(self, x, y):
        self._write(self.EV_ABS, self.ABS_X, x)
        self._write(self.EV_ABS, self.ABS_Y, y)
        self._syn()

    def inject_button(self, button, press):
        btn = {1: self.BTN_LEFT, 2: self.BTN_MIDDLE, 3: self.BTN_RIGHT}.get(button, self.BTN_LEFT)
        self._write(self.EV_KEY, btn, 1 if press else 0)
        self._syn()

    def inject_scroll(self, direction):
        self._write(self.EV_REL, self.REL_WHEEL, 1 if direction == 4 else -1)
        self._syn()

    def close(self):
        if self.fd >= 0:
            try: fcntl.ioctl(self.fd, self.UI_DEV_DESTROY)
            except: pass
            os.close(self.fd); self.fd = -1


class XTestInputBackend:
    """Input injection via XTest — X11 only fallback."""
    def __init__(self, x11_lib, display):
        self.x11 = x11_lib; self.display = display
        try:
            self.xtst = ctypes.CDLL('libXtst.so.6')
            self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            self.xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            self.xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            self.x11.XKeysymToKeycode.restype = ctypes.c_uint8
            self.x11.XFlush.argtypes = [ctypes.c_void_p]
        except Exception as e:
            raise RuntimeError(f"XTest: {e}")
        _vlog("Input: XTest (X11 fallback)")

    def inject_key(self, keysym, press):
        kc = self.x11.XKeysymToKeycode(self.display, keysym)
        if kc:
            self.xtst.XTestFakeKeyEvent(self.display, kc, int(bool(press)), 0)
            self.x11.XFlush(self.display)

    def inject_motion(self, x, y):
        self.xtst.XTestFakeMotionEvent(self.display, -1, x, y, 0)
        self.x11.XFlush(self.display)

    def inject_button(self, button, press):
        self.xtst.XTestFakeButtonEvent(self.display, button, 1 if press else 0, 0)
        self.x11.XFlush(self.display)

    def inject_scroll(self, direction):
        self.xtst.XTestFakeButtonEvent(self.display, direction, 1, 0)
        self.xtst.XTestFakeButtonEvent(self.display, direction, 0, 0)
        self.x11.XFlush(self.display)

    def close(self): pass


class PipeWireCapture:
    """Screen capture via GStreamer + PipeWire ScreenCast portal."""
    def __init__(self, scale=1):
        self.scr_w = 0; self.scr_h = 0; self.scale = scale
        self.autoscale_floor = scale; self.prev_rgb = None
        self.frame_id = 0; self.delta_ratios = []; self.last_scale_time = 0
        self._autoscale_pending = False
        self._zero_row = b''; self._zero_row_w = 0
        self._frame = None; self._lock = threading.Lock()
        self._pipeline = None; self._loop = None
        try:
            import gi
            gi.require_version('Gst', '1.0')
            from gi.repository import Gst, GLib
            Gst.init(None)
            self._Gst = Gst; self._GLib = GLib
        except Exception as e:
            raise RuntimeError(f"GStreamer not available: {e}")
        node_id = self._portal_screencast()
        self._pipeline = Gst.parse_launch(
            f'pipewiresrc path={node_id} ! videoconvert ! '
            f'video/x-raw,format=RGB ! '
            f'appsink name=sink emit-signals=true max-buffers=2 drop=true')
        sink = self._pipeline.get_by_name('sink')
        sink.connect('new-sample', self._on_sample)
        self._pipeline.set_state(Gst.State.PLAYING)
        deadline = time.time() + 15
        while time.time() < deadline:
            with self._lock:
                if self._frame: break
            time.sleep(0.1)
        if not self._frame:
            self._pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("PipeWire: no frames in 15s")
        with self._lock:
            self.scr_w, self.scr_h = self._frame[:2]
        register_cleanup(self.close)
        _vlog(f"PipeWire capture: {self.scr_w}x{self.scr_h}")
        self._glib_thread = threading.Thread(target=self._run_glib, daemon=True)
        self._glib_thread.start()

    def _run_glib(self):
        try:
            loop = self._GLib.MainLoop()
            self._glib_loop = loop
            loop.run()
        except Exception: pass

    def _portal_screencast(self):
        """Request screen cast via XDG Desktop Portal."""
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        portal = bus.get_object('org.freedesktop.portal.Desktop',
                                '/org/freedesktop/portal/desktop')
        sc = dbus.Interface(portal, 'org.freedesktop.portal.ScreenCast')
        token = f'icmpvnc_{os.getpid()}'
        response = [None]
        def on_resp(code, results):
            response[0] = results if code == 0 else {}
        response[0] = None
        rp = sc.CreateSession(dbus.Dictionary({
            'session_handle_token': token + '_s',
            'handle_token': token + '_r1'}, signature='sv'))
        bus.add_signal_receiver(on_resp, signal_name='Response',
            dbus_interface='org.freedesktop.portal.Request', path=rp)
        loop = self._GLib.MainLoop()
        self._GLib.timeout_add(8000, loop.quit)
        loop.run()
        if not response[0]: raise RuntimeError("Portal: CreateSession timeout")
        session = str(response[0].get('session_handle', ''))
        response[0] = None
        rp = sc.SelectSources(dbus.ObjectPath(session), dbus.Dictionary({
            'types': dbus.UInt32(1), 'multiple': dbus.Boolean(False),
            'cursor_mode': dbus.UInt32(2),
            'handle_token': token + '_r2'}, signature='sv'))
        bus.add_signal_receiver(on_resp, signal_name='Response',
            dbus_interface='org.freedesktop.portal.Request', path=rp)
        loop = self._GLib.MainLoop()
        self._GLib.timeout_add(30000, loop.quit)
        loop.run()
        response[0] = None
        rp = sc.Start(dbus.ObjectPath(session), '', dbus.Dictionary({
            'handle_token': token + '_r3'}, signature='sv'))
        bus.add_signal_receiver(on_resp, signal_name='Response',
            dbus_interface='org.freedesktop.portal.Request', path=rp)
        loop = self._GLib.MainLoop()
        self._GLib.timeout_add(10000, loop.quit)
        loop.run()
        if not response[0]: raise RuntimeError("Portal: Start timeout")
        streams = response[0].get('streams', [])
        if not streams: raise RuntimeError("Portal: no streams")
        return int(streams[0][0])

    def _on_sample(self, sink):
        sample = sink.emit('pull-sample')
        if not sample: return self._Gst.FlowReturn.ERROR
        buf = sample.get_buffer(); caps = sample.get_caps()
        s = caps.get_structure(0)
        w = s.get_value('width'); h = s.get_value('height')
        ok, mi = buf.map(self._Gst.MapFlags.READ)
        if ok:
            with self._lock:
                self._frame = (w, h, bytes(mi.data))
            buf.unmap(mi)
        return self._Gst.FlowReturn.OK

    def _grab_pixels(self):
        """Returns (rgb_bytes, w, h) or raises."""
        with self._lock:
            if self._frame:
                w, h, rgb = self._frame
                return rgb, w, h
        return None, 0, 0

    def grab(self, force_keyframe=False):
        rgb, raw_w, raw_h = self._grab_pixels()
        if not rgb: return None, 0, 0, 0, 0, 0, 0
        self.scr_w = raw_w; self.scr_h = raw_h
        sc = self.scale; out_w = raw_w // sc; out_h = raw_h // sc
        if sc > 1:
            out = bytearray(out_w * out_h * 3); di = 0
            for y in range(0, out_h * sc, sc):
                ro = y * raw_w * 3
                for x in range(0, out_w * sc, sc):
                    si = ro + x * 3
                    out[di] = rgb[si]; out[di+1] = rgb[si+1]; out[di+2] = rgb[si+2]; di += 3
            rgb = bytes(out)
        first_row = 0; row_count = out_h
        if force_keyframe or self.prev_rgb is None or len(rgb) != len(self.prev_rgb):
            compressed = zlib.compress(rgb, 6); mode = 1
        else:
            delta = _xor_bytes(rgb, self.prev_rgb)
            compressed = zlib.compress(delta, 1); mode = 0
        self.prev_rgb = bytearray(rgb) if not force_keyframe else None
        if mode != 1: self.prev_rgb = bytearray(rgb)
        fid = self.frame_id; self.frame_id += 1
        return compressed, mode, out_w, out_h, fid, first_row, row_count

    def close(self):
        if self._pipeline:
            try: self._pipeline.set_state(self._Gst.State.NULL)
            except: pass
        if hasattr(self, '_glib_loop') and self._glib_loop:
            try: self._glib_loop.quit()
            except: pass


class DRMCapture:
    def __init__(self, scale=1):
        self.scr_w = 0; self.scr_h = 0; self.scale = scale
        self.autoscale_floor = scale; self.prev_rgb = None
        self.frame_id = 0; self.delta_ratios = []; self.last_scale_time = 0
        self._autoscale_pending = False
        self._zero_row = b''; self._zero_row_w = 0
        self._drm_fd = -1; self._fb_w = 0; self._fb_h = 0
        self._fb_stride = 0; self._fb_handle = 0; self._fb_bpp = 0
        self._mode = None
        self._mmap_ptr = None; self._mmap_sz = 0
        self._gbm = None; self._gbm_dev = None; self._gbm_bo = None
        self._open_drm()

    def _iowr(self, nr, sz):
        return 0xC0000000 | (sz << 16) | (0x64 << 8) | nr

    def _open_drm(self):
        for i in range(4):
            path = f'/dev/dri/card{i}'
            if not os.path.exists(path): continue
            try: self._drm_fd = os.open(path, os.O_RDWR); break
            except: continue
        if self._drm_fd < 0: raise RuntimeError("No DRM device found")
        res = bytearray(64)
        struct.pack_into('QQQQ IIII', res, 0, 0,0,0,0, 0,0,0,0)
        try: fcntl.ioctl(self._drm_fd, self._iowr(0xA0, 64), res)
        except Exception as e:
            os.close(self._drm_fd); raise RuntimeError(f"DRM getresources: {e}")
        n_fb, n_crtc, n_conn, n_enc = struct.unpack_from('IIII', res, 32)
        if n_crtc == 0: raise RuntimeError("DRM: no CRTCs")
        fbs = (ctypes.c_uint32 * max(n_fb,1))(); crtcs = (ctypes.c_uint32 * max(n_crtc,1))()
        conns = (ctypes.c_uint32 * max(n_conn,1))(); encs = (ctypes.c_uint32 * max(n_enc,1))()
        struct.pack_into('QQQQ IIII', res, 0,
            ctypes.addressof(fbs), ctypes.addressof(crtcs),
            ctypes.addressof(conns), ctypes.addressof(encs),
            n_fb, n_crtc, n_conn, n_enc)
        fcntl.ioctl(self._drm_fd, self._iowr(0xA0, 64), res)
        crtc_data = bytearray(128); found = False
        for ci in range(n_crtc):
            struct.pack_into('I', crtc_data, 0, crtcs[ci])
            try:
                fcntl.ioctl(self._drm_fd, self._iowr(0xA1, 128), crtc_data)
                fb_id = struct.unpack_from('I', crtc_data, 52)[0]
                if fb_id > 0:
                    self._fb_id = fb_id; found = True; break
            except: continue
        if not found: raise RuntimeError("DRM: no active CRTC/framebuffer")
        fb_info = bytearray(28)
        struct.pack_into('I', fb_info, 0, self._fb_id)
        fcntl.ioctl(self._drm_fd, self._iowr(0xAD, 28), fb_info)
        self._fb_w = struct.unpack_from('I', fb_info, 4)[0]
        self._fb_h = struct.unpack_from('I', fb_info, 8)[0]
        self._fb_stride = struct.unpack_from('I', fb_info, 12)[0]
        self._fb_bpp = struct.unpack_from('I', fb_info, 16)[0]
        self._fb_handle = struct.unpack_from('I', fb_info, 20)[0]
        self.scr_w = self._fb_w; self.scr_h = self._fb_h
        if self._try_mmap_dumb():
            register_cleanup(self.close)
            _vlog(f"DRM capture: {self._fb_w}x{self._fb_h} (linear mmap)")
            return
        if self._try_gbm():
            register_cleanup(self.close)
            _vlog(f"DRM capture: {self._fb_w}x{self._fb_h} (GBM de-tiled)")
            return
        raise RuntimeError("DRM: both linear mmap and GBM failed")

    def _try_mmap_dumb(self):
        map_req = bytearray(16)
        struct.pack_into('II', map_req, 0, self._fb_handle, 0)
        try:
            fcntl.ioctl(self._drm_fd, self._iowr(0xB3, 16), map_req)
            offset = struct.unpack_from('Q', map_req, 8)[0]
            self._mmap_sz = self._fb_stride * self._fb_h
            self._mmap_ptr = mmap.mmap(self._drm_fd, self._mmap_sz,
                                        mmap.MAP_SHARED, mmap.PROT_READ, offset=offset)
            self._mode = 'mmap'; return True
        except Exception as e:
            _vlog(f"DRM linear mmap failed ({e}), trying GBM...")
            return False

    def _try_gbm(self):
        try: self._gbm = ctypes.CDLL('libgbm.so.1')
        except OSError:
            _vlog("libgbm.so.1 not available"); return False
        gbm = self._gbm
        gbm.gbm_create_device.argtypes = [ctypes.c_int]
        gbm.gbm_create_device.restype = ctypes.c_void_p
        gbm.gbm_bo_import.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                        ctypes.c_void_p, ctypes.c_uint32]
        gbm.gbm_bo_import.restype = ctypes.c_void_p
        gbm.gbm_bo_map.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.POINTER(ctypes.c_uint32),
                                     ctypes.POINTER(ctypes.c_void_p)]
        gbm.gbm_bo_map.restype = ctypes.c_void_p
        gbm.gbm_bo_unmap.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gbm.gbm_bo_destroy.argtypes = [ctypes.c_void_p]
        gbm.gbm_device_destroy.argtypes = [ctypes.c_void_p]
        prime_req = bytearray(12)
        struct.pack_into('II', prime_req, 0, self._fb_handle, 2)
        try: fcntl.ioctl(self._drm_fd, self._iowr(0x2D, 12), prime_req)
        except Exception as e:
            _vlog(f"PRIME_HANDLE_TO_FD failed: {e}"); return False
        dmabuf_fd = struct.unpack_from('i', prime_req, 8)[0]
        self._gbm_dev = gbm.gbm_create_device(self._drm_fd)
        if not self._gbm_dev:
            os.close(dmabuf_fd); return False
        fmt = 0x34325258 if self._fb_bpp == 32 else 0x34325241
        import_data = struct.pack('iIIII', dmabuf_fd, self._fb_w, self._fb_h,
                                   self._fb_stride, fmt)
        import_buf = ctypes.create_string_buffer(import_data)
        self._gbm_bo = gbm.gbm_bo_import(self._gbm_dev, 0x5503, import_buf, 0x0001)
        os.close(dmabuf_fd)
        if not self._gbm_bo:
            gbm.gbm_device_destroy(self._gbm_dev); self._gbm_dev = None
            _vlog("gbm_bo_import failed"); return False
        self._mode = 'gbm'; return True

    def _grab_pixels(self):
        w = self._fb_w; h = self._fb_h; stride = self._fb_stride
        if self._mode == 'mmap':
            self._mmap_ptr.seek(0)
            raw = self._mmap_ptr.read(self._mmap_sz)
        elif self._mode == 'gbm':
            map_stride = ctypes.c_uint32()
            map_data = ctypes.c_void_p()
            ptr = self._gbm.gbm_bo_map(self._gbm_bo, 0, 0, w, h, 0x01,
                                         ctypes.byref(map_stride), ctypes.byref(map_data))
            if not ptr: return bytes(w * h * 3)
            stride = map_stride.value
            raw = ctypes.string_at(ptr, stride * h)
            self._gbm.gbm_bo_unmap(self._gbm_bo, map_data)
        else:
            return bytes(w * h * 3)
        rgb = bytearray(w * h * 3); di = 0
        for y in range(h):
            ro = y * stride
            for x in range(w):
                si = ro + x * 4
                rgb[di]=raw[si+2]; rgb[di+1]=raw[si+1]; rgb[di+2]=raw[si]; di+=3
        return bytes(rgb)

    def grab(self, force_keyframe=False):
        rgb_full = self._grab_pixels()
        sc = self.scale; out_w = self.scr_w // sc; out_h = self.scr_h // sc
        if sc > 1:
            out = bytearray(out_w * out_h * 3); di = 0
            for y in range(0, out_h * sc, sc):
                ro = y * self.scr_w * 3
                for x in range(0, out_w * sc, sc):
                    si = ro + x * 3
                    out[di]=rgb_full[si]; out[di+1]=rgb_full[si+1]; out[di+2]=rgb_full[si+2]; di+=3
            rgb = bytes(out)
        else: rgb = rgb_full
        first_row = 0; row_count = out_h
        if force_keyframe or self.prev_rgb is None or len(rgb) != len(self.prev_rgb):
            compressed = zlib.compress(rgb, 6); mode = 1
        else:
            delta = _xor_bytes(rgb, self.prev_rgb)
            compressed = zlib.compress(delta, 1); mode = 0
        self.prev_rgb = bytearray(rgb) if not force_keyframe else None
        if mode != 1: self.prev_rgb = bytearray(rgb)
        fid = self.frame_id; self.frame_id += 1
        return compressed, mode, out_w, out_h, fid, first_row, row_count

    def close(self):
        if self._mode == 'mmap' and self._mmap_ptr:
            try: self._mmap_ptr.close()
            except: pass
        elif self._mode == 'gbm':
            if self._gbm_bo:
                try: self._gbm.gbm_bo_destroy(self._gbm_bo)
                except: pass
            if self._gbm_dev:
                try: self._gbm.gbm_device_destroy(self._gbm_dev)
                except: pass
        if self._drm_fd >= 0:
            try: os.close(self._drm_fd)
            except: pass
            self._drm_fd = -1

class FileBuffer:
    """Caches a file for chunked download serving."""
    def __init__(self):
        self.file_id = -1; self.filename = ''
        self.data = b''; self.chunks = []

    def load(self, path, chunk_sz):
        """Read file from disk and chunk it. Returns (success, error_msg)."""
        try:
            rpath = os.path.realpath(path)
            if not os.path.isfile(rpath):
                return False, f"Not found: {path}"
            sz = os.path.getsize(rpath)
            with open(rpath, 'rb') as f:
                self.data = f.read()
            self.filename = os.path.basename(rpath)
            self.file_id = (self.file_id + 1) & 0xFFFFFFFF
            self.chunks = [self.data[i:i+chunk_sz] for i in range(0, len(self.data), chunk_sz)]
            if not self.chunks:
                self.chunks = [b'']
            return True, None
        except PermissionError:
            return False, f"Permission denied: {path}"
        except Exception as e:
            return False, str(e)

    def header_bytes(self):
        name_b = self.filename.encode('utf-8')[:200]
        return struct.pack('!IIIB', self.file_id, len(self.data),
                           len(self.chunks), len(name_b)) + name_b

    def get_chunk(self, idx):
        if 0 <= idx < len(self.chunks): return self.chunks[idx]
        return b''

class UploadReceiver:
    """Receives chunked file uploads from client."""
    def __init__(self):
        self.file_id = -1; self.filename = ''
        self.total_size = 0; self.num_chunks = 0
        self.chunks = {}; self.save_dir = '.'

    def prepare(self, file_id, filename, total_size, num_chunks):
        self.file_id = file_id; self.filename = filename
        self.total_size = total_size; self.num_chunks = num_chunks
        self.chunks = {}
        return True

    def add_chunk(self, file_id, chunk_idx, data):
        if file_id != self.file_id: return False, "Wrong file_id"
        self.chunks[chunk_idx] = data
        if len(self.chunks) >= self.num_chunks:
            return True, self._save()
        return False, None

    def _save(self):
        data = b''.join(self.chunks.get(i, b'') for i in range(self.num_chunks))
        safe = os.path.basename(self.filename).replace('..', '_')
        if not safe: safe = f'upload_{self.file_id}'
        path = os.path.join(self.save_dir, safe)
        base, ext = os.path.splitext(path)
        counter = 1
        while os.path.exists(path):
            path = f"{base}_{counter}{ext}"; counter += 1
        try:
            with open(path, 'wb') as f:
                f.write(data)
            return f"Saved: {path} ({len(data):,} bytes)"
        except Exception as e:
            return f"Save failed: {e}"

class RawTransport:
    def __init__(self, iface, suppress_echo=False):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        if iface: self.sock.setsockopt(socket.SOL_SOCKET, 25, iface.encode() + b'\0')
        self.sock.setblocking(False)
        self._old_echo = None
        if suppress_echo: self._suppress()
        register_cleanup(self.close)

    def _suppress(self):
        try:
            with open('/proc/sys/net/ipv4/icmp_echo_ignore_all') as f: self._old_echo = f.read().strip()
            with open('/proc/sys/net/ipv4/icmp_echo_ignore_all', 'w') as f: f.write('1')
            _vlog("Suppressed kernel ICMP Echo replies")
        except: _vlog("Could not suppress kernel Echo replies")

    def _restore(self):
        if self._old_echo is not None:
            try:
                with open('/proc/sys/net/ipv4/icmp_echo_ignore_all', 'w') as f: f.write(self._old_echo)
                _vlog("Restored kernel ICMP Echo setting")
            except: pass

    def recv_batch(self):
        out = []
        for _ in range(BATCH_SIZE):
            try:
                data, addr = self.sock.recvfrom(65535)
                ihl = (data[0] & 0x0F) * 4
                if len(data) > ihl: out.append((bytearray(data[ihl:]), addr[0]))
            except BlockingIOError: break
            except: break
        return out

    def send(self, icmp_bytes, dst_ip):
        try: self.sock.sendto(bytes(icmp_bytes), (dst_ip, 0)); return True
        except: return False

    def fileno(self): return self.sock.fileno()
    def close(self):
        self._restore()
        try: self.sock.close()
        except: pass

SYS_bpf = 321
BPF_MAP_CREATE = 0; BPF_MAP_UPDATE_ELEM = 2; BPF_PROG_LOAD = 5
BPF_MAP_TYPE_XSKMAP = 17; BPF_PROG_TYPE_XDP = 6
XDP_FLAGS_SKB_MODE = 1 << 1
AF_XDP = 44; SOL_XDP = 283
XDP_MMAP_OFFSETS = 1; XDP_RX_RING = 2; XDP_TX_RING = 3
XDP_UMEM_REG = 4; XDP_UMEM_FILL_RING = 5; XDP_UMEM_COMPLETION_RING = 6; XDP_COPY = 1 << 1
XDP_PGOFF_RX_RING = 0; XDP_PGOFF_TX_RING = 0x80000000
XDP_UMEM_PGOFF_FILL_RING = 0x100000000; XDP_UMEM_PGOFF_COMPLETION_RING = 0x180000000
NETLINK_ROUTE = 0; RTM_SETLINK = 19; NLM_F_REQUEST = 1; NLM_F_ACK = 4
IFLA_XDP = 43; IFLA_XDP_FD = 1; IFLA_XDP_FLAGS = 3
MAP_FAILED = ctypes.c_void_p(-1).value

libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.mmap.restype = ctypes.c_void_p
libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]; libc.munmap.restype = ctypes.c_int
libc.memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]; libc.memfd_create.restype = ctypes.c_int
libc.ftruncate.argtypes = [ctypes.c_int, ctypes.c_long]; libc.ftruncate.restype = ctypes.c_int
libc.bind.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]; libc.bind.restype = ctypes.c_int
libc.sendto.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
libc.sendto.restype = ctypes.c_ssize_t

def _bi(op, dst, src, off, imm):
    if imm < 0: imm = imm & 0xFFFFFFFF
    return struct.pack('<BBhI', op, (src << 4) | dst, off, imm)

def build_xdp_bytecode(map_fd, drop_other=False):
    I = _bi; SID = 0x1111; HID = 0xEFBE; MAG = 0xFECA
    XDP_DROP = 1; XDP_PASS = 2
    insns = []
    def e(x): insns.append(x); return len(insns) - 1
    jp = []; jd = []; jr = []

    e(I(0x61,6,1,0,0)); e(I(0x61,7,1,4,0))
    e(I(0xbf,2,6,0,0)); e(I(0x07,2,0,0,14))
    jp.append(e(I(0x2d,2,7,0,0)))
    e(I(0x69,2,6,12,0))
    v4j = e(I(0x15,2,0,0,0x0008))
    v6j = e(I(0x15,2,0,0,0xDD86))
    jp.append(e(I(0x05,0,0,0,0)))

    v4s = len(insns)
    e(I(0xbf,2,6,0,0)); e(I(0x07,2,0,0,42))
    jp.append(e(I(0x2d,2,7,0,0)))
    e(I(0x71,2,6,23,0))
    v4i = e(I(0x15,2,0,0,1))
    jd.append(e(I(0x15,2,0,0,6)))
    jp.append(e(I(0x55,2,0,0,17)))
    e(I(0x69,2,6,34,0))
    jp.append(e(I(0x15,2,0,0,0x4300)))
    jp.append(e(I(0x15,2,0,0,0x4400)))
    e(I(0x69,2,6,36,0))
    jp.append(e(I(0x15,2,0,0,0x4300)))
    jp.append(e(I(0x15,2,0,0,0x4400)))
    jd.append(e(I(0x05,0,0,0,0)))

    v4is = len(insns)
    e(I(0x69,2,6,38,0))
    jr.append(e(I(0x15,2,0,0,SID)))
    jr.append(e(I(0x15,2,0,0,HID)))
    e(I(0xbf,2,6,0,0)); e(I(0x07,2,0,0,44))
    jp.append(e(I(0x2d,2,7,0,0)))
    e(I(0x69,2,6,42,0))
    jr.append(e(I(0x15,2,0,0,MAG)))
    jp.append(e(I(0x05,0,0,0,0)))

    v6s = len(insns)
    e(I(0xbf,2,6,0,0)); e(I(0x07,2,0,0,58))
    jp.append(e(I(0x2d,2,7,0,0)))
    e(I(0x71,2,6,20,0))
    v6i = e(I(0x15,2,0,0,58))
    jd.append(e(I(0x15,2,0,0,6)))
    jp.append(e(I(0x55,2,0,0,17)))
    e(I(0x69,2,6,54,0))
    jp.append(e(I(0x15,2,0,0,0x2202)))
    jp.append(e(I(0x15,2,0,0,0x2302)))
    e(I(0x69,2,6,56,0))
    jp.append(e(I(0x15,2,0,0,0x2202)))
    jp.append(e(I(0x15,2,0,0,0x2302)))
    jd.append(e(I(0x05,0,0,0,0)))

    v6is = len(insns)
    e(I(0xbf,2,6,0,0)); e(I(0x07,2,0,0,64))
    jp.append(e(I(0x2d,2,7,0,0)))
    e(I(0x69,2,6,58,0))
    jr.append(e(I(0x15,2,0,0,SID)))
    jr.append(e(I(0x15,2,0,0,HID)))
    e(I(0x69,2,6,62,0))
    jr.append(e(I(0x15,2,0,0,MAG)))
    jp.append(e(I(0x05,0,0,0,0)))

    pi = len(insns)
    e(I(0xb7,0,0,0,XDP_PASS)); e(I(0x95,0,0,0,0))
    di = len(insns)
    e(I(0xb7,0,0,0, XDP_DROP if drop_other else XDP_PASS)); e(I(0x95,0,0,0,0))
    ri = len(insns)
    insns.append(struct.pack('<BBhI', 0x18, (1<<4)|1, 0, map_fd) + struct.pack('<BBhI', 0, 0, 0, 0))
    e(I(0xb7,2,0,0,0)); e(I(0xb7,3,0,0,XDP_PASS)); e(I(0x85,0,0,0,0x33)); e(I(0x95,0,0,0,0))

    def fix(indices, target):
        for idx in indices:
            raw = bytearray(insns[idx])
            struct.pack_into('<h', raw, 2, target - idx - 1)
            insns[idx] = bytes(raw)
    fix(jp, pi); fix(jd, di); fix(jr, ri)
    for idx, tgt in [(v4j,v4s),(v6j,v6s),(v4i,v4is),(v6i,v6is)]:
        raw = bytearray(insns[idx])
        struct.pack_into('<h', raw, 2, tgt - idx - 1)
        insns[idx] = bytes(raw)
    return b''.join(insns)

def bpf_syscall(cmd, attr, sz): return libc.syscall(SYS_bpf, cmd, ctypes.byref(attr), sz)
def libc_setsockopt(fd, l, n, v, s): return libc.setsockopt(fd, l, n, ctypes.byref(v), s)
def libc_getsockopt(fd, l, n, v, s):
    o = ctypes.c_uint32(s); libc.getsockopt(fd, l, n, ctypes.byref(v), ctypes.byref(o)); return o.value

def create_xsk_map():
    class A(ctypes.Structure):
        _fields_ = [('mt',ctypes.c_uint32),('ks',ctypes.c_uint32),('vs',ctypes.c_uint32),
                    ('me',ctypes.c_uint32),('mf',ctypes.c_uint32),('imf',ctypes.c_uint32),
                    ('nn',ctypes.c_uint32),('mn',ctypes.c_char*16)]
    a = A(); a.mt = BPF_MAP_TYPE_XSKMAP; a.ks = 4; a.vs = 4; a.me = 1; a.mn = b'xsk_map'
    fd = bpf_syscall(BPF_MAP_CREATE, a, ctypes.sizeof(a))
    if fd < 0: raise OSError(ctypes.get_errno(), "map create failed")
    return fd

def load_xdp_prog(prog):
    ls = ctypes.create_string_buffer(b"GPL")
    insns = (ctypes.c_char * len(prog)).from_buffer_copy(prog)
    class A(ctypes.Structure):
        _fields_ = [('pt',ctypes.c_uint32),('ic',ctypes.c_uint32),('insns',ctypes.c_uint64),
                    ('lic',ctypes.c_uint64),('ll',ctypes.c_uint32),('ls',ctypes.c_uint32),
                    ('lb',ctypes.c_uint64),('kv',ctypes.c_uint32),('pf',ctypes.c_uint32),
                    ('pn',ctypes.c_char*16)]
    a = A(); a.pt = BPF_PROG_TYPE_XDP; a.ic = len(prog) // 8
    a.insns = ctypes.addressof(insns); a.lic = ctypes.addressof(ls); a.pn = b'icmpvnc'
    fd = bpf_syscall(BPF_PROG_LOAD, a, ctypes.sizeof(a))
    if fd < 0: raise OSError(ctypes.get_errno(), "prog load failed")
    return fd

def update_xsk_map(mfd, key, xfd):
    class A(ctypes.Structure):
        _fields_ = [('mf',ctypes.c_uint32),('p',ctypes.c_uint32),('k',ctypes.c_uint64),('v',ctypes.c_uint64),('fl',ctypes.c_uint64)]
    kv = ctypes.c_uint32(key); vv = ctypes.c_uint32(xfd)
    a = A(); a.mf = mfd; a.k = ctypes.addressof(kv); a.v = ctypes.addressof(vv)
    if bpf_syscall(BPF_MAP_UPDATE_ELEM, a, ctypes.sizeof(a)) < 0:
        raise OSError(ctypes.get_errno(), "map update failed")

def attach_xdp(ifidx, pfd):
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE); sock.bind((0,0))
    xfd = struct.pack('=HHi', 8, IFLA_XDP_FD, pfd)
    xfl = struct.pack('=HHI', 8, IFLA_XDP_FLAGS, XDP_FLAGS_SKB_MODE)
    xd = xfd + xfl; xa = struct.pack('=HH', 4+len(xd), IFLA_XDP) + xd
    while len(xa) % 4: xa += b'\x00'
    ifi = struct.pack('=BBHiII', 0, 0, 0, ifidx, 0, 0); ml = 16 + len(ifi) + len(xa)
    nl = struct.pack('=IHHII', ml, RTM_SETLINK, NLM_F_REQUEST|NLM_F_ACK, int(time.time()), 0)
    sock.send(nl + ifi + xa); sock.recv(4096); sock.close()

def detach_xdp(ifidx):
    try:
        sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE); sock.bind((0,0))
        xfd = struct.pack('=HHi', 8, IFLA_XDP_FD, -1)
        xa = struct.pack('=HH', 4+len(xfd), IFLA_XDP) + xfd
        while len(xa) % 4: xa += b'\x00'
        ifi = struct.pack('=BBHiII', 0, 0, 0, ifidx, 0, 0); ml = 16 + len(ifi) + len(xa)
        nl = struct.pack('=IHHII', ml, RTM_SETLINK, NLM_F_REQUEST|NLM_F_ACK, int(time.time()), 0)
        sock.send(nl + ifi + xa); sock.recv(4096); sock.close()
    except: pass

class XDPTransport:
    def __init__(self, iface, drop_other=False):
        self.iface = iface; self.ifidx = socket.if_nametoindex(iface)
        self.drop_other = drop_other
        self.map_fd = -1; self.prog_fd = -1; self.xsk = None; self.xsk_fd = -1
        self.umem = None; self.umem_sz = FRAME_SIZE * NUM_FRAMES; self.umem_fd = -1
        self.offs = None; self.frames = list(range(NUM_FRAMES)); self.ready = False
        self.rx_p = self.tx_p = self.fr_p = self.cr_p = None
        self.rx_s = self.tx_s = self.fr_s = self.cr_s = 0
        self.buf = None; self.closed = False
        register_cleanup(self.close)

    def setup(self):
        self.map_fd = create_xsk_map()
        self.prog_fd = load_xdp_prog(build_xdp_bytecode(self.map_fd, self.drop_other))
        attach_xdp(self.ifidx, self.prog_fd)
        self.umem_fd = libc.memfd_create(b'umem', 0); libc.ftruncate(self.umem_fd, self.umem_sz)
        self.umem = mmap.mmap(self.umem_fd, self.umem_sz, mmap.MAP_SHARED|mmap.MAP_POPULATE, mmap.PROT_READ|mmap.PROT_WRITE)
        self.xsk = socket.socket(AF_XDP, socket.SOCK_RAW, 0); self.xsk_fd = self.xsk.fileno()
        for o, v in [(XDP_RX_RING,RING_SIZE),(XDP_TX_RING,RING_SIZE),(XDP_UMEM_FILL_RING,RING_SIZE),(XDP_UMEM_COMPLETION_RING,RING_SIZE)]:
            self.xsk.setsockopt(SOL_XDP, o, struct.pack('I', v))
        class UR(ctypes.Structure):
            _fields_ = [('addr',ctypes.c_uint64),('len',ctypes.c_uint64),('cs',ctypes.c_uint32),('hr',ctypes.c_uint32),('fl',ctypes.c_uint32)]
        a = UR(); BT = ctypes.c_char * self.umem_sz; self.buf = BT.from_buffer(self.umem)
        a.addr = ctypes.addressof(self.buf); a.len = self.umem_sz; a.cs = FRAME_SIZE
        libc_setsockopt(self.xsk_fd, SOL_XDP, XDP_UMEM_REG, a, ctypes.sizeof(a))
        class SA(ctypes.Structure):
            _fields_ = [('f',ctypes.c_uint16),('fl',ctypes.c_uint16),('idx',ctypes.c_uint32),('q',ctypes.c_uint32),('fd',ctypes.c_uint32)]
        sa = SA(); sa.f = AF_XDP; sa.fl = XDP_COPY; sa.idx = self.ifidx
        libc.bind(self.xsk_fd, ctypes.byref(sa), ctypes.sizeof(sa))
        class RO(ctypes.Structure):
            _fields_ = [('producer',ctypes.c_uint64),('consumer',ctypes.c_uint64),('desc',ctypes.c_uint64),('flags',ctypes.c_uint64)]
        class MO(ctypes.Structure):
            _fields_ = [('rx',RO),('tx',RO),('fr',RO),('cr',RO)]
        o = MO(); libc_getsockopt(self.xsk_fd, SOL_XDP, XDP_MMAP_OFFSETS, o, ctypes.sizeof(o)); self.offs = o
        P = mmap.PROT_READ|mmap.PROT_WRITE; M = mmap.MAP_SHARED|mmap.MAP_POPULATE
        self.rx_s = o.rx.desc + RING_SIZE*16; self.rx_p = libc.mmap(None, self.rx_s, P, M, self.xsk_fd, XDP_PGOFF_RX_RING)
        self.tx_s = o.tx.desc + RING_SIZE*16; self.tx_p = libc.mmap(None, self.tx_s, P, M, self.xsk_fd, XDP_PGOFF_TX_RING)
        self.fr_s = o.fr.desc + RING_SIZE*8;  self.fr_p = libc.mmap(None, self.fr_s, P, M, self.xsk_fd, XDP_UMEM_PGOFF_FILL_RING)
        self.cr_s = o.cr.desc + RING_SIZE*8;  self.cr_p = libc.mmap(None, self.cr_s, P, M, self.xsk_fd, XDP_UMEM_PGOFF_COMPLETION_RING)
        fp = ctypes.cast(self.fr_p + o.fr.producer, ctypes.POINTER(ctypes.c_uint32))
        db = self.fr_p + o.fr.desc
        for i in range(RING_SIZE):
            ctypes.cast(db + i*8, ctypes.POINTER(ctypes.c_uint64)).contents.value = i * FRAME_SIZE
        fp.contents.value = RING_SIZE
        update_xsk_map(self.map_fd, 0, self.xsk_fd); self.ready = True
        _vlog(f"XDP socket ready (ring={RING_SIZE}, frames={NUM_FRAMES})")

    def recv_batch(self, mx=BATCH_SIZE):
        if not self.ready: return []
        p = ctypes.cast(self.rx_p + self.offs.rx.producer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        c = ctypes.cast(self.rx_p + self.offs.rx.consumer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        av = p - c
        if av == 0: return []
        n = min(av, mx); res = []; addrs = []
        for i in range(n):
            idx = (c+i) % RING_SIZE; db = self.rx_p + self.offs.rx.desc + idx*16
            a = ctypes.cast(db, ctypes.POINTER(ctypes.c_uint64)).contents.value
            l = ctypes.cast(db+8, ctypes.POINTER(ctypes.c_uint32)).contents.value
            res.append((a, l)); addrs.append(a)
        ctypes.cast(self.rx_p + self.offs.rx.consumer, ctypes.POINTER(ctypes.c_uint32)).contents.value = c + n
        fp = ctypes.cast(self.fr_p + self.offs.fr.producer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        for i, a in enumerate(addrs):
            fd = self.fr_p + self.offs.fr.desc + ((fp+i) % RING_SIZE) * 8
            ctypes.cast(fd, ctypes.POINTER(ctypes.c_uint64)).contents.value = a
        ctypes.cast(self.fr_p + self.offs.fr.producer, ctypes.POINTER(ctypes.c_uint32)).contents.value = fp + n
        return res

    def send_frame(self, fi, length):
        p = ctypes.cast(self.tx_p + self.offs.tx.producer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        db = self.tx_p + self.offs.tx.desc + (p % RING_SIZE) * 16
        ctypes.cast(db, ctypes.POINTER(ctypes.c_uint64)).contents.value = fi * FRAME_SIZE
        ctypes.cast(db+8, ctypes.POINTER(ctypes.c_uint32)).contents.value = length
        ctypes.cast(db+12, ctypes.POINTER(ctypes.c_uint32)).contents.value = 0
        ctypes.cast(self.tx_p + self.offs.tx.producer, ctypes.POINTER(ctypes.c_uint32)).contents.value = p + 1

    def kick_tx(self): libc.sendto(self.xsk_fd, None, 0, socket.MSG_DONTWAIT, None, 0)

    def reclaim(self):
        cp = ctypes.cast(self.cr_p + self.offs.cr.producer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        cc = ctypes.cast(self.cr_p + self.offs.cr.consumer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        n = cp - cc
        if n == 0: return
        for i in range(n):
            db = self.cr_p + self.offs.cr.desc + ((cc+i) % RING_SIZE) * 8
            a = ctypes.cast(db, ctypes.POINTER(ctypes.c_uint64)).contents.value
            self.frames.append(a // FRAME_SIZE)
        ctypes.cast(self.cr_p + self.offs.cr.consumer, ctypes.POINTER(ctypes.c_uint32)).contents.value = cc + n

    def fileno(self): return self.xsk_fd

    def close(self):
        if self.closed: return; self.closed = True; self.ready = False
        _vlog("Cleaning up XDP...")
        if self.ifidx: detach_xdp(self.ifidx)
        for ptr, sz in [(self.rx_p,self.rx_s),(self.tx_p,self.tx_s),(self.fr_p,self.fr_s),(self.cr_p,self.cr_s)]:
            if ptr and ptr != MAP_FAILED: libc.munmap(ptr, sz)
        if self.umem:
            try: self.umem.close()
            except: pass
        if self.xsk:
            try: self.xsk.close()
            except: pass
        for fd in [self.umem_fd, self.map_fd, self.prog_fd]:
            if fd >= 0:
                try: os.close(fd)
                except: pass
        _vlog("XDP cleanup complete")

def _type_fixup(reply, req_type, reply_type, ico):
    reply[ico] = reply_type; reply[ico+1] = 0
    if req_type == 13:
        ts = ms_now(); struct.pack_into('!II', reply, ico+12, ts, ts)
    elif req_type == 17:
        struct.pack_into('!I', reply, ico+8, 0xFFFFFF00)
    elif req_type == 10:
        reply[ico+4] = 0; reply[ico+5] = 0; struct.pack_into('!H', reply, ico+6, 300)

def build_raw_reply(icmp, req_type, reply_type, has_id, magic_extra,
                    cmd, seq, reply_data, crypto):
    light = cmd == CMD_FRAME_DATA
    enc_data = crypto.encrypt(reply_data, light=light) if crypto.mac_key else reply_data
    po = 8 + magic_extra
    total = po + 9 + len(enc_data)
    reply = bytearray(total)
    reply[:8+magic_extra] = icmp[:8+magic_extra]
    reply[po:po+2] = MAGIC
    struct.pack_into('!I', reply, po+2, seq)
    reply[po+6] = cmd
    struct.pack_into('!H', reply, po+7, len(enc_data))
    reply[po+9:po+9+len(enc_data)] = enc_data
    _type_fixup(reply, req_type, reply_type, 0)
    reply[2:4] = b'\x00\x00'
    struct.pack_into('!H', reply, 2, cksum(bytes(reply)))
    return bytes(reply)

def build_raw_hs_reply(icmp, server_nonce, server_pub, req_type, reply_type, has_id, magic_extra):
    """Build handshake reply using the same ICMP type family as the request."""
    payload = MAGIC + b'VNC4' + server_nonce + server_pub
    hdr_sz = 8 + magic_extra
    r = bytearray(icmp[:hdr_sz]) + payload
    _type_fixup(r, req_type, reply_type, 0)
    r[2:4] = b'\x00\x00'
    struct.pack_into('!H', r, 2, cksum(bytes(r)))
    return bytes(r)

def _v4_swap(umem, d):
    dm = bytes(umem[d:d+6]); sm = bytes(umem[d+6:d+12])
    umem[d:d+6] = sm; umem[d+6:d+12] = dm
    si = bytes(umem[d+26:d+30]); di_ = bytes(umem[d+30:d+34])
    umem[d+26:d+30] = di_; umem[d+30:d+34] = si; umem[d+22] = 64
    ihl = (umem[d+14] & 0x0F) * 4
    umem[d+24] = 0; umem[d+25] = 0
    struct.pack_into('!H', umem, d+24, cksum(umem[d+14:d+14+ihl]))
    return 14 + ihl

def _v6_swap(umem, d):
    dm = bytes(umem[d:d+6]); sm = bytes(umem[d+6:d+12])
    umem[d:d+6] = sm; umem[d+6:d+12] = dm
    si = bytes(umem[d+22:d+38]); di_ = bytes(umem[d+38:d+54])
    umem[d+22:d+38] = di_; umem[d+38:d+54] = si; umem[d+21] = 64
    return 54

def _v4_icmp_ck(umem, d, ico, total):
    umem[d+ico+2] = 0; umem[d+ico+3] = 0
    struct.pack_into('!H', umem, d+ico+2, cksum(umem[d+ico:d+total]))

def _v6_icmpv6_ck(umem, d, ico, total):
    src = umem[d+22:d+38]; dst = umem[d+38:d+54]; ln = total - ico
    ph = bytes(src) + bytes(dst) + struct.pack('!I', ln) + b'\x00\x00\x00\x3a'
    umem[d+ico+2] = 0; umem[d+ico+3] = 0
    struct.pack_into('!H', umem, d+ico+2, cksum(ph + umem[d+ico:d+total]))

def xdp_build_hs_reply(frame, server_nonce, server_pub, req_type, reply_type, has_id, magic_extra):
    """Build XDP handshake reply using the same ICMP type family as the request."""
    r = bytearray(frame)
    dm = r[0:6]; sm = r[6:12]; r[0:6] = sm; r[6:12] = dm
    si = r[26:30]; di_ = r[30:34]; r[26:30] = di_; r[30:34] = si; r[22] = 64
    ihl = (r[14] & 0x0F) * 4
    r[24:26] = b'\x00\x00'; struct.pack_into('!H', r, 24, cksum(r[14:14+ihl]))
    ico = 14 + ihl
    payload = MAGIC + b'VNC4' + server_nonce + server_pub
    po = ico + 8 + magic_extra; total = po + len(payload)
    r = r[:po] + payload; r = bytearray(r)
    _type_fixup(r, req_type, reply_type, ico)
    struct.pack_into('!H', r, 16, total - 14)
    r[24:26] = b'\x00\x00'; struct.pack_into('!H', r, 24, cksum(r[14:14+ihl]))
    r[ico+2:ico+4] = b'\x00\x00'
    struct.pack_into('!H', r, ico+2, cksum(r[ico:total]))
    return bytes(r)

def xdp_build_reply(umem, sa, sl, fi, req_type, reply_type, has_id, magic_extra,
                    cmd, seq, reply_data, crypto, is_v6=False):
    d = fi * FRAME_SIZE
    umem[d:d+sl] = umem[sa:sa+sl]
    ico = _v6_swap(umem, d) if is_v6 else _v4_swap(umem, d)
    _type_fixup(umem, req_type, reply_type, d+ico)
    light = cmd == CMD_FRAME_DATA
    enc_data = crypto.encrypt(reply_data, light=light) if crypto.mac_key else reply_data
    po = d + ico + 8 + magic_extra
    umem[po:po+2] = MAGIC
    struct.pack_into('!I', umem, po+2, seq)
    umem[po+6] = cmd
    struct.pack_into('!H', umem, po+7, len(enc_data))
    data_start = po + 9
    umem[data_start:data_start+len(enc_data)] = enc_data
    total = (data_start + len(enc_data)) - d
    if not is_v6:
        struct.pack_into('!H', umem, d+16, total - 14)
        ihl = (umem[d+14] & 0x0F) * 4
        umem[d+24] = 0; umem[d+25] = 0
        struct.pack_into('!H', umem, d+24, cksum(umem[d+14:d+14+ihl]))
    else:
        struct.pack_into('!H', umem, d+18, total - 54)
    if is_v6: _v6_icmpv6_ck(umem, d, ico, total)
    else: _v4_icmp_ck(umem, d, ico, total)
    return total

class ICMPVNCServer:
    def __init__(self, iface, mode, psk, scale, drop_other):
        self.iface = iface; self.mode = mode
        self.crypto = CryptoV3(psk); self.scale = scale
        self.drop_other = drop_other
        self.input_backend = None
        self._shutdown_requested = False
        self.client_ip = None; self.client_time = 0
        self.transport = None; self.capture = None
        self.fbuf = FrameBuffer()
        self.kf_buf = FrameBuffer()
        self.file_buf = FileBuffer()
        self.upload_recv = UploadReceiver()
        self.rx_total = 0; self.tx_total = 0; self.start_time = 0
        self.chunk_sz = CryptoV3.max_plaintext(MAX_PAYLOAD, 9) - 4

    def _register(self, ip_str, client_nonce, client_pub):
        if self.client_ip and self.client_ip != ip_str:
            _event("✗", f"Rejected {ip_str}: client {self.client_ip} owns session", _C.YELLOW)
            return None, None
        server_nonce = os.urandom(32)
        server_pub = self.crypto.generate_dh()
        self.crypto.derive(client_nonce, server_nonce, client_pub, server_pub)
        self.capture.prev_rgb = None
        self.client_ip = ip_str; self.client_time = time.time()
        _event("✓", f"Client {ip_str} connected (X25519+PSK)", _C.GREEN)
        return server_nonce, server_pub

    def _disconnect(self, ip_str):
        if ip_str == self.client_ip:
            _event("✗", f"Client {ip_str} disconnected", _C.YELLOW)
            self.client_ip = None; self.client_time = 0
            self.crypto = CryptoV3(self.crypto.psk)
            self.capture.prev_rgb = None

    def _check_handshake_payload(self, payload):
        if len(payload) < 70: return None, None
        if payload[:2] != MAGIC: return None, None
        if payload[2:6] != b'VNC3': return None, None
        return payload[6:38], payload[38:70]

    def _process_cmd(self, cmd, seq, enc_data, src_ip):
        light = cmd == CMD_FRAME_DATA
        data = self.crypto.decrypt(enc_data, light=light) if self.crypto.mac_key else enc_data
        if data is None: return None

        if cmd == CMD_TEST:
            return data
        elif cmd == CMD_DISCONNECT:
            self._disconnect(src_ip); return b'BYE'
        elif cmd == CMD_FRAME_REQ:
            sc = data[0] if len(data) >= 1 and data[0] > 0 else self.scale
            force_key = data[1] if len(data) >= 2 else 0
            if len(data) >= 3 and hasattr(self.capture, 'cursor_visible'):
                self.capture.cursor_visible = (data[2] == 0)
            if sc != self.capture.scale:
                self.capture.scale = sc
                self.capture.autoscale_floor = sc
                self.capture.prev_rgb = None
            try:
                comp, mode, w, h, fid, fr, rc = self.capture.grab(force_keyframe=bool(force_key))
            except Exception as e:
                _event("✗", f"Capture error: {e}", _C.BRED)
                return struct.pack('!IBHHHIHH', 0, 0, 0, 0, 0, 0, 0, 0)
            if mode == 1:
                self.kf_buf.update(fid, 1, w, h, comp, self.chunk_sz, 0, h)
                return self.kf_buf.header_bytes()
            else:
                self.fbuf.update(fid, mode, w, h, comp, self.chunk_sz, fr, rc)
                return self.fbuf.header_bytes()
        elif cmd == CMD_FRAME_DATA:
            if len(data) < 4: return b''
            fid = struct.unpack('!I', data[:4])[0]
            if fid != self.fbuf.frame_id: return struct.pack('!I', fid)
            return struct.pack('!I', fid) + self.fbuf.get_chunk(seq)
        elif cmd == CMD_KEY_CHUNK:
            if len(data) < 4: return b''
            fid = struct.unpack('!I', data[:4])[0]
            if fid != self.kf_buf.frame_id: return struct.pack('!I', fid)
            return struct.pack('!I', fid) + self.kf_buf.get_chunk(seq)
        elif cmd == CMD_FILE_REQ:
            path = data.decode('utf-8', errors='replace').strip('\x00').strip()
            ok, err = self.file_buf.load(path, self.chunk_sz)
            if ok:
                _event("↓", f"File download: {path} ({len(self.file_buf.data):,}B)", _C.GOLD)
                return b'\x01' + self.file_buf.header_bytes()
            else:
                _event("✗", f"File failed: {err}", _C.BRED)
                return b'\x00' + err.encode('utf-8')[:200]
        elif cmd == CMD_FILE_DATA:
            if len(data) < 4: return b''
            fid = struct.unpack('!I', data[:4])[0]
            if fid != self.file_buf.file_id: return struct.pack('!I', fid)
            return struct.pack('!I', fid) + self.file_buf.get_chunk(seq)
        elif cmd == CMD_FILE_UP_HDR:
            if len(data) < 13: return b'\x00Bad header'
            fid, fsz, nchunks, nlen = struct.unpack('!IIIB', data[:13])
            fname = data[13:13+nlen].decode('utf-8', errors='replace')
            self.upload_recv.prepare(fid, fname, fsz, nchunks)
            _event("↑", f"File upload: {fname} ({fsz:,}B)", _C.GOLD)
            return b'\x01'
        elif cmd == CMD_FILE_UP_DATA:
            if len(data) < 4: return b'\x00'
            fid = struct.unpack('!I', data[:4])[0]
            done, result = self.upload_recv.add_chunk(fid, seq, data[4:])
            if done:
                _event("✓", f"Upload done: {result}", _C.GREEN)
                return b'\x02' + result.encode('utf-8')[:200]
            return b'\x01'
        elif cmd == CMD_INPUT_KEY:
            if self.input_backend and len(data) >= 5:
                press = data[0]
                keysym = struct.unpack('!I', data[1:5])[0]
                self.input_backend.inject_key(keysym, bool(press))
            return b'\x01'
        elif cmd == CMD_INPUT_MOUSE:
            if self.input_backend and len(data) >= 6:
                etype = data[0]
                fx, fy = struct.unpack('!HH', data[1:5])
                btn = data[5]
                sx = min(fx * self.capture.scale, self.capture.scr_w - 1)
                sy = min(fy * self.capture.scale, self.capture.scr_h - 1)
                if etype == 0:
                    self.input_backend.inject_motion(sx, sy)
                elif etype == 1:
                    self.input_backend.inject_motion(sx, sy)
                    self.input_backend.inject_button(btn, True)
                elif etype == 2:
                    self.input_backend.inject_button(btn, False)
                elif etype == 3:
                    self.input_backend.inject_scroll(btn)
            return b'\x01'
        return b''

    def _print_status(self):
        el = time.time() - self.start_time
        if el <= 0: return
        if not self.client_ip:
            _waiting_server(); return
        _status_server(self.rx_total, self.tx_total, el,
                       self.client_ip, self.fbuf.frame_id,
                       len(self.fbuf.chunks),
                       self.rx_total / el, self.tx_total / el)

    def _run_raw(self):
        t = RawTransport(self.iface, suppress_echo=True); self.transport = t
        last_print = time.time()
        _waiting_server()
        while True:
            if self._shutdown_requested: break
            batch = t.recv_batch()
            if not batch:
                select.select([t.fileno()], [], [], 0.1); continue
            for icmp, src in batch:
                if len(icmp) < 8: continue
                it = icmp[0]; info = DISPATCH_V4.get(it)
                if not info: continue
                reply_type, name, has_id, magic_extra = info
                is_handshake = False
                if has_id and len(icmp) >= 6:
                    if struct.unpack('!H', icmp[4:6])[0] == HANDSHAKE_ID:
                        is_handshake = True
                elif not has_id:
                    hs_off = 8 + magic_extra
                    if len(icmp) >= hs_off + 6 and icmp[hs_off:hs_off+2] == MAGIC and icmp[hs_off+2:hs_off+6] == b'VNC3':
                        is_handshake = True
                if is_handshake:
                    cn, cpub = self._check_handshake_payload(bytes(icmp[8+magic_extra:]))
                    if cn:
                        sn, spub = self._register(src, cn, cpub)
                        if sn:
                            t.send(build_raw_hs_reply(icmp, sn, spub, it, reply_type, has_id, magic_extra), src)
                    continue
                if src != self.client_ip: continue
                self.client_time = time.time()
                if has_id:
                    if len(icmp) < 6: continue
                    rid = struct.unpack('!H', icmp[4:6])[0]
                    if rid != SESSION_ID: continue
                po = 8 + magic_extra
                if len(icmp) < po + 9: continue
                if icmp[po:po+2] != MAGIC: continue
                seq = struct.unpack('!I', icmp[po+2:po+6])[0]
                cmd = icmp[po+6]
                dlen = struct.unpack('!H', icmp[po+7:po+9])[0]
                enc_data = bytes(icmp[po+9:po+9+dlen])
                self.rx_total += 1
                reply_data = self._process_cmd(cmd, seq, enc_data, src)
                if reply_data is None: continue
                reply = build_raw_reply(icmp, it, reply_type, has_id, magic_extra,
                                       cmd, seq, reply_data, self.crypto)
                if t.send(reply, src): self.tx_total += 1
                if self._shutdown_requested: break
            now = time.time()
            if now - last_print >= 1.0: self._print_status(); last_print = now

    def _run_xdp(self, ip4):
        xdp = XDPTransport(self.iface, self.drop_other); xdp.setup(); self.transport = xdp
        umem = xdp.umem; my_ip4 = socket.inet_aton(ip4) if ip4 else None
        last_print = time.time(); tx_pending = 0
        _waiting_server()
        while True:
            if self._shutdown_requested: break
            if tx_pending >= BATCH_SIZE:
                xdp.kick_tx(); xdp.reclaim(); tx_pending = 0
            batch = xdp.recv_batch()
            if not batch:
                if tx_pending > 0: xdp.kick_tx(); xdp.reclaim(); tx_pending = 0
                select.select([xdp.xsk], [], [], 0.1); continue
            for addr, length in batch:
                if length < 42: continue
                fr = umem[addr:addr+length]
                etype = fr[12:14]; is_v6 = False; ico = 0; src_ip = None
                if etype == ETH_IP and fr[23] == IPPROTO_ICMP:
                    if my_ip4 and fr[30:34] != my_ip4: continue
                    ihl = (fr[14] & 0x0F) * 4; ico = 14 + ihl
                    if length < ico + 8: continue
                    src_ip = socket.inet_ntoa(fr[26:30])
                    hs_info = DISPATCH_V4.get(fr[ico])
                    if hs_info:
                        hs_reply_type, hs_name, hs_has_id, hs_magic_extra = hs_info
                        is_hs = False
                        if hs_has_id and length >= ico + 6:
                            if struct.unpack('!H', fr[ico+4:ico+6])[0] == HANDSHAKE_ID:
                                is_hs = True
                        elif not hs_has_id:
                            hs_off = ico + 8 + hs_magic_extra
                            if length >= hs_off + 6 and fr[hs_off:hs_off+2] == MAGIC and fr[hs_off+2:hs_off+6] == b'VNC3':
                                is_hs = True
                        if is_hs:
                            cn, cpub = self._check_handshake_payload(bytes(fr[ico+8+hs_magic_extra:length]))
                            if cn:
                                sn, spub = self._register(src_ip, cn, cpub)
                                if sn and xdp.frames:
                                    hr = xdp_build_hs_reply(fr[:length], sn, spub,
                                                            fr[ico], hs_reply_type, hs_has_id, hs_magic_extra)
                                    fi = xdp.frames.pop(); o = fi * FRAME_SIZE
                                    umem[o:o+len(hr)] = hr
                                    xdp.send_frame(fi, len(hr)); tx_pending += 1
                            continue
                    if src_ip != self.client_ip: continue
                    self.client_time = time.time()
                    info = DISPATCH_V4.get(fr[ico])
                elif etype == ETH_IP6 and length >= 62:
                    if fr[20] != IPPROTO_ICMPV6: continue
                    ico = 54; is_v6 = True
                    src_ip = socket.inet_ntop(socket.AF_INET6, fr[22:38])
                    if src_ip != self.client_ip: continue
                    self.client_time = time.time()
                    info = DISPATCH_V6.get(fr[ico])
                else: continue
                if not info: continue
                reply_type, name, has_id, magic_extra = info
                req_type = fr[ico]
                if has_id:
                    if length < ico + 6: continue
                    rid = struct.unpack('!H', fr[ico+4:ico+6])[0]
                    if rid == HANDSHAKE_ID: continue
                    if rid != SESSION_ID: continue
                po = ico + 8 + magic_extra
                if length < po + 9: continue
                if fr[po:po+2] != MAGIC: continue
                seq = struct.unpack('!I', fr[po+2:po+6])[0]
                cmd = fr[po+6]
                dlen = struct.unpack('!H', fr[po+7:po+9])[0]
                enc_data = bytes(fr[po+9:po+9+dlen])
                self.rx_total += 1
                reply_data = self._process_cmd(cmd, seq, enc_data, src_ip)
                if reply_data is None: continue
                if not xdp.frames: xdp.reclaim()
                if not xdp.frames: continue
                fi = xdp.frames.pop()
                total = xdp_build_reply(umem, addr, length, fi, req_type, reply_type,
                                       has_id, magic_extra, cmd, seq, reply_data,
                                       self.crypto, is_v6)
                xdp.send_frame(fi, total); tx_pending += 1; self.tx_total += 1
                if self._shutdown_requested:
                    if tx_pending > 0: xdp.kick_tx(); xdp.reclaim()
                    break
            now = time.time()
            if now - last_print >= 1.0: self._print_status(); last_print = now
            if tx_pending > 0: xdp.kick_tx(); xdp.reclaim(); tx_pending = 0

    def run(self):
        ip4, mac, ip6 = get_iface_info(self.iface)
        _compile_native()
        self.capture = None; capture_label = 'NONE'
        if os.environ.get('DISPLAY'):
            try:
                self.capture = ScreenCapture(self.scale)
                cap = self.capture
                if cap.use_shm:
                    shm_labels = {'xcb_fd': 'SHM/xcb-fd', 'memfd': 'SHM/memfd', 'sysv': 'SHM/SysV'}
                    capture_label = f"X11 {shm_labels.get(cap._shm_method, 'SHM')}"
                else:
                    capture_label = 'X11 XGetImage'
                wl = os.environ.get('WAYLAND_DISPLAY')
                if wl: capture_label += ' (via XWayland)'
            except Exception as e:
                _vlog(f"X11 capture failed: {e}")
                self.capture = None
        if not self.capture:
            try:
                self.capture = PipeWireCapture(self.scale)
                capture_label = 'PipeWire (GStreamer)'
            except Exception as e:
                _vlog(f"PipeWire capture failed: {e}")
        if not self.capture:
            try:
                self.capture = DRMCapture(self.scale)
                capture_label = 'DRM/KMS framebuffer'
            except Exception as e:
                _vlog(f"DRM capture failed: {e}")
        if not self.capture:
            _event("✗", "No capture backend available — cannot start.", _C.BRED)
            sys.exit(1)
        self.input_backend = None
        try:
            self.input_backend = UInputBackend(self.capture.scr_w, self.capture.scr_h)
        except Exception as e:
            _vlog(f"uinput failed: {e}")
        if not self.input_backend and hasattr(self.capture, 'x11') and hasattr(self.capture, 'display'):
            try:
                self.input_backend = XTestInputBackend(self.capture.x11, self.capture.display)
            except Exception as e:
                _vlog(f"XTest failed: {e}")
        if not self.input_backend:
            _vlog("Input: no backend available — view-only")

        _print_banner()
        in_label = type(self.input_backend).__name__ if self.input_backend else 'none (view-only)'
        mode_str = 'XDP (native)' if self.mode == 'xdp' else 'Raw Socket'
        scr_str = (f"{self.capture.scr_w}x{self.capture.scr_h} → "
                   f"{self.capture.scr_w//self.scale}x{self.capture.scr_h//self.scale}")
        _tunnel_box("Server", [
            ("Mode",       mode_str),
            ("Interface",  f"{self.iface} ({ip4})"),
            ("Capture",    f"{capture_label} · {scr_str}"),
            ("Input",      in_label),
            ("Crypto",     "BitRev→Sub→ARX→Speck→SHA256"),
            ("Key Ex",     "X25519 ECDH + PSK"),
            ("Encoding",   f"zlib delta · {self.chunk_sz}B chunks"),
            ("Session",    f"0x{SESSION_ID:04X}"),
        ])
        caps = ["screen"]
        if self.input_backend:
            caps.extend(["keyboard", "mouse"])
        caps.extend(["file transfer"])
        if not _QUIET:
            print(f"  {_C.DIMW}Capabilities: {' │ '.join(caps)}{_C.RST}")
            print()
        self.start_time = time.time()
        try:
            if self.mode == 'xdp': self._run_xdp(ip4)
            else: self._run_raw()
        except Exception as e:
            _event("✗", f"Error: {e}", _C.BRED); import traceback; traceback.print_exc()
        el = time.time() - self.start_time
        mins, secs = divmod(int(el), 60)
        hrs, mins = divmod(mins, 60)
        dur = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s" if mins else f"{secs}s"
        _summary_box([
            ("Duration",  dur),
            ("RX",        f"{self.rx_total:,} packets"),
            ("TX",        f"{self.tx_total:,} packets"),
            ("Avg PPS",   f"{self.rx_total/max(el,1):,.0f} / {self.tx_total/max(el,1):,.0f}"),
        ])
        if self._shutdown_requested:
            run_cleanup()
            sys.exit(0)

def main():
    global _VERBOSE, _QUIET
    p = argparse.ArgumentParser(
        description='ICMPVNC Server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  sudo -E python3 server.py -i eth0 --xdp -k mysecret\n"
               "  sudo -E python3 server.py -i eth0 --xdp --drop -k mysecret\n"
               "  sudo -E python3 server.py -i wlan0 --raw -k mysecret --scale 2\n")
    p.add_argument('-i', '--interface', required=True, help='Network interface')
    p.add_argument('-k', '--key', default=None, help='Pre-shared key')
    p.add_argument('--scale', type=int, default=2, help='Downscale 1-6 (default: 2)')
    p.add_argument('--drop', action='store_true', help='XDP: drop TCP/UDP. Raw: suppress echo')
    p.add_argument('-v', '--verbose', action='store_true', help='Show detailed subsystem init')
    p.add_argument('-q', '--quiet', action='store_true', help='Errors only (no banner/status)')
    p.add_argument('--no-color', action='store_true', help='Disable ANSI colors')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--xdp', action='store_const', const='xdp', dest='mode')
    g.add_argument('--raw', action='store_const', const='raw', dest='mode')
    a = p.parse_args()
    _VERBOSE = a.verbose; _QUIET = a.quiet
    _init_colors()
    if a.no_color: _C.off()
    validate_interface(a.interface)
    if a.mode == 'xdp' and is_wireless(a.interface):
        warn_wireless_xdp(a.interface)
    psk = a.key or getpass.getpass("Pre-shared key: ")
    if not psk: _event("✗", "Key required", _C.BRED); sys.exit(1)
    ICMPVNCServer(a.interface, a.mode, psk, max(1, min(6, a.scale)), a.drop).run()

if __name__ == '__main__':
    main()
