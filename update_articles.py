#!/usr/bin/env python3
"""
update_articles.py
自动抓取 TechCrunch (Venture 创投) 和 Seeking Alpha (市场与商业投资) 最新一手文章，
过滤广告与杂讯，提炼核心商业/技术难词，并自动从今日新闻中提炼最新的口语跟读语料（带中文对照），
同步注入 index.html。
"""

import subprocess
import re
import json
import xml.etree.ElementTree as ET
import os
import urllib.parse
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

def translate_sentence(text):
    """自动将英文短句翻译为中文对照"""
    clean = re.sub(r'<[^>]+>', '', text).strip()
    if not clean or len(clean) < 10:
        return ""
    try:
        url = 'https://api.mymemory.translated.net/get?q=' + urllib.parse.quote(clean[:450]) + '&langpair=en|zh-CN'
        out = subprocess.run(['curl', '-s', url], capture_output=True, text=True, timeout=8).stdout
        data = json.loads(out)
        res = data.get('responseData', {}).get('translatedText', '')
        # 如果返回的是原英文或报错，降级处理
        if res and not res.startswith('MYMEMORY WARNING'):
            return res
    except Exception:
        pass
    return ""

def highlight_keywords(text):
    """自动给文章中的核心商业与AI词汇包裹 <span class='w hard'>"""
    for w in AUTO_HIGHLIGHT_WORDS:
        pattern = re.compile(rf'\b({re.escape(w)})\b', re.IGNORECASE)
        text = pattern.sub(r'<span class="w hard" data-word="\1">\1</span>', text)
    return text

def extract_spoken_chunks(paragraphs, max_chunks=4):
    """从文章段落中提炼适合跟读练习的纯净口播单句"""
    chunks = []
    for p in paragraphs:
        # 去除 HTML 标签
        raw = re.sub(r'<[^>]+>', '', p).strip()
        # 按句号、感叹号、问号切分句子
        sentences = re.split(r'(?<=[.!?])\s+', raw)
        for s in sentences:
            s_clean = s.strip()
            # 挑选 35~140 字符的自然句
            if 35 <= len(s_clean) <= 140:
                if not any(k in s_clean.lower() for k in ['click here', 'subscribe', 'view bio', 'terms', 'privacy']):
                    chunks.append(s_clean)
            if len(chunks) >= max_chunks:
                break
        if len(chunks) >= max_chunks:
            break
    return chunks

def get_techcrunch_articles():
    print(">>> 正在从 TechCrunch Venture 抓取最新创投报道...")
    articles = []
    xml_data = curl_fetch('https://techcrunch.com/category/venture/feed/')
    if not xml_data:
        return articles

    NOISE_KEYWORDS = ['exhibit table', 'side event', 'disrupt 2026', 'tickets', 'deadline', 'apply for your', 'mark wahlberg']

    try:
        root = ET.fromstring(xml_data)
        items = root.findall('.//item')
        for item in items:
            title = item.find('title').text.strip()
            link = item.find('link').text.strip()
            creator = item.find('{http://purl.org/dc/elements/1.1/}creator')
            author = creator.text.strip() if creator is not None else 'TechCrunch'

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

def generate_daily_shadowing(articles):
    """根据今日最新抓取的文章，动态生成今日专属口语跟读语料"""
    print("\n>>> 正在根据今日一手文章生成【口语跟读】语料与中文对照...")
    shadow_list = []

    # 1. 创投头条跟读
    if len(articles) > 0:
        a = articles[0]
        chunks = extract_spoken_chunks(a['paragraphs'], max_chunks=4)
        if chunks:
            print(f"  翻译主题 1 ({a['title'][:25]})...")
            tr_parts = [translate_sentence(c) for c in chunks]
            cn_full = " ".join([t for t in tr_parts if t]) or "今日创投一手报道口播练习，关注头部初创公司与顶级资本动向。"
            shadow_list.append({
                "badge": "🔥 创投口播",
                "title": a['title'][:22] + "...",
                "chunks": chunks,
                "cn": cn_full
            })

    # 2. AI / 技术前沿口播
    ai_candidates = [a for a in articles if 'ai' in a['title'].lower() or 'model' in a['title'].lower() or 'tech' in a['title'].lower()]
    target_ai = ai_candidates[0] if ai_candidates else (articles[1] if len(articles) > 1 else None)
    if target_ai:
        chunks = extract_spoken_chunks(target_ai['paragraphs'], max_chunks=4)
        if chunks:
            print(f"  翻译主题 2 ({target_ai['title'][:25]})...")
            tr_parts = [translate_sentence(c) for c in chunks]
            cn_full = " ".join([t for t in tr_parts if t]) or "今日人工智能与技术创新焦点，掌握前沿科技表达。"
            shadow_list.append({
                "badge": "🤖 AI口播",
                "title": target_ai['title'][:22] + "...",
                "chunks": chunks,
                "cn": cn_full
            })

    # 3. 市场分析 / 商业动态口播
    sa_candidates = [a for a in articles if 'Seeking Alpha' in a['meta'] or 'SA' in a['tag']]
    target_sa = sa_candidates[0] if sa_candidates else (articles[-1] if len(articles) > 2 else None)
    if target_sa:
        chunks = extract_spoken_chunks(target_sa['paragraphs'], max_chunks=4)
        if chunks:
            print(f"  翻译主题 3 ({target_sa['title'][:25]})...")
            tr_parts = [translate_sentence(c) for c in chunks]
            cn_full = " ".join([t for t in tr_parts if t]) or "今日华尔街与二级市场分析，提升商业与财务词汇语感。"
            shadow_list.append({
                "badge": "📊 市场口播",
                "title": target_sa['title'][:22] + "...",
                "chunks": chunks,
                "cn": cn_full
            })

    # 4. 保留经典投资人沟通与路演（作为常驻基准材料）
    shadow_list.append({
        "badge": "🎤 商务表达",
        "title": "商务路演与投资人沟通",
        "chunks": [
            "I'd like to walk you through our core thesis and why we believe this represents an exceptional opportunity.",
            "Our platform addresses the single largest operational bottleneck currently facing enterprise clients.",
            "We've structured the round with top-tier venture participation to accelerate our market expansion.",
            "We're comfortable operating with ambiguity and iterating rapidly based on customer telemetry.",
            "We look forward to partnering with your investment team as we scale out this deployment."
        ],
        "cn": "我想向各位介绍我们核心的投资逻辑，以及为什么这代表着一个不可多得的商业机遇。我们的平台直接解决了目前企业客户面临的最大业务瓶颈。我们引入了一流创投资本参与本轮融资，以加速区域市场的拓展步伐。我们的团队非常善于在不确定性中迅速推进并根据用户真实数据迭代。非常期待能与各位投资团队紧密携手，推进本次商业部署。"
    })

    return shadow_list

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

    # 更新 HTML 中的展示时间戳（阅读标签和跟读标签）
    content = re.sub(
        r'id="last-update-time">.*?<',
        f'id="last-update-time">{bj_time}<',
        content
    )
    content = re.sub(
        r'id="shadow-update-time">.*?<',
        f'id="shadow-update-time">{bj_time}<',
        content
    )

    # 1. 寻找 DEFAULT_ARTICLES 数组并更新
    match_articles = re.search(r'const DEFAULT_ARTICLES\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if not match_articles:
        print("[Error] 未能在 index.html 中定位到 DEFAULT_ARTICLES")
        return False

    try:
        existing_articles = json.loads(match_articles.group(1))
    except Exception:
        existing_articles = []

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
    new_articles_json = json.dumps(updated_deck, ensure_ascii=False, indent=2)
    content = content[:match_articles.start()] + f"const DEFAULT_ARTICLES = {new_articles_json};" + content[match_articles.end():]

    # 2. 生成今日专属口语跟读语料并注入 SHADOW_TEXTS
    new_shadow_texts = generate_daily_shadowing(new_articles)
    match_shadow = re.search(r'const SHADOW_TEXTS\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if match_shadow:
        new_shadow_json = json.dumps(new_shadow_texts, ensure_ascii=False, indent=2)
        content = content[:match_shadow.start()] + f"const SHADOW_TEXTS = {new_shadow_json};" + content[match_shadow.end():]
        print(f"✅ 成功将 {len(new_shadow_texts)} 组今日专属口语跟读语料注入 index.html！")

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"\n🎉 完美同步！精读文章与口语跟读均已更新至最新时间 [{bj_time}]！")
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
