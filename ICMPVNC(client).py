"""
ICMPVNC Client Germanna v1.0 ⛏

Usage: ip -i interface --mode -ICMPtype -k password
  sudo python3 client.py 192.168.1.111 -i eth0 --xdp -E -k password
  sudo python3 client.py 192.168.1.111 -i eth0 --xdp -E -k password -r 10000 -s 1400        (max)
  sudo python3 client.py 192.168.1.111 --raw -E -k password
  sudo python3 client.py 192.168.1.111 --raw -E -k password -r 7000 -s 1400        (max)
"""
import socket, struct, os, sys, time, mmap, ctypes, ctypes.util, re
import select, fcntl, argparse, signal, atexit, zlib, hashlib, hmac
import subprocess, tempfile, getpass, shlex, threading, queue, collections


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
{GOLD}                    1714-1717 ⛏{RST}
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

def _status_client(rx, tx, fps, frame_id, mb, el, extras=''):
    if _QUIET: return
    pps = rx / max(el, 1)
    spark = _sparkline(pps)
    mins, secs = divmod(int(el), 60)
    t = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
    _status_write(
        f"  {_C.GOLD}⛏{_C.RST} "
        f"{_C.DIMW}FPS{_C.RST} {_C.WHITE}{fps:.1f}{_C.RST} "
        f"{_C.DIMW}│ F:{_C.RST}{_C.WHITE}{frame_id}{_C.RST} "
        f"{_C.DIMW}│{_C.RST} {_C.WHITE}{mb:.1f}{_C.RST}{_C.DIMW}MB{_C.RST} "
        f"{_C.DIMW}│{_C.RST} {_C.GOLD}{spark}{_C.RST} "
        f"{_C.DIMW}│{_C.RST} {t}"
        f"{extras}"
    )

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
MAX_RATE     = 10000
ENCRYPT_OH   = 28

ETH_P_IP     = 0x0800
IPPROTO_ICMP = 1
FRAME_SIZE   = 2048
NUM_FRAMES   = 4096
RING_SIZE    = 4096
BATCH_SIZE   = 256
MASK64       = 0xFFFFFFFFFFFFFFFF
P25519       = 2**255 - 19

ICMP_TYPES = {
    'E':  (8,0,    'Echo',            True,  0),
    'T':  (13,14,  'Timestamp',       True,  12),
    'M':  (17,18,  'Address Mask',    True,  4),
    'R':  (15,16,  'Information',     True,  0),
    'S':  (10,9,   'Router Solicit',  False, 0),
    'X':  (253,254,'Experimental',    True,  0),
    'D':  (37,38,  'Domain Name',     True,  0),
    'O':  (35,36,  'Mobile Reg',      True,  0),
    'TR': (30,0,   'Traceroute',      False, 0),
    'P':  (40,40,  'Photuris',        False, 0),
    'EE': (42,43,  'Extended Echo',   True,  0),
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
def _sigterm(s, f):
    _event("⏻", "Shutting down...", _C.MAG); run_cleanup(); sys.exit(0)
signal.signal(signal.SIGTERM, _sigterm)

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
    s.close(); return ip4, mac

def get_default_iface():
    try:
        with open('/proc/net/route') as f:
            for l in f:
                p = l.split()
                if len(p) >= 2 and p[1] == '00000000': return p[0]
    except: pass
    return 'eth0'

def resolve_next_hop(dst):
    try:
        out = subprocess.check_output(['ip','route','get',dst], text=True, timeout=5)
        p = out.split()
        if 'via' in p: return p[p.index('via')+1]
    except: pass
    return dst

def resolve_mac(ip, iface=None):
    mac = _arp_read(ip)
    if mac: return mac
    try: subprocess.run(['ping','-c','1','-W','1',ip], capture_output=True, timeout=3)
    except: pass
    return _arp_read(ip)

def _arp_read(ip):
    try:
        with open('/proc/net/arp') as f:
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[0] == ip and p[3] not in ('00:00:00:00:00:00','<incomplete>'):
                    return bytes.fromhex(p[3].replace(':',''))
    except: pass
    return None

def _xor_apply(frame_ba, delta, offset=0):
    """XOR delta into frame_ba at byte offset. For row-strip: offset = first_row * w * 3."""
    n = len(delta)
    ai = int.from_bytes(frame_ba[offset:offset+n], 'little')
    bi = int.from_bytes(delta, 'little')
    frame_ba[offset:offset+n] = (ai ^ bi).to_bytes(n, 'little')

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

class RawTransport:
    def __init__(self, iface, server_ip):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        if iface: self.sock.setsockopt(socket.SOL_SOCKET, 25, iface.encode() + b'\0')
        self.sock.setblocking(False)
        self.server_ip = server_ip
        register_cleanup(self.close)

    def send(self, icmp_bytes):
        try: self.sock.sendto(bytes(icmp_bytes), (self.server_ip, 0)); return True
        except: return False

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

    def fileno(self): return self.sock.fileno()
    def close(self):
        try: self.sock.close()
        except: pass

SYS_bpf=321; BPF_MAP_CREATE=0; BPF_MAP_UPDATE_ELEM=2; BPF_PROG_LOAD=5
BPF_MAP_TYPE_XSKMAP=17; BPF_PROG_TYPE_XDP=6; XDP_FLAGS_SKB_MODE=1<<1
AF_XDP=44; SOL_XDP=283; XDP_MMAP_OFFSETS=1; XDP_RX_RING=2; XDP_TX_RING=3
XDP_UMEM_REG=4; XDP_UMEM_FILL_RING=5; XDP_UMEM_COMPLETION_RING=6; XDP_COPY=1<<1
XDP_PGOFF_RX_RING=0; XDP_PGOFF_TX_RING=0x80000000
XDP_UMEM_PGOFF_FILL_RING=0x100000000; XDP_UMEM_PGOFF_COMPLETION_RING=0x180000000
NETLINK_ROUTE=0; RTM_SETLINK=19; NLM_F_REQUEST=1; NLM_F_ACK=4
IFLA_XDP=43; IFLA_XDP_FD=1; IFLA_XDP_FLAGS=3; MAP_FAILED=ctypes.c_void_p(-1).value

libc=ctypes.CDLL(ctypes.util.find_library('c'),use_errno=True)
libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
libc.mmap.restype=ctypes.c_void_p
libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]; libc.munmap.restype=ctypes.c_int
libc.memfd_create.argtypes=[ctypes.c_char_p,ctypes.c_uint]; libc.memfd_create.restype=ctypes.c_int
libc.ftruncate.argtypes=[ctypes.c_int,ctypes.c_long]; libc.ftruncate.restype=ctypes.c_int
libc.bind.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_int]; libc.bind.restype=ctypes.c_int
libc.sendto.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
libc.sendto.restype=ctypes.c_ssize_t

def _bi(op,dst,src,off,imm):
    if imm<0: imm=imm&0xFFFFFFFF
    return struct.pack('<BBhI',op,(src<<4)|dst,off,imm)

def build_xdp_bytecode(map_fd):
    I=_bi; SID=0x1111; HID=0xEFBE; MAG=0xFECA; P=2
    insns=[
        I(0x61,6,1,0,0),I(0x61,7,1,4,0),
        I(0xbf,2,6,0,0),I(0x07,2,0,0,14),I(0x2d,2,7,31,0),
        I(0x69,2,6,12,0),
        I(0x15,2,0,2,0x0008),I(0x15,2,0,15,0xDD86),I(0x05,0,0,27,0),
        I(0xbf,2,6,0,0),I(0x07,2,0,0,42),I(0x2d,2,7,24,0),
        I(0x71,2,6,23,0),I(0x55,2,0,22,1),
        I(0x69,2,6,38,0),
        I(0x15,2,0,22,SID),I(0x15,2,0,21,HID),
        I(0xbf,2,6,0,0),I(0x07,2,0,0,44),I(0x2d,2,7,16,0),
        I(0x69,2,6,42,0),I(0x15,2,0,16,MAG),I(0x05,0,0,13,0),
        I(0xbf,2,6,0,0),I(0x07,2,0,0,62),I(0x2d,2,7,10,0),
        I(0x71,2,6,20,0),I(0x55,2,0,8,58),
        I(0x69,2,6,58,0),
        I(0x15,2,0,8,SID),I(0x15,2,0,7,HID),
        I(0xbf,2,6,0,0),I(0x07,2,0,0,64),I(0x2d,2,7,2,0),
        I(0x69,2,6,62,0),I(0x15,2,0,2,MAG),
        I(0xb7,0,0,0,P),I(0x95,0,0,0,0),
    ]
    redir=struct.pack('<BBhI',0x18,(1<<4)|1,0,map_fd)+struct.pack('<BBhI',0,0,0,0)
    redir+=I(0xb7,2,0,0,0)+I(0xb7,3,0,0,P)+I(0x85,0,0,0,0x33)+I(0x95,0,0,0,0)
    return b''.join(insns)+redir

def bpf_syscall(c,a,s): return libc.syscall(SYS_bpf,c,ctypes.byref(a),s)
def libc_setsockopt(fd,l,n,v,s): return libc.setsockopt(fd,l,n,ctypes.byref(v),s)
def libc_getsockopt(fd,l,n,v,s):
    o=ctypes.c_uint32(s); libc.getsockopt(fd,l,n,ctypes.byref(v),ctypes.byref(o)); return o.value

def create_xsk_map():
    class A(ctypes.Structure):
        _fields_=[('mt',ctypes.c_uint32),('ks',ctypes.c_uint32),('vs',ctypes.c_uint32),
                  ('me',ctypes.c_uint32),('mf',ctypes.c_uint32),('imf',ctypes.c_uint32),
                  ('nn',ctypes.c_uint32),('mn',ctypes.c_char*16)]
    a=A(); a.mt=BPF_MAP_TYPE_XSKMAP; a.ks=4; a.vs=4; a.me=1; a.mn=b'xsk_map'
    fd=bpf_syscall(BPF_MAP_CREATE,a,ctypes.sizeof(a))
    if fd<0: raise OSError(ctypes.get_errno(),"map create failed")
    return fd

def load_xdp_prog(prog):
    ls=ctypes.create_string_buffer(b"GPL")
    insns=(ctypes.c_char*len(prog)).from_buffer_copy(prog)
    class A(ctypes.Structure):
        _fields_=[('pt',ctypes.c_uint32),('ic',ctypes.c_uint32),('insns',ctypes.c_uint64),
                  ('lic',ctypes.c_uint64),('ll',ctypes.c_uint32),('ls',ctypes.c_uint32),
                  ('lb',ctypes.c_uint64),('kv',ctypes.c_uint32),('pf',ctypes.c_uint32),
                  ('pn',ctypes.c_char*16)]
    a=A(); a.pt=BPF_PROG_TYPE_XDP; a.ic=len(prog)//8
    a.insns=ctypes.addressof(insns); a.lic=ctypes.addressof(ls); a.pn=b'icmpvnc'
    fd=bpf_syscall(BPF_PROG_LOAD,a,ctypes.sizeof(a))
    if fd<0: raise OSError(ctypes.get_errno(),"prog load failed")
    return fd

def update_xsk_map(mfd,k,xfd):
    class A(ctypes.Structure):
        _fields_=[('mf',ctypes.c_uint32),('p',ctypes.c_uint32),('k',ctypes.c_uint64),('v',ctypes.c_uint64),('fl',ctypes.c_uint64)]
    kv=ctypes.c_uint32(k); vv=ctypes.c_uint32(xfd)
    a=A(); a.mf=mfd; a.k=ctypes.addressof(kv); a.v=ctypes.addressof(vv)
    if bpf_syscall(BPF_MAP_UPDATE_ELEM,a,ctypes.sizeof(a))<0:
        raise OSError(ctypes.get_errno(),"map update failed")

def attach_xdp(ifidx,pfd):
    sock=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,NETLINK_ROUTE); sock.bind((0,0))
    xfd=struct.pack('=HHi',8,IFLA_XDP_FD,pfd); xfl=struct.pack('=HHI',8,IFLA_XDP_FLAGS,XDP_FLAGS_SKB_MODE)
    xd=xfd+xfl; xa=struct.pack('=HH',4+len(xd),IFLA_XDP)+xd
    while len(xa)%4: xa+=b'\x00'
    ifi=struct.pack('=BBHiII',0,0,0,ifidx,0,0); ml=16+len(ifi)+len(xa)
    nl=struct.pack('=IHHII',ml,RTM_SETLINK,NLM_F_REQUEST|NLM_F_ACK,int(time.time()),0)
    sock.send(nl+ifi+xa); sock.recv(4096); sock.close()

def detach_xdp(ifidx):
    try:
        sock=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,NETLINK_ROUTE); sock.bind((0,0))
        xfd=struct.pack('=HHi',8,IFLA_XDP_FD,-1); xa=struct.pack('=HH',4+len(xfd),IFLA_XDP)+xfd
        while len(xa)%4: xa+=b'\x00'
        ifi=struct.pack('=BBHiII',0,0,0,ifidx,0,0); ml=16+len(ifi)+len(xa)
        nl=struct.pack('=IHHII',ml,RTM_SETLINK,NLM_F_REQUEST|NLM_F_ACK,int(time.time()),0)
        sock.send(nl+ifi+xa); sock.recv(4096); sock.close()
    except: pass

class XDPTransport:
    def __init__(self, iface):
        self.iface=iface; self.ifidx=socket.if_nametoindex(iface)
        self.map_fd=-1; self.prog_fd=-1; self.xsk=None; self.xsk_fd=-1
        self.umem=None; self.umem_sz=FRAME_SIZE*NUM_FRAMES; self.umem_fd=-1
        self.offs=None; self.frames=list(range(NUM_FRAMES)); self.ready=False
        self.rx_p=self.tx_p=self.fr_p=self.cr_p=None
        self.rx_s=self.tx_s=self.fr_s=self.cr_s=0
        self.buf=None; self.closed=False
        register_cleanup(self.close)

    def setup(self):
        self.map_fd=create_xsk_map(); self.prog_fd=load_xdp_prog(build_xdp_bytecode(self.map_fd))
        attach_xdp(self.ifidx,self.prog_fd)
        self.umem_fd=libc.memfd_create(b'umem',0); libc.ftruncate(self.umem_fd,self.umem_sz)
        self.umem=mmap.mmap(self.umem_fd,self.umem_sz,mmap.MAP_SHARED|mmap.MAP_POPULATE,mmap.PROT_READ|mmap.PROT_WRITE)
        self.xsk=socket.socket(AF_XDP,socket.SOCK_RAW,0); self.xsk_fd=self.xsk.fileno()
        for o,v in [(XDP_RX_RING,RING_SIZE),(XDP_TX_RING,RING_SIZE),(XDP_UMEM_FILL_RING,RING_SIZE),(XDP_UMEM_COMPLETION_RING,RING_SIZE)]:
            self.xsk.setsockopt(SOL_XDP,o,struct.pack('I',v))
        class UR(ctypes.Structure):
            _fields_=[('addr',ctypes.c_uint64),('len',ctypes.c_uint64),('cs',ctypes.c_uint32),('hr',ctypes.c_uint32),('fl',ctypes.c_uint32)]
        a=UR(); BT=ctypes.c_char*self.umem_sz; self.buf=BT.from_buffer(self.umem)
        a.addr=ctypes.addressof(self.buf); a.len=self.umem_sz; a.cs=FRAME_SIZE
        libc_setsockopt(self.xsk_fd,SOL_XDP,XDP_UMEM_REG,a,ctypes.sizeof(a))
        class SA(ctypes.Structure):
            _fields_=[('f',ctypes.c_uint16),('fl',ctypes.c_uint16),('idx',ctypes.c_uint32),('q',ctypes.c_uint32),('fd',ctypes.c_uint32)]
        sa=SA(); sa.f=AF_XDP; sa.fl=XDP_COPY; sa.idx=self.ifidx
        libc.bind(self.xsk_fd,ctypes.byref(sa),ctypes.sizeof(sa))
        class RO(ctypes.Structure):
            _fields_=[('producer',ctypes.c_uint64),('consumer',ctypes.c_uint64),('desc',ctypes.c_uint64),('flags',ctypes.c_uint64)]
        class MO(ctypes.Structure):
            _fields_=[('rx',RO),('tx',RO),('fr',RO),('cr',RO)]
        o=MO(); libc_getsockopt(self.xsk_fd,SOL_XDP,XDP_MMAP_OFFSETS,o,ctypes.sizeof(o)); self.offs=o
        P=mmap.PROT_READ|mmap.PROT_WRITE; M=mmap.MAP_SHARED|mmap.MAP_POPULATE
        self.rx_s=o.rx.desc+RING_SIZE*16; self.rx_p=libc.mmap(None,self.rx_s,P,M,self.xsk_fd,XDP_PGOFF_RX_RING)
        self.tx_s=o.tx.desc+RING_SIZE*16; self.tx_p=libc.mmap(None,self.tx_s,P,M,self.xsk_fd,XDP_PGOFF_TX_RING)
        self.fr_s=o.fr.desc+RING_SIZE*8;  self.fr_p=libc.mmap(None,self.fr_s,P,M,self.xsk_fd,XDP_UMEM_PGOFF_FILL_RING)
        self.cr_s=o.cr.desc+RING_SIZE*8;  self.cr_p=libc.mmap(None,self.cr_s,P,M,self.xsk_fd,XDP_UMEM_PGOFF_COMPLETION_RING)
        fp=ctypes.cast(self.fr_p+o.fr.producer,ctypes.POINTER(ctypes.c_uint32))
        db=self.fr_p+o.fr.desc
        for i in range(RING_SIZE):
            ctypes.cast(db+i*8,ctypes.POINTER(ctypes.c_uint64)).contents.value=i*FRAME_SIZE
        fp.contents.value=RING_SIZE
        update_xsk_map(self.map_fd,0,self.xsk_fd); self.ready=True
        _vlog("XDP socket ready")

    def send_one(self, data):
        if not self.ready or len(data)>FRAME_SIZE: return False
        if not self.frames: self.reclaim()
        if not self.frames: return False
        fi=self.frames.pop()
        self.umem[fi*FRAME_SIZE:fi*FRAME_SIZE+len(data)]=data
        p=ctypes.cast(self.tx_p+self.offs.tx.producer,ctypes.POINTER(ctypes.c_uint32)).contents.value
        db=self.tx_p+self.offs.tx.desc+(p%RING_SIZE)*16
        ctypes.cast(db,ctypes.POINTER(ctypes.c_uint64)).contents.value=fi*FRAME_SIZE
        ctypes.cast(db+8,ctypes.POINTER(ctypes.c_uint32)).contents.value=len(data)
        ctypes.cast(db+12,ctypes.POINTER(ctypes.c_uint32)).contents.value=0
        ctypes.cast(self.tx_p+self.offs.tx.producer,ctypes.POINTER(ctypes.c_uint32)).contents.value=p+1
        libc.sendto(self.xsk_fd,None,0,socket.MSG_DONTWAIT,None,0)
        return True

    def recv_batch(self, mx=BATCH_SIZE):
        if not self.ready: return []
        p=ctypes.cast(self.rx_p+self.offs.rx.producer,ctypes.POINTER(ctypes.c_uint32)).contents.value
        c=ctypes.cast(self.rx_p+self.offs.rx.consumer,ctypes.POINTER(ctypes.c_uint32)).contents.value
        av=p-c
        if av==0: return []
        n=min(av,mx); res=[]; addrs=[]
        for i in range(n):
            idx=(c+i)%RING_SIZE; db=self.rx_p+self.offs.rx.desc+idx*16
            a=ctypes.cast(db,ctypes.POINTER(ctypes.c_uint64)).contents.value
            l=ctypes.cast(db+8,ctypes.POINTER(ctypes.c_uint32)).contents.value
            res.append((a,l)); addrs.append(a)
        ctypes.cast(self.rx_p+self.offs.rx.consumer,ctypes.POINTER(ctypes.c_uint32)).contents.value=c+n
        fp=ctypes.cast(self.fr_p+self.offs.fr.producer,ctypes.POINTER(ctypes.c_uint32)).contents.value
        for i,a in enumerate(addrs):
            fd=self.fr_p+self.offs.fr.desc+((fp+i)%RING_SIZE)*8
            ctypes.cast(fd,ctypes.POINTER(ctypes.c_uint64)).contents.value=a
        ctypes.cast(self.fr_p+self.offs.fr.producer,ctypes.POINTER(ctypes.c_uint32)).contents.value=fp+n
        return res

    def reclaim(self):
        cp=ctypes.cast(self.cr_p+self.offs.cr.producer,ctypes.POINTER(ctypes.c_uint32)).contents.value
        cc=ctypes.cast(self.cr_p+self.offs.cr.consumer,ctypes.POINTER(ctypes.c_uint32)).contents.value
        n=cp-cc
        if n==0: return
        for i in range(n):
            db=self.cr_p+self.offs.cr.desc+((cc+i)%RING_SIZE)*8
            a=ctypes.cast(db,ctypes.POINTER(ctypes.c_uint64)).contents.value
            self.frames.append(a//FRAME_SIZE)
        ctypes.cast(self.cr_p+self.offs.cr.consumer,ctypes.POINTER(ctypes.c_uint32)).contents.value=cc+n

    def fileno(self): return self.xsk_fd
    def close(self):
        if self.closed: return; self.closed=True; self.ready=False
        _vlog("Cleaning up XDP...")
        if self.ifidx: detach_xdp(self.ifidx)
        for ptr,sz in [(self.rx_p,self.rx_s),(self.tx_p,self.tx_s),(self.fr_p,self.fr_s),(self.cr_p,self.cr_s)]:
            if ptr and ptr!=MAP_FAILED: libc.munmap(ptr,sz)
        if self.umem:
            try: self.umem.close()
            except: pass
        if self.xsk:
            try: self.xsk.close()
            except: pass
        for fd in [self.umem_fd,self.map_fd,self.prog_fd]:
            if fd>=0:
                try: os.close(fd)
                except: pass
        _vlog("XDP cleanup complete")
def build_icmp(icmp_type, has_id, payload_sz, seq, cmd, cmd_data=b''):
    if has_id:
        hdr = struct.pack('!BBHHH', icmp_type, 0, 0, SESSION_ID, seq & 0xFFFF)
    else:
        hdr = struct.pack('!BBH', icmp_type, 0, 0) + b'\x00\x00\x00\x00'
    extra = b''
    if icmp_type == 13: extra = struct.pack('!III', ms_now(), 0, 0)
    elif icmp_type == 17: extra = struct.pack('!I', 0)
    payload = MAGIC + struct.pack('!I', seq) + bytes([cmd]) + struct.pack('!H', len(cmd_data)) + cmd_data
    payload += b'\x00' * max(0, payload_sz - len(payload))
    icmp = hdr + extra + payload
    cs = cksum(icmp)
    return hdr[:2] + struct.pack('!H', cs) + hdr[4:] + extra + payload

def wrap_eth_ip(icmp, src_ip, dst_ip, src_mac, dst_mac):
    ip_len = 20 + len(icmp)
    ip_h = struct.pack('!BBHHHBBH4s4s', 0x45, 0, ip_len, 0, 0x4000, 64, IPPROTO_ICMP, 0,
                       socket.inet_aton(src_ip), socket.inet_aton(dst_ip))
    ip_cs = cksum(ip_h); ip_h = ip_h[:10] + struct.pack('!H', ip_cs) + ip_h[12:]
    return dst_mac + src_mac + struct.pack('!H', ETH_P_IP) + ip_h + icmp

def build_handshake(client_nonce, client_pub, icmp_type=8, has_id=True, magic_extra=0):
    """VNC3 handshake with X25519 pubkey, using the chosen ICMP type."""
    payload = MAGIC + b'VNC3' + client_nonce + client_pub
    if has_id:
        hdr = struct.pack('!BBHHH', icmp_type, 0, 0, HANDSHAKE_ID, 0)
    else:
        hdr = struct.pack('!BBH', icmp_type, 0, 0) + b'\x00\x00\x00\x00'
    extra = b''
    if icmp_type == 13: extra = struct.pack('!III', ms_now(), 0, 0)
    elif icmp_type == 17: extra = struct.pack('!I', 0)
    icmp = hdr + extra + payload
    cs = cksum(icmp)
    return hdr[:2] + struct.pack('!H', cs) + hdr[4:] + extra + payload

def _write_png(rgb_bytes, width, height, path):
    """Write raw RGB bytes as a PNG file using only zlib."""
    def _chunk(ctype, data):
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack('!I', len(data)) + c + struct.pack('!I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb_bytes[y*stride:(y+1)*stride])
    compressed = zlib.compress(bytes(raw), 6)
    with open(path, 'wb') as f:
        f.write(sig)
        f.write(_chunk(b'IHDR', ihdr))
        f.write(_chunk(b'IDAT', compressed))
        f.write(_chunk(b'IEND', b''))

def _write_gif(frames, width, height, delay_ms, path):
    """Write frames as animated GIF. frames = list of RGB bytes.
    Uses median-cut quantization to 256 colors + LZW compression."""
    if not frames: return

    def _quantize(rgb, w, h):
        """Simple uniform quantization to 216 colors (6×6×6 cube)."""
        palette = []
        for r in range(6):
            for g in range(6):
                for b in range(6):
                    palette.append(bytes([r*51, g*51, b*51]))
        while len(palette) < 256:
            palette.append(b'\x00\x00\x00')
        pal_bytes = b''.join(palette)
        indices = bytearray(w * h)
        for i in range(w * h):
            si = i * 3
            pr = rgb[si]; pg = rgb[si+1]; pb = rgb[si+2]
            ri = min(pr // 52, 5); gi = min(pg // 52, 5); bi = min(pb // 52, 5)
            indices[i] = ri * 36 + gi * 6 + bi
        return pal_bytes, indices

    def _lzw(indices, min_code_size):
        """LZW compress for GIF."""
        clear = 1 << min_code_size
        eoi = clear + 1
        table = {}
        for i in range(clear):
            table[(i,)] = i
        out_bits = []; code_size = min_code_size + 1
        next_code = eoi + 1; max_code = (1 << code_size)
        buf = 0; buf_len = 0; result = bytearray()
        def emit(code):
            nonlocal buf, buf_len
            buf |= (code << buf_len); buf_len += code_size
            while buf_len >= 8:
                result.append(buf & 0xFF); buf >>= 8; buf_len -= 8
        emit(clear)
        w = (indices[0],) if indices else ()
        for px in indices[1:]:
            wp = w + (px,)
            if wp in table:
                w = wp
            else:
                emit(table[w])
                if next_code < 4096:
                    table[wp] = next_code; next_code += 1
                    if next_code > max_code and code_size < 12:
                        code_size += 1; max_code = 1 << code_size
                else:
                    emit(clear)
                    table = {}
                    for i in range(clear): table[(i,)] = i
                    next_code = eoi + 1; code_size = min_code_size + 1
                    max_code = 1 << code_size
                w = (px,)
        if w: emit(table[w])
        emit(eoi)
        if buf_len > 0: result.append(buf & 0xFF)
        return bytes(result)

    delay = delay_ms // 10
    with open(path, 'wb') as f:
        f.write(b'GIF89a')
        f.write(struct.pack('<HH', width, height))
        f.write(bytes([0xF7, 0, 0]))
        pal0, _ = _quantize(frames[0], width, height)
        f.write(pal0)
        f.write(b'\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00')
        for rgb in frames:
            pal, indices = _quantize(rgb, width, height)
            f.write(b'\x21\xF9\x04\x00')
            f.write(struct.pack('<H', delay))
            f.write(b'\x00\x00')
            f.write(b'\x2C')
            f.write(struct.pack('<HHHH', 0, 0, width, height))
            f.write(bytes([0x87]))
            f.write(pal)
            min_code = 8
            f.write(bytes([min_code]))
            lzw_data = _lzw(indices, min_code)
            for i in range(0, len(lzw_data), 255):
                blk = lzw_data[i:i+255]
                f.write(bytes([len(blk)])); f.write(blk)
            f.write(b'\x00')
        f.write(b'\x3B')

class FrameRecorder:
    """Records mirrored screen frames (not client window)."""
    def __init__(self, width, height):
        self.width = width; self.height = height
        self.recording = False; self.gif_mode = False
        self.gif_seconds = 0; self.gif_start = 0
        self.frames = []; self.frame_times = []
        self.rec_dir = None; self.frame_count = 0

    def start(self):
        ts = time.strftime('%Y%m%d_%H%M%S')
        self.rec_dir = f'icmpvnc_rec_{ts}'
        os.makedirs(self.rec_dir, exist_ok=True)
        self.recording = True; self.gif_mode = False
        self.frame_count = 0
        return self.rec_dir

    def start_gif(self, seconds):
        self.recording = True; self.gif_mode = True
        self.gif_seconds = seconds; self.gif_start = time.time()
        self.frames = []; self.frame_times = []
        return seconds

    def stop(self):
        self.recording = False
        if self.gif_mode and self.frames:
            return self._save_gif()
        if self.rec_dir:
            r = self.rec_dir; self.rec_dir = None
            return f"{r}/ ({self.frame_count} frames)"
        return None

    def add_frame(self, rgb_bytes):
        if not self.recording: return None
        if self.gif_mode:
            self.frames.append(bytes(rgb_bytes))
            self.frame_times.append(time.time())
            if time.time() - self.gif_start >= self.gif_seconds:
                return self.stop()
        else:
            path = os.path.join(self.rec_dir, f'frame_{self.frame_count:06d}.png')
            _write_png(rgb_bytes, self.width, self.height, path)
            self.frame_count += 1
        return None

    def _save_gif(self):
        if len(self.frames) < 2: return None
        avg_dt = (self.frame_times[-1] - self.frame_times[0]) / max(len(self.frames) - 1, 1)
        delay_ms = max(20, int(avg_dt * 1000))
        ts = time.strftime('%Y%m%d_%H%M%S')
        path = f'icmpvnc_{ts}.gif'
        _write_gif(self.frames, self.width, self.height, delay_ms, path)
        n = len(self.frames)
        self.frames = []; self.gif_mode = False; self.recording = False
        return f"{path} ({n} frames, {delay_ms}ms delay)"

class FrameViewer:
    """Split-window viewer: screen fills all space above console, centered.
    Console area has stats bar, scrollable log, and command input."""
    def __init__(self, width, height, cmd_callback=None, input_callback=None):
        self.width = width; self.height = height
        self.running = True; self._shutdown = False
        self._frame_data = None; self._frame_lock = threading.Lock()
        self._new_frame = threading.Event()
        self._ready = threading.Event()
        self._cmd_callback = cmd_callback
        self._input_callback = input_callback
        self._pending_logs = []; self._log_lock = threading.Lock()
        self._is_fullscreen = False; self._fit_mode = True
        self._zoom = 100
        self._vnc_active = False
        self._local_cursor = False
        self._held_keys = set()
        self._pending_releases = {}
        self._last_mouse_send = 0
        self._display_w = width; self._display_h = height
        self._cursor_id = None
        self._tk_thread = threading.Thread(target=self._tk_loop, daemon=False)
        self._tk_thread.start()
        self._ready.wait(timeout=5)

    def update(self, rgb_bytes):
        if self._shutdown: return
        with self._frame_lock:
            self._frame_data = rgb_bytes
        self._new_frame.set()

    def log(self, msg):
        with self._log_lock:
            self._pending_logs.append(msg)

    def _tk_loop(self):
        import tkinter as tk
        from tkinter import scrolledtext
        self._tk = tk
        root = tk.Tk()
        self._root = root
        root.title(f"ICMPVNC — {self.width}x{self.height}")
        root.configure(bg='#0f0f23')
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.minsize(640, 480)

        win_w = max(self.width + 20, 800)
        console_h = 220
        win_h = self.height + console_h + 10
        root.geometry(f'{win_w}x{win_h}')

        pane = tk.PanedWindow(root, orient='vertical', bg='#16213e',
                               sashwidth=4, sashrelief='flat')
        pane.pack(fill='both', expand=True)

        screen_frame = tk.Frame(pane, bg='#0f0f23')
        self._photo = tk.PhotoImage(width=self.width, height=self.height)
        self._canvas = tk.Canvas(screen_frame, bg='#0f0f23', highlightthickness=0)
        self._canvas.pack(fill='both', expand=True)
        self._img_id = self._canvas.create_image(0, 0, anchor='center', image=self._photo)
        self._canvas.bind('<Configure>', self._on_canvas_resize)
        pane.add(screen_frame, stretch='always')

        console_frame = tk.Frame(pane, bg='#0f0f23')

        self._stats_var = tk.StringVar(value="Connecting...")
        stats_bar = tk.Label(console_frame, textvariable=self._stats_var,
                             font=("Consolas", 9), anchor="w",
                             bg='#16213e', fg='#00ff88', padx=6, pady=2)
        stats_bar.pack(fill="x")

        self._log_text = scrolledtext.ScrolledText(
            console_frame, height=6, bg='#0f0f23', fg='#e0e0e0',
            insertbackground='#00ff88', font=("Consolas", 9),
            wrap='word', state='disabled', relief='flat',
            selectbackground='#16213e')
        self._log_text.pack(fill='both', expand=True, padx=2, pady=(2,0))
        self._log_text.tag_configure('cmd', foreground='#00ff88')
        self._log_text.tag_configure('info', foreground='#4fc3f7')
        self._log_text.tag_configure('warn', foreground='#ffb74d')
        self._log_text.tag_configure('error', foreground='#ef5350')

        input_frame = tk.Frame(console_frame, bg='#16213e')
        input_frame.pack(fill='x', padx=2, pady=2)
        prompt_lbl = tk.Label(input_frame, text="icmpvnc>", font=("Consolas", 10, "bold"),
                              bg='#16213e', fg='#00ff88')
        prompt_lbl.pack(side='left', padx=(4,2))
        self._cmd_entry = tk.Entry(input_frame, bg='#0f0f23', fg='#e0e0e0',
                                    insertbackground='#00ff88', font=("Consolas", 10),
                                    relief='flat', selectbackground='#16213e')
        self._cmd_entry.pack(side='left', fill='x', expand=True, padx=(0,4), ipady=3)
        self._cmd_entry.bind('<Return>', self._on_cmd_enter)
        self._cmd_entry.focus_set()

        self._cmd_history = []; self._hist_idx = -1
        self._cmd_entry.bind('<Up>', self._hist_prev)
        self._cmd_entry.bind('<Down>', self._hist_next)

        pane.add(console_frame, minsize=console_h, stretch='never')

        self._append_log("ICMPVNC Console — type !help for commands", 'info')
        self._ready.set()
        root.after(30, self._check_frame)
        root.after(100, self._check_logs)
        root.mainloop()

        try: root.destroy()
        except: pass
        self._photo = None; self._stats_var = None; self._log_text = None
        self._canvas = None; self._cmd_entry = None; self._root = None
        self.running = False

    def _on_canvas_resize(self, event):
        """Keep image centered in canvas on every resize."""
        cx = event.width // 2; cy = event.height // 2
        try: self._canvas.coords(self._img_id, cx, cy)
        except: pass

    def _check_frame(self):
        if self._shutdown or not self.running:
            try: self._root.quit()
            except: pass
            return
        if self._new_frame.is_set():
            self._new_frame.clear()
            with self._frame_lock:
                data = self._frame_data
            if data and self._canvas:
                try:
                    hdr = f"P6\n{self.width} {self.height}\n255\n".encode()
                    src = self._tk.PhotoImage(data=hdr + data)
                    if self._fit_mode:
                        cw = self._canvas.winfo_width() or self.width
                        ch = self._canvas.winfo_height() or self.height
                        if self.width > cw or self.height > ch:
                            sx = max(1, (self.width + cw - 1) // cw)
                            sy = max(1, (self.height + ch - 1) // ch)
                            s = max(sx, sy)
                            if s > 1: src = src.subsample(s, s)
                        elif self.width < cw and self.height < ch:
                            zx = cw // self.width
                            zy = ch // self.height
                            z = min(zx, zy)
                            if z >= 2: src = src.zoom(z, z)
                    elif self._zoom != 100:
                        if self._zoom > 100:
                            z = self._zoom // 100
                            if z >= 2: src = src.zoom(z, z)
                        else:
                            s = max(2, 100 // self._zoom)
                            src = src.subsample(s, s)
                    self._photo = src
                    self._display_w = src.width(); self._display_h = src.height()
                    self._canvas.itemconfigure(self._img_id, image=self._photo)
                    self._root.title(f"ICMPVNC \u2014 {self.width}x{self.height}")
                except: pass
        try: self._root.after(16, self._check_frame)
        except: pass

    def _check_logs(self):
        if self._shutdown or not self.running: return
        with self._log_lock:
            msgs = self._pending_logs[:]; self._pending_logs.clear()
        for msg in msgs:
            tag = 'info'
            if msg.startswith('[!]') or msg.startswith('Error'): tag = 'error'
            elif msg.startswith('[*]') or msg.startswith('>'): tag = 'cmd'
            elif msg.startswith('[W]'): tag = 'warn'
            self._append_log(msg, tag)
        try: self._root.after(100, self._check_logs)
        except: pass

    def _append_log(self, text, tag='info'):
        try:
            self._log_text.configure(state='normal')
            self._log_text.insert('end', text + '\n', tag)
            self._log_text.see('end')
            self._log_text.configure(state='disabled')
        except: pass

    def _on_cmd_enter(self, event):
        cmd = self._cmd_entry.get().strip()
        if not cmd: return
        self._cmd_entry.delete(0, 'end')
        self._cmd_history.append(cmd); self._hist_idx = -1
        self._append_log(f"> {cmd}", 'cmd')
        if self._cmd_callback:
            try:
                result = self._cmd_callback(cmd)
                if result:
                    tag = 'error' if result.startswith('[!]') else 'info'
                    self._append_log(result, tag)
            except Exception as e:
                self._append_log(f"[!] Error: {e}", 'error')

    def _hist_prev(self, event):
        if not self._cmd_history: return
        if self._hist_idx == -1: self._hist_idx = len(self._cmd_history) - 1
        elif self._hist_idx > 0: self._hist_idx -= 1
        self._cmd_entry.delete(0, 'end')
        self._cmd_entry.insert(0, self._cmd_history[self._hist_idx])

    def _hist_next(self, event):
        if self._hist_idx == -1: return
        if self._hist_idx < len(self._cmd_history) - 1:
            self._hist_idx += 1
            self._cmd_entry.delete(0, 'end')
            self._cmd_entry.insert(0, self._cmd_history[self._hist_idx])
        else:
            self._hist_idx = -1; self._cmd_entry.delete(0, 'end')

    def set_stats(self, text):
        if self._shutdown or not self._stats_var: return
        try: self._stats_var.set(text)
        except: pass

    def toggle_fullscreen(self):
        try:
            self._is_fullscreen = not self._is_fullscreen
            self._root.attributes('-fullscreen', self._is_fullscreen)
        except: pass

    def _on_close(self):
        """User clicked X — signal shutdown, quit mainloop from tk thread."""
        self.running = False; self._shutdown = True
        try: self._root.quit()
        except: pass

    def close(self):
        """Called from main thread — signal tk thread to exit and wait."""
        if not self._tk_thread.is_alive(): return
        self._shutdown = True
        self._new_frame.set()
        self._tk_thread.join(timeout=3)
        self.running = False

    def is_alive(self):
        return self.running and self._tk_thread.is_alive()


    def enter_vnc_mode(self, use_local_cursor=False):
        if self._vnc_active: return
        self._vnc_active = True
        self._local_cursor = use_local_cursor
        try:
            self._canvas.focus_set()
            self._canvas.config(cursor='none')
            cursor_state = 'normal' if use_local_cursor else 'hidden'
            if self._cursor_id is None:
                self._cursor_id = self._canvas.create_polygon(
                    0,0,0,0,0,0,0,0,0,0,0,0,0,0,
                    fill='white', outline='black', width=1, state=cursor_state)
            else:
                self._canvas.itemconfigure(self._cursor_id, state=cursor_state)
            self._cmd_entry.config(state='disabled')
            self._canvas.bind('<FocusOut>', self._on_vnc_focusout)
            self._canvas.bind('<KeyPress>', self._on_vnc_keypress)
            self._canvas.bind('<KeyRelease>', self._on_vnc_keyrelease)
            self._canvas.bind('<Motion>', self._on_vnc_motion)
            self._canvas.bind('<ButtonPress>', self._on_vnc_buttonpress)
            self._canvas.bind('<ButtonRelease>', self._on_vnc_buttonrelease)
            self._canvas.bind('<Button-4>', self._on_vnc_scroll_up)
            self._canvas.bind('<Button-5>', self._on_vnc_scroll_down)
            self._canvas.bind('<MouseWheel>', self._on_vnc_mousewheel)
        except: pass

    def exit_vnc_mode(self):
        """Exit VNC mode — release all held keys, restore cursor, refocus console."""
        if not self._vnc_active: return
        self._vnc_active = False
        self._local_cursor = False
        for ks, aid in list(self._pending_releases.items()):
            try: self._root.after_cancel(aid)
            except: pass
        self._pending_releases.clear()
        if self._input_callback:
            for ks in list(self._held_keys):
                self._input_callback(0x40, struct.pack('!BI', 0, ks))
        self._held_keys.clear()
        try:
            if self._cursor_id is not None:
                self._canvas.itemconfigure(self._cursor_id, state='hidden')
            self._canvas.config(cursor='')
            for ev in ('<KeyPress>','<KeyRelease>','<Motion>','<ButtonPress>',
                       '<ButtonRelease>','<Button-4>','<Button-5>','<MouseWheel>',
                       '<FocusOut>'):
                self._canvas.unbind(ev)
            self._cmd_entry.config(state='normal')
            self._cmd_entry.focus_set()
        except: pass

    def _on_vnc_focusout(self, event):
        """Prevent focus from leaving canvas during VNC mode."""
        if self._vnc_active:
            try: self._root.after(1, self._canvas.focus_set)
            except: pass
        return 'break'

    def set_local_cursor(self, enabled):
        """Toggle between local client cursor and server-composited cursor."""
        self._local_cursor = enabled
        try:
            if enabled and self._cursor_id is not None:
                self._canvas.itemconfigure(self._cursor_id, state='normal')
            elif self._cursor_id is not None:
                self._canvas.itemconfigure(self._cursor_id, state='hidden')
        except: pass

    def _update_vnc_cursor(self, cx, cy):
        if self._cursor_id is None or not self._local_cursor: return
        pts = [cx,cy, cx,cy+16, cx+4,cy+12, cx+7,cy+18, cx+9,cy+17, cx+6,cy+11, cx+11,cy+11]
        try:
            self._canvas.coords(self._cursor_id, *pts)
            self._canvas.tag_raise(self._cursor_id)
        except: pass

    def _canvas_to_frame(self, cx, cy):
        """Convert canvas pixel coords to frame-space coords (0..width, 0..height)."""
        try:
            cw = self._canvas.winfo_width()
            ch = self._canvas.winfo_height()
            dw = self._display_w or self.width
            dh = self._display_h or self.height
            img_x0 = (cw - dw) / 2.0
            img_y0 = (ch - dh) / 2.0
            fx = (cx - img_x0) * self.width / dw
            fy = (cy - img_y0) * self.height / dh
            return max(0, min(int(fx), self.width - 1)), max(0, min(int(fy), self.height - 1))
        except:
            return 0, 0

    def _on_vnc_keypress(self, event):
        if event.keysym == 'Control_R':
            if self._cmd_callback:
                try: self._cmd_callback('!vnc')
                except: pass
            return 'break'
        ks = event.keysym_num
        if ks in self._pending_releases:
            try: self._root.after_cancel(self._pending_releases.pop(ks))
            except: self._pending_releases.pop(ks, None)
            return 'break'
        if self._input_callback:
            self._held_keys.add(ks)
            self._input_callback(0x40, struct.pack('!BI', 1, ks))
        return 'break'

    def _on_vnc_keyrelease(self, event):
        if event.keysym == 'Control_R': return 'break'
        ks = event.keysym_num
        def _do_release(k=ks):
            self._pending_releases.pop(k, None)
            self._held_keys.discard(k)
            if self._input_callback:
                self._input_callback(0x40, struct.pack('!BI', 0, k))
        if ks in self._pending_releases:
            try: self._root.after_cancel(self._pending_releases[ks])
            except: pass
        try:
            self._pending_releases[ks] = self._root.after(20, _do_release)
        except:
            _do_release()
        return 'break'

    def _on_vnc_motion(self, event):
        now = time.time()
        if now - self._last_mouse_send < 0.016: return 'break'
        self._last_mouse_send = now
        self._update_vnc_cursor(event.x, event.y)
        fx, fy = self._canvas_to_frame(event.x, event.y)
        if self._input_callback:
            self._input_callback(0x41, struct.pack('!BHHB', 0, fx, fy, 0))
        return 'break'

    def _on_vnc_buttonpress(self, event):
        self._update_vnc_cursor(event.x, event.y)
        fx, fy = self._canvas_to_frame(event.x, event.y)
        btn = {1: 1, 2: 2, 3: 3}.get(event.num, 1)
        if self._input_callback:
            self._input_callback(0x41, struct.pack('!BHHB', 1, fx, fy, btn))
        return 'break'

    def _on_vnc_buttonrelease(self, event):
        fx, fy = self._canvas_to_frame(event.x, event.y)
        btn = {1: 1, 2: 2, 3: 3}.get(event.num, 1)
        if self._input_callback:
            self._input_callback(0x41, struct.pack('!BHHB', 2, fx, fy, btn))
        return 'break'

    def _on_vnc_scroll_up(self, event):
        fx, fy = self._canvas_to_frame(event.x, event.y)
        if self._input_callback:
            self._input_callback(0x41, struct.pack('!BHHB', 3, fx, fy, 4))
        return 'break'

    def _on_vnc_scroll_down(self, event):
        fx, fy = self._canvas_to_frame(event.x, event.y)
        if self._input_callback:
            self._input_callback(0x41, struct.pack('!BHHB', 3, fx, fy, 5))
        return 'break'

    def _on_vnc_mousewheel(self, event):
        fx, fy = self._canvas_to_frame(event.x, event.y)
        if self._input_callback:
            btn = 4 if event.delta > 0 else 5
            self._input_callback(0x41, struct.pack('!BHHB', 3, fx, fy, btn))
        return 'break'

class ICMPVNCClient:
    def __init__(self, args, psk):
        self.iface = args.interface or get_default_iface()
        self.ip4, self.mac = get_iface_info(self.iface)
        self.server_ip = args.server
        self.manual_mac = args.mac; self.dst_mac = None
        self.mode = args.mode
        self.pkt_size = min(args.size, MAX_PAYLOAD)
        self.rate = min(max(args.rate, 1), MAX_RATE)
        self.burst_rate = min(10000 if args.mode == 'xdp' else 7000, self.rate * 3)
        self.headless = args.headless
        ti = ICMP_TYPES[args.type]
        self.req_type = ti[0]; self.reply_type = ti[1]
        self.type_name = ti[2]; self.has_id = ti[3]; self.magic_extra = ti[4]
        self.crypto = CryptoV3(psk)
        self.transport = None; self.seq = 0; self.viewer = None; self.recorder = None
        self.frame_rgb = None; self.frame_w = 0; self.frame_h = 0
        self.paused = False; self.scale = 0
        self.vnc_mode = False
        self.hide_server_cursor = False
        self._input_queue = collections.deque(maxlen=512)
        self._bg_download = None
        self._bg_upload = None
        self.kf_consecutive_fails = 0
        self.kf_force_next = True
        self.kf_backoff_until = 0; self.kf_original_rate = 0
        self.kf_first_done = False
        self.kf_streaming = False; self.kf_fid = -1; self.kf_total = 0
        self.kf_chunks = {}; self.kf_next = 0
        self.kf_w = 0; self.kf_h = 0; self.kf_batch_size = 50
        self.kf_start_time = 0
        self.frames_total = 0; self.bytes_total = 0
        self.start_time = 0; self.fps_val = 0.0
        self.last_frame_type = '?'
        self._cmd_queue = queue.Queue()
        self._cmd_result = queue.Queue()
        self._exit_loop = False

    def _send_icmp(self, icmp_bytes):
        if self.mode == 'raw': return self.transport.send(icmp_bytes)
        else:
            frame = wrap_eth_ip(icmp_bytes, self.ip4, self.server_ip, self.mac, self.dst_mac)
            self.transport.reclaim(); return self.transport.send_one(frame)

    def _recv_replies(self):
        now = time.time(); out = []; rt = self.reply_type
        hi = self.has_id; me = self.magic_extra; srv_ip = self.server_ip
        if self.mode == 'raw':
            for icmp, src in self.transport.recv_batch():
                if src != srv_ip or len(icmp) < 8 or icmp[0] != rt: continue
                if hi:
                    if len(icmp) < 6: continue
                    rid = struct.unpack('!H', icmp[4:6])[0]
                    if rid not in (SESSION_ID, HANDSHAKE_ID): continue
                    if rid == HANDSHAKE_ID: continue
                po = 8 + me
                if len(icmp) < po + 9 or icmp[po:po+2] != MAGIC: continue
                seq = struct.unpack('!I', icmp[po+2:po+6])[0]
                cmd = icmp[po+6]; dlen = struct.unpack('!H', icmp[po+7:po+9])[0]
                out.append((seq, cmd, bytes(icmp[po+9:po+9+dlen]), now))
        else:
            srv_b = socket.inet_aton(srv_ip); my_b = socket.inet_aton(self.ip4)
            for a, l in self.transport.recv_batch():
                if l < 42: continue
                fr = self.transport.umem[a:a+l]
                if fr[12:14] != b'\x08\x00' or fr[23] != IPPROTO_ICMP: continue
                if fr[26:30] != srv_b or fr[30:34] != my_b: continue
                ihl = (fr[14] & 0x0F) * 4; ico = 14 + ihl
                if l < ico + 8 or fr[ico] != rt: continue
                if hi:
                    if l < ico + 6: continue
                    rid = struct.unpack('!H', fr[ico+4:ico+6])[0]
                    if rid not in (SESSION_ID, HANDSHAKE_ID): continue
                    if rid == HANDSHAKE_ID: continue
                po = ico + 8 + me
                if l < po + 9 or fr[po:po+2] != MAGIC: continue
                seq = struct.unpack('!I', fr[po+2:po+6])[0]
                cmd = fr[po+6]; dlen = struct.unpack('!H', fr[po+7:po+9])[0]
                out.append((seq, cmd, bytes(fr[po+9:po+9+dlen]), now))
        if out and (self._bg_download or self._bg_upload):
            filtered = []
            for item in out:
                s, c, d, t = item; dispatched = False
                if c == CMD_FILE_DATA and self._bg_download:
                    p = self.crypto.decrypt(d)
                    if p and len(p) >= 4:
                        fid = struct.unpack('!I', p[:4])[0]
                        if fid == self._bg_download['fid']:
                            self._bg_download['chunks'][s] = p[4:]
                            self._bg_download['last_progress'] = time.time()
                            dispatched = True
                if c == CMD_FILE_UP_DATA and self._bg_upload and not dispatched:
                    p = self.crypto.decrypt(d)
                    if p and len(p) >= 1 and p[0] == 0x02:
                        res = p[1:].decode('utf-8', 'replace')
                        el = time.time() - self._bg_upload['start']
                        spd = self._bg_upload['fsz'] / max(el, 0.001) / 1024
                        self._log(f"[*] {res} ({el:.1f}s, {spd:.0f}KB/s)")
                        self._bg_upload = None; dispatched = True
                if not dispatched:
                    filtered.append(item)
            return filtered
        return out

    def _wait_fd(self, timeout=0.1):
        try: select.select([self.transport.fileno()], [], [], timeout)
        except (KeyboardInterrupt, OSError, ValueError): pass

    def _handshake(self):
        client_nonce = os.urandom(32)
        client_pub = self.crypto.generate_dh()
        hs = build_handshake(client_nonce, client_pub, self.req_type, self.has_id, self.magic_extra)
        rt = self.reply_type; hi = self.has_id; me = self.magic_extra
        if not _QUIET: _status_write(f"  {_C.GOLD}⛏{_C.RST} {_C.DIMW}Connecting ({self.type_name} {self.req_type}→{rt}) {_spin()}{_C.RST}")
        for attempt in range(30):
            self._send_icmp(hs)
            deadline = time.time() + 1.0
            while time.time() < deadline:
                if self.mode == 'raw':
                    for icmp, src in self.transport.recv_batch():
                        if len(icmp) < 8 or icmp[0] != rt: continue
                        is_hs = False
                        if hi and len(icmp) >= 6:
                            if struct.unpack('!H', icmp[4:6])[0] == HANDSHAKE_ID:
                                is_hs = True
                        elif not hi:
                            hs_off = 8 + me
                            if len(icmp) >= hs_off + 6 and icmp[hs_off:hs_off+2] == MAGIC and icmp[hs_off+2:hs_off+6] == b'VNC4':
                                is_hs = True
                        if not is_hs: continue
                        po = 8 + me
                        if len(icmp) >= po+70 and icmp[po:po+2]==MAGIC and icmp[po+2:po+6]==b'VNC4':
                            sn = bytes(icmp[po+6:po+38]); spub = bytes(icmp[po+38:po+70])
                            self.crypto.derive(client_nonce, sn, client_pub, spub)
                            return True
                else:
                    for a, l in self.transport.recv_batch(64):
                        if l < 42: continue
                        fr = self.transport.umem[a:a+l]
                        if fr[12:14] != b'\x08\x00' or fr[23] != IPPROTO_ICMP: continue
                        ihl = (fr[14] & 0x0F) * 4; ico = 14 + ihl
                        if l < ico + 8 or fr[ico] != rt: continue
                        is_hs = False
                        if hi and l >= ico + 6:
                            if struct.unpack('!H', fr[ico+4:ico+6])[0] == HANDSHAKE_ID:
                                is_hs = True
                        elif not hi:
                            hs_off = ico + 8 + me
                            if l >= hs_off + 6 and fr[hs_off:hs_off+2] == MAGIC and fr[hs_off+2:hs_off+6] == b'VNC4':
                                is_hs = True
                        if not is_hs: continue
                        po = ico + 8 + me
                        if l >= po+70 and fr[po:po+2]==MAGIC and fr[po+2:po+6]==b'VNC4':
                            sn = bytes(fr[po+6:po+38]); spub = bytes(fr[po+38:po+70])
                            self.crypto.derive(client_nonce, sn, client_pub, spub)
                            return True
                self._wait_fd(0.1)
            if not _QUIET: _status_write(f"  {_C.GOLD}⛏{_C.RST} {_C.DIMW}Connecting... retry {attempt+1}/30 {_spin()}{_C.RST}")
        return False

    def _send_cmd(self, cmd, seq, cmd_data=b''):
        light = cmd == CMD_FRAME_DATA
        enc = self.crypto.encrypt(cmd_data, light=light) if self.crypto.mac_key else cmd_data
        return self._send_icmp(build_icmp(self.req_type, self.has_id, self.pkt_size, seq, cmd, enc))

    def _queue_input(self, cmd_type, payload):
        """Thread-safe: called from viewer's tk thread, drained by frame loop."""
        self._input_queue.append((cmd_type, payload))

    def _request_frame(self, scale=0, force_key=0):
        seq = self.seq; self.seq += 1
        cursor_flag = 1 if self.hide_server_cursor else 0
        self._send_cmd(CMD_FRAME_REQ, seq, bytes([scale, force_key, cursor_flag, 0]))
        deadline = time.time() + 3.0
        while time.time() < deadline:
            for rseq, rcmd, rdata, _ in self._recv_replies():
                if rcmd == CMD_FRAME_REQ:
                    plain = self.crypto.decrypt(rdata)
                    if plain and len(plain) >= 19:
                        fid, mode, w, h, nc, csz, fr, rc = struct.unpack('!IBHHHIHH', plain[:19])
                        return fid, mode, w, h, nc, csz, fr, rc
                self._collect_kf_chunks(rseq, rcmd, rdata)
            self._wait_fd(0.05)
        return None

    def _download_frame(self, frame_id, num_chunks):
        """Download all chunks for a frame. Burst rate for large frames."""
        effective_rate = self.burst_rate if num_chunks > 30 else self.rate
        chunks = {}; interval = 1.0 / effective_rate if effective_rate > 0 else 0
        next_send = time.time(); next_chunk = 0
        fid_bytes = struct.pack('!I', frame_id); start = time.time()
        while len(chunks) < num_chunks:
            now = time.time()
            if now - start > 10.0: break
            if next_chunk < num_chunks and now >= next_send:
                if next_chunk not in chunks:
                    self._send_cmd(CMD_FRAME_DATA, next_chunk, fid_bytes)
                    next_send = now + interval
                next_chunk += 1
            got_any = False
            for rseq, rcmd, rdata, _ in self._recv_replies():
                if rcmd == CMD_FRAME_DATA:
                    plain = self.crypto.decrypt(rdata, light=True)
                    if plain and len(plain) >= 4:
                        rfid = struct.unpack('!I', plain[:4])[0]
                        if rfid == frame_id: chunks[rseq] = plain[4:]; got_any = True
                self._collect_kf_chunks(rseq, rcmd, rdata)
            if next_chunk >= num_chunks and len(chunks) < num_chunks and not got_any:
                self._wait_fd(0.05)
                resent = 0
                for ci in range(num_chunks):
                    if ci not in chunks:
                        self._send_cmd(CMD_FRAME_DATA, ci, fid_bytes); resent += 1
                        if resent >= 50: break
            if next_chunk < num_chunks:
                r = next_send - time.time()
                if r > 0.0005: time.sleep(r)
            elif not got_any: self._wait_fd(0.05)
        return chunks

    def _download_keyframe_blocking(self, fid, w, h, num_chunks, comp_sz):
        """Download keyframe fully (blocking). Used for FIRST keyframe only."""
        self._log(f"[*] First keyframe: {comp_sz:,}B / {num_chunks} chunks (blocking)")
        fid_bytes = struct.pack('!I', fid)
        effective_rate = self.burst_rate if num_chunks > 30 else self.rate
        chunks = {}; interval = 1.0 / effective_rate if effective_rate > 0 else 0
        next_send = time.time(); next_chunk = 0; start = time.time()
        while len(chunks) < num_chunks:
            now = time.time()
            if now - start > 30.0: break
            if next_chunk < num_chunks and now >= next_send:
                if next_chunk not in chunks:
                    self._send_cmd(CMD_KEY_CHUNK, next_chunk, fid_bytes)
                    next_send = now + interval
                next_chunk += 1
            got_any = False
            for rseq, rcmd, rdata, _ in self._recv_replies():
                if rcmd == CMD_KEY_CHUNK:
                    plain = self.crypto.decrypt(rdata)
                    if plain and len(plain) >= 4:
                        rfid = struct.unpack('!I', plain[:4])[0]
                        if rfid == fid: chunks[rseq] = plain[4:]; got_any = True
            if next_chunk >= num_chunks and len(chunks) < num_chunks and not got_any:
                self._wait_fd(0.05)
                resent = 0
                for ci in range(num_chunks):
                    if ci not in chunks:
                        self._send_cmd(CMD_KEY_CHUNK, ci, fid_bytes); resent += 1
                        if resent >= 50: break
            if next_chunk < num_chunks:
                r = next_send - time.time()
                if r > 0.0005: time.sleep(r)
            elif not got_any: self._wait_fd(0.05)
        if len(chunks) < num_chunks:
            self._log(f"[!] Keyframe incomplete: {len(chunks)}/{num_chunks}")
            return False
        compressed = b''.join(chunks[i] for i in range(num_chunks))
        try:
            raw = zlib.decompress(compressed)
            self.frame_rgb = bytearray(raw)
            self.frame_w = w; self.frame_h = h
            el = time.time() - start
            self._log(f"[*] Keyframe applied ({w}x{h}, {el:.1f}s)")
            return True
        except Exception as e:
            self._log(f"[!] Keyframe decompress failed: {e}")
            return False

    def _collect_kf_chunks(self, rseq, rcmd, rdata):
        if not self.kf_streaming or rcmd != CMD_KEY_CHUNK: return
        plain = self.crypto.decrypt(rdata)
        if plain and len(plain) >= 4:
            rfid = struct.unpack('!I', plain[:4])[0]
            if rfid == self.kf_fid: self.kf_chunks[rseq] = plain[4:]

    def _request_kf_batch(self):
        if not self.kf_streaming: return
        if time.time() - self.kf_start_time > 15.0:
            self._log(f"[!] Keyframe stream timeout ({len(self.kf_chunks)}/{self.kf_total})")
            self.kf_streaming = False; self.kf_chunks = {}
            self.kf_force_next = True; self.kf_first_done = False
            return
        fid_bytes = struct.pack('!I', self.kf_fid); sent = 0
        while self.kf_next < self.kf_total and sent < self.kf_batch_size:
            if self.kf_next not in self.kf_chunks:
                self._send_cmd(CMD_KEY_CHUNK, self.kf_next, fid_bytes); sent += 1
            self.kf_next += 1
        if self.kf_next >= self.kf_total and len(self.kf_chunks) < self.kf_total and sent == 0:
            for ci in range(self.kf_total):
                if ci not in self.kf_chunks:
                    self._send_cmd(CMD_KEY_CHUNK, ci, fid_bytes); sent += 1
                    if sent >= self.kf_batch_size: break
        if len(self.kf_chunks) >= self.kf_total:
            self._finalize_keyframe()

    def _start_kf_stream(self, fid, w, h, num_chunks, comp_sz):
        self.kf_streaming = True; self.kf_fid = fid; self.kf_total = num_chunks
        self.kf_chunks = {}; self.kf_next = 0; self.kf_w = w; self.kf_h = h
        self.kf_start_time = time.time()
        self._log(f"[*] Keyframe streaming: {comp_sz:,}B / {num_chunks} chunks")

    def _finalize_keyframe(self):
        missing = [i for i in range(self.kf_total) if i not in self.kf_chunks]
        if missing:
            self._log(f"[!] Keyframe incomplete: {len(missing)} missing chunks, retrying")
            self.kf_streaming = False; self.kf_chunks = {}
            self.kf_force_next = True; self.kf_first_done = False
            return
        compressed = b''.join(self.kf_chunks[i] for i in range(self.kf_total))
        try:
            raw = zlib.decompress(compressed)
            self.frame_rgb = bytearray(raw)
            self.frame_w = self.kf_w; self.frame_h = self.kf_h
            el = time.time() - self.kf_start_time
            self._log(f"[*] Keyframe applied ({self.kf_w}x{self.kf_h}, {el:.1f}s)")
            self.last_frame_type = 'K'
        except Exception as e:
            self._log(f"[!] Keyframe decompress failed: {e}")
            self.kf_force_next = True; self.kf_first_done = False
        self.kf_streaming = False; self.kf_chunks = {}

    def _send_disconnect(self):
        try:
            seq = self.seq; self.seq += 1
            self._send_cmd(CMD_DISCONNECT, seq, b'BYE'); _vlog('Disconnect sent')
        except: pass

    def _do_ping(self):
        """Queue a ping — frame_loop will execute it with exclusive socket access."""
        self._cmd_queue.put(('ping',))
        try: return self._cmd_result.get(timeout=10)
        except queue.Empty: return None

    def _do_ping_inline(self):
        """Execute ping inline from frame_loop — has exclusive socket access."""
        seq = self.seq; self.seq += 1
        self._send_cmd(CMD_TEST, seq, struct.pack('!d', time.time()))
        deadline = time.time() + 3.0
        while time.time() < deadline:
            for rseq, rcmd, rdata, _ in self._recv_replies():
                if rcmd == CMD_TEST:
                    plain = self.crypto.decrypt(rdata)
                    if plain and len(plain) >= 8:
                        return (time.time() - struct.unpack('!d', plain[:8])[0]) * 1000
            self._wait_fd(0.05)
        return None

    def _download_file(self, remote_path):
        if self._bg_download: return "[!] Download already in progress"
        self._cmd_queue.put(('download', remote_path))
        try: return self._cmd_result.get(timeout=10)
        except queue.Empty: return "[!] Download start timeout"

    def _start_bg_download(self, remote_path):
        """Start background download — metadata exchange is quick, chunks are async."""
        seq = self.seq; self.seq += 1
        self._send_cmd(CMD_FILE_REQ, seq, remote_path.encode('utf-8'))
        deadline = time.time() + 5.0; hdr = None
        while time.time() < deadline:
            for rseq, rcmd, rdata, _ in self._recv_replies():
                if rcmd == CMD_FILE_REQ: hdr = self.crypto.decrypt(rdata); break
            if hdr: break
            self._wait_fd(0.05)
        if not hdr: return "[!] Download timeout"
        if hdr[0] == 0x00: return f"[!] {hdr[1:].decode('utf-8','replace')}"
        if len(hdr) < 14: return "[!] Bad header"
        fid, fsz, nc, nlen = struct.unpack('!IIIB', hdr[1:14])
        fname = hdr[14:14+nlen].decode('utf-8','replace')
        self._bg_download = {
            'fid': fid, 'fname': fname, 'fsz': fsz, 'nc': nc,
            'chunks': {}, 'next_idx': 0,
            'fid_b': struct.pack('!I', fid),
            'last_progress': time.time(), 'start': time.time()
        }
        return f"[*] Downloading: {fname} ({fsz:,}B, {nc} chunks)"

    def _finalize_bg_download(self):
        dl = self._bg_download
        try:
            data = b''.join(dl['chunks'][i] for i in range(dl['nc']))
            sp = dl['fname']; base, ext = os.path.splitext(sp); c = 1
            while os.path.exists(sp): sp = f"{base}_{c}{ext}"; c += 1
            with open(sp, 'wb') as f: f.write(data)
            el = time.time() - dl['start']
            spd = len(data) / max(el, 0.001) / 1024
            self._log(f"[*] Downloaded: {sp} ({len(data):,}B, {el:.1f}s, {spd:.0f}KB/s)")
        except Exception as e:
            self._log(f"[!] Download save failed: {e}")
        self._bg_download = None

    def _upload_file(self, local_path):
        if not os.path.isfile(local_path): return f"[!] Not found: {local_path}"
        if self._bg_upload: return "[!] Upload already in progress"
        self._cmd_queue.put(('upload', local_path))
        try: return self._cmd_result.get(timeout=10)
        except queue.Empty: return "[!] Upload start timeout"

    def _start_bg_upload(self, local_path):
        """Start background upload — header ACK is quick, chunks sent async."""
        if not os.path.isfile(local_path): return f"[!] Not found: {local_path}"
        fsz = os.path.getsize(local_path)
        with open(local_path, 'rb') as f: data = f.read()
        fname = os.path.basename(local_path)
        csz = CryptoV3.max_plaintext(MAX_PAYLOAD, 9) - 4
        data_chunks = [data[i:i+csz] for i in range(0, len(data), csz)]
        if not data_chunks: data_chunks = [b'']
        fid = int(time.time()) & 0xFFFFFFFF
        seq = self.seq; self.seq += 1; fn_b = fname.encode('utf-8')[:200]
        self._send_cmd(CMD_FILE_UP_HDR, seq, struct.pack('!IIIB', fid, fsz, len(data_chunks), len(fn_b)) + fn_b)
        dl = time.time() + 5; acked = False
        while time.time() < dl:
            for _, rc, rd, _ in self._recv_replies():
                if rc == CMD_FILE_UP_HDR:
                    p = self.crypto.decrypt(rd)
                    if p and p[0] == 0x01: acked = True
            if acked: break
            self._wait_fd(0.05)
        if not acked: return "[!] Not acked"
        self._bg_upload = {
            'fid': fid, 'fname': fname, 'fsz': fsz,
            'data_chunks': data_chunks, 'total': len(data_chunks),
            'next_idx': 0, 'fid_b': struct.pack('!I', fid),
            'start': time.time()
        }
        return f"[*] Uploading: {fname} ({fsz:,}B, {len(data_chunks)} chunks)"

    def _service_bg_transfers(self):
        """Non-blocking: send a batch of file chunks per frame loop iteration."""
        if self._bg_download:
            dl = self._bg_download; sent = 0
            while dl['next_idx'] < dl['nc'] and sent < 50:
                if dl['next_idx'] not in dl['chunks']:
                    self._send_cmd(CMD_FILE_DATA, dl['next_idx'], dl['fid_b']); sent += 1
                dl['next_idx'] += 1
            if dl['next_idx'] >= dl['nc'] and len(dl['chunks']) < dl['nc']:
                for ci in range(dl['nc']):
                    if ci not in dl['chunks']:
                        self._send_cmd(CMD_FILE_DATA, ci, dl['fid_b']); sent += 1
                        if sent >= 50: break
                dl['next_idx'] = 0
            if len(dl['chunks']) >= dl['nc']:
                self._finalize_bg_download()
            elif time.time() - dl['last_progress'] > 30:
                pct = len(dl['chunks']) * 100 // max(dl['nc'], 1)
                self._log(f"[!] Download stalled at {pct}% ({len(dl['chunks'])}/{dl['nc']})")
                self._bg_download = None
        if self._bg_upload:
            ul = self._bg_upload; sent = 0
            while ul['next_idx'] < ul['total'] and sent < 50:
                ci = ul['next_idx']
                self._send_cmd(CMD_FILE_UP_DATA, ci, ul['fid_b'] + ul['data_chunks'][ci])
                ul['next_idx'] += 1; sent += 1
            if ul['next_idx'] >= ul['total'] and self._bg_upload:
                if time.time() - ul['start'] > 30:
                    self._log(f"[!] Upload: no server ack after 30s"); self._bg_upload = None

    def _handle_command(self, cmd_str):
        parts = cmd_str.strip().split()
        if not parts: return None
        cmd = parts[0].lower()
        if not cmd.startswith('!'): return "[!] Commands start with ! — type !help"
        cmd = cmd[1:]; args = parts[1:]
        if cmd == 'help':
            return ("Session:  !pause !resume !reconnect !disconnect !quality <1-6>\n"
                    "Capture:  !screenshot !ss !record start/stop !record gif <sec>\n"
                    "Files:    !download <path> !dl  !upload <path> !ul\n"
                    "Display:  !fit !fullscreen !fs !zoom <pct> !stats !cursor (server cursor)\n"
                    "Input:    !vnc (toggle, Right Ctrl to release) !cad (Ctrl+Alt+Del)\n"
                    "Diag:     !ping !info !bandwidth !keyframe")
        elif cmd == 'pause': self.paused = True; return "[*] Paused"
        elif cmd == 'resume': self.paused = False; return "[*] Resumed"
        elif cmd in ('disconnect','quit','exit'):
            self._cmd_queue.put(('disconnect',))
            if self.viewer: self.viewer.running = False
            return "[*] Disconnecting..."
        elif cmd == 'reconnect':
            self._cmd_queue.put(('reconnect',))
            try:
                result = self._cmd_result.get(timeout=35)
                return result if result else "[!] Reconnect failed"
            except queue.Empty: return "[!] Reconnect timeout"
        elif cmd == 'quality':
            if not args: return "[!] !quality <1-6>"
            try:
                q = int(args[0])
                if 1<=q<=6:
                    self.scale=q; self.frame_rgb=None
                    self.kf_force_next=True; self.kf_first_done=False
                    self.kf_streaming=False; self.kf_chunks={}
                    return f"[*] Scale {q}"
                return "[!] 1-6"
            except: return "[!] Invalid"
        elif cmd in ('screenshot','ss'):
            if not self.frame_rgb: return "[!] No frame"
            ts=time.strftime('%Y%m%d_%H%M%S'); path=f'icmpvnc_ss_{ts}.png'
            try: _write_png(bytes(self.frame_rgb),self.frame_w,self.frame_h,path); return f"[*] {path}"
            except Exception as e: return f"[!] {e}"
        elif cmd == 'record':
            if not args: return "[!] !record start|stop|gif <sec>"
            sub=args[0].lower()
            if sub=='start':
                if not self.recorder: self.recorder=FrameRecorder(self.frame_w or 0,self.frame_h or 0)
                if self.recorder.recording: return "[!] Already recording"
                return f"[*] Recording → {self.recorder.start()}/"
            elif sub=='stop':
                if not self.recorder or not self.recorder.recording: return "[!] Not recording"
                return f"[*] {self.recorder.stop()}"
            elif sub=='gif':
                if len(args)<2: return "[!] !record gif <sec>"
                try:
                    s=int(args[1])
                    if s<1 or s>60: return "[!] 1-60s"
                    if not self.recorder: self.recorder=FrameRecorder(self.frame_w or 0,self.frame_h or 0)
                    self.recorder.start_gif(s); return f"[*] Recording {s}s GIF..."
                except: return "[!] Invalid"
            return "[!] start|stop|gif"
        elif cmd in ('download','dl'):
            if not args: return "[!] !download <path>"
            return self._download_file(' '.join(args))
        elif cmd in ('upload','ul'):
            if not args: return "[!] !upload <path>"
            return self._upload_file(' '.join(args))
        elif cmd in ('fit','fitwindow'):
            if self.viewer:
                self.viewer._fit_mode=not self.viewer._fit_mode
                if self.viewer._fit_mode: self.viewer._zoom=100
                return f"[*] Fit: {'ON' if self.viewer._fit_mode else 'OFF'}"
            return "[!] No viewer"
        elif cmd in ('fullscreen','fs'):
            if self.viewer: self.viewer.toggle_fullscreen(); return f"[*] FS: {'ON' if self.viewer._is_fullscreen else 'OFF'}"
            return "[!] No viewer"
        elif cmd == 'zoom':
            if not args: return "[!] !zoom <50-400>"
            try:
                z=int(args[0])
                if 50<=z<=400:
                    if self.viewer:
                        self.viewer._zoom=z; self.viewer._fit_mode=False
                    return f"[*] Zoom: {z}%"
                return "[!] 50-400"
            except: return "[!] Invalid"
        elif cmd == 'stats':
            el=time.time()-self.start_time if self.start_time else 0
            bw=self.bytes_total/max(el,.1)/1e6
            return f"[*] FPS:{self.fps_val:.1f} | Frames:{self.frames_total} | {self.bytes_total/1e6:.1f}MB | {bw:.2f}MB/s | {el:.0f}s"
        elif cmd == 'ping':
            rtt=self._do_ping()
            return f"[*] {rtt:.2f}ms" if rtt else "[!] Timeout"
        elif cmd == 'info':
            el=time.time()-self.start_time if self.start_time else 0
            return (f"[*] Server:{self.server_ip} | {'XDP' if self.mode=='xdp' else 'Raw'}\n"
                    f"[*] {self.type_name} ({self.req_type}→{self.reply_type})\n"
                    f"[*] {self.rate}pps×{self.pkt_size}B | Crypto:ARX(2)+Speck(2)+SHA256\n"
                    f"[*] {self.frame_w}x{self.frame_h} | DH:X25519+PSK | {el:.0f}s")
        elif cmd == 'bandwidth':
            el=time.time()-self.start_time if self.start_time else 0
            avg=self.bytes_total/max(el,.1); bud=self.rate*self.pkt_size
            return f"[*] Budget:{bud/1e6:.1f}MB/s | Actual:{avg/1e6:.2f}MB/s | Total:{self.bytes_total/1e6:.1f}MB"
        elif cmd == 'keyframe':
            self.kf_force_next = True; self.kf_first_done = False
            self.kf_streaming = False; self.kf_chunks = {}
            return "[*] Keyframe requested (will block)"
        elif cmd == 'vnc':
            self.vnc_mode = not self.vnc_mode
            if self.vnc_mode:
                if self.viewer and self.viewer.is_alive():
                    self.viewer.enter_vnc_mode(use_local_cursor=self.hide_server_cursor)
                return "[*] VNC mode ON — Right Ctrl to release"
            else:
                if self.viewer and self.viewer.is_alive():
                    self.viewer.exit_vnc_mode()
                return "[*] VNC mode OFF"
        elif cmd == 'cursor':
            self.hide_server_cursor = not self.hide_server_cursor
            if self.hide_server_cursor:
                return "[*] Server cursor hidden (saves FPS, white arrow backup in VNC)"
            else:
                return "[*] Server cursor visible"
        elif cmd == 'cad':
            for ks in (0xffe3, 0xffe9, 0xffff):
                self._queue_input(CMD_INPUT_KEY, struct.pack('!BI', 1, ks))
            for ks in (0xffff, 0xffe9, 0xffe3):
                self._queue_input(CMD_INPUT_KEY, struct.pack('!BI', 0, ks))
            return "[*] Sent Ctrl+Alt+Del"
        elif cmd == 'sendkey':
            if not args: return "[!] !sendkey <keysym_name>"
            import tkinter as _tk
            try:
                tmp = _tk.Tk(); tmp.withdraw()
                ks = tmp.winfo_id()
                tmp.destroy()
            except: pass
            return "[*] Use !cad for Ctrl+Alt+Del"
        return f"[!] Unknown: !{cmd}"

    def _frame_loop(self):
        fps_count = 0; fps_start = time.time()
        self._log(f"[*] Bandwidth: {self.rate*self.pkt_size/1e6:.1f}MB/s")
        self._log(f"[*] Crypto: BitRev→Sub→ARX(2)→Speck(2)→SHA256→HMAC (keyframes)")
        self._log(f"[*] Crypto: SHA256→HMAC (deltas)")
        self._log(f"[*] Keyframes: adaptive only (first blocks, rest stream)")
        self.kf_original_rate = self.rate

        while True:
            if self._exit_loop: break
            if self.viewer and not self.viewer.is_alive(): break
            if self.paused: time.sleep(0.1); continue

            try:
                while not self._cmd_queue.empty():
                    cmd_item = self._cmd_queue.get_nowait()
                    if cmd_item[0] == 'ping':
                        self._cmd_result.put(self._do_ping_inline())
                    elif cmd_item[0] == 'download':
                        self._cmd_result.put(self._start_bg_download(cmd_item[1]))
                    elif cmd_item[0] == 'upload':
                        self._cmd_result.put(self._start_bg_upload(cmd_item[1]))
                    elif cmd_item[0] == 'disconnect':
                        self._send_disconnect()
                        self._exit_loop = True
                        break
                    elif cmd_item[0] == 'reconnect':
                        self.crypto = CryptoV3(self.crypto.psk)
                        self.frame_rgb = None; self.kf_force_next = True
                        self.kf_first_done = False
                        self.kf_streaming = False; self.kf_chunks = {}
                        if self._handshake():
                            self._cmd_result.put("[*] Reconnected (fresh X25519+PSK)")
                        else:
                            self._cmd_result.put("[!] Reconnect failed")
            except queue.Empty: pass

            while self._input_queue:
                try:
                    icmd, ipayload = self._input_queue.popleft()
                    self._send_cmd(icmd, 0, ipayload)
                except IndexError:
                    break

            self._service_bg_transfers()

            if time.time() < self.kf_backoff_until: time.sleep(0.1); continue

            self._request_kf_batch()

            if self.kf_streaming:
                for rseq, rcmd, rdata, _ in self._recv_replies():
                    self._collect_kf_chunks(rseq, rcmd, rdata)
                time.sleep(0.01)
                continue

            force_key = self.kf_force_next
            self.kf_force_next = False

            hdr = self._request_frame(scale=self.scale, force_key=int(force_key))
            if hdr is None:
                self.kf_force_next = True; self.kf_consecutive_fails += 1
                if self.kf_consecutive_fails >= 3:
                    self.kf_backoff_until = time.time() + 0.5
                    self.rate = max(100, self.rate // 2)
                    self._log(f"[!] Backoff: rate → {self.rate}pps")
                time.sleep(0.5); continue

            fid, mode, w, h, num_chunks, comp_sz, first_row, row_count = hdr

            if w != self.frame_w or h != self.frame_h:
                self.frame_w = w; self.frame_h = h; self.frame_rgb = None
                if self.recorder and self.recorder.recording:
                    self.recorder.width = w; self.recorder.height = h

            if self.viewer is None and not self.headless:
                try: self.viewer = FrameViewer(w, h, cmd_callback=self._handle_command,
                                               input_callback=self._queue_input)
                except: self.headless = True

            if mode == 1:
                if not self.kf_first_done:
                    ok = self._download_keyframe_blocking(fid, w, h, num_chunks, comp_sz)
                    if ok:
                        self.kf_first_done = True
                        self.kf_consecutive_fails = 0
                        self.last_frame_type = 'K'
                        if self.rate < self.kf_original_rate: self.rate = self.kf_original_rate
                        if self.viewer and self.viewer.is_alive():
                            self.viewer.update(bytes(self.frame_rgb))
                            self.viewer.width = w; self.viewer.height = h
                    else:
                        self.kf_force_next = True
                    continue
                else:
                    if not self.kf_streaming:
                        self._start_kf_stream(fid, w, h, num_chunks, comp_sz)
                    continue

            elif mode == 2:
                if self.frame_rgb is None and not self.kf_first_done:
                    self.kf_force_next = True; continue
                chunks = self._download_frame(fid, num_chunks)
                if len(chunks) < num_chunks:
                    self.kf_consecutive_fails += 1
                    if self.kf_consecutive_fails >= 3:
                        self.kf_backoff_until = time.time() + 0.5
                    continue
                compressed = b''.join(chunks[i] for i in range(num_chunks))
                self.bytes_total += len(compressed)
                try:
                    raw = zlib.decompress(compressed)
                    self.frame_rgb = bytearray(raw)
                    self.frame_w = w; self.frame_h = h
                    self.last_frame_type = 'K'
                    self.kf_consecutive_fails = 0
                    if self.rate < self.kf_original_rate: self.rate = self.kf_original_rate
                except:
                    self.kf_force_next = True; continue

            else:
                ref = self.frame_rgb
                if ref is None:
                    if not self.kf_streaming:
                        self.kf_force_next = True
                    time.sleep(0.05); continue

                chunks = self._download_frame(fid, num_chunks)
                if len(chunks) < num_chunks:
                    self.kf_consecutive_fails += 1
                    if not self.kf_streaming: self.kf_force_next = True
                    if self.kf_consecutive_fails >= 3:
                        self.kf_backoff_until = time.time() + 0.5
                        self.rate = max(100, self.rate // 2)
                    continue

                compressed = b''.join(chunks[i] for i in range(num_chunks))
                self.bytes_total += len(compressed)
                try: raw = zlib.decompress(compressed)
                except:
                    if not self.kf_streaming: self.kf_force_next = True
                    continue

                ref = self.frame_rgb
                offset = first_row * w * 3
                expected = row_count * w * 3
                if ref is None or len(raw) != expected:
                    if not self.kf_streaming: self.kf_force_next = True
                    continue
                _xor_apply(ref, raw, offset)
                self.last_frame_type = 'D'

            self.kf_consecutive_fails = 0
            if self.rate < self.kf_original_rate: self.rate = self.kf_original_rate

            if self.viewer and self.viewer.is_alive():
                self.viewer.update(bytes(self.frame_rgb))
                self.viewer.width = w; self.viewer.height = h
            if self.recorder and self.recorder.recording:
                result = self.recorder.add_frame(bytes(self.frame_rgb))
                if result: self._log(f"[*] {result}")
            self.frames_total += 1; fps_count += 1
            now = time.time()
            if now - fps_start >= 1.0:
                self.fps_val = fps_count / (now - fps_start)
                fps_count = 0; fps_start = now; el = now - self.start_time
                rec = " REC" if (self.recorder and self.recorder.recording) else ""
                kfs = f" KF:{len(self.kf_chunks)}/{self.kf_total}" if self.kf_streaming else ""
                vnc = " | VNC:ON" if self.vnc_mode else ""
                xfer = ""
                if self._bg_download:
                    dl = self._bg_download
                    pct = len(dl['chunks']) * 100 // max(dl['nc'], 1)
                    xfer = f" | DL:{pct}%"
                elif self._bg_upload:
                    ul = self._bg_upload
                    pct = ul['next_idx'] * 100 // max(ul['total'], 1)
                    xfer = f" | UL:{pct}%"
                ft = self.last_frame_type
                if ft == 'D' and h > 0 and row_count < h:
                    ft = f"D {row_count*100//h}%"
                stats = (f"FPS:{self.fps_val:.1f} [{ft}] | {w}x{h} | {comp_sz:,}B/{num_chunks}ch | "
                         f"{self.bytes_total/1e6:.1f}MB | {el:.0f}s{rec}{kfs}{vnc}{xfer}")
                if self.viewer: self.viewer.set_stats(stats)
                elif self.headless:
                    _status_client(self.bytes_total, self.bytes_total,
                                   self.fps_val, self.frames_total,
                                   self.bytes_total/1e6,
                                   time.time() - self.start_time,
                                   f" {_C.DIMW}[{ft}]{_C.RST}{rec}{kfs}{vnc}{xfer}")

    def _log(self, msg):
        sys.stdout.write("\r\033[K")
        print(msg)
        if self.viewer: self.viewer.log(msg)

    def run(self):
        _print_banner()
        mode_str = 'XDP (native)' if self.mode == 'xdp' else 'Raw Socket'
        rows = [
            ("Mode",       mode_str),
            ("Interface",  f"{self.iface} ({self.ip4})"),
            ("Server",     self.server_ip),
            ("Transport",  f"{self.type_name} ({self.req_type}→{self.reply_type})"),
            ("Rate",       f"{self.rate} pps · {self.pkt_size}B payload"),
            ("Crypto",     "BitRev→Sub→ARX→Speck→SHA256"),
            ("Key Ex",     "X25519 ECDH + PSK"),
        ]
        if self.headless: rows.append(("Display", "Headless"))
        _tunnel_box("Client", rows)
        print()
        if self.mode == 'xdp':
            if self.manual_mac:
                self.dst_mac = bytes.fromhex(self.manual_mac.replace(':','').replace('-',''))
            else:
                nh = resolve_next_hop(self.server_ip)
                if nh != self.server_ip: _vlog(f"Gateway: {nh}")
                _vlog("Resolving MAC...")
                self.dst_mac = resolve_mac(nh, self.iface)
                if not self.dst_mac: _event("✗", "MAC resolution failed. Use -m", _C.BRED); return
                _vlog(f"MAC: {self.dst_mac.hex(':')}")
            self.transport = XDPTransport(self.iface); self.transport.setup()
        else:
            self.transport = RawTransport(self.iface, self.server_ip); _vlog("Raw socket ready")
        print()
        try:
            if not self._handshake(): _event("✗", "Server not responding.", _C.BRED); return
        except KeyboardInterrupt:
            _event("⏻", "Interrupted.", _C.MAG); return
        _event("✓", "Connected — X25519+PSK session keys derived", _C.GREEN); print()
        self.start_time = time.time()
        try: self._frame_loop()
        except KeyboardInterrupt: pass
        except Exception as e: _event("✗", f"Error: {e}", _C.BRED); import traceback; traceback.print_exc()
        try: self._send_disconnect()
        except: pass
        if self.viewer: self.viewer.close()
        el = time.time() - self.start_time
        mins, secs = divmod(int(el), 60)
        hrs, mins = divmod(mins, 60)
        dur = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s" if mins else f"{secs}s"
        fps = self.frames_total / max(el, 1)
        _summary_box([
            ("Duration",  dur),
            ("Frames",    f"{self.frames_total:,}"),
            ("Data",      f"{self.bytes_total/1e6:.1f} MB"),
            ("Avg FPS",   f"{fps:.1f}"),
        ])

def main():
    global _VERBOSE, _QUIET
    p = argparse.ArgumentParser(description='ICMPVNC Client',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="ICMP: -E Echo -T Timestamp -M Mask -X Experimental\n"
               "Commands: !help !pause !screenshot !download !upload !ping !keyframe\n")
    p.add_argument('server'); p.add_argument('-i','--interface')
    p.add_argument('-m','--mac'); p.add_argument('-s','--size',type=int,default=1000)
    p.add_argument('-r','--rate',type=int,default=5000)
    p.add_argument('-k','--key',default=None); p.add_argument('--headless',action='store_true')
    p.add_argument('-v','--verbose',action='store_true',help='Show detailed init')
    p.add_argument('-q','--quiet',action='store_true',help='Errors only')
    p.add_argument('--no-color',action='store_true',help='Disable ANSI colors')
    mg = p.add_mutually_exclusive_group(required=True)
    mg.add_argument('--xdp',action='store_const',const='xdp',dest='mode')
    mg.add_argument('--raw',action='store_const',const='raw',dest='mode')
    tg = p.add_mutually_exclusive_group()
    for flag,(_, _, name, _, _) in ICMP_TYPES.items():
        tg.add_argument(f'-{flag}',action='store_const',const=flag,dest='type',help=name)
    p.set_defaults(type='E')
    a = p.parse_args()
    _VERBOSE = a.verbose; _QUIET = a.quiet
    _init_colors()
    if a.no_color: _C.off()
    if a.size > MAX_PAYLOAD: a.size = MAX_PAYLOAD
    if a.rate > MAX_RATE: a.rate = MAX_RATE
    if a.rate < 1: a.rate = 1
    iface = a.interface or get_default_iface()
    a.interface = iface
    validate_interface(iface)
    if a.mode == 'xdp' and is_wireless(iface):
        warn_wireless_xdp(iface)
    psk = a.key or getpass.getpass("Pre-shared key: ")
    if not psk: _event("✗", "Key required", _C.BRED); sys.exit(1)
    ICMPVNCClient(a, psk).run()

if __name__ == '__main__':
    main()
