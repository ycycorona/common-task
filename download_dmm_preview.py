import argparse
import sys
import os
import yt_dlp
from jvav.utils import DmmUtil

def download_video(url, filename, proxy, dmm, threads):
    ydl_opts = {
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

def download_dmm_preview(av_id, threads=8):
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
    args = parser.parse_args()
    
    download_dmm_preview(args.av_id, args.threads)
