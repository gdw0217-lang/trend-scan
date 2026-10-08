"""매일 트렌드를 모아 앱 소재 후보를 적는다.

    python scripts/scan.py            → docs/latest.md, docs/latest.json, docs/history.jsonl 갱신

원천
- Google Trends 급상승 검색어 (한국, RSS)
- 나무위키 최근 변경 (새로 만들어진 문서는 새 유행의 가장 빠른 신호)
- 유튜브 인기 급상승 (한국, 헤드리스 브라우저 — 실패하면 건너뜀)

판단은 사람이 한다. 이 스크립트는 '이틀 연속 올라온 것', '처음 보는 것'만 표시해 준다.
"""
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
HIST = DOCS / "history.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (trend-scan)", "Accept-Language": "ko-KR,ko;q=0.9"}

# 앱 소재가 되기 어려운 것: 정치·사건·연예 인물. 제목에 이 말이 있으면 뒤로 보낸다.
NOISE = re.compile(r"의원|대통령|장관|검찰|경찰|사망|사고|화재|체포|구속|선고|판결|논란|폭행|추행|국회|여당|야당|북한|미사일|주가|환율")


def google_trends():
    r = requests.get("https://trends.google.com/trending/rss?geo=KR", headers=UA, timeout=30)
    r.raise_for_status()
    out = []
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        traffic = re.search(r"<ht:approx_traffic>(.*?)</ht:approx_traffic>", item, re.S)
        news = re.findall(r"<ht:news_item_title>(.*?)</ht:news_item_title>", item, re.S)
        if title:
            out.append({"kw": title.group(1).strip(), "traffic": (traffic.group(1).strip() if traffic else ""),
                        "news": [re.sub(r"<!\[CDATA\[|\]\]>", "", n).strip() for n in news[:2]]})
    return out


def namu_recent():
    r = requests.get("https://namu.wiki/sidebar.json", headers=UA, timeout=20)
    r.raise_for_status()
    return [{"doc": x.get("document"), "status": x.get("status")} for x in r.json()]


def youtube_trending():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page(locale="ko-KR")
            pg.goto("https://www.youtube.com/feed/trending?gl=KR&hl=ko", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(6000)
            titles = pg.evaluate(
                "() => [...document.querySelectorAll('a#video-title, ytd-video-renderer #video-title')]"
                ".map(a => a.textContent.trim()).filter(Boolean)")
            b.close()
            return list(dict.fromkeys(titles))[:40]
    except Exception as e:  # 유튜브는 자주 바뀌니 실패해도 전체를 멈추지 않는다
        print("유튜브 급상승 실패:", type(e).__name__, file=sys.stderr)
        return []


def load_history():
    if not HIST.exists():
        return []
    return [json.loads(l) for l in HIST.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    now = dt.datetime.now(KST)
    gt, namu, yt = google_trends(), namu_recent(), youtube_trending()
    history = load_history()
    seen_kw = Counter(k for h in history for k in h.get("google", []))
    seen_docs = {d for h in history for d in h.get("namu", [])}
    seen_yt = {t for h in history for t in h.get("youtube", [])}

    gt_sorted = sorted(gt, key=lambda x: (bool(NOISE.search(x["kw"] + " ".join(x["news"]))), -seen_kw[x["kw"]]))
    new_docs = [d for d in namu if d["doc"] not in seen_docs]
    new_yt = [t for t in yt if t not in seen_yt]

    lines = [f"# 트렌드 스캔 {now:%Y-%m-%d %H:%M}", "",
             "## Google 급상승 검색어 (앱 소재 될 만한 것 먼저, ★=이틀 이상 연속)", ""]
    for x in gt_sorted:
        mark = "★ " if seen_kw[x["kw"]] else ""
        noise = " _(뉴스성)_" if NOISE.search(x["kw"] + " ".join(x["news"])) else ""
        lines.append(f"- {mark}**{x['kw']}** {x['traffic']}{noise}" + (f" — {x['news'][0]}" if x["news"] else ""))
    lines += ["", "## 나무위키 최근 변경 (처음 보는 문서 = 새 유행 후보)", ""]
    for d in namu:
        lines.append(f"- {'🆕 ' if d in new_docs else ''}{d['doc']}")
    if yt:
        lines += ["", "## 유튜브 인기 급상승 (🆕 = 오늘 처음)", ""]
        lines += [f"- {'🆕 ' if t in new_yt else ''}{t}" for t in yt]
    lines += ["", "---", "소재 판단 기준: 10대·초등·2030이 **지금 사거나 하는 것**(장난감·놀이·챌린지·먹거리)인가, "
              "가상으로 체험하거나 계산하거나 기록할 수 있는가, 토스 금지 분야(금융·사행·의료·정치)가 아닌가."]

    DOCS.mkdir(exist_ok=True)
    (DOCS / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DOCS / "latest.json").write_text(json.dumps({"at": now.isoformat(timespec="minutes"), "google": gt,
                                                  "namu": namu, "youtube": yt}, ensure_ascii=False), encoding="utf-8")
    with HIST.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="minutes"), "google": [x["kw"] for x in gt],
                            "namu": [d["doc"] for d in namu], "youtube": yt}, ensure_ascii=False) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
