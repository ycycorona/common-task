import yt_dlp
import os

proxy = os.environ.get('all_proxy') or os.environ.get('http_proxy') or ''
url = "https://cc3001.dmm.co.jp/pv/CzYiHCsPrqkixkMOvGjTNAZyd5YRhKS1ItcgbA-nScjbYi7HPUlVY5UWzIs-4/playlist.m3u8"
ydl_opts = {
    'outtmpl': 'test_video.mp4',
    'proxy': proxy,
    'http_headers': {'Referer': 'https://www.dmm.co.jp/'}
}
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])
