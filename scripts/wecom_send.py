#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path("/root/polymarket_bot")
CACHE_PATH = PROJECT_ROOT / ".runtime" / ".wecom_token_cache.json"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env: {name}")
    return value


def _read_cache() -> dict | None:
    try:
        if not CACHE_PATH.exists():
            return None
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not payload.get("access_token") or not payload.get("expire_at"):
            return None
        return payload
    except Exception:
        return None


def _write_cache(access_token: str, expires_in: int) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    payload = {
        "access_token": access_token,
        "expire_at": now + int(expires_in or 7200),
        "saved_at": now,
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _http_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def get_access_token(corp_id: str, corp_secret: str) -> str:
    cached = _read_cache()
    now = int(time.time())
    if cached and now < int(cached["expire_at"]) - 60:
        return str(cached["access_token"])

    query = urllib.parse.urlencode({"corpid": corp_id, "corpsecret": corp_secret})
    data = _http_json(f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?{query}")
    if int(data.get("errcode", -1)) != 0:
        raise RuntimeError(f"WeCom gettoken error: {json.dumps(data, ensure_ascii=False)}")
    _write_cache(str(data["access_token"]), int(data.get("expires_in", 7200)))
    return str(data["access_token"])


def send_text(*, token: str, agent_id: str, content: str, to_user: str = "", to_party: str = "", to_tag: str = "") -> dict:
    payload = {
        "touser": to_user,
        "toparty": to_party,
        "totag": to_tag,
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": content},
        "safe": 0,
    }
    query = urllib.parse.urlencode({"access_token": token})
    data = _http_json(f"https://qyapi.weixin.qq.com/cgi-bin/message/send?{query}", method="POST", payload=payload)
    if int(data.get("errcode", -1)) != 0:
        raise RuntimeError(f"WeCom send error: {json.dumps(data, ensure_ascii=False)}")
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WeCom text sender")
    parser.add_argument("--content", required=True)
    parser.add_argument("--to_user", default=os.getenv("WECOM_TO_USER", ""))
    parser.add_argument("--to_party", default=os.getenv("WECOM_TO_PARTY", ""))
    parser.add_argument("--to_tag", default=os.getenv("WECOM_TO_TAG", ""))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.to_user and not args.to_party and not args.to_tag:
        print("No recipients configured", file=sys.stderr)
        return 2

    token = get_access_token(_required_env("WECOM_CORP_ID"), _required_env("WECOM_CORP_SECRET"))
    result = send_text(
        token=token,
        agent_id=_required_env("WECOM_AGENT_ID"),
        content=args.content,
        to_user=args.to_user,
        to_party=args.to_party,
        to_tag=args.to_tag,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
