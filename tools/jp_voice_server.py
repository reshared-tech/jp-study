#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jp-chat 语音服务（快道）。

把我们这套系统的"大脑"——学习哲学 + ledger 进度 + N2/N3 导航——脱离 Claude Code
交互界面，headless 跑在一个本地 HTTP 接口后面。iPhone 的 Siri 快捷指令把你说的话
POST 过来，本脚本读取仓库里的真实状态、拼成 system 提示词、调 Claude API，返回
{speak, text}，Siri 朗读 speak（纯日语）、屏幕显示 text（中文讲解）。

每轮对话追加到 日志/voice-transcript-YYYY-MM-DD.md（慢道：之后回 /jp-chat 回写账本用）。
对话历史存 tools/.voice_session.json，支持多轮；说"重置/新话题"清空。

依赖：仅标准库。需环境变量 ANTHROPIC_API_KEY。
启动：ANTHROPIC_API_KEY=sk-... python3 tools/jp_voice_server.py
默认监听 0.0.0.0:8765（Tailscale 私网内 iPhone 可直连）。
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "ledger.json")
TARGET = os.path.join(REPO, "target-n2.json")
PHILOSOPHY = os.path.join(REPO, "学习哲学.md")
SESSION = os.path.join(REPO, "tools", ".voice_session.json")
LOG_DIR = os.path.join(REPO, "日志")

# ---- 供应商可切：环境变量 JP_PROVIDER = claude | gemini（默认 claude）----
PROVIDER = os.environ.get("JP_PROVIDER", "claude").lower()
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
DEFAULT_MODEL = {"claude": "claude-opus-4-8", "gemini": "gemini-3.5-flash"}
# 模型可被 JP_MODEL 覆盖（Gemini ID 拿不准时改这个，不用动代码）
MODEL = os.environ.get("JP_MODEL") or DEFAULT_MODEL.get(PROVIDER, "claude-opus-4-8")
MAX_OUT = int(os.environ.get("JP_MAX_TOKENS", "1024"))

CLAUDE_URL = "https://api.anthropic.com/v1/messages"

PORT = int(os.environ.get("JP_VOICE_PORT", "8765"))
MAX_HISTORY_TURNS = 16  # 保留最近 N 轮（user+assistant 各算一条的对数）


def active_key():
    return GEMINI_KEY if PROVIDER == "gemini" else CLAUDE_KEY


def today():
    return datetime.date.today().isoformat()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_state_brief():
    """从 ledger + target 算出今日计划要点，喂给 system。"""
    ledger = load_json(LEDGER, {"meta": {}, "items": []})
    target = load_json(TARGET, {})
    t = today()
    meta = ledger.get("meta", {})
    items = ledger.get("items", [])

    due, struggling, active = [], [], []
    for it in items:
        jp = it.get("jp", "")
        kana = it.get("kana", "")
        gloss = it.get("gloss", "")
        label = f"{jp}（{kana}）" if kana else jp
        active.append(label)
        if it.get("due", "9999") <= t:
            due.append(f"{label}={gloss}")
        if it.get("mastery", 5) <= 1:
            struggling.append(f"{label}={gloss}")

    cap = meta.get("struggling_cap", 6)
    gate = len(struggling) >= cap

    n3 = target.get("progress_n3", {}).get("covered", {})
    n2 = target.get("progress", {}).get("covered", {})
    vocab = target.get("components", {}).get("vocab", {})

    lines = []
    lines.append(f"今天日期：{t}")
    lines.append(f"阶段：{meta.get('phase_name','基础期')}；中日比例约 {int(meta.get('ratio_jp',0.1)*100)}% 日语。")
    lines.append(f"到期复习项（今天必须自然复现）：{('；'.join(due)) or '无'}")
    lines.append(f"挣扎项 mastery≤1（优先逼主动产出）：{('；'.join(struggling)) or '无'}")
    if gate:
        lines.append(f"⚠️ 挣扎项已达 {len(struggling)}≥{cap}：今天禁止加新词/新语法，只复习+练挣扎项。")
    else:
        lines.append(f"今日可加新内容：daily_new_quota={meta.get('daily_new_quota',5)}；优先 N3 断层(のに/にとって/について 一类)。")
    lines.append(f"N3 已覆盖语法：{('、'.join(n3.keys())) or '无'}")
    lines.append(f"N2 已覆盖语法：{('、'.join(n2.keys())) or '无'}")
    lines.append(f"已激活活词 {vocab.get('active_in_ledger','?')}/{vocab.get('target_cumulative','?')}。")
    lines.append(f"学过的词/语法（只能用这些，别冒没教过的）：{('、'.join(active)) or '无'}")
    return "\n".join(lines)


SYSTEM_TEMPLATE = """你是「日语陪聊导演」，运行在用户自建的 jp-chat 学习系统里——不是通用助手。
严格遵循下面这套**学习哲学（摘自 学习哲学.md）**和**当前真实进度**。

## 最高规则（冲突时按此取舍）
1. 聊天质量 > 教学完整性  2. 复习 > 新增  3. 连接 > 数量  4. 表达 > 记忆
5. 真实生活 > 教材  6. 兴趣 > 计划  7. 任何知识点若不能自然融入当前对话，就不要教。

## 怎么聊
- 讲解一律用**简体中文**，日语内容保留日文；生词给**假名提示**。
- 检索 > 讲解，生成 > 讲解：多问"如果是你，你会怎么说？"，逼用户主动开口（允许中日混说、说错）。
- 错误是金矿：先懂用户意图，再自然示范，做记忆钩子。
- 每轮 ≤1–3 新词或 ≤1 语法，且紧贴当前话题；撞到下面的"禁止加新"门槛就只复习。
- 让到期复习项在新话题里**自然复现**（隐式复习，别喊"开始复习"）。
- 话题取材用户真实生活兴趣：日本买房/看房、技术与自动化、学习方法、分析拆解系统推演结果、对象/对象弟弟/家人。

## 当前真实进度（每次请求实时注入）
{state}

## 输出格式（务必遵守）
只输出一个 JSON 对象，不要任何额外文字、不要 markdown 代码块：
{{"speak": "<只放这轮最该让用户跟读的 1 句日语，纯日文，给 Siri 朗读；若这轮不该产出日语就给一句简短日语鼓励>",
  "text": "<完整回复：中文讲解 + 日语示范 + 假名 + 反问逼输出，给屏幕显示>"}}
保持简短：text ≤ 120 字，适合语音场景。"""


def build_system():
    return SYSTEM_TEMPLATE.format(state=build_state_brief())


def load_history():
    return load_json(SESSION, [])


def save_history(msgs):
    msgs = msgs[-(MAX_HISTORY_TURNS * 2):]
    with open(SESSION, "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False, indent=2)


def reset_history():
    try:
        os.remove(SESSION)
    except FileNotFoundError:
        pass


def append_transcript(user_text, reply_text):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"voice-transcript-{today()}.md")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(f"# 语音陪聊 transcript · {today()}\n\n"
                    "> 慢道用：回 /jp-chat 时读这个，回写 ledger/target/日志/音频。\n\n")
        stamp = datetime.datetime.now().strftime("%H:%M")
        f.write(f"- **{stamp} 你**：{user_text}\n")
        f.write(f"- **{stamp} 老师**：{reply_text}\n\n")


def call_claude(system, messages):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_OUT,
        "system": system,
        "messages": messages,
    }).encode("utf-8")
    req = urllib.request.Request(CLAUDE_URL, data=body, method="POST", headers={
        "x-api-key": CLAUDE_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip()


def call_gemini(system, messages):
    # Gemini 用 user/model 两种角色（assistant→model），system 单独放。
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        # responseMimeType=json 直接逼它输出合法 JSON（正好对上我们的 {speak,text}）
        "generationConfig": {"maxOutputTokens": MAX_OUT,
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent?key={GEMINI_KEY}")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cands = data.get("candidates", [])
    if not cands:
        return ""
    parts = cands[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


def call_llm(system, messages):
    return call_gemini(system, messages) if PROVIDER == "gemini" else call_claude(system, messages)


def parse_reply(raw):
    """模型应只输出 JSON {speak,text}；容错：解析失败就整段当 text。"""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    try:
        obj = json.loads(s)
        speak = (obj.get("speak") or "").strip()
        text = (obj.get("text") or "").strip()
        if text:
            return speak or text, text
    except Exception:
        pass
    return raw, raw


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass  # 静音默认访问日志

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "today": today(),
                             "provider": PROVIDER, "model": MODEL,
                             "has_key": bool(active_key())})
        else:
            self._send(404, {"error": "use POST /jp"})

    def do_POST(self):
        if self.path.rstrip("/") != "/jp":
            self._send(404, {"error": "use POST /jp"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            self._send(400, {"error": "bad json"})
            return

        text = (req.get("text") or "").strip()
        if not text:
            self._send(400, {"error": "empty text"})
            return

        if text in ("重置", "新话题", "重新开始", "reset"):
            reset_history()
            self._send(200, {"speak": "はい、新しい話題を始めましょう。",
                             "text": "好，已重置，开个新话题。你想聊点什么？"})
            return

        if not active_key():
            envname = "GEMINI_API_KEY" if PROVIDER == "gemini" else "ANTHROPIC_API_KEY"
            self._send(500, {"error": f"no {envname}"})
            return

        history = load_history()
        history.append({"role": "user", "content": text})
        try:
            raw = call_llm(build_system(), history)
        except urllib.error.HTTPError as e:
            self._send(502, {"error": f"api {e.code}", "detail": e.read().decode("utf-8", "ignore")[:300]})
            return
        except Exception as e:
            self._send(502, {"error": str(e)})
            return

        speak, disp = parse_reply(raw)
        history.append({"role": "assistant", "content": raw})
        save_history(history)
        append_transcript(text, disp)
        self._send(200, {"speak": speak, "text": disp})


def main():
    if not active_key():
        envname = "GEMINI_API_KEY" if PROVIDER == "gemini" else "ANTHROPIC_API_KEY"
        print(f"⚠️  未设置 {envname}，/jp 会返回 500。", file=sys.stderr)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"jp 语音服务启动：http://0.0.0.0:{PORT}  (POST /jp, GET /health)")
    print(f"供应商：{PROVIDER}  模型：{MODEL}")
    print(f"仓库：{REPO}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
