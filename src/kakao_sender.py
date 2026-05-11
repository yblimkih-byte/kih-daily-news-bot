"""Kakao memo (talk-to-self) message sender."""
import json
import requests
import time


KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
MAX_TEXT_LENGTH = 195  # 카카오 text 템플릿 최대 200자 (안전 마진)


def _split_message(text: str, max_len: int = MAX_TEXT_LENGTH) -> list[str]:
    """Split a long message into chunks <= max_len, breaking on newlines."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current.rstrip())
            # Line itself too long? Force-split
            while len(line) > max_len:
                chunks.append(line[:max_len])
                line = line[max_len:]
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def _send_one(access_token: str, text: str, link_url: str = "https://www.koreainvestment.com") -> dict:
    """Send a single text message via Kakao memo API."""
    template = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": link_url,
            "mobile_web_url": link_url,
        },
    }
    response = requests.post(
        KAKAO_MEMO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def send_long_message(access_token: str, text: str, header: str = "") -> int:
    """Send a possibly-long message as a series of chunks.

    Returns: number of chunks sent.
    """
    chunks = _split_message(text)
    n = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        if n > 1:
            prefix = f"({i}/{n}) "
            body = prefix + chunk
            # Re-trim if prefix push over limit
            if len(body) > 200:
                body = body[:200]
        else:
            body = chunk
        try:
            _send_one(access_token, body)
        except requests.HTTPError as e:
            print(f"[ERROR] Kakao send failed (chunk {i}/{n}): {e}", flush=True)
            if e.response is not None:
                print(f"[ERROR] Response: {e.response.text}", flush=True)
            raise
        time.sleep(0.3)  # Avoid Kakao rate limits
    return n
