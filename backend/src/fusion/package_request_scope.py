"""Bounded admission checks for new player questions, outside frozen model wire.

This recognizes explicit external tasks and the existing meta-request policy.
It is not a classifier for arbitrary off-topic language or story relevance.
Only the actual player question/authorized reply target is inspected.
"""
import re
import unicodedata

from src.fusion.package_dialogue_model import refuses_meta_request


REQUEST_SCOPE_POLICY = 'package-request-scope/1.0'

# Task verbs must begin a sentence (with optional direct-request phrasing).
# A story mentioning weather, medicine, letters or someone else's request is
# not itself a request for an external service. No private text is searched.
_START = r'(?:^|[。！？!?；;\n])\s*(?:(?:请|請|麻烦|麻煩|帮我|幫我|给我|給我|替我|你能|能否|可否|可以|能不能|请你|請你)\s*){0,3}'
_EXTERNAL_TASKS = (
    r'(?:写|寫|编写|編寫|生成|调试|調試|运行|運行)(?:一段|一个|一個|一些)?(?:python|javascript|typescript|java|c\+\+|sql)(?:代码|代碼|程序|脚本|腳本)',
    r'(?:查|查询|查詢|查看|搜索|搜一下|查一下)(?:今天|今日|明天|当前|當前|现在|現在)(?:的)?(?:天气|天氣|天气预报|天氣預報)',
    r'(?:推荐|推薦|分析|预测|預測)(?:一下|一些|几只|幾隻|几支|幾支|一只|一隻)?(?:股票|基金|投资组合|投資組合)',
    r'(?:写|寫|生成|设计|設計)(?:一段|一篇|一份|一些)?(?:广告文案|廣告文案|营销文案|營銷文案|推广文案|推廣文案)',
)
_ENGLISH_TASKS = (
    r'(?:please\s+)?(?:write|generate|debug|run)\s+(?:some\s+|a\s+)?(?:python|javascript|typescript|java|sql)\s+(?:code|script)\b',
    r'(?:please\s+)?(?:check|look\s+up|search)\s+(?:today[’\x27]?s|tomorrow[’\x27]?s|current)\s+weather\b',
    r'(?:please\s+)?recommend\s+(?:some\s+)?stocks\b',
    r'(?:please\s+)?(?:write|generate)\s+(?:some\s+)?(?:advertising|marketing|ad)\s+copy\b',
)

# Direct requests for real-world harmful instructions are refused, while story
# questions such as "现场的炸弹是谁制作的？" remain admissible. This intentionally
# does not claim to recognize every unsafe paraphrase or all sensitive topics.
_HARMFUL_TASKS = (
    r'(?:教我|告诉我如何|告訴我如何|指导我|指導我)(?:如何|怎么|怎麼)?(?:制作|製作|制造|製造)(?:炸弹|炸彈|爆炸物)',
    r'(?:教我|告诉我如何|告訴我如何|指导我|指導我)(?:如何|怎么|怎麼)?(?:入侵|盗取|盜取)(?:他人|别人|別人)(?:账号|賬號|帐号|账户|手机|手機)',
    r'(?:写|寫|生成)(?:一段|一篇|一些)?(?:色情|露骨性描写|露骨性描寫)',
)


def needs_question_clarification(text: str) -> bool:
    """Recognize only stand-alone ambiguous identity/reference questions.

    A character name or contextual clause in the actual question is not dropped.
    In particular, "你是谁" is an explicit request to the selected character and
    "我是甲，你是谁" must not be mistaken for "我是谁".
    """
    compact = re.sub(r'[\s，,。.!！?？；;：:]', '', unicodedata.normalize('NFKC', text).casefold())
    compact = re.sub(r'^(?:(?:请问|請問|请告诉我|請告訴我))', '', compact)
    return bool(re.fullmatch(
        r'(?:我是[谁誰]|我是什么人|我是什麼人|[他她它]是[谁誰]|这是谁|這是誰|那是[谁誰]|whoami|whoishe|whoisshe)',
        compact,
    ))


def refuses_player_request(text: str) -> bool:
    text = unicodedata.normalize('NFKC', text)
    # Reuse, without modifying, the legacy bounded metadata refusal definition.
    if refuses_meta_request({'reply_to': 'target', 'discussion': [{'id': 'target', 'text': text}]}):
        return True
    compact = re.sub(r'[ \t\r\f\v]+', '', text.casefold())
    if any(re.search(_START + pattern, compact) for pattern in _EXTERNAL_TASKS):
        return True
    if any(re.search(_START + pattern, compact) for pattern in _HARMFUL_TASKS):
        return True
    return any(re.search(r'(?:^|[.!?;\n])\s*' + pattern, text.casefold()) for pattern in _ENGLISH_TASKS)
