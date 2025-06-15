from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas import UserResponse, UserUpdate, PasswordReset
from app.models.user import User
from app.models.sleep import SleepSession
from app.auth.jwt import get_current_user, verify_password, get_password_hash

router = APIRouter(prefix="/user", tags=["user"])

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return UserResponse.from_orm(current_user)


@router.put("/profile", response_model=UserResponse)
async def update_user_profile(
    profile_data: UserUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update user profile information."""
    # Update user fields
    current_user.first_name = profile_data.first_name
    current_user.last_name = profile_data.last_name
    current_user.date_of_birth = profile_data.date_of_birth
    
    # Save changes
    await current_user.save()
    
    return UserResponse.from_orm(current_user)


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    password_data: PasswordReset,
    current_user: User = Depends(get_current_user)
):
    """Reset user password."""
    # Verify old password
    if not verify_password(password_data.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect old password"
        )
    
    # Hash new password and update
    current_user.hashed_password = get_password_hash(password_data.new_password)
    await current_user.save()
    
    return {"message": "Password updated successfully"}


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(current_user: User = Depends(get_current_user)):
    """Delete user account and all related data."""
    # Delete all sleep sessions associated with the user
    await SleepSession.filter(user_id=current_user.id).delete()
    
    # Delete the user account
    await current_user.delete()
    
    return None
