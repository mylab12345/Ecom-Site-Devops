from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    database_url: str = "postgresql://ecom:ecom123@postgres:5432/notification_db"
    service_name: str = "notification-service"
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    class Config:
        env_file=".env"
settings=Settings()
