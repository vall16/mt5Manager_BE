from pydantic_settings  import BaseSettings

class Settings(BaseSettings):
    MT5_PATH: str
    API_PORT: int = 8081
    API_NAME: str = "MT5 API"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
