from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class LoginRequest(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: str
    username: str

class LoginResponse(BaseModel):
    success: bool
    user: UserResponse | None = None
    message: str | None = None

class ServerResponse(BaseModel):
    id: int
    user: str
    pwd: str
    server: str
    platform: str
    ip: str
    port: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

class ServerCheckRequest(BaseModel):
    server: str
    login: int
    password: str
    port: int
    path:str

class ServerRequest(BaseModel):
    user: str
    pwd: Optional[str] = None
    server: str
    platform: str
    ip: str
    port: int
    is_active: bool
    path: Optional[str] = None
    login: Optional[str] = None
    password: Optional[str] = None
    
class Trader(BaseModel):
    id: Optional[int]
    name: str
    status: str  # 'active' | 'inactive'
    master_server_id: Optional[int]
    slave_server_id: Optional[int]
    sl: Optional[float]
    tp: Optional[float]
    tsl: Optional[float]
    moltiplicatore: Optional[float]
    fix_lot: Optional[float]
    created_at: Optional[str]
    updated_at: Optional[str]


