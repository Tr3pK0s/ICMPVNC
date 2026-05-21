# ICMPVNC ⛏

## Description/Overview
This is a 100% ICMP based VNC, featuring screen sharing, full keyboard/mouse input, bidirectional file transfer, wide Linux compatibility using a multi capture engine designed to work for X11, Wayland, headless/ARM/VMs, along with a cross compatible raw or XDP (kernel bypass) tranport system crafted for usable FPS over many ICMP types.

## Features
- A broad toolkit of many ICMP types (e.g., Echo, Timestamp, Traceroute)
- X25519 ECDH + PSK
- Light encryption for deltas using SHA256-CTR "Counter mode"(CSPRNG stream cipher) + HMAC-SHA256
- Full encryption for keyframes using BitReversal + substitution + PrimeARX(2) + Speck128/256(2) + SHA256-CTR + HMAC-SHA256
- Capture fallback: xcb_shm_attach_fd (X11) --> XShmAttachFd --> SysV IPC + XShmAttach --> XGetImage --> PipeWire + GStreamer (Wayland) --> DRM/KMS (ARM/VM)
- Input fallback: uinput --> XTest
- DRM/KMS Direct GPU Framebuffer access, linear for ARM/VM, X-Y tiling for Intel/AMD64 (non Nvidia GPUs)
- A full eBPF/XDP program assembled as raw bytecode from Python enabling linux network stack bypassing (partial kernel bypass)
- Adaptive delta compression with bidirectional dirty row scanning
- Tkinter based live viewing 
- An extensive !command system for controlling VNC
- Background file transfers, uninterrupted streaming/input
- PNG encoder capable of screenshots or recording frames (24bit RGB PNG assembler using zlib compressed IDAT chunks) 
- GIF animator (216 color quantization --> LZW --> GIF89a)

### Native Helper
- At startup, writes a small C file with bgra_to_rgb() (BGRA-->RGB with downscale in one pass) and xor_bytes() (buffer XOR for delta computation)
- Compiles with gcc -shared -O2 -fPIC, dlopens the result via ctypes
- Falls back to pure Python if GCC is absent

## Prerequisites/Requirements
- sudo/root for raw ICMP sockets, AF_XDP, uinput ioctls, DRM ioctls
- Python 3.6+ (standard libraries, no pip installs needed, this is pure python except for Native C Helper)
- Tkinter (Most Python installs already include; check with 'python3 -m tkinter')
- Linux kernel 4.18+ (recommended for full XDP performance)

## Installation
Clone the repository

## Legal Notice
User takes on full responsibility to use in a legal manner

## Getting Started
**Transport modes are cross compatible (XDP) server - (Raw) client  or  (Raw) server - (XDP) client**

### Server quick start
- sudo -E python3 server.py -i eth0 --xdp -k <PSK>          (XDP transport)
- sudo -E python3 server.py -i wlan0 --raw -k <PSK>          (raw transport)

 **XDP is primarily for eth0, only some drivers/adapters support wireless XDP**

### Server flags
- -i, --interface  — Network interface
- --xdp — Use high performance XDP (AF_XDP) transport
- --raw — Use classic raw socket transport
- -k, --key — Pre shared key (prompted if omitted)
- --scale <1-6> — Initial downscale factor (default: 2, auto adjusts)
- --drop — On XDP BPF returns XDP_DROP for all non ICMPVNC traffic (ALL TCP/UDP dropped), on Raw it writes '1' to icmp_echo_ignore_all
- -v, --verbose — Detailed initialization and debug logs
- -q, --quiet — Error only output (suppress banner and status)
- --no-color — Disable ANSI colors in console output
- -h, --help — Run 'python3 server.py --help' for help

### Client quick start
- sudo python3 client.py <serverIP> -i wlan0 --raw -E -k <PSK>                    (Echo type)
- sudo python3 client.py <serverIP> -i eth0 --xdp -X -k <PSK>                     (Experimental type)
- sudo python3 client.py <serverIP> -i eth0 --xdp -E -k <PSK> -r 10000 -s 1400    (Theoretical max (XDP) 14.0Mb/s)
- sudo python3 client.py <serverIP> -i wlan0 --raw -E -k <PSK> -r 7000 -s 1400    (Theoretical max (Raw) 9.8Mb/s)

### Client Flags
- -i, --interface — Network interface (auto detected if omitted)
- --xdp — Use high performance XDP transport
- --raw — Use classic raw socket transport
- -k, --key — Pre shared key (prompted if omitted)
- -s, --size — Payload size (max 1400)bytes
- -r, --rate — Packets per second (max Raw 7000, max XDP 10000)
- -m, --mac — Manual destination MAC override
- --headless — Run without GUI (for scripting)
- -v, --verbose — Enable detailed logs
- -q, --quiet — Errors only output
- --no-color — Disable ANSI colors
- -h, --help — Run 'python3 client.py --help' for help

### Client ICMP type Flags 
The server auto detects all types. The client selects one.
- -E Echo Request/Reply (types 8/0)
- -T Timestamp (13/14)
- -M Address Mask (17/18)
- -R Information (15/16)
- -S Router Solicitation/Advertisement (10/9)
- -X Experimental (253/254).
- -D Domain Name (37/38)
- -O Mobile Registration (35/36)
- -TR Traceroute (30/0)
- -P Photuris (40/40)
- -EE Extended Echo (42/43)

### Client VNC !Commands
- `!help` — provides full list of !Commands
- `!pause/!resume` — freeze/unfreeze live viewer
- `!reconnect` — fresh X25519+PSK handshake, clears frame state
- `!disconnect` (also `!quit`, `!exit`) — clean shutdown
- `!quality <1-6>` — set downscale floor. Forces keyframe at new resolution
- `!screenshot` / `!ss` — save current frame as PNG
- `!record start/stop` — save every frame as individual PNGs to a timestamped directory
- `!record gif <1-60>` — capture N seconds, saves as GIF
- `!download <remote_path>` / `!dl` — file download from server. Progress: `DL:XX%` in status bar
- `!upload <local_path>` / `!ul` — file upload to server. Progress: `DL:XX%` in status bar
- `!fit` — toggle fit to window (default: on)
- `!fullscreen` / `!fs` — toggle fullscreen
- `!zoom <50-400>` — fixed zoom percentage, disables fit mode
- `!stats` — FPS, frames, bytes, bandwidth, uptime
- `!vnc` — toggle VNC mode. Captures all keyboard + mouse in the viewer window. **Right Ctrl releases the grab.** 
- `!cad` — sends Ctrl+Alt+Del
- `!cursor` — toggle server side cursor compositing (default: on)
- `!ping` — latency test
- `!info` — server IP, transport, ICMP type, rate, packet size, crypto, resolution, uptime
- `!bandwidth` — budget (rate×size), actual throughput, total transferred
- `!keyframe` — force full frame download, clears delta state
**!cursor command toggle disables server side cursor compositing. Reduces dirty rows per frame (cursor movement no longer dirties the delta). When hidden + VNC mode active, a white arrow overlay is drawn client-side**

## Bandwidth Limits
- Theoretical max Raw 1400 bytes x 7000 pps = 9.8Mb/s
- Theoretical max XDP 1400 bytes x 10000 pps = 14.0Mb/s
- You could push packet size a little higher but already nearing MTU 1500 limit
- 7000 & 10000 pps were the max I could get with zero packet loss testing arbitrary data on POS hardware, but livecapturing/compression/crypto will all limit anyway.
- Actual throughput will depend heavily on screen content. A static desktop at scale 2 compresses to near zero deltas. Full motion content at scale 1 will saturate budget.
 
##Trouble Shooting
- **"Cannot open X11 display"** — use `sudo -E` (not bare `sudo`)
- **Fast motion/Low FPS** — increase rate (`-r 10000`), packet size (`-s 1400`), or scale up (`!quality 3`+)
- **Corrupted image** — `!keyframe` forces a fresh full-frame download

## License 
MIT License

Copyright (c) 2026 ICMPVNC

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
