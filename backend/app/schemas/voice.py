from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

MAX_ID = 9007199254740991


class PageVoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_session_id: StrictInt = Field(alias="readingSessionId", ge=1, le=MAX_ID)
    page_number: StrictInt = Field(alias="pageNumber", ge=1, le=MAX_ID)
    revision: StrictInt = Field(ge=0, le=MAX_ID)


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interaction_event_id: StrictInt | None = Field(default=None, alias="interactionEventId", ge=1, le=MAX_ID)
    reading_session_id: StrictInt | None = Field(default=None, alias="readingSessionId", ge=1, le=MAX_ID)
    page_number: StrictInt | None = Field(default=None, alias="pageNumber", ge=1, le=MAX_ID)
    revision: StrictInt | None = Field(default=None, ge=0, le=MAX_ID)

    @model_validator(mode="after")
    def exactly_one_source(self):
        if self.model_fields_set == {"interaction_event_id"} and self.interaction_event_id is not None:
            return self
        if self.model_fields_set == {"reading_session_id", "page_number", "revision"} and all(
                value is not None for value in (self.reading_session_id, self.page_number, self.revision)):
            return self
        raise ValueError("Choose exactly one complete event or page source")
