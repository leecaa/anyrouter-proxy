"""
抓取 httpx (Anthropic SDK 底层) 和 curl_cffi 的 TLS 指纹
通过 tls.peet.ws 在线服务对比
"""
import json
import httpx
from curl_cffi import requests as cf_requests


def get_httpx_fingerprint():
    """获取 httpx (Anthropic SDK) 的 TLS 指纹"""
    print("=== httpx (Anthropic SDK 底层) TLS 指纹 ===")
    with httpx.Client(http2=True) as client:
        resp = client.get("https://tls.peet.ws/api/all")
        data = resp.json()
        print(f"  JA3 Hash:      {data.get('tls', {}).get('ja3_hash', 'N/A')}")
        print(f"  JA3:           {data.get('tls', {}).get('ja3', 'N/A')[:80]}...")
        print(f"  JA4:           {data.get('tls', {}).get('ja4', 'N/A')}")
        print(f"  Akamai H2 FP:  {data.get('http2', {}).get('akamai_fingerprint_hash', 'N/A')}")
        print(f"  H2 Akamai FP:  {data.get('http2', {}).get('akamai_fingerprint', 'N/A')}")
        print(f"  HTTP Version:  {data.get('http_version', 'N/A')}")
        print(f"  User-Agent:    {data.get('user_agent', 'N/A')}")
        return data


def get_curl_cffi_fingerprint(impersonate=None):
    """获取 curl_cffi 的 TLS 指纹"""
    label = impersonate or "default"
    print(f"\n=== curl_cffi (impersonate={label}) TLS 指纹 ===")
    resp = cf_requests.get(
        "https://tls.peet.ws/api/all",
        impersonate=impersonate,
    )
    data = resp.json()
    print(f"  JA3 Hash:      {data.get('tls', {}).get('ja3_hash', 'N/A')}")
    print(f"  JA3:           {data.get('tls', {}).get('ja3', 'N/A')[:80]}...")
    print(f"  JA4:           {data.get('tls', {}).get('ja4', 'N/A')}")
    print(f"  Akamai H2 FP:  {data.get('http2', {}).get('akamai_fingerprint_hash', 'N/A')}")
    print(f"  H2 Akamai FP:  {data.get('http2', {}).get('akamai_fingerprint', 'N/A')}")
    print(f"  HTTP Version:  {data.get('http_version', 'N/A')}")
    print(f"  User-Agent:    {data.get('user_agent', 'N/A')}")
    return data


if __name__ == "__main__":
    httpx_data = get_httpx_fingerprint()
    chrome_data = get_curl_cffi_fingerprint("chrome")

    # 对比关键指纹字段
    print("\n\n=== 指纹对比 ===")
    httpx_ja3 = httpx_data.get("tls", {}).get("ja3_hash", "")
    chrome_ja3 = chrome_data.get("tls", {}).get("ja3_hash", "")
    print(f"  httpx  JA3 Hash: {httpx_ja3}")
    print(f"  chrome JA3 Hash: {chrome_ja3}")
    print(f"  JA3 匹配: {'YES' if httpx_ja3 == chrome_ja3 else 'NO'}")

    httpx_ja4 = httpx_data.get("tls", {}).get("ja4", "")
    chrome_ja4 = chrome_data.get("tls", {}).get("ja4", "")
    print(f"  httpx  JA4: {httpx_ja4}")
    print(f"  chrome JA4: {chrome_ja4}")

    # 保存完整数据供后续分析
    with open("/Users/zhangweiteng/claude-tls-analysis/fingerprints.json", "w") as f:
        json.dump({"httpx": httpx_data, "curl_cffi_chrome": chrome_data}, f, indent=2)
    print("\n完整指纹数据已保存到 fingerprints.json")
