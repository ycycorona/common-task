import requests

url = "https://cc3001.dmm.co.jp/litevideo/freepv/m/mis/mism00433/mism00433_dmb_w.mp4"
headers = {
    'User-Agent': "Mozilla/5.0",
    'Referer': "https://www.dmm.co.jp/"
}

try:
    resp = requests.head(url, headers=headers, allow_redirects=True)
    print("DMB:", resp.status_code, resp.headers.get('content-length'))
except Exception as e:
    print(e)
    
url2 = "https://cc3001.dmm.co.jp/litevideo/freepv/m/mis/mism00433/mism00433_sm_w.mp4"
try:
    resp2 = requests.head(url2, headers=headers, allow_redirects=True)
    print("SM:", resp2.status_code, resp2.headers.get('content-length'))
except Exception as e:
    print(e)

url3 = "https://cc3001.dmm.co.jp/litevideo/freepv/m/mis/mism00433/mism00433_mhb_w.mp4"
try:
    resp3 = requests.head(url3, headers=headers, allow_redirects=True)
    print("MHB:", resp3.status_code, resp3.headers.get('content-length'))
except Exception as e:
    print(e)
