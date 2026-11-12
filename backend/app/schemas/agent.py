from pydantic import BaseModel
from typing import List, Optional, Literal

# ================= 1. 知识检索模块接口 =================

class KnowledgeQuery(BaseModel):
    age_band: Optional[str]  # 例如 "AGE_3_4" 或 None(全龄)
    topics: List[str]        # 例如 ["动物"]
    # 修复 2：严格对齐公共术语表，强制大写，防止拼写错误导致检索漏掉
    task_type: Literal["GUIDANCE", "RETRIEVAL", "SAFETY"] 
    query_text: str          # 用户输入或当前上下文

class KnowledgeItem(BaseModel):
    knowledge_item_id: int
    item_key: str
    content: str
    source_title: str
    source_ref: Optional[str] = None

class KnowledgeRetrievalResult(BaseModel):
    items: List[KnowledgeItem]
    has_results: bool        # 明确告知引导工作流：是否有可用知识

# ================= 2. 模型适配器接口 =================

class ModelGenerateRequest(BaseModel):
    system_prompt: str
    context_text: str
    user_input: str

class ModelGenerateResult(BaseModel):
    success: bool
    # 修复 1：给 text 增加默认值 ""，保证超时等失败场景下可以直接构造对象
    text: str = ""           
    error_code: Optional[str] = None  # 超时: TIMEOUT, 失败: API_ERROR

# ================= 3. 引导工作流 (AgentResult) 冻结 =================
# 严格对齐《接口契约 v0.4》中的 common.resultMapping 和 API-016/017 的响应

class AgentSource(BaseModel):
    knowledgeItemId: int
    sourceTitle: str

class AgentResult(BaseModel):
    # 对应 API-016/017 的 data 部分
    status: Literal["GENERATED", "FALLBACK", "BLOCKED"]
    outputText: str
    strategyType: Literal["CONTEXT_QUESTION", "CONCRETE_RECALL", "SKIP", "TIMEOUT_FALLBACK", "NO_KNOWLEDGE_FALLBACK", "SAFETY_BLOCK"]
    sources: List[AgentSource] = []
    
    # 以下字段由业务后端在保存数据库时补充，Agent 内部不直接生成
    # interactionEventId: int
    # revision: int