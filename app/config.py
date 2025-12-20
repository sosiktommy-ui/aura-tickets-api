from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    DATABASE_URL: str
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
