"""G05 guidance: use approved G09 knowledge and render a child-facing question about this page."""

import re
import unicodedata
from difflib import SequenceMatcher

from app.adapters.model import ModelAdapter
from app.agent import build_prompt, retrieve_knowledge
from app.agent.prompts import select_knowledge_items
from app.errors import ServiceError
from app.schemas.agent import AgentResult, AgentSource, KnowledgeQuery

HANDOFF = "这个问题我们请爸爸妈妈来一起说好吗？"
CHILD_QUESTIONS = ("这一页发生了什么？", "故事里有谁？")
PAGE_LEAD = "看看这一页，"
INSTRUCTIONS = {
    "INITIAL": "针对当前页提出一个适龄的情境问题。",
    "UNDERSTOOD": "孩子已理解上一题，请围绕同一情节进一步引导。",
    "NOT_UNDERSTOOD": "孩子没有听懂上一题，请换成更具体、容易观察的问题。",
}
# The model selects the focus. Every displayed word comes from the published page or these questions.
QUESTION_FORMS = {
    "INITIAL": ("你看见了谁？", "角色在做什么？", "角色现在是什么心情？"),
    "UNDERSTOOD": ("你从哪里看出了角色的想法？", "你猜接下来会发生什么？", "你为什么觉得角色会这样想？"),
    "NOT_UNDERSTOOD": ("你能指一指画面里的角色吗？", "角色手里有什么？", "角色的表情是什么样的？"),
}
# These polar questions can all be answered with the approved child-mode buttons.
CHILD_QUESTION_FORMS = {
    "INITIAL": ("图里有你认识的东西吗？", "图里也发生了这件事吗？", "你喜欢这一幕吗？"),
    "UNDERSTOOD": ("你觉得下一页还会看到刚才的东西吗？", "你猜下一页会有变化吗？", "你觉得下一页会让你开心吗？"),
    "NOT_UNDERSTOOD": ("你在画里找到了刚才读到的东西吗？", "你想再看看这里发生的事吗？", "你能找到喜欢的颜色吗？"),
}
FOCUS_CUES = (
    ("谁", "哪里", "哪个", "哪一个", "指一指"),
    ("做什么", "做了什么", "有什么", "发生", "接下来", "后来", "了吗", "有没有", "拿着"),
    ("心情", "表情", "感受", "感觉", "为什么", "怎么想"),
)
SENSITIVE = re.compile(
    r"诊断|确诊|(?:阅读|学习|发育|心理)障碍|多动症|自闭症|抑郁|焦虑症|智商|有病|笨蛋|你(?:真|很|太)?没用|真笨|"
    r"住址|家庭地址|家庭住处|真实姓名|电话号码|手机号|身份证|密码|保密|"
    r"自残|自杀|伤害自己|危险操作|玩火|触电|模仿危险"
)
# A paraphrase may invite observation, but cannot ask about the child or speak on their behalf.
PERSONAL_QUESTION = re.compile(r"您|我|你(?!觉得|猜|看见|看到|能指一指)")
SHORT_QUESTION = re.compile(r"[\u3400-\u9fff，、, ]{2,72}\?")
PAGE_CLAUSE = re.compile(r"[\u3400-\u9fffA-Za-z0-9 ]+")


def question_options(page_text: str, forms: tuple[str, ...]) -> dict[str, list[str]]:
    # Quote only short excerpts of the already published page. Never quote model or user prose.
    excerpts = []
    for clause in re.split(r"[，。！？；：、‘’“”「」\n,.!?;:]", page_text):
        clause = clause.strip()
        if len(clause) >= 2 and PAGE_CLAUSE.fullmatch(clause) and not SENSITIVE.search(clause):
            excerpts.append(clause if len(clause) <= 18 else clause[:18] + "…")
        if len(excerpts) == 3:
            break
    options = {}
    for excerpt in excerpts or [""]:
        lead = f"读到“{excerpt}”时，" if excerpt else PAGE_LEAD
        options[excerpt] = [lead + form for form in forms]
    return options


async def answer_question(model_adapter: ModelAdapter, *, input_text: str, age_band: str | None,
                          topics: list[str], page_text: str) -> AgentResult:
    if SENSITIVE.search(unicodedata.normalize("NFKC", input_text)):
        return AgentResult(status="BLOCKED", strategyType="SAFETY_BLOCK", outputText=HANDOFF)
    knowledge = retrieve_knowledge(KnowledgeQuery(age_band=age_band, topics=topics,
                                                  task_type="RETRIEVAL", query_text=input_text))
    selected = select_knowledge_items(knowledge.items, 5)
    fallback = "我们一起看看这一页的图和文字，找找线索吧。"
    if not selected:
        return AgentResult(status="FALLBACK", strategyType="NO_KNOWLEDGE_FALLBACK", outputText=fallback)

    # Only display a sentence from the approved page. Model text is never spoken directly.
    answers = [sentence.strip() for sentence in re.findall(r"[^。！？\n]+[。！？]?", page_text)
               if 1 <= len(sentence.strip()) <= 100 and not SENSITIVE.search(sentence)]
    request = build_prompt("STORY_ANSWER", age_band=age_band, page_text=page_text, items=selected,
                           user_input=input_text)
    request.system_prompt += ("\n本次为后台选句任务。根据当前页和提问选择最能直接回答的一句，"
                              "只返回该句的阿拉伯数字编号。如果这些句子不能回答，只返回0。")
    choices = {str(index): sentence for index, sentence in enumerate(answers, start=1)}
    request.context_text += "\n可选编号与原文：\n0. 无法从当页回答\n" + "\n".join(
        f"{key}. {value}" for key, value in choices.items())
    result = await model_adapter.generate(request)
    if not result.success:
        if result.error_code == "TIMEOUT":
            return AgentResult(status="FALLBACK", strategyType="TIMEOUT_FALLBACK", outputText=fallback)
        raise ServiceError("DEPENDENCY_UNAVAILABLE", "暂时无法回答问题")
    answer = result.text.strip()
    if answer == "0" or answer == fallback:
        return AgentResult(status="FALLBACK", strategyType="CONCRETE_RECALL", outputText=fallback)
    answer = choices.get(answer, answer)
    if answer not in answers:
        return AgentResult(status="BLOCKED", strategyType="SAFETY_BLOCK", outputText=HANDOFF)
    return AgentResult(status="GENERATED", strategyType="CONCRETE_RECALL", outputText=answer,
                       sources=[AgentSource(knowledgeItemId=item.knowledge_item_id, sourceTitle=item.source_title)
                                for item in selected])


def selected_question(text: str, options: dict[str, list[str]], *, child_forms: tuple[str, ...] = ()) -> str | None:
    candidate = unicodedata.normalize("NFKC", text.strip())
    for group in options.values():
        for option in group:
            if candidate == unicodedata.normalize("NFKC", option):
                return option
    for index, form in enumerate(child_forms):
        if candidate == unicodedata.normalize("NFKC", form):
            group = options[""] if "" in options else next(iter(options.values()))
            return group[index]
    # Providers can paraphrase a choice. Use its reading focus, never forward the paraphrase.
    if not SHORT_QUESTION.fullmatch(candidate) or PERSONAL_QUESTION.search(candidate) or SENSITIVE.search(candidate):
        return None
    excerpt = max(options, key=lambda part: SequenceMatcher(None, part, candidate).find_longest_match().size)
    return next((option for option, cues in zip(options[excerpt], FOCUS_CUES)
                 if any(cue in candidate for cue in cues)), None)


async def run_agent(model_adapter: ModelAdapter, *, feedback: str, age_band: str | None,
                    topics: list[str], page_text: str, previous_question: str, child_mode: bool) -> AgentResult:
    """Return a displayable result or raise a dependency error for the event ledger."""
    if previous_question and SENSITIVE.search(unicodedata.normalize("NFKC", previous_question)):
        return AgentResult(status="BLOCKED", strategyType="SAFETY_BLOCK", outputText=HANDOFF)
    knowledge = retrieve_knowledge(KnowledgeQuery(age_band=age_band, topics=topics,
                                                  task_type="GUIDANCE", query_text=page_text))
    selected = select_knowledge_items(knowledge.items, 5)
    if not selected:
        question = "你喜欢这一页吗？" if child_mode else "这个故事里，你最喜欢谁呀？"
        return AgentResult(status="FALLBACK", strategyType="NO_KNOWLEDGE_FALLBACK", outputText=question)

    forms = (CHILD_QUESTION_FORMS if child_mode else QUESTION_FORMS)[feedback]
    options = question_options(page_text, forms)
    if child_mode and len(options) > 1:
        # A body shared by several excerpts selects the page-wide question.
        options[""] = [PAGE_LEAD + form for form in forms]
    request = build_prompt("CONTEXT_QUESTION", age_band=age_band, page_text=page_text, items=selected,
                           user_input=INSTRUCTIONS[feedback])
    request.system_prompt += "根据年龄、页面和反馈，从候选问句中选最合适的一句，原样返回。"
    if previous_question:
        request.context_text += "\n当前页已保存的问题（仅作参考）：" + previous_question
    request.context_text += "\n候选问句：\n" + "\n".join(option for group in options.values() for option in group)
    result = await model_adapter.generate(request)
    if not result.success:
        if result.error_code != "TIMEOUT":
            raise ServiceError("DEPENDENCY_UNAVAILABLE", "文本模型未能生成引导问题")
        question = "你想再看看这一页吗？" if child_mode else "我们先看看画面上有什么吧？"
        return AgentResult(status="FALLBACK", strategyType="TIMEOUT_FALLBACK", outputText=question)
    question = selected_question(result.text, options, child_forms=forms if child_mode else ())
    if question is None:
        return AgentResult(status="BLOCKED", strategyType="SAFETY_BLOCK", outputText=HANDOFF)
    return AgentResult(
        status="GENERATED", outputText=question,
        strategyType="CONCRETE_RECALL" if feedback == "NOT_UNDERSTOOD" else "CONTEXT_QUESTION",
        sources=[AgentSource(knowledgeItemId=item.knowledge_item_id, sourceTitle=item.source_title)
                 for item in selected],
    )
