from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
import os

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/railway")
    API_SECRET_KEY: str = "aura_api_secret_key_2024_random"
    QR_SECRET_KEY: str = "aura_club_secret_2024"
    APP_NAME: str = "AURA Tickets API"
    DEBUG: bool = False
    
    class Config:
        env_file = ".env"
        extra = "allow"

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
