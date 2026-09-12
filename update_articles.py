#!/usr/bin/env python3
"""
update_articles.py
自动抓取 TechCrunch (Venture 创投) 和 Seeking Alpha (市场与商业投资) 最新一手文章，
过滤广告与展位杂讯，提炼核心商业/技术难词，并自动将更新时间戳注入 index.html。
"""

import subprocess
import re
import json
import xml.etree.ElementTree as ET
import os
from datetime import datetime, timezone, timedelta

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

# 高频核心术语自动标注库
AUTO_HIGHLIGHT_WORDS = [
    'distill', 'distillation', 'open-weight', 'frontier', 'valuation', 'unicorn',
    'accelerator', 'run rate', 'operating leverage', 'forward P/E', 'EPS',
    'antitrust', 'CapEx', 'tailwinds', 'headwinds', 'balance sheet', 'moat',
    'arbitrage', 'sovereign wealth fund', 'hyperscaler', 'acquisition', 'term sheet',
    'due diligence', 'liquidity', 'monetary policy', 'inflection'
]

def curl_fetch(url):
    """通过 curl 抓取网页内容，避免被拦截"""
    try:
        cmd = [
            'curl', '-s', '-L',
            '-A', UA,
            '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            '-H', 'Accept-Language: en-US,en;q=0.9',
            '--compressed',
            url
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return res.stdout
    except Exception as e:
        print(f"  [Error] curl 请求失败: {e}")
        return ""

def highlight_keywords(text):
    """自动给文章中的核心商业与AI词汇包裹 <span class='w hard'>"""
    for w in AUTO_HIGHLIGHT_WORDS:
        # 使用正则单词边界匹配
        pattern = re.compile(rf'\b({re.escape(w)})\b', re.IGNORECASE)
        text = pattern.sub(r'<span class="w hard" data-word="\1">\1</span>', text)
    return text

def get_techcrunch_articles():
    print(">>> 正在从 TechCrunch Venture 抓取最新创投报道...")
    articles = []
    xml_data = curl_fetch('https://techcrunch.com/category/venture/feed/')
    if not xml_data:
        return articles

    # 广告、展位、票务等过滤黑名单关键词
    NOISE_KEYWORDS = ['exhibit table', 'side event', 'disrupt 2026', 'tickets', 'deadline', 'apply for your', 'mark wahlberg']

    try:
        root = ET.fromstring(xml_data)
        items = root.findall('.//item')
        for item in items:
            title = item.find('title').text.strip()
            link = item.find('link').text.strip()
            creator = item.find('{http://purl.org/dc/elements/1.1/}creator')
            author = creator.text.strip() if creator is not None else 'TechCrunch'

            # 过滤展会宣传与广告
            if any(k in title.lower() for k in NOISE_KEYWORDS):
                continue

            print(f"  抓取 TC 文章: {title[:45]}...")
            html = curl_fetch(link)
            paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
            clean_paras = []
            for p in paras:
                c = re.sub(r'<[^>]+>', '', p).strip()
                c = re.sub(r'\s+', ' ', c)
                if len(c) > 60 and not any(k in c for k in ['Disrupt', 'Logo', 'Newsletter', 'Subscribe', 'Privacy Notice', 'Terms', 'exhibit table']):
                    clean_paras.append(highlight_keywords(c))

            if clean_paras:
                articles.append({
                    'id': 'tc-' + re.sub(r'\W+', '-', title.lower())[:30],
                    'title': title,
                    'meta': f"{author} · TechCrunch Venture",
                    'tag': 'TC 创投',
                    'tagClass': 'badge-tc',
                    'src': link,
                    'paragraphs': clean_paras[:6]
                })

            if len(articles) >= 3:
                break
    except Exception as e:
        print(f"  [Warning] TechCrunch 解析错误: {e}")
    return articles

def get_seeking_alpha_articles():
    print(">>> 正在从 Seeking Alpha 抓取最新市场与商业分析...")
    articles = []
    xml_data = curl_fetch('https://seekingalpha.com/market_currents.xml')
    if not xml_data:
        return articles

    try:
        root = ET.fromstring(xml_data)
        items = root.findall('.//item')
        for item in items:
            title = item.find('title').text.strip()
            link = item.find('link').text.strip()
            print(f"  抓取 SA 快讯: {title[:45]}...")
            html = curl_fetch(link)
            clean_paras = []

            m = re.search(r'window\.SSR_DATA\s*=\s*(\{.*?\});', html)
            if m:
                try:
                    data = json.loads(m.group(1))
                    for key in ['article', 'news']:
                        if key in data and 'response' in data[key]:
                            content = data[key]['response']['data']['attributes'].get('content', '')
                            p_matches = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
                            for pm in p_matches:
                                clean_p = re.sub(r'<[^>]+>', '', pm).strip()
                                clean_p = re.sub(r'\s+', ' ', clean_p)
                                if len(clean_p) > 25 and not any(k in clean_p for k in ['Getty', 'Editorial', 'Photo by']):
                                    clean_paras.append(highlight_keywords(clean_p))
                except Exception:
                    pass

            if not clean_paras:
                paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
                for p in paras:
                    c = re.sub(r'<[^>]+>', '', p).strip()
                    c = re.sub(r'\s+', ' ', c)
                    if len(c) > 40 and not any(k in c for k in ['Cookie', 'Explore', 'Premium', 'feedback forum', 'Getty']):
                        clean_paras.append(highlight_keywords(c))

            if clean_paras:
                articles.append({
                    'id': 'sa-' + re.sub(r'\W+', '-', title.lower())[:30],
                    'title': title,
                    'meta': 'Seeking Alpha Market News',
                    'tag': 'SA 市场',
                    'tagClass': 'badge-sa',
                    'src': link,
                    'paragraphs': clean_paras[:4]
                })

            if len(articles) >= 2:
                break
    except Exception as e:
        print(f"  [Warning] Seeking Alpha 解析错误: {e}")
    return articles

def update_index_html(new_articles):
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
    if not os.path.exists(html_path):
        print(f"[Error] 找不到文件: {html_path}")
        return False

    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 计算北京时间时间戳
    bj_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    print(f"  当前北京时间时间戳: {bj_time}")

    # 更新 HTML 中的展示时间戳
    content = re.sub(
        r'id="last-update-time">.*?<',
        f'id="last-update-time">{bj_time}<',
        content
    )

    # 寻找 DEFAULT_ARTICLES 数组
    match = re.search(r'const DEFAULT_ARTICLES\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if not match:
        print("[Error] 未能在 index.html 中定位到 DEFAULT_ARTICLES")
        return False

    try:
        existing_articles = json.loads(match.group(1))
    except Exception:
        existing_articles = []

    # 保留经典文章（Paul Graham）
    classics = [a for a in existing_articles if 'paulgraham' in a.get('src', '') or 'superlinear' in a.get('id', '')]
    if not classics:
        classics = [{
            "id": "pg-superlinear",
            "title": "Paul Graham: Superlinear Returns in Tech & Startups",
            "meta": "Paul Graham · October 2023 · paulgraham.com",
            "tag": "经典创投",
            "tagClass": "badge-invest",
            "src": "https://paulgraham.com/superlinear.html",
            "paragraphs": [
                "One of the most important things I didn't realize about the world when I was young is the degree to which performance returns are fundamentally superlinear.",
                "Teachers and coaches implicitly taught us that rewards were strictly linear: 'You get out what you put in.' But in business and technology, if your product is only half as compelling as your competitor's, you do not capture half as many users. You capture zero users and shut down.",
                "Superlinear returns reduce to two primary mechanisms: <span class=\"w hard\" data-word=\"exponential\">exponential</span> compounding and critical thresholds. Whenever your current performance determines your subsequent resources, growth compounds exponentially.",
                "In the age of AI and sovereign software, individual leverage is expanding dramatically. Ambitious founders who focus on compounding knowledge and crossing critical performance thresholds will surf the largest wave of value creation in modern history."
            ]
        }]

    updated_deck = new_articles + classics
    new_json_str = json.dumps(updated_deck, ensure_ascii=False, indent=2)
    
    new_content = content[:match.start()] + f"const DEFAULT_ARTICLES = {new_json_str};" + content[match.end():]
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"\n✅ 成功将 {len(new_articles)} 篇最新一手报道及更新时间 [{bj_time}] 同步写入 index.html！")
    return True

if __name__ == '__main__':
    tc = get_techcrunch_articles()
    sa = get_seeking_alpha_articles()
    total = len(tc) + len(sa)
    print(f"\n[OK] 本次抓取统计: {len(tc)} 篇 TechCrunch 创投报道, {len(sa)} 篇 Seeking Alpha 市场文章。")
    if total > 0:
        update_index_html(tc + sa)
    else:
        print("未能抓取到新文章，保留原有内容。")
