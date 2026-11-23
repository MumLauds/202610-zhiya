"""QA-only, local reduction of a synthetic answer to closed semantic cues.

Nothing here scores a child, decides a response, or proves an answer correct.
Evidence/focus IDs describe references, never an answered focus.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.services.reading_agent import (
    ACTOR_ALIASES, ACTOR_WORD, DIRECT_SPEECH, NEAR_MISS, OBJECT_ALIASES, OBJECT_WORD, AnswerAction, AnswerSignals,
    _actions, _answer_cue, _answer_page, _answer_signals, _fact_phrase, _spoken_answer,
    answer_is_unsafe, page_clauses,
)
from qa.p6.co_reading_eval.qwen_decision import (
    Evidence, Question, SemanticSignals, _FEELING, _ROLE_PLAY, _question_parts,
)

_EXTRA_PRIVATE = re.compile(
    r"(?:[\w.+-]+\s*@\s*[\w.-]+\s*\.\s*[A-Za-z]{2,})|"
    r"(?:[\u3400-\u9fff]{1,16}(?:路|街|巷)\s*\d{1,6}\s*号)|"
    r"(?:(?:忽略|无视|绕过|别管)[^。！？!?]{0,14}(?:规则|指令|要求|提示))|"
    r"(?:(?:泄露|显示|念出|告诉我|输出)[^。！？!?]{0,14}(?:系统提示|开发者指令|系统规则))|"
    r"(?:disregard|reveal|print).{0,28}(?:system|developer).{0,12}(?:prompt|instructions)",
    re.IGNORECASE,
)
_ACKNOWLEDGEMENT = re.compile(r"(?:嗯{1,3}(?:,?(?:是这样|对|是的|我懂了))?|好(?:的|呀)?|知道了|明白了|懂了|是这样)")
_EXPLICIT_POSITIVE = {"是", "是的", "对", "对的", "有"}
_EXPLICIT_NEGATIVE = {"不", "没", "没有", "不对", "不是", "没做"}
_CONTRAST = re.compile(r"(?:不是|并非).{1,32}(?:而是|[,，]\s*是)[^,，]+")
_CORRECTION = re.compile(r"^不对[,，]|^(?:刚才说错了|我改一下)")
_QUESTION_ACT = re.compile(r"^(?:为什么|为啥|怎么|哪里|是谁|什么是|是不是|会不会|能不能|可不可以)|(?:吗|么)[?？]?$")
_EXPLICIT_UNSURE = re.compile(r"^(?:我)?(?:不明白|没听懂|忘了|不记得)")
_EMOTIONS = r"害怕|开心|高兴|着急|紧张|难过|伤心|生气|惊讶|平静|慌张|失望|兴奋|害羞|担心"
_EPISTEMIC = re.compile(
    r"^(?:(?:我)?猜|我觉得|我想大概|也许|说不定|可能|好像)|(?:可能|也许|好像)(?:已经|刚才|会|是|打开)"
    rf"|(?:可能|也许|好像)(?:有点|有些|很)?(?:{_EMOTIONS})"
)
_EMOTION_RELATION = re.compile(
    r"(?:(?:刚才|当时|现在|可能|也许|好像|是不是|不是|并不|没有|不|有点|有些|很|真|也|还|感到|感觉|是|在))*"
    rf"(?:{_EMOTIONS}|什么心情|什么感受|感觉怎么样)(?:了|呢|啊|吗)?"
)
_ROLE_FRAME = re.compile(rf"^(?:如果|假如|要是)(?:我|你)是({ACTOR_WORD.pattern})\s*[,;，；]")
_FABRICATION_MARKERS = re.compile(r"(?:想(?:用|把|让).{1,12}(?:变成|变出)|想.{0,12}魔法|在想象里)")
_OPEN_IMAGINATION = re.compile(r"^(?:我)?想象")
_PROSPECTIVE = re.compile(r"(?:准备|快要|将要|打算)(?:把|打开|撑开|拿起|走|跑|收起|递给)")
_IF_MARKER = re.compile(r"如果|假如|要是")
_IF_CONSEQUENCE = re.compile(r"[,;，；]\s*(?:(?:我|你|它|他|她|小兔|小熊|小狐狸)\s*)?(?:那么|就|可能(?:就)?会|会|也会|还会).{2,}")
_DOUBLE_NEGATION = re.compile(r"(?:不是|并非|没有|并不)\s*(?:没有|不曾|未曾|没|不(?!过|得))")
_REPORTED = re.compile(r"(?:说|讲|喊|问|告诉|听说)[,:，：]|(?:说|讲|喊|问)(?:小兔|小熊|小狐狸)")
_NEGATION = re.compile(r"没有|并非|并不|不是|从来没|未曾|没能|没(?:把|去|做|看|拿|带|到)|不(?:把|去|做|肯|愿意)|别(?:把|打|撑|拿)")
_PLAIN_STATEMENT = re.compile(r"^(?:(?:我|它|他|她|小兔|小熊|小狐狸)?(?:喜欢|想把|想给|想要)|(?:等)?(?:雨停|雨下完))")
_RECIPIENT = re.compile(rf"(?:递给|交给|送给|给了?)({ACTOR_WORD.pattern})")
_ID = re.compile(r"f[1-9][0-9]?")
_MAX_ANSWER = 200
_MAX_REFS = 2


@dataclass(frozen=True)
class ExtractionResult:
    signals: SemanticSignals
    unsafe_turn: bool = False


def _unsafe(text: str) -> bool:
    return answer_is_unsafe(text) or bool(_EXTRA_PRIVATE.search(text))


def _canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip(" ,。.!?")
    text = re.sub(r"雨(?:下完|停了)", "雨停", text)
    text = ACTOR_WORD.sub(lambda match: ACTOR_ALIASES.get(match[0], match[0]), text)
    return _fact_phrase(text)


def _same_action(answer: AnswerAction, candidate: AnswerAction) -> bool:
    # Negation is preserved separately: mentioning a denied action still refers to it.
    return (
        answer.verb == candidate.verb and answer.target != "未识别" and candidate.target != "未识别"
        and (not answer.actor or answer.actor == candidate.actor)
        and (not answer.target or answer.target == candidate.target)
        and (answer.verb not in {"打开", "收起", "拿起", "放下"} or bool(answer.target))
    )


def _references(
    raw: str, checked: str, signals: AnswerSignals, allowed: Sequence[Evidence], assertable_ids: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    def phrase(text: str) -> str:
        return _canonical(re.sub(r"^(?:因为|所以|由于|因此)", "", text))

    phrases = {phrase(clause) for clause in page_clauses(checked)}
    # A short evidence window cannot make an actorless answer unique. The
    # production signal keeps the full safe page unless a known question scopes it.
    source_actions = _actions(signals.evidence_page)
    ambiguous_ids: set[str] = set()
    mentioned: set[str] = set()
    corroborated: set[str] = set()
    for item in allowed:
        if phrase(item.quote) in phrases:
            mentioned.add(item.id)
            corroborated.add(item.id)

    for action, status in zip(signals.actions, signals.evidence):
        candidates = {
            item.id: [candidate for candidate in _actions(item.quote) if _same_action(action, candidate)]
            for item in allowed
        }
        options = {evidence_id for evidence_id, matches in candidates.items() if matches}
        if not action.actor and sum(_same_action(action, candidate) for candidate in source_actions) > 1:
            ambiguous_ids.update(options)
            continue
        if len(options) == 1:
            mentioned.update(options)
            if status == "有依据" and not (action.hypothetical or action.near_miss or signals.unverified_detail):
                evidence_id, = options
                if any(candidate.negated == action.negated and not (candidate.hypothetical or candidate.near_miss)
                       for candidate in candidates[evidence_id]):
                    corroborated.add(evidence_id)

    recipient = _RECIPIENT.fullmatch(checked.strip(" ,。.!?呀啊啦呢哦"))
    if recipient:
        # A short utterance can name the recipient without asserting a giver or object.
        actor = ACTOR_ALIASES.get(recipient[1], recipient[1])
        transfers = {
            item.id for item in allowed if not _NEGATION.search(item.quote)
            and any(ACTOR_ALIASES.get(match[1], match[1]) == actor for match in _RECIPIENT.finditer(item.quote))
        }
        if len(transfers) == 1:
            mentioned.update(transfers)
            corroborated.update(transfers)

    raw_fragment = raw.strip(" ,。.!?呀啊啦呢哦")
    if raw_fragment == "伞伞":
        raw_fragment = "雨伞"
    fragment_target = OBJECT_ALIASES.get(raw_fragment, raw_fragment)
    fragment_items = {
        item.id for item in allowed if any(
            OBJECT_ALIASES.get(name, name) == fragment_target
            for name in OBJECT_WORD.findall(item.quote)
        ) and not _NEGATION.search(item.quote)
    } if OBJECT_WORD.fullmatch(raw_fragment) else set()
    if fragment_items and not any(action.actor for action in signals.actions) and sum(
        OBJECT_ALIASES.get(name, name) == fragment_target for name in OBJECT_WORD.findall(signals.evidence_page)
    ) > 1:
        ambiguous_ids.update(fragment_items)
    elif len(fragment_items) == 1:
        mentioned.update(fragment_items)
        corroborated.update(fragment_items)

    # Only an exact full approved clause may carry the existing locally checked cue.
    cue = _answer_cue(checked, signals) if signals.scoped and signals.supports_page_reply else None
    corroborated.update(item.id for item in allowed if item.quote == cue)
    mentioned.update(corroborated)
    corroborated.intersection_update(assertable_ids)
    corroborated.difference_update(ambiguous_ids)
    mentioned.difference_update(ambiguous_ids)
    if (signals.stance in ("推测", "不确定") or _EXPLICIT_UNSURE.search(raw)
            or _EPISTEMIC.search(raw) or _OPEN_IMAGINATION.search(raw) or _FABRICATION_MARKERS.search(raw)
            or _IF_MARKER.match(raw) or _DOUBLE_NEGATION.search(raw) or _PROSPECTIVE.search(raw)
            or _REPORTED.search(raw) or _QUESTION_ACT.search(raw) or raw.endswith(("?", "？"))):
        corroborated.clear()
    return (tuple(item.id for item in allowed if item.id in corroborated)[:_MAX_REFS],
            tuple(item.id for item in allowed if item.id in mentioned)[:_MAX_REFS])


def _speech_and_polarity(raw: str, checked: str, signals: AnswerSignals, refs: tuple[str, ...]) -> tuple[str, str]:
    text = raw.strip(" ,。.!?！？，")
    if signals.stance == "不确定" or _EXPLICIT_UNSURE.search(text):
        return "UNSURE", "UNKNOWN"
    if _QUESTION_ACT.search(text) or raw.endswith(("?", "？")):
        return "ASKS", "UNKNOWN"
    if _DOUBLE_NEGATION.search(text):
        return "ASSERTS", "UNKNOWN"
    if _CONTRAST.search(text):
        return "CORRECTS", "MIXED"
    if _CORRECTION.search(text) and text not in _EXPLICIT_NEGATIVE:
        return "CORRECTS", "UNKNOWN"
    if _ACKNOWLEDGEMENT.fullmatch(text) or text in _EXPLICIT_POSITIVE:
        return "ACKNOWLEDGES", "AFFIRMS" if text in _EXPLICIT_POSITIVE else "UNKNOWN"
    if _IF_MARKER.match(text):
        return ("IMAGINES", "UNKNOWN") if _IF_CONSEQUENCE.search(text) else ("UNKNOWN", "UNKNOWN")
    if _OPEN_IMAGINATION.search(text) or _FABRICATION_MARKERS.search(text):
        return "IMAGINES", "UNKNOWN"
    if _EPISTEMIC.search(text):
        return "UNSURE", "UNKNOWN"
    if _REPORTED.search(text):
        return "ASSERTS", "UNKNOWN"

    if _PROSPECTIVE.search(text):
        return "ASSERTS", "UNKNOWN"
    direct = [action for action in signals.actions if not (action.hypothetical or action.near_miss)]
    unqualified = [part for part in page_clauses(text) if not re.search(NEAR_MISS, part)]
    negated = (any(action.negated for action in direct) or any(_NEGATION.search(part) for part in unqualified)
               or text in _EXPLICIT_NEGATIVE)
    positive = any(not action.negated for action in direct)
    if negated:
        return "ASSERTS", "MIXED" if positive else "NEGATES"
    if positive or signals.causal == "有当页因果依据":
        return "ASSERTS", "AFFIRMS"
    if refs or _PLAIN_STATEMENT.search(text) or checked != raw or any(action.near_miss for action in signals.actions):
        return "ASSERTS", "UNKNOWN"
    return "UNKNOWN", "UNKNOWN"


def _typed_focus(question: Question, body: str) -> tuple[str, str] | None:
    if question.intent == "EMOTION" and (feeling := _FEELING.fullmatch(body)):
        return "EMOTION", ACTOR_ALIASES.get(feeling[2], feeling[2])
    if question.intent == "IMAGINATION" and (role := _ROLE_PLAY.fullmatch(body)):
        return "ROLE_PLAY", ACTOR_ALIASES.get(role[1], role[1])
    return None


def _spoken_focuses(raw: str, speech: str) -> tuple[tuple[str, str], ...]:
    if role := _ROLE_FRAME.match(raw):
        # Role-playing Xiaoxiong is not an observation of Xiaoxiong's feelings.
        return (("ROLE_PLAY", ACTOR_ALIASES.get(role[1], role[1])),) if speech == "IMAGINES" else ()
    matches = []
    for clause in page_clauses(raw):
        names = list(ACTOR_WORD.finditer(clause))
        if len(names) == 1 and _EMOTION_RELATION.fullmatch(clause[names[0].end():]):
            actor = names[0][0]
            matches.append(("EMOTION", ACTOR_ALIASES.get(actor, actor)))
    return tuple(dict.fromkeys(matches))


def _focus_references(
    raw: str, speech: str, mentioned: tuple[str, ...], allowed: Sequence[Evidence],
    known_focuses: Mapping[str, Sequence[str]] | None, reviewed_focuses: Sequence[Question] | None,
) -> tuple[str, ...]:
    legacy = known_focuses or {}
    types: dict[str, set[tuple[str, str]]] = {}
    if reviewed_focuses is not None:
        # Only the caller's active-visit reviewed catalog can authorize a focus.
        available = {item.id: item.quote for item in allowed}
        typed_ids = set()
        legacy_in_visit = {}
        for question in reviewed_focuses:
            anchor, body = _question_parts(question.text)
            typed = _typed_focus(question, body)
            if typed or question.intent == "EMOTION":
                typed_ids.add(question.focus_id)
            ids = set(question.evidence_ids)
            if not ids <= available.keys() or (
                known_focuses is not None and ids != set(legacy.get(question.focus_id, ()))
            ):
                continue
            excerpts = [available[evidence_id] for evidence_id in question.evidence_ids]
            if anchor and anchor not in {unicodedata.normalize("NFKC", text) for text in excerpts}:
                continue
            if typed:
                actors = {ACTOR_ALIASES.get(name, name) for text in excerpts for name in ACTOR_WORD.findall(text)}
                if typed[1] in actors:
                    types.setdefault(question.focus_id, set()).add(typed)
            elif question.focus_id in legacy:
                legacy_in_visit[question.focus_id] = legacy[question.focus_id]
        legacy = {focus_id: ids for focus_id, ids in legacy_in_visit.items() if focus_id not in typed_ids}

    focus_ids = []
    typed_mentions = _spoken_focuses(raw, speech) if types else ()
    for mention in typed_mentions:
        options = [focus_id for focus_id, relations in types.items() if relations == {mention}]
        if len(options) == 1 and options[0] not in focus_ids:
            focus_ids.append(options[0])
    for evidence_id in mentioned:
        options = [focus_id for focus_id, sources in legacy.items() if _ID.fullmatch(focus_id) and evidence_id in sources]
        if len(options) == 1 and options[0] not in focus_ids:
            focus_ids.append(options[0])
    return tuple(focus_ids[:_MAX_REFS])


def extract_semantic_signals(
    *, question: str, answer: str, page_text: str, evidence: Sequence[Evidence] = (),
    known_focuses: Mapping[str, Sequence[str]] | None = None,
    reviewed_focuses: Sequence[Question] | None = None,
) -> ExtractionResult:
    """Recompute local cues. Omit the catalog for legacy behavior; pass only this visit's questions."""
    raw = unicodedata.normalize("NFKC", answer).strip()
    normalized_question = unicodedata.normalize("NFKC", question).strip()
    if _unsafe(raw) or _unsafe(normalized_question):
        return ExtractionResult(SemanticSignals(), unsafe_turn=True)
    if not raw or len(raw) > _MAX_ANSWER or not any(c.isalnum() for c in raw):
        return ExtractionResult(SemanticSignals())

    raw = DIRECT_SPEECH.sub("。", raw).strip()
    if not any(c.isalnum() for c in raw):
        return ExtractionResult(SemanticSignals())
    page = DIRECT_SPEECH.sub("。", _answer_page(page_text))
    clauses = set(page_clauses(page))
    # Keep sentence scope for a conditional leading clause; a comma is not a fact boundary.
    asserted_clauses = {
        clause for sentence in re.findall(r"[^。！？.!?\n]+[。！？.!?]*", page)
        if not (re.search(r"[?？]", sentence) or _IF_MARKER.search(sentence) or _EPISTEMIC.search(sentence.strip())
                or _REPORTED.search(sentence) or _DOUBLE_NEGATION.search(sentence))
        for clause in page_clauses(sentence)
    }
    allowed = tuple(item for item in evidence if item.quote in clauses and not _unsafe(item.quote))
    if len({item.id for item in allowed}) != len(allowed):
        allowed = ()
    # Reuse the production's narrowly grounded umbrella reduplication before other safe local forms.
    checked = _spoken_answer(raw, normalized_question, page)
    checked = re.sub(r"跑跑(?=了|呀|啊|[,.!?]|$)", "跑", checked)
    checked = re.sub(r"(?<=给)熊熊(?=啦|呀|啊|[,.!?]|$)", "小熊", checked)
    signals = _answer_signals(checked, page, normalized_question)
    assertable_ids = {item.id for item in allowed if item.quote in asserted_clauses}
    refs, mentioned = _references(raw, checked, signals, allowed, assertable_ids)
    speech, polarity = _speech_and_polarity(raw, checked, signals, refs)

    focus_ids = _focus_references(raw, speech, mentioned, allowed, known_focuses, reviewed_focuses)
    return ExtractionResult(SemanticSignals(
        speech_act=speech, polarity=polarity, referenced_evidence_ids=refs,
        referenced_focus_ids=focus_ids,
    ))
