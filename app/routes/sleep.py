from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, status, Depends, Query
from app.schemas import SleepSessionCreate, SleepSessionResponse, SleepStats, WeeklySummary
from app.models.user import User
from app.models.sleep import SleepSession
from app.auth.jwt import get_current_user
from datetime import datetime, timedelta

router = APIRouter(prefix="/sleep", tags=["sleep"])

@router.post("/", response_model=SleepSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_sleep_session(
    session_data: SleepSessionCreate,
    current_user: User = Depends(get_current_user)
):
    """Log a new sleep session."""
    # Additional validation for reasonable sleep durations
    sleep_duration = session_data.sleep_end - session_data.sleep_start
    if sleep_duration.total_seconds() < 300:  # 5 minutes minimum
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sleep session must be at least 5 minutes"
        )
    
    if sleep_duration.total_seconds() > 86400:  # 24 hours maximum
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sleep session cannot exceed 24 hours"
        )
    
    # Create sleep session
    sleep_session = await SleepSession.create(
        user_id=current_user.id,
        sleep_start=session_data.sleep_start,
        sleep_end=session_data.sleep_end,
        quality_rating=session_data.quality_rating
    )
    
    return SleepSessionResponse.model_validate(sleep_session)


@router.get("/", response_model=Dict[str, Any])
async def get_sleep_sessions(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user)
):
    """Get user's sleep sessions with total count."""
    total_count = await SleepSession.filter(user_id=current_user.id).count()
    sessions = await SleepSession.filter(user_id=current_user.id).offset(skip).limit(limit).all()
    return {
        "count": total_count,
        "sessions": [SleepSessionResponse.from_orm(session) for session in sessions]
    }


@router.get("/stats", response_model=SleepStats)
async def get_sleep_stats(current_user: User = Depends(get_current_user)):
    """Get sleep statistics for the user."""
    sessions = await SleepSession.filter(user_id=current_user.id).all()
    
    if not sessions:
        return SleepStats(
            total_sessions=0,
            average_sleep_hours=0.0,
            average_quality_rating=None,
            total_sleep_time_minutes=0
        )
    
    # Calculate statistics
    total_sessions = len(sessions)
    total_sleep_minutes = sum(
        (session.sleep_end - session.sleep_start).total_seconds() / 60
        for session in sessions
    )
    average_sleep_hours = (total_sleep_minutes / 60) / total_sessions
    
    # Calculate average quality rating
    quality_ratings = [s.quality_rating for s in sessions if s.quality_rating is not None]
    average_quality_rating = sum(quality_ratings) / len(quality_ratings) if quality_ratings else None
    
    return SleepStats(
        total_sessions=total_sessions,
        average_sleep_hours=average_sleep_hours,
        average_quality_rating=average_quality_rating,
        total_sleep_time_minutes=int(total_sleep_minutes)
    )



@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sleep_session(
    session_id: int,
    current_user: User = Depends(get_current_user)
):
    """Delete a sleep session by ID."""
    # Find the sleep session
    session = await SleepSession.filter(
        id=session_id, 
        user_id=current_user.id
    ).first()
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sleep session not found"
        )
    
    # Delete the session
    await session.delete()
    
    return None


@router.get("/weekly-summary", response_model=WeeklySummary)
async def get_weekly_summary(current_user: User = Depends(get_current_user)):
    """Get weekly sleep summary analytics."""
    # Calculate date range for the past 7 days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)
    
    # Fetch sleep sessions for the past week
    sessions = await SleepSession.filter(
        user_id=current_user.id,
        sleep_start__gte=start_date,
        sleep_start__lte=end_date
    ).order_by('sleep_start').all()
    
    if not sessions:
        return WeeklySummary(
            week_start=start_date.date(),
            week_end=end_date.date(),
            total_sessions=0,
            total_sleep_hours=0.0,
            average_sleep_hours=0.0,
            average_bedtime="00:00",
            average_wake_time="00:00",
            average_quality_rating=None,
            daily_breakdown=[],
            sleep_consistency_score=0.0,
            quality_trend="stable"
        )
    
    # Calculate analytics
    total_sessions = len(sessions)
    total_sleep_minutes = sum(
        (session.sleep_end - session.sleep_start).total_seconds() / 60
        for session in sessions
    )
    total_sleep_hours = total_sleep_minutes / 60
    average_sleep_hours = total_sleep_hours / 7  # Average per day over 7 days
    
    # Calculate average bedtime and wake time
    bedtimes = [(session.sleep_start.hour * 60 + session.sleep_start.minute) for session in sessions]
    wake_times = [(session.sleep_end.hour * 60 + session.sleep_end.minute) for session in sessions]
    
    avg_bedtime_minutes = sum(bedtimes) / len(bedtimes) if bedtimes else 0
    avg_wake_minutes = sum(wake_times) / len(wake_times) if wake_times else 0
    
    avg_bedtime = f"{int(avg_bedtime_minutes // 60):02d}:{int(avg_bedtime_minutes % 60):02d}"
    avg_wake_time = f"{int(avg_wake_minutes // 60):02d}:{int(avg_wake_minutes % 60):02d}"
    
    # Calculate quality rating
    quality_ratings = [s.quality_rating for s in sessions if s.quality_rating is not None]
    average_quality_rating = sum(quality_ratings) / len(quality_ratings) if quality_ratings else None
    
    # Create daily breakdown
    daily_breakdown = []
    for i in range(7):
        day_date = (start_date + timedelta(days=i)).date()
        day_sessions = [s for s in sessions if s.sleep_start.date() == day_date]
        
        if day_sessions:
            day_sleep_minutes = sum(
                (s.sleep_end - s.sleep_start).total_seconds() / 60 
                for s in day_sessions
            )
            day_quality = sum(s.quality_rating for s in day_sessions if s.quality_rating) / len([s for s in day_sessions if s.quality_rating]) if any(s.quality_rating for s in day_sessions) else None
            
            daily_breakdown.append({
                "date": day_date.isoformat(),
                "sleep_hours": round(day_sleep_minutes / 60, 2),
                "quality_rating": round(day_quality, 1) if day_quality else None,
                "sessions_count": len(day_sessions)
            })
        else:
            daily_breakdown.append({
                "date": day_date.isoformat(),
                "sleep_hours": 0.0,
                "quality_rating": None,
                "sessions_count": 0
            })
    
    # Calculate sleep consistency score (based on variance in sleep duration)
    sleep_durations = [(session.sleep_end - session.sleep_start).total_seconds() / 3600 for session in sessions]
    if len(sleep_durations) > 1:
        avg_duration = sum(sleep_durations) / len(sleep_durations)
        variance = sum((d - avg_duration) ** 2 for d in sleep_durations) / len(sleep_durations)
        std_dev = variance ** 0.5
        # Convert to consistency score (lower std_dev = higher consistency)
        sleep_consistency_score = max(0, 100 - (std_dev * 20))
    else:
        sleep_consistency_score = 100 if sleep_durations else 0
    
    # Determine quality trend
    quality_trend = "stable"
    if len(quality_ratings) >= 3:
        recent_quality = sum(quality_ratings[-3:]) / 3
        earlier_quality = sum(quality_ratings[:-3]) / len(quality_ratings[:-3]) if len(quality_ratings) > 3 else recent_quality
        
        if recent_quality > earlier_quality + 0.3:
            quality_trend = "improving"
        elif recent_quality < earlier_quality - 0.3:
            quality_trend = "declining"
    
    return WeeklySummary(
        week_start=start_date.date(),
        week_end=end_date.date(),
        total_sessions=total_sessions,
        total_sleep_hours=round(total_sleep_hours, 2),
        average_sleep_hours=round(average_sleep_hours, 2),
        average_bedtime=avg_bedtime,
        average_wake_time=avg_wake_time,
        average_quality_rating=round(average_quality_rating, 1) if average_quality_rating else None,
        daily_breakdown=daily_breakdown,
        sleep_consistency_score=round(sleep_consistency_score, 1),
        quality_trend=quality_trend
    )