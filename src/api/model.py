from pydantic import BaseModel, Field
from typing import List, Literal, Optional

# --- Request/Response models ---

class TokenRequest(BaseModel):
    user_name: str
    user_id: str | None = None # optional; ignored when looking up by name
    channel_id: str | None = None # join code from the group's invite link; defaults to DEFAULT_CHANNEL_ID


class TokenResponse(BaseModel):
    token: str
    user_id: str
    user_name: str
    channel_id: str # the channel the user was actually joined to


class StartAiAgentRequest(BaseModel):
    channel_id: str
    channel_type: str = "messaging"


class StartAiAgentResponse(BaseModel):
    status: str = "started"

class NewMessageRequest(BaseModel):
    cid: Optional[str]
    type: Optional[str]
    message: Optional[object]


class ChatMessage(BaseModel):
    role: Literal['user', 'assistant', 'system'] = Field(..., description='The role of the message sender')
    content: str = Field(..., description='The content of the message', min_length=1, max_length=3000)


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., description='List of messages in the conversation', min_length=1)


class ChatResponse(BaseModel):
    messages: List[ChatMessage] = Field(..., description='List of messages in the conversation')