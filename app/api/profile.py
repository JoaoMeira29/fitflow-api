from fastapi import APIRouter, HTTPException, status
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import CurrentUser, DbSession
from app.models.profile import Profile
from app.schemas.profile import ProfileRead, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.put("", response_model=ProfileRead)
async def upsert_profile(
    payload: ProfileUpdate, current_user: CurrentUser, db: DbSession
):
    values = payload.model_dump()
    # INSERT ... ON CONFLICT DO UPDATE creates or replaces the profile in one
    # atomic statement, so two simultaneous requests cannot both try to insert.
    statement = (
        insert(Profile)
        .values(user_id=current_user.id, **values)
        .on_conflict_do_update(index_elements=[Profile.user_id], set_=values)
        .returning(Profile)
    )
    profile = await db.scalar(
        statement, execution_options={"populate_existing": True}
    )
    await db.commit()
    return profile


@router.get("", response_model=ProfileRead)
async def read_profile(current_user: CurrentUser, db: DbSession):
    profile = await db.get(Profile, current_user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    return profile
