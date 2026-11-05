# Usage (DMM blocks non-JP IPs; `--via` opens and closes its own SOCKS5 tunnel):
#   .venv/bin/python download_dmm_preview.py --via vps_jp SSIS-001
# With a tunnel you already run, or any proxy in all_proxy/https_proxy/http_proxy:
#   ssh -N -C -D 1080 vps_jp
#   all_proxy=socks5h://127.0.0.1:1080 .venv/bin/python download_dmm_preview.py SSIS-001
# socks5h (not socks5) so DNS resolves on the remote side too.
import argparse
import socket
import subprocess
import sys
import os
import time
from typing import Any

import yt_dlp
import yt_dlp.utils  # yt_dlp.utils.DownloadError 靠子模块导入才可见（类型检查需要）
from jvav.utils import DmmUtil


def find_free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_tunnel(host, port=None, timeout=10.0):
    """Open a local SOCKS5 port through `ssh -D`; return (process, port)."""
    port = port or find_free_port()
    proc = subprocess.Popen(["ssh", "-N", "-C", "-D", f"127.0.0.1:{port}", host])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"ssh tunnel to {host} exited with code {proc.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc, port
        except OSError:
            time.sleep(0.1)
    proc.kill()
    proc.wait()
    raise RuntimeError(f"ssh tunnel to {host} did not open port {port} within {timeout}s")

def download_video(url, filename, proxy, dmm, threads):
    # yt-dlp 的 params 是 TypedDict(_Params)，这里混着字符串/布尔/整数/嵌套 dict，只能按 Any 传
    ydl_opts: Any = {
        'outtmpl': filename,
        'http_headers': {
            'User-Agent': dmm.ua(),
            'Referer': dmm.BASE_URL
        },
        'proxy': proxy if proxy else None,
        'quiet': False,
        'no_warnings': True,
        'concurrent_fragment_downloads': threads
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            error_code = ydl.download([url])
            if error_code != 0:
                print("\n[-] Error downloading video with yt-dlp.")
                if os.path.exists(filename):
                    os.remove(filename)
                return False
        print(f"\n[+] Successfully downloaded {filename}")
        return True
    except yt_dlp.utils.DownloadError as e:
        print(f"\n[-] Download Error: {e}")
        if os.path.exists(filename):
            os.remove(filename)
        return False
    except Exception as e:
        print(f"\n[-] Error downloading video: {e}")
        if os.path.exists(filename):
            os.remove(filename)
        return False

def download_dmm_preview(av_id, threads=8, proxy=''):
    if not proxy:
        proxy = os.environ.get('all_proxy') or os.environ.get('https_proxy') or os.environ.get('http_proxy') or ''
    
    print(f"[*] Fetching DMM preview URL for: {av_id}")
    dmm = DmmUtil(proxy_addr=proxy)
    
    status, pv_url = dmm.get_pv_by_id(av_id)
    
    if status != 200 or not pv_url:
        print(f"[-] Failed to get preview. Status code: {status}")
        return False
        
    print(f"[+] Found original preview URL: {pv_url}")
    
    # HD preview
    nice_pv_url = dmm.get_nice_pv_by_src(pv_url)
    
    filename = f"{av_id.upper()}_preview.mp4"
    if os.path.exists(filename):
        # We also need to be careful not to skip if the file is an empty placeholder
        if os.path.getsize(filename) > 1024 * 10:  # > 10KB
            print(f"[*] File {filename} already exists. Skipping download.")
            return True
        else:
            print(f"[*] Found incomplete/tiny file {filename}. Removing it and redownloading...")
            os.remove(filename)

    print(f"[*] Attempting to download high-quality preview: {nice_pv_url}")
    success = download_video(nice_pv_url, filename, proxy, dmm, threads)
    
    if not success and nice_pv_url != pv_url:
        print(f"[*] High-quality preview not found. Falling back to original preview: {pv_url}")
        success = download_video(pv_url, filename, proxy, dmm, threads)
        
    return success

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download DMM preview video using jvav")
    parser.add_argument("av_id", type=str, help="The AV ID (e.g. SSIS-123)")
    parser.add_argument("-t", "--threads", type=int, default=8, help="Number of concurrent fragment downloads (default: 8)")
    parser.add_argument("--via", metavar="SSH_HOST", help="Open a SOCKS5 tunnel through this SSH host for this run, then close it")
    args = parser.parse_args()

    tunnel = None
    proxy = ''
    try:
        if args.via:
            print(f"[*] Opening SOCKS5 tunnel via {args.via}")
            tunnel, port = start_tunnel(args.via)
            proxy = f"socks5h://127.0.0.1:{port}"
            print(f"[+] Tunnel ready on 127.0.0.1:{port}")
        success = download_dmm_preview(args.av_id, args.threads, proxy)
    finally:
        if tunnel:
            tunnel.kill()
            tunnel.wait()
            print("[*] Tunnel closed")

    sys.exit(0 if success else 1)
