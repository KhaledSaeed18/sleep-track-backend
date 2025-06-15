from datetime import timedelta
from fastapi import APIRouter, HTTPException, status, Depends
from app.schemas import UserCreate, UserLogin, UserResponse, Token
from app.models.user import User
from app.auth.jwt import (
    authenticate_user,
    create_access_token,
    get_password_hash,
)
from app.config import settings

router = APIRouter(prefix="/auth", tags=["authentication"])

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserCreate):
    """Register a new user."""
    # Check if user already exists
    existing_user = await User.filter(email=user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    user = await User.create(
        email=user_data.email,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        date_of_birth=user_data.date_of_birth,
        hashed_password=hashed_password
    )
    
    return UserResponse.model_validate(user)


@router.post("/login", response_model=Token)
async def login(user_data: UserLogin):
    """Authenticate user and return JWT token."""
    user = await authenticate_user(user_data.email, user_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    
    # Create user response
    user_response = UserResponse.model_validate(user)
    
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "user": user_response
    }
