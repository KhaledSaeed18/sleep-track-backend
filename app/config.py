import os
from typing import Optional

try:
    from pydantic import BaseSettings, Field
except ImportError:
    # Fallback for older pydantic versions
    from pydantic.v1 import BaseSettings, Field


class Settings(BaseSettings):
    """Application configuration settings loaded from environment variables."""
    
    # Database Configuration
    database_url: str = Field(
        env="DATABASE_URL",
        description="PostgreSQL database connection URL"
    )

    # JWT Configuration
    secret_key: str = Field(
        env="SECRET_KEY",
        description="Secret key for JWT token signing"
    )
    algorithm: str = Field(
        default="HS256",
        env="ALGORITHM",
        description="Algorithm used for JWT token signing"
    )
    access_token_expire_minutes: int = Field(
        default=30,
        env="ACCESS_TOKEN_EXPIRE_MINUTES",
        description="JWT token expiration time in minutes"
    )
    
    # Application Environment
    environment: str = Field(
        default="development",
        env="ENVIRONMENT",
        description="Application environment (development, production, etc.)"
    )
    debug: bool = Field(
        default=True,
        env="DEBUG",
        description="Enable debug mode"
    )
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment.lower() == "development"


settings = Settings()

# Tortoise ORM configuration
TORTOISE_ORM = {
    "connections": {"default": settings.database_url},
    "apps": {
        "models": {
            "models": ["app.models.user", "app.models.sleep"],
            "default_connection": "default",
        },
    },
}
