from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")
    
    SECRET_KEY: str = "your-super-secret-key-change-in-prod"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480 # 8 hours work shift
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./burger_pos.db"
    
    # Default Printer Settings (can be overridden by ShopProfile)
    DEFAULT_PRINTER_IP: str = "mock"

settings = Settings()
