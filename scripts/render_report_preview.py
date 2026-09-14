#!/usr/bin/env python3
"""Render a generated report as portable, escaped HTML and Markdown for review."""
import html
import json
import sys
from pathlib import Path


def render(source, directory):
    report=json.loads(source.read_text())
    directory.mkdir(parents=True,exist_ok=True)
    title=report['date']+' · DeepSeek V4.1 Flash 试刊'
    markdown=['# '+title,'',report['oneLiner'],'']
    sections=[]
    for section in report['sections']:
        markdown.extend(['## '+section['title'],''])
        items=[]
        for item in section['items']:
            markdown.extend(['### '+item['headline'],'',item['summary'],''])
            links=[]
            for source_item in item['sources']:
                markdown.append(f"- [{source_item['name']}]({source_item['url']})")
                links.append('<a href="'+html.escape(source_item['url'],quote=True)+'" target="_blank" rel="noopener noreferrer">'+html.escape(source_item['name'])+'</a>')
            markdown.append('')
            items.append('<article><h3>'+html.escape(item['headline'])+'</h3><p>'+html.escape(item['summary'])+'</p><div class="sources">'+' · '.join(links)+'</div></article>')
        sections.append('<section><h2>'+html.escape(section['title'])+'</h2>'+''.join(items)+'</section>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+html.escape(title)+'''</title><style>body{background:#f5f3ef;color:#18211e;font:17px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0}main{max-width:850px;margin:45px auto;padding:0 28px}h1{font-size:34px;line-height:1.3}h2{margin:44px 0 16px;font-size:24px}h3{font-size:19px;line-height:1.6;margin:0}article{background:white;border:1px solid #e5e7e1;padding:24px 28px;margin:16px 0;border-radius:12px}p{margin:12px 0}.sources{font-size:13px;color:#506e63}a{color:#326653}.intro{border-left:4px solid #326653;padding:15px 22px;background:#e8eee7}.eyebrow{font-size:12px;letter-spacing:2px;color:#658074}.note{font-size:14px;color:#68716a}</style><main><div class="eyebrow">EVAN / AI DAILY / REVIEW COPY</div><h1>'''+html.escape(title)+'</h1><p class="note">试跑产出 · '+html.escape(report['generatedAt'])+'</p><p class="intro">'+html.escape(report['oneLiner'])+'</p>'+''.join(sections)+'</main></html>'
    (directory/'report.html').write_text(page)
    (directory/'report.md').write_text('\n'.join(markdown))

if __name__=='__main__':render(Path(sys.argv[1]),Path(sys.argv[2]))
